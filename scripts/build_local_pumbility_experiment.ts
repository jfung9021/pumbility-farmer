import { readFile, mkdir, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { COMBINED_LOCAL_RESULTS_PATH, AVERAGE_PUMBILITY_LOCAL_RESULTS_PATH, HYBRID_PUMBILITY_LOCAL_RESULTS_PATH, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { applyAveragePumbility, recoverMaximumPumbility } from "../lib/pumbility-experiment.ts";

const hybrid = process.argv.includes("--hybrid");
const inputIndex = process.argv.indexOf("--input");
const input = inputIndex < 0 ? hybrid ? path.join(path.dirname(HYBRID_PUMBILITY_LOCAL_RESULTS_PATH), "maximum.json") : COMBINED_LOCAL_RESULTS_PATH : process.argv[inputIndex + 1];
if (!input) throw new Error("--input requires a path.");
const output = hybrid ? HYBRID_PUMBILITY_LOCAL_RESULTS_PATH : AVERAGE_PUMBILITY_LOCAL_RESULTS_PATH;
const base = validateLocalAnalysisPayload(JSON.parse(await readFile(input, "utf8")), "combined");
assert.equal(base.schemaVersion, hybrid ? 47 : 44, "Unexpected source generation for local experiment.");
const candidate = validateLocalAnalysisPayload(applyAveragePumbility(base), "combined");
assert.deepEqual(recoverMaximumPumbility(candidate), base);
await mkdir(path.dirname(output), { recursive: true });
await writeFile(`${output}.tmp`, `${JSON.stringify(candidate)}\n`, "utf8");
await rename(`${output}.tmp`, output);
console.log(JSON.stringify({ input, output, schemaVersion: candidate.schemaVersion,
  rated: [...candidate.singles, ...candidate.doubles].filter(r => r.tierMetrics!.pumbility.estimatedDifficulty !== null).length }));
