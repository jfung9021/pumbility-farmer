import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { cachedFileReader } from "../lib/local-file-cache.ts";
import { tierBrowserJson } from "../lib/tier-browser-payload.ts";
import type { AnalysisPayload } from "../lib/types";

async function temporaryFiles(run: (directory: string) => Promise<void>) {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-tier-loading-"));
  try { await run(directory); }
  finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(tmpdir()));
    await rm(directory, {recursive:true, force:true});
  }
}

test("concurrent tier requests share validation and repeated requests reuse the result", async () => {
  await temporaryFiles(async directory => {
    const file = path.join(directory, "tiers.json");
    await writeFile(file, "first");
    let loads = 0;
    let release!: () => void;
    let started!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const firstLoad = new Promise<void>(resolve => { started = resolve; });
    const read = cachedFileReader(async filePath => {
      loads++; started(); await gate;
      return {value:await readFile(filePath, "utf8")};
    });
    const requests = Array.from({length:4}, () => read(file));
    await firstLoad;
    release();
    const results = await Promise.all(requests);
    assert.equal(loads, 1);
    assert.ok(results.every(result => result === results[0]));
    assert.equal(await read(file), results[0]);
    assert.equal(loads, 1);
  });
});

test("replacing data or selecting another file invalidates the cached revision", async () => {
  await temporaryFiles(async directory => {
    const first = path.join(directory, "first.json"), second = path.join(directory, "second.json");
    await writeFile(first, "old"); await writeFile(second, "other");
    let loads = 0;
    const read = cachedFileReader(async filePath => { loads++; return readFile(filePath, "utf8"); });
    assert.equal(await read(first), "old");
    await writeFile(first, "new");
    const changed = new Date(Date.now() + 1000);
    await utimes(first, changed, changed);
    assert.equal(await read(first), "new");
    assert.equal(await read(second), "other");
    assert.equal(loads, 3);
  });
});

test("failed validation is retried and cannot evict a newer file's successful load", async () => {
  await temporaryFiles(async directory => {
    const bad = path.join(directory, "bad.json"), good = path.join(directory, "good.json");
    await writeFile(bad, "bad"); await writeFile(good, "good");
    let reject!: (error: Error) => void;
    let started!: () => void;
    let loads = 0;
    const firstLoad = new Promise<void>(resolve => { started = resolve; });
    const read = cachedFileReader(async filePath => {
      loads++;
      if (filePath === bad) {
        started();
        return new Promise<string>((_, fail) => { reject = fail; });
      }
      return readFile(filePath, "utf8");
    });
    const failed = assert.rejects(read(bad), /invalid tiers/);
    await firstLoad;
    assert.equal(await read(good), "good");
    reject(new Error("invalid tiers")); await failed;
    assert.equal(await read(good), "good");
    assert.equal(loads, 2);
    let attempts = 0;
    const retry = cachedFileReader(async () => {
      if (++attempts === 1) throw new Error("invalid tiers");
      return "fixed";
    });
    await assert.rejects(retry(good), /invalid tiers/);
    assert.equal(await retry(good), "fixed");
    assert.equal(attempts, 2);
  });
});

test("browser projection removes rollback copies and retains active tier data without mutation", () => {
  const payload = {
    schemaVersion:57,
    sourceAwareBaseline:{privateHistory:"unused"},
    summary:{method:{submittedSssplusCalibration:{sharedScale:.69, fitIdentity:{target:995000}}}},
    doubles:[{
      chartId:"chart", level:18,
      submittedSssplusCalibrationBaseline:{scoring:{changed:{estimatedDifficulty:null},added:[]}},
      scoringVariants:{general:{estimatedDifficulty:18.3},sss:{estimatedDifficulty:18.4},sssplus:{
        estimatedDifficulty:18.5, difficultyCi95Low:18.2, difficultyCi95High:18.8,
        initialFit:{estimatedDifficulty:18.5},scoringTarget:995000,
        scoringSubmittedCalibration:{effect:1.8},scoringRefinement:{range:{lower:18,upper:19}},
      }},
      pumbilityVariants:{sssplus:{estimatedDifficulty:18.6}},
      tierMetrics:{clearing:{estimatedDifficulty:18.7}},
      chartSourceExclusions:{rules:[{source:"Phoenix",reason:"changed chart"}]},
    }],
  } as unknown as AnalysisPayload;
  const before = structuredClone(payload);
  const json = tierBrowserJson(payload), browser = JSON.parse(json);
  assert.equal(browser.sourceAwareBaseline, undefined);
  assert.equal(browser.doubles[0].submittedSssplusCalibrationBaseline, undefined);
  assert.deepEqual(browser.doubles[0].scoringVariants, payload.doubles[0].scoringVariants);
  assert.deepEqual(browser.doubles[0].pumbilityVariants, payload.doubles[0].pumbilityVariants);
  assert.deepEqual(browser.doubles[0].tierMetrics, payload.doubles[0].tierMetrics);
  assert.deepEqual(browser.doubles[0].chartSourceExclusions, payload.doubles[0].chartSourceExclusions);
  assert.deepEqual(browser.summary, payload.summary);
  assert.deepEqual(payload, before);
  assert.equal(tierBrowserJson(payload), json);
});
