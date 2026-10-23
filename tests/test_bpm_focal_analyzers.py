"""Versioned focal categorization without changing legacy scoring contracts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.skill_radar import router
from official_player_comparison import (ComparisonUnavailableError, aggregate_bpm_skill,
    _chart_metadata, build_official_comparison, read_comparison_profile, write_comparison_artifacts)
from pumbility_contract import (combined_tier_blob_path, recommendation_blob_path,
    recommendation_phoenix2_shard_path)
from skill_radar import (BPM_BUCKETS, aggregate_bpm_progression, bpm_bucket, build_skill_radar,
    build_suggestion_catalog)
from skill_radar_runtime import SKILL_METADATA_PATH, _cache, _catalog_cache, read_skill_radar
from song_bpm_metadata import bpm_metadata_identity, enrich_charts_bpm

ROOT = Path(__file__).resolve().parents[1]
KEY = "a" * 20
POLICY = "focal-v1"
STAMP = "2026-10-24T00:00:00+00:00"
EXPECTED = {
    "* this game does not exist *": 230, "Alice in Misanthrope": 172,
    "Appassionata": 210, "Aragami": 196, "Banya Classic Remix": 170,
    "Banya-P Classic Remix": 180, "Beat of The War": 202, "Beat of The War 2": 190,
    "Beat of the War 2 - FULL SONG -": 190, "BRAIN POWER": 173,
    "CHAOS AGAIN": 230, "Come to Me": 107, "Crash-Landing Rendezvous": 190,
    "Galaxy Collapse": 320, "Giselle": 152, "Halcyon": 191, "Karyawisata": 173,
    "Love is a Danger Zone 2 - FULL SONG -": 162,
    "Love is a Danger Zone 2 Try To B.P.M": 169, "Love Witch": 170,
    "Lucid(PIU Edit)": 190, "Mental Rider": 140, "Neo Catharsis": 210,
    "Neo Catharsis - SHORT CUT -": 210, "PaPa helloizing": 158,
    "Reincarnate in “A”": 220, "Switronic": 120, "The Devil": 240,
    "Transmission": 169, "Tream Vook of the war": 202, "Turkey Virus": 162,
    "Versailles": 175,
}


def chart(cid="t", **changes):
    return {"id": cid, "songName": "Transmission", "songType": "Arcade", "type": "Single",
            "level": 24, "bpmMin": 100, "bpmMax": 225, **changes}


def score(cid="t"):
    return {"chartId": cid, "playerId": "private", "score": 1_000_000, "plate": "PG", "isBroken": False}


def tiers(charts):
    return {"generatedAtUtc": STAMP, "singles": [{**row, "chartId": row["id"], "difficulty": "S24",
        "pumbilityVariants": {"sssplus": {"estimatedDifficulty": 24.5}}} for row in charts], "doubles": []}


class Store:
    def __init__(self, charts):
        self.values = {
            recommendation_blob_path(): {"generationKey": "g", "players": [{"playerKey": KEY,
                "internalPlayerId": "private", "inputShard": 0, "displayName": "PLAYER"}]},
            recommendation_phoenix2_shard_path("g", 0): {"players": [{"playerId": "private", "scores": [score()]}]},
            combined_tier_blob_path(): tiers(charts),
            SKILL_METADATA_PATH: {"schemaVersion": 1, "charts": charts},
        }

    def get_json(self, path):
        return self.values.get(path)

    def read_skill_radar_score_counts(self, player_id=None):
        return {"private": 100}


class FocalAnalyzerTests(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        _catalog_cache.clear()

    def test_half_open_boundaries_use_scalar_without_rounding_or_endpoint_fallback(self):
        for value, expected in ((149.999, 0), (150, 1), (159.999, 1), (160, 2),
                (169, 2), (169.999, 2), (170, 3), (219.999, 7), (220, 8)):
            with self.subTest(value=value):
                self.assertEqual(bpm_bucket({"bpmFocal": value}, bpm_policy=POLICY), expected)
        for value in (None, 0, -1, True, float("nan"), float("inf")):
            self.assertEqual(bpm_bucket(chart(bpmFocal=value), bpm_policy=POLICY), "unknownBpm")
        self.assertEqual(bpm_bucket(chart()), "spanningBpm")

    def test_all_32_accepted_variants_agree_in_personal_official_and_suggestions(self):
        research = json.loads((ROOT / "config/phoenix2-song-primary-bpm-research.json").read_text(encoding="utf-8"))["songs"]
        self.assertEqual({row["songName"] for row in research}, set(EXPECTED))
        rows = enrich_charts_bpm([chart(str(i), songName=row["songName"], songType=row["songType"])
            for i, row in enumerate(research)], mix="Phoenix2")
        catalog = build_suggestion_catalog(tiers(rows), rows, bpm_policy=POLICY)
        suggestions = {row["chartId"]: row for row in catalog["charts"]}
        official = [{**row, "chartId": row["id"], "mode": "singles"} for row in rows]
        for row in rows:
            with self.subTest(song=row["songName"]):
                value = EXPECTED[row["songName"]]
                index = 0 if value < 150 else min(8, int((value - 150) // 10) + 1)
                self.assertEqual(row["bpmFocal"], value)
                self.assertEqual(bpm_bucket(row, bpm_policy=POLICY), index)
                self.assertEqual(suggestions[row["id"]]["bpmBucketId"], str(index))
                personal = aggregate_bpm_progression(rows, [score(row["id"])], bpm_policy=POLICY)
                self.assertEqual(personal["buckets"][index]["singles"]["scoreCount"], 1)
                compared = aggregate_bpm_skill(official, {row["id"]: 1_000_000}, bpm_policy=POLICY)
                self.assertEqual(compared[index]["contributingCount"], 1)

    def test_focal_profile_preserves_display_range_and_legacy_shape(self):
        rows = [chart()]
        legacy_catalog = build_suggestion_catalog(tiers(rows), rows)
        legacy = build_skill_radar(rows, [score()], legacy_catalog, player_key=KEY,
            display_name="PLAYER", generated_at_utc=STAMP)
        self.assertEqual(legacy["methodVersion"], 6)
        self.assertFalse(any(key.startswith("bpmMetadata") or key == "bpmPolicy" for key in legacy))
        self.assertEqual(legacy_catalog["charts"], [])
        catalog = build_suggestion_catalog(tiers(rows), rows, bpm_policy=POLICY)
        result = build_skill_radar(rows, [score()], catalog, player_key=KEY,
            display_name="PLAYER", generated_at_utc=STAMP, bpm_policy=POLICY)
        self.assertEqual(result["methodVersion"], 8)
        self.assertEqual(result["buckets"][4]["selectedCharts"], 1)
        candidate = catalog["charts"][0]
        self.assertEqual((candidate["bpmMin"], candidate["bpmMax"], candidate["bpmFocal"]), (100, 225, 169))
        changed = {**catalog, "bpmMetadataRevision": "0" * 64}
        with self.assertRaisesRegex(ValueError, "revision"):
            build_skill_radar(rows, [], changed, player_key=KEY, display_name="PLAYER",
                generated_at_utc=STAMP, bpm_policy=POLICY)

    def test_official_future_song_fallback_keeps_exact_variants_and_fixed_focal(self):
        current = {"a": chart("a", songName="A future song", songType="FullSong", bpmMin=None, bpmMax=None)}
        songs = [{"name": "A future song", "type": "Arcade", "bpm": {"min": 180, "max": 180}},
                 {"name": "A future song", "type": "FullSong", "bpm": {"min": 156.118, "max": 156.118}}]
        row = _chart_metadata(current, [], songs, bpm_policy=POLICY)[0]
        self.assertEqual((row["songType"], row["bpmMin"], row["bpmMax"], row["bpmFocal"]),
            ("FullSong", 156.118, 156.118, 156.118))

    def test_focal_groups_select_five_weakest_and_five_strongest_without_overlap(self):
        rows = [chart(f"{mode}-{i}", songName=f"Fixture {mode} {i}", type=mode,
            bpmMin=bucket["min"] or 140, bpmMax=bucket["min"] or 140)
            for i, bucket in enumerate(BPM_BUCKETS) for mode in ("Single", "Double")]
        rows = enrich_charts_bpm(rows)
        scores = [score(row["id"]) for row in rows]
        aggregate = aggregate_bpm_progression(rows, scores, bpm_policy=POLICY)
        # Give the eighteen mode/range pairs known ascending normalized ratings.
        for i, bucket in enumerate(BPM_BUCKETS):
            for offset, mode in enumerate(("singles", "doubles")):
                rating = (2*i+offset+1)*5
                ceiling = bucket["radarScale"][mode]["maximumAlignedLevel"]
                aggregate["buckets"][i][mode]["equivalentLevel"] = 10+(ceiling-10)*rating/100+offset
        catalog = build_suggestion_catalog({"generatedAtUtc":STAMP}, rows, bpm_policy=POLICY)
        with patch("skill_radar.aggregate_bpm_progression", return_value=aggregate):
            result = build_skill_radar(rows, scores, catalog, player_key=KEY,
                display_name="PLAYER", generated_at_utc=STAMP, bpm_policy=POLICY)
        self.assertEqual(result["groups"]["weaknesses"],
            ["singles:0", "doubles:0", "singles:1", "doubles:1", "singles:2"])
        self.assertEqual(result["groups"]["strengths"],
            ["doubles:8", "singles:8", "doubles:7", "singles:7", "doubles:6"])
        self.assertEqual(len(result["groups"]["everythingElse"]), 8)
        members = sum(result["groups"].values(), [])
        self.assertEqual(len(members), 18)
        self.assertEqual(len(set(members)), 18)
        for bucket in result["buckets"]:
            self.assertIn(bucket["key"], result["groups"][bucket["group"]])

    def test_five_range_groups_preserve_empty_and_sparse_strengths(self):
        for rows, scores, expected_strengths in (([], [], []), ([chart()], [score()], ["singles:2"])):
            with self.subTest(evidence=len(scores)):
                catalog = build_suggestion_catalog(tiers(rows), rows, bpm_policy=POLICY)
                result = build_skill_radar(rows, scores, catalog, player_key=KEY,
                    display_name="PLAYER", generated_at_utc=STAMP, bpm_policy=POLICY)
                self.assertEqual(len(result["groups"]["weaknesses"]), 5)
                self.assertEqual(result["groups"]["strengths"], expected_strengths)
                self.assertEqual(len(set(sum(result["groups"].values(), []))), 18)

    def test_runtime_json_and_relational_loaders_resolve_and_invalidate_focal_revision(self):
        store = Store([chart()])
        first = read_skill_radar(store, KEY, bpm_policy=POLICY)
        self.assertEqual(first["methodVersion"], 8)
        self.assertIs(first, read_skill_radar(store, KEY, bpm_policy=POLICY))
        store.read_skill_radar_metadata = lambda: store.values[SKILL_METADATA_PATH]
        self.assertEqual(read_skill_radar(store, KEY, bpm_policy=POLICY), first)
        identity = {**bpm_metadata_identity(), "bpmMetadataRevision": "f" * 64}
        with patch("skill_radar_runtime.bpm_metadata_identity", return_value=identity), \
                patch("skill_radar.bpm_metadata_identity", return_value=identity):
            changed = read_skill_radar(store, KEY, bpm_policy=POLICY)
            self.assertIsNot(changed, first)
            self.assertEqual(changed["bpmMetadataRevision"], "f" * 64)
        app = FastAPI()
        app.include_router(router)
        with patch("api.skill_radar.PrivateBlobStore", return_value=store):
            client = TestClient(app)
            self.assertEqual(client.get(f"/api/skill-radar?playerKey={KEY}").json()["methodVersion"], 6)
            self.assertEqual(client.get(f"/api/skill-radar?playerKey={KEY}&bpmPolicy={POLICY}").json()["methodVersion"], 8)
            self.assertEqual(client.get(f"/api/skill-radar?playerKey={KEY}&bpmPolicy=wrong").status_code, 400)

    def test_official_versioned_generation_changes_and_readers_reject_mixed_revision(self):
        rows = [chart()]
        canonical = "player:" + "1" * 64
        merged = {"manifestSha256": "m", "sourceDates": {}, "boards": {"t": {canonical: 1_000_000}},
            "_privateProvenance": {"t": {canonical: {"observations": []}}}}
        source = {"_currentCharts": {"t": rows[0]}, "_captures": {}}
        with patch("official_player_comparison.merge_official_scores", return_value=merged):
            old, _ = build_official_comparison(source, chart_metadata=rows)
            index, profiles = build_official_comparison(source, chart_metadata=rows, bpm_policy=POLICY)
        self.assertNotEqual(index["generation"], old["generation"])
        self.assertEqual((old["schemaVersion"], index["schemaVersion"]), (2, 3))
        self.assertNotIn("bpmFocal", old["charts"][0])
        self.assertEqual(index["charts"][0]["bpmFocal"], 169)
        key = next(iter(profiles))
        index["players"][0]["recordedScoreCount"] = 200
        prefix = "comparison"
        path = f"{prefix}/generations/{index['generation']}/players/{key}.json"
        store = Store([])
        store.values = {f"{prefix}/index.json": index, path: profiles[key]}
        self.assertEqual(read_comparison_profile(store, key, prefix=prefix, bpm_policy=POLICY), profiles[key])
        store.values[path] = {**profiles[key], "bpmMetadataRevision": "f" * 64}
        with self.assertRaises(ComparisonUnavailableError):
            read_comparison_profile(store, key, prefix=prefix, bpm_policy=POLICY)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            write_comparison_artifacts(index, profiles, target)
            previous = (target / "index.json").read_bytes()
            with self.assertRaises(ValueError):
                write_comparison_artifacts(index, {}, target)
            self.assertEqual((target / "index.json").read_bytes(), previous)

    def test_local_focal_generation_is_complete_and_failed_staging_preserves_pointer(self):
        from scripts.build_local_skill_radar import build, write_json
        rows = [score()] + [{**score(str(i)), "isBroken": True} for i in range(99)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tier_file = root / "tiers.json"
            tier_file.write_text(json.dumps(tiers([chart()])), encoding="utf-8")
            loaded = ([{"playerId": "private", "username": "PLAYER"}], [chart()], rows)
            with patch("scripts.build_local_skill_radar.load_snapshot", return_value=loaded):
                report = build(root, root / "out", tiers_path=tier_file, bpm_policy=POLICY)
                pointer = root / "out/focal-v1/current.json"
                previous = pointer.read_bytes()
                generation = root / "out/focal-v1/generations" / report["generation"]
                self.assertEqual(report["profileVersion"], 13)
                self.assertTrue((generation / "players" / (report["players"][0] + ".json")).is_file())
                def interrupted(path, value):
                    if path.name == "players.json":
                        raise OSError("interrupted staging")
                    write_json(path, value)
                with patch("scripts.build_local_skill_radar.write_json", side_effect=interrupted):
                    with self.assertRaisesRegex(OSError, "interrupted"):
                        build(root, root / "out", tiers_path=tier_file, bpm_policy=POLICY)
                self.assertEqual(pointer.read_bytes(), previous)
                with self.assertRaisesRegex(ValueError, "every eligible"):
                    build(root, root / "out", tiers_path=tier_file, player_key=KEY, bpm_policy=POLICY)


if __name__ == "__main__":
    unittest.main()
