from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from api.official_comparison import get_official_comparison, get_official_comparison_players
from official_player_comparison import (
    DEFAULT_BLOB_PREFIX, METHOD, ComparisonGenerationChangedError, ComparisonPlayerNotFoundError,
    ComparisonUnavailableError, aggregate_bpm_skill, bpm_bucket, build_official_comparison,
    read_comparison_index, read_comparison_profile, write_comparison_artifacts,
)
from official_scoring_sources import import_research_scoring_sources
from phoenix2_pumbility import GRADE_BANDS, grade_for_score, phoenix2_pumbility


def chart(number, name="Example", mode="Single", level=24, bpm=155):
    return {"id": f"00000000-0000-0000-0000-{number:012d}", "songName": name, "type": mode,
            "level": level, "noteCount": 1000, "bpmMin": bpm, "bpmMax": bpm}


def radar_chart(number, mode="singles", level=24, bpm=155, name="Example"):
    return {"chartId": str(number), "songName": name, "mode": mode, "level": level, "bpmMin": bpm, "bpmMax": bpm}


def entry(pid, tag="PLAYER#1234", score=990000):
    return {"playerId": pid, "gameTag": tag, "score": score, "place": 1, "isSupplemented": False}


def manifest(root, catalog, old, new):
    source = root / "research"
    for mix, observations in (("Phoenix", old), ("Phoenix2", new)):
        (source / mix).mkdir(parents=True)
        (source / f"{mix}-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        for current in catalog:
            cid = current["id"]
            board = {"chartId": cid, "mix": mix, "asOf": "2026-10-11T00:00:00Z", "entries": observations.get(cid, [])}
            (source / mix / f"{cid}.json").write_text(json.dumps(board), encoding="utf-8")
    return import_research_scoring_sources(source, root / "private/manifest.json")


class MemoryStore:
    def __init__(self, values):
        self.values = values

    def get_json(self, path):
        return self.values.get(path)


class OfficialPlayerComparisonTests(unittest.TestCase):
    def test_bpm_requires_whole_valid_interval_in_exact_bucket(self):
        self.assertEqual(bpm_bucket(149.999, 149.999), 0)
        self.assertEqual(bpm_bucket(150, 159.999), 1)
        self.assertEqual(bpm_bucket(160, 169.999), 2)
        self.assertEqual(bpm_bucket(219, 219.999), 7)
        self.assertEqual(bpm_bucket(220, 999), 8)
        for low, high in ((150, 160), (149, 150), (220, 219), (0, 20), (None, 150), (150, None), (True, 150), (float("nan"), 160)):
            self.assertIsNone(bpm_bucket(low, high))

    def test_perfect_pg_anchor_sparse_padding_top_five_and_doubles_alignment(self):
        charts = [radar_chart(i) for i in range(6)]
        scores = {str(i): 1_000_000 for i in range(6)}
        full = aggregate_bpm_skill(charts, scores)[1]
        self.assertAlmostEqual(full["pg"], 24)
        self.assertAlmostEqual(full["radarScore"], 100 * 14 / 15)
        self.assertEqual((full["recordedCount"], full["contributingCount"]), (6, 5))
        self.assertEqual([row["chartId"] for row in full["contributors"]], [str(i) for i in range(5)])
        self.assertEqual(len({row["chartId"] for row in full["contributors"]}), 5)
        sparse = aggregate_bpm_skill(charts, {str(i): 1_000_000 for i in range(4)})[1]
        self.assertAlmostEqual(sparse["pg"], 14.6, places=3)
        self.assertEqual(sparse["contributingCount"], 4)
        self.assertEqual(len(sparse["contributors"]), 4)
        double = aggregate_bpm_skill([radar_chart(i, "doubles", 25) for i in range(5)], scores)[10]
        self.assertAlmostEqual(double["pg"], full["pg"])
        self.assertAlmostEqual(double["radarScore"], full["radarScore"])
        self.assertEqual(aggregate_bpm_skill([], {})[0], {"mode": "singles", "bucketIndex": 0, "pg": None, "radarScore": None, "recordedCount": 0, "contributingCount": 0, "contributors": []})

    def test_sg_below_perfect_boundary_and_grades_recomputed(self):
        charts = [radar_chart(i) for i in range(5)]
        boundaries = {0, 999999, 1000000}
        boundaries.update(threshold for threshold, _, _ in GRADE_BANDS)
        boundaries.update(threshold - 1 for threshold, _, _ in GRADE_BANDS if threshold > 0)
        for score in sorted(boundaries):
            with self.subTest(score=score):
                result = aggregate_bpm_skill(charts, {str(i): score for i in range(5)})[1]
                plate = "Perfect Game" if score == 1000000 else "Superb Game"
                cents = round(phoenix2_pumbility("Single", 24, grade_for_score(score), plate) * 100)
                base = (cents / 100) / (760 / 750)
                expected = base / 7.5 - 27 if base <= 375 else 23 + (base - 375) / 15
                self.assertAlmostEqual(result["pg"], expected)
                self.assertEqual(result["contributors"], [
                    {"chartId": str(i), "score": score, "pumbilityCents": cents,
                     "assumedPlate": "PG" if score == 1000000 else "SG"}
                    for i in range(5)
                ])
        sg = aggregate_bpm_skill(charts, {str(i): 999999 for i in range(5)})[1]
        pg = aggregate_bpm_skill(charts, {str(i): 1000000 for i in range(5)})[1]
        self.assertGreater(pg["pg"], sg["pg"])
        mg_cents = round(phoenix2_pumbility("Single", 24, "SSS+", "Marvelous Game") * 100)
        self.assertGreater(sg["contributors"][0]["pumbilityCents"], mg_cents)

    def test_contributors_use_highest_pumbility_with_deterministic_title_and_chart_id_ties(self):
        charts = [radar_chart(1, level=20, name="Highest raw score"),
                  radar_chart(2, level=28, name="Highest Pumbility"),
                  radar_chart(3, name="Zulu"), radar_chart(4, name="alpha"),
                  radar_chart(5, name="alpha"), radar_chart(6, name="Beta"),
                  radar_chart(7, name="Delta")]
        scores = {str(i): 995000 for i in range(1, 8)}
        scores.update({"1": 1000000, "2": 990000})
        selected = aggregate_bpm_skill(charts + [charts[1]], scores)[1]
        self.assertEqual(selected["recordedCount"], 7)
        self.assertEqual([row["chartId"] for row in selected["contributors"]], ["2", "4", "5", "6", "7"])
        self.assertEqual(aggregate_bpm_skill(list(reversed(charts)), dict(reversed(list(scores.items()))))[1], selected)
        for count in range(1, 6):
            with self.subTest(count=count):
                result = aggregate_bpm_skill(charts, {str(i): scores[str(i)] for i in range(1, count + 1)})[1]
                self.assertEqual(len(result["contributors"]), result["contributingCount"])
                total = sum(row["pumbilityCents"] for row in result["contributors"]) / 100
                base = (total / 5) / (760 / 750)
                expected = max(0, base / 7.5 - 27 if base <= 375 else 23 + (base - 375) / 15)
                self.assertAlmostEqual(result["pg"], expected)

    def test_raw_max_identity_all_catalog_rows_metadata_and_public_privacy(self):
        a, b, c, excluded = chart(1, "Zulu"), chart(2, "Alpha", "Double", 25), chart(3, "Alpha", level=20), chart(4, "Below", level=19)
        for without_bpm in (b, c):
            without_bpm.pop("bpmMin")
            without_bpm.pop("bpmMax")
        # Manifest target scope excludes lower charts, but catalogs retain them.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            loaded = manifest(root, [a, b, c, excluded],
                              {a["id"]: [entry(123456789, score=999000)], b["id"]: [entry(5, "OTHER#5678")]},
                              {a["id"]: [entry(987654321, score=995000)]})
            index, profiles = build_official_comparison(loaded, chart_metadata=[a],
                songs=[{"name": "Alpha", "bpm": {"min": 150, "max": 160}}], generated_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
            self.assertEqual(len(index["players"]), 2)
            self.assertEqual(index["schemaVersion"], 2)
            self.assertEqual(index["method"]["version"], "official-comparison-sg-perfect-pg-v2")
            self.assertEqual(index["method"]["plateAssumption"], "SG below 1,000,000; PG at exactly 1,000,000")
            self.assertEqual([r["songName"] for r in index["charts"]], ["Alpha", "Alpha", "Zulu"])
            self.assertEqual({r["chartId"] for r in index["charts"]}, {a["id"], b["id"], c["id"]})
            profile = next(p for p in profiles.values() if p["displayName"] == "PLAYER#1234")
            self.assertEqual(profile["scores"], {a["id"]: 999000})
            self.assertEqual(profile["recordedScoreCount"], 1)
            self.assertEqual(next(row for row in index["players"] if row["playerKey"] == profile["playerKey"])["recordedScoreCount"], 1,
                             "The same chart on both Phoenix leaderboards must count only once")
            self.assertEqual(profile["schemaVersion"], 2)
            self.assertEqual(profile["radarEligibleScoreCount"], 1)
            self.assertNotIn(c["id"], profile["scores"])
            other = next(p for p in profiles.values() if p["displayName"] == "OTHER#5678")
            self.assertEqual(other["radarEligibleScoreCount"], 0)
            self.assertTrue(all(row["pg"] is None for row in other["buckets"]))
            self.assertTrue(all(row["contributors"] == [] for row in other["buckets"]))
            encoded = json.dumps({"index": index, "profiles": profiles})
            for secret in ("123456789", "987654321", "playerId", "privateProvenance", "winningSources"):
                self.assertNotIn(secret, encoded)
            # Rebuilds preserve keys/generation and emit only Windows-safe names.
            index2, profiles2 = build_official_comparison(loaded, chart_metadata=[a], songs=[{"name": "Alpha", "bpm": {"min": 150, "max": 160}}])
            self.assertEqual(index2["generation"], index["generation"])
            self.assertEqual(profiles2, profiles)
            with patch("official_player_comparison.METHOD", {**METHOD,
                       "version": "official-comparison-mg-perfect-pg-v1",
                       "plateAssumption": "MG below 1,000,000; PG at exactly 1,000,000"}):
                legacy_index, _ = build_official_comparison(loaded, chart_metadata=[a],
                    songs=[{"name": "Alpha", "bpm": {"min": 150, "max": 160}}])
            self.assertNotEqual(index["generation"], legacy_index["generation"])
            self.assertEqual(index["players"], legacy_index["players"])
            write_comparison_artifacts(index, profiles, root / "output")
            published = json.loads((root / "output/index.json").read_bytes())
            self.assertEqual(published, index)
            for key, expected in profiles.items():
                self.assertNotIn(":", key)
                path = root / "output/generations" / index["generation"] / "players" / f"{key}.json"
                self.assertEqual(json.loads(path.read_bytes()), expected)

    def test_ambiguous_same_tags_never_merge_source_identities(self):
        a = chart(1)
        with tempfile.TemporaryDirectory() as temp:
            loaded = manifest(Path(temp), [a], {a["id"]: [entry(1), entry(2)]}, {a["id"]: [entry(3)]})
            index, profiles = build_official_comparison(loaded)
            self.assertEqual(len(index["players"]), 3)
            self.assertEqual(len(profiles), 3)

    def test_reader_and_routes_missing_generation_invalid_player_and_success(self):
        key, generation = "op_" + "1" * 64, "2" * 64
        index = {"schemaVersion": 2, "generation": generation, "players": [{"playerKey": key, "recordedScoreCount": 200}]}
        profile = {"schemaVersion": 2, "generation": generation, "playerKey": key}
        profile_path = f"{DEFAULT_BLOB_PREFIX}/generations/{generation}/players/{key}.json"
        values = {f"{DEFAULT_BLOB_PREFIX}/index.json": index, profile_path: profile}
        store = MemoryStore(values)
        self.assertEqual(read_comparison_index(store), index)
        self.assertEqual(read_comparison_profile(store, key, generation), profile)
        for unsupported_version in (1, 3):
            with self.subTest(schemaVersion=unsupported_version):
                values[f"{DEFAULT_BLOB_PREFIX}/index.json"] = {**index, "schemaVersion": unsupported_version}
                with self.assertRaises(ComparisonUnavailableError):
                    read_comparison_index(store)
                values[f"{DEFAULT_BLOB_PREFIX}/index.json"] = index
                values[profile_path] = {**profile, "schemaVersion": unsupported_version}
                with self.assertRaises(ComparisonUnavailableError):
                    read_comparison_profile(store, key, generation)
                values[profile_path] = profile
        with self.assertRaises(ComparisonGenerationChangedError):
            read_comparison_profile(store, key, "old")
        with self.assertRaises(ValueError):
            read_comparison_profile(store, "../private")
        with self.assertRaises(ComparisonPlayerNotFoundError):
            read_comparison_profile(store, "op_" + "3" * 64)
        with patch("api.official_comparison.PrivateBlobStore", return_value=store):
            self.assertEqual(get_official_comparison_players().status_code, 200)
            self.assertEqual(get_official_comparison(key, generation).status_code, 200)
            self.assertEqual(get_official_comparison(key, "old").status_code, 409)
            self.assertEqual(get_official_comparison("../private", "").status_code, 400)
            self.assertEqual(get_official_comparison("op_" + "3" * 64, "").status_code, 404)
            del values[profile_path]
            self.assertEqual(get_official_comparison(key, "").status_code, 503)
            values.clear()
            self.assertEqual(get_official_comparison_players().status_code, 503)
        with self.assertRaises(ComparisonUnavailableError):
            read_comparison_index(store)

    def test_player_roster_requires_at_least_200_combined_chart_scores(self):
        generation = "2" * 64
        keys = ["op_" + digit * 64 for digit in ("1", "2", "3")]
        rows = [{"playerKey": key, "recordedScoreCount": count} for key, count in zip(keys, (199, 200, 201))]
        index = {"schemaVersion": 2, "generation": generation, "players": rows}
        store = MemoryStore({f"{DEFAULT_BLOB_PREFIX}/index.json": index})
        self.assertEqual(read_comparison_index(store)["players"], rows[1:])
        self.assertEqual(index["players"], rows, "Filtering must not mutate the stored roster")
        with self.assertRaises(ComparisonPlayerNotFoundError):
            read_comparison_profile(store, keys[0], generation)
        with patch("api.official_comparison.PrivateBlobStore", return_value=store):
            response = get_official_comparison_players()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.body)["players"], rows[1:])
            self.assertEqual(get_official_comparison(keys[0], generation).status_code, 404)
        for invalid in (None, True, -1, 200.5):
            with self.subTest(recordedScoreCount=invalid):
                index["players"] = [{"playerKey": keys[0], "recordedScoreCount": invalid}]
                with self.assertRaises(ComparisonUnavailableError):
                    read_comparison_index(store)


if __name__ == "__main__":
    unittest.main()
