import assert from "node:assert/strict";
import test from "node:test";
import { recommendationModeFromSearchParams, recommendationViewFromSearchParams } from "../lib/page-view-state.ts";

test("Pro Farming preserves its three modes and normalizes unsupported modes", () => {
  for (const mode of ["overall", "singles", "doubles", "coop", "invalid", ""]) {
    const params = new URLSearchParams({ view: "pro", mode, player: "abc123" });
    assert.equal(recommendationViewFromSearchParams(params), "pro");
    assert.equal(recommendationModeFromSearchParams(params), mode === "singles" || mode === "doubles" ? mode : "overall");
  }
});

test("Normal recommendation and Top 50 links retain their selected mode", () => {
  for (const view of ["recommendations", "top50"]) {
    for (const mode of ["singles", "doubles", "coop"] as const) {
      const params = new URLSearchParams({ view, mode });
      assert.equal(recommendationViewFromSearchParams(params), view);
      assert.equal(recommendationModeFromSearchParams(params), mode);
    }
  }
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams("view=invalid")), "recommendations");
});
test("Recommendations restores the saved view unless the URL selects a view", () => {
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams(), "pro"), "pro");
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams(), "top50"), "top50");
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams(), "invalid"), "recommendations");
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams("view=recommendations"), "pro"), "recommendations");
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams("view=top50"), "pro"), "top50");
});
