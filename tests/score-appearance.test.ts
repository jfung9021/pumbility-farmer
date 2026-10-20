import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { gradeColor, gradeForScore, NEUTRAL_RESULT_COLOR, normalizePlate, plateColor, plateLabel, scoreColor } from "../lib/score-appearance.ts";

test("Phoenix 2 grade boundaries match the backend authority, including zero and absent values", () => {
  const source = readFileSync(new URL("../phoenix2_pumbility.py", import.meta.url), "utf8");
  const bands = [...source.split("GRADE_BANDS:")[1].split("SINGLE_GRADE_PENALTY_UNITS")[0].matchAll(/\(([\d_]+), "([A-Z+]+)",/g)].map(match => [Number(match[1].replaceAll("_", "")), match[2]] as const);
  assert.equal(bands.length, 16);
  bands.forEach(([threshold, grade], index) => {
    assert.equal(gradeForScore(threshold), grade);
    if (threshold > 0) assert.equal(gradeForScore(threshold - 1), bands[index + 1][1]);
  });
  assert.equal(gradeForScore(1_000_001), "SSS+");
  assert.equal(gradeForScore(0), "F");
  for (const value of [null, undefined, NaN, Infinity, -1]) assert.equal(gradeForScore(value), null);
  assert.equal(scoreColor(null), NEUTRAL_RESULT_COLOR);
  assert.equal(scoreColor(0), gradeColor("F"));
});

test("all actual plate codes and full names normalize independently of grades", () => {
  const entries = {PG: "Perfect Game", UG: "Ultimate Game", EG: "Extreme Game", SG: "Superb Game", MG: "Marvelous Game", TG: "Talented Game", FG: "Fair Game", RG: "Rough Game"};
  for (const [code, label] of Object.entries(entries)) {
    assert.equal(normalizePlate(` ${code.toLowerCase()} `), code);
    assert.equal(normalizePlate(label.toUpperCase()), code);
    assert.equal(plateLabel(code), label);
    assert.equal(plateColor(code), plateColor(label));
    assert.notEqual(plateColor(code), NEUTRAL_RESULT_COLOR);
  }
  assert.equal(normalizePlate("SSS+"), null);
  assert.equal(normalizePlate(null), null);
  assert.equal(plateColor("unknown"), NEUTRAL_RESULT_COLOR);
  assert.equal(gradeColor("unknown"), NEUTRAL_RESULT_COLOR);
  assert.equal(plateLabel("unrecognized"), "unrecognized");
  assert.equal(gradeColor("sss+"), "#7FC7EF");
  assert.equal(plateColor("SG"), "#EDBB2E");
});
