import assert from "node:assert/strict";
import test from "node:test";
import { playerProfileHref, readPlayerPreference, serializePlayerPreference } from "../lib/player-profile.ts";

test("saved personal profile round trips only a version and the public player key", () => {
  const key = "a".repeat(20);
  const serialized = serializePlayerPreference(key);
  assert.deepEqual(JSON.parse(serialized), { version: 1, playerKey: key });
  assert.equal(readPlayerPreference(serialized), key);
  assert.equal(readPlayerPreference(null), "");
  for (const malformed of ["{", "null", "[]", '{"version":2,"playerKey":"a"}', '{"version":1,"playerKey":null}', '{"version":1,"playerKey":" "}']) {
    assert.throws(() => readPlayerPreference(malformed));
  }
});

test("temporary player links carry only player context between personal destinations", () => {
  assert.equal(playerProfileHref("/tier-list?mode=doubles#charts", "b"), "/tier-list?mode=doubles&player=b#charts");
  assert.equal(playerProfileHref("/recommendations?player=a", "b"), "/recommendations?player=b");
  assert.equal(playerProfileHref("/skill-radar", "a&mode=coop"), "/skill-radar?player=a%26mode%3Dcoop");
  assert.equal(playerProfileHref("/tier-list", ""), "/tier-list?player=");
  for (const href of ["/", "/official-leaderboard-comparison?left=op_abc", "https://example.com/tier-list", "//example.com/tier-list"]) {
    assert.equal(playerProfileHref(href, "b"), href);
  }
  assert.equal(playerProfileHref("/recommendations?mode=singles", null), "/recommendations?mode=singles");
});
