from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_song_bpm_runtime import build, main, render, write_preview
from song_bpm_metadata import (
    BPM_POLICY, BpmMetadataError, ROOT, bpm_bucket_index, bpm_metadata_identity,
    compile_runtime_catalog, enrich_chart_bpm, enrich_charts_bpm,
    load_runtime_catalog, resolve_song_bpm, validate_runtime_catalog,
)


def authority_documents():
    catalog = {
        "schemaVersion": 1, "mix": "Phoenix2",
        "songs": [
            {"name": "Fictional Variable", "type": "Arcade", "bpm": {"min": 100, "max": 225}},
            {"name": "Fictional Variable - FULL SONG -", "type": "FullSong", "bpm": {"min": 140, "max": 140}},
            {"name": "Fictional Fixed", "type": "Arcade", "bpm": {"min": 81.5, "max": 81.5}},
        ],
    }
    intervals = {"schemaVersion": 1, "songs": {}}
    research = {
        "schemaVersion": 1, "mix": "Phoenix2", "songs": [{
            "songName": "Fictional Variable", "songType": "Arcade",
            "originalBpmRange": {"min": 100, "max": 225}, "recommendedPrimaryBpm": 180,
            "selectionStatus": "user-preferred", "userDirection": {"recommendedPrimaryBpm": 169},
        }],
    }
    focals = {"schemaVersion": 1, "mix": "Phoenix2", "songs": {}}
    return catalog, intervals, research, focals


class SongBpmMetadataTests(unittest.TestCase):
    def setUp(self):
        self.documents = authority_documents()
        self.runtime = compile_runtime_catalog(*self.documents)

    def test_retained_user_precedence_and_full_range_identity(self):
        chart = {"id": "stable-id", "songName": "Fictional Variable", "type": "Double", "bpmMin": 1, "bpmMax": 1}
        result = enrich_chart_bpm(chart, catalog=self.runtime)
        self.assertEqual((result["bpmMin"], result["bpmMax"], result["bpmFocal"]), (100, 225, 169))
        self.assertEqual(result["songType"], "Arcade")
        self.assertEqual(result["id"], "stable-id")
        self.assertEqual(chart["bpmMin"], 1)
        full = resolve_song_bpm("Fictional Variable - FULL SONG -", "FullSong", catalog=self.runtime)
        self.assertEqual(full["bpmFocal"], 140)
        with self.assertRaisesRegex(BpmMetadataError, "mismatches"):
            enrich_chart_bpm({**chart, "songType": "FullSong"}, catalog=self.runtime)
        self.assertIsNone(resolve_song_bpm("fictional variable", catalog=self.runtime))

    def test_explicit_overrides_win_and_interval_precision_is_retained(self):
        catalog, intervals, research, focals = deepcopy(self.documents)
        focals["songs"]["Fictional Variable"] = {"recommendedPrimaryBpm": 170, "confirmedBy": "user"}
        intervals["songs"]["Fictional Fixed"] = {"bpm": {"min": 156.118, "max": 156.118}, "confirmedBy": "user"}
        runtime = compile_runtime_catalog(catalog, intervals, research, focals)
        self.assertEqual(resolve_song_bpm("Fictional Variable", catalog=runtime)["bpmFocal"], 170)
        self.assertEqual(resolve_song_bpm("Fictional Fixed", catalog=runtime)["bpmFocal"], 156.118)

    def test_future_ranges_never_acquire_an_automatic_upper_bound(self):
        future = {"songName": "Future Song", "songType": "Arcade", "bpmMin": 120, "bpmMax": 300}
        self.assertIsNone(enrich_chart_bpm(future, catalog=self.runtime)["bpmFocal"])
        self.assertEqual(enrich_chart_bpm({**future, "bpmFocal": 210}, catalog=self.runtime)["bpmFocal"], 210)
        fixed = enrich_chart_bpm({**future, "bpmMin": 156.118, "bpmMax": 156.118}, catalog=self.runtime)
        self.assertEqual(fixed["bpmFocal"], 156.118)
        self.assertEqual(enrich_chart_bpm({"songName": "Unknown"}, catalog=self.runtime)["bpmFocal"], None)

    def test_phoenix1_is_an_explicit_unchanged_passthrough(self):
        original = {"songName": "Fictional Variable", "bpmMin": 100, "bpmMax": 225, "type": "Single"}
        for mix in ("Phoenix", "Phoenix1", "phoenix1"):
            result = enrich_chart_bpm(original, mix=mix, catalog=self.runtime)
            self.assertEqual(result, original)
            self.assertIsNot(result, original)
        self.assertIsNone(resolve_song_bpm("Fictional Variable", mix="Phoenix"))

    def test_invalid_unknown_focals_and_intervals_are_rejected(self):
        for value in (True, False, 0, -1, float("nan"), float("inf"), "169", 99, 226):
            with self.subTest(value=value), self.assertRaises(BpmMetadataError):
                enrich_chart_bpm({"songName": "Unknown", "bpmMin": 100, "bpmMax": 225, "bpmFocal": value}, catalog=self.runtime)
        for low, high in ((True, 170), (0, 170), (180, 170), (None, 170)):
            with self.subTest(low=low), self.assertRaises(BpmMetadataError):
                enrich_chart_bpm({"songName": "Unknown", "bpmMin": low, "bpmMax": high}, catalog=self.runtime)

    def test_ambiguous_legacy_names_fail_instead_of_picking_a_variant(self):
        documents = deepcopy(self.documents)
        documents[0]["songs"].append({"name": "Fictional Fixed", "type": "Remix", "bpm": {"min": 150, "max": 150}})
        runtime = compile_runtime_catalog(*documents)
        with self.assertRaisesRegex(BpmMetadataError, "Ambiguous"):
            enrich_chart_bpm({"songName": "Fictional Fixed", "type": "Single"}, catalog=runtime)
        self.assertEqual(resolve_song_bpm("Fictional Fixed", "Remix", catalog=runtime)["bpmFocal"], 150)

    def test_source_schema_identity_interval_and_focal_validation(self):
        mutations = [
            lambda d: d[0].update(schemaVersion=True),
            lambda d: d[1].update(mix="Phoenix"),
            lambda d: d[0]["songs"].append(d[0]["songs"][0]),
            lambda d: d[2]["songs"][0].update(songType="FullSong"),
            lambda d: d[2]["songs"][0].update(originalBpmRange={"min": 100, "max": 224}),
            lambda d: d[2]["songs"][0].update(recommendedPrimaryBpm=True),
            lambda d: d[2]["songs"][0].update(recommendedPrimaryBpm=226),
            lambda d: d[2]["songs"].clear(),
            lambda d: d[3]["songs"].update({"Unknown": {"recommendedPrimaryBpm": 170, "confirmedBy": "user"}}),
        ]
        for mutate in mutations:
            documents = deepcopy(self.documents)
            mutate(documents)
            with self.subTest(mutation=mutate), self.assertRaises(BpmMetadataError):
                compile_runtime_catalog(*documents)

    def test_semantic_revision_ignores_evidence_time_order_but_changes_with_values(self):
        documents = deepcopy(self.documents)
        documents[0]["generatedAtUtc"] = "different time"
        documents[0]["songs"].reverse()
        documents[2]["songs"][0]["summary"] = "different evidence phrasing"
        self.assertEqual(compile_runtime_catalog(*documents)["bpmMetadataRevision"], self.runtime["bpmMetadataRevision"])
        documents[2]["songs"][0]["userDirection"]["recommendedPrimaryBpm"] = 170
        changed = compile_runtime_catalog(*documents)
        self.assertNotEqual(changed["bpmMetadataRevision"], self.runtime["bpmMetadataRevision"])
        changed["songs"][0]["bpmFocal"] += 1
        with self.assertRaises(BpmMetadataError):
            validate_runtime_catalog(changed)

    def test_scalar_bucket_edges_are_half_open_without_rounding(self):
        examples = {149.999: 0, 150: 1, 159.999: 1, 160: 2, 169: 2, 169.999: 2,
                    170: 3, 179.999: 3, 180: 4, 190: 5, 200: 6, 210: 7, 219.999: 7, 220: 8, 320: 8}
        for value, expected in examples.items():
            self.assertEqual(bpm_bucket_index(value), expected)
        for value in (None, True, False, 0, -1, float("nan"), float("inf"), "170"):
            self.assertIsNone(bpm_bucket_index(value))

    def test_frozen_catalog_has_every_correction_and_accepted_focal(self):
        runtime = build(ROOT / "config")
        self.assertEqual(runtime["summary"], {"songEntries": 675, "fixedBpmSongs": 643, "rangedBpmSongs": 32})
        rows = {row["songName"]: row for row in runtime["songs"]}
        for name, focal in {"The Devil": 240, "Appassionata": 210, "Transmission": 169,
                            "Banya Classic Remix": 170, "Love Witch": 170, "Moment Day": 158}.items():
            self.assertEqual(rows[name]["bpmFocal"], focal)
        self.assertEqual((rows["Transmission"]["bpmMin"], rows["Transmission"]["bpmMax"]), (100, 225))
        corrections = {
            "Barber's Madness": (195, 195), "Dignity - FULL SONG -": (195, 195),
            "Euphorianic - SHORT CUT -": (140, 140), "HUSH - FULL SONG -": (81.5, 81.5),
            "Hyacinth": (192, 192), "Love is a Danger Zone 2 Try To B.P.M": (140, 169),
            "Meteo5cience (GADGET mix)": (140, 140), "Moment Day": (158, 158),
            "Phantom": (136, 136),
            "Pneumonoultramicroscopicsilicovolcanoconiosis ft. Kagamine Len/GUMI": (156, 156),
            "She Likes Pizza": (230, 230), "Simon Says, EURODANCE!! (feat. Sara\u2606M)": (157, 157),
            "Spooky Macaron": (200, 200), "Trotpris - SHORT CUT -": (156.118, 156.118),
            "Violet Perfume": (140, 140), "Viyella's Nightmare": (220, 220),
        }
        for name, interval in corrections.items():
            self.assertEqual((rows[name]["bpmMin"], rows[name]["bpmMax"]), interval)
        research = json.loads((ROOT / "config/phoenix2-song-primary-bpm-research.json").read_text(encoding="utf-8"))
        self.assertEqual(len(research["songs"]), 32)
        for decision in research["songs"]:
            resolved = resolve_song_bpm(decision["songName"], decision["songType"], catalog=runtime)
            self.assertEqual(resolved["bpmFocal"], decision["recommendedPrimaryBpm"])
        source_rows = [
            {"id": f"fixture-chart-{index}", "songName": row["songName"], "songType": row["songType"],
             "type": ("Single", "Double", "CoOp")[index % 3]}
            for index, row in enumerate(runtime["songs"])
        ]
        charts = enrich_charts_bpm(source_rows, catalog=runtime)
        self.assertEqual(len(charts), len(runtime["songs"]))
        self.assertEqual([row["id"] for row in charts], [row["id"] for row in source_rows])
        self.assertTrue(all(row["bpmMin"] <= row["bpmFocal"] <= row["bpmMax"] for row in charts))
        self.assertEqual(load_runtime_catalog(), runtime)
        self.assertEqual(bpm_metadata_identity()["bpmPolicy"], BPM_POLICY)

    def test_offline_check_detects_stale_generation_and_preview_has_exact_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.json"
            path.write_text(render(build(ROOT / "config")), encoding="utf-8")
            self.assertEqual(main(["--offline", "--check", "--output", str(path)]), 0)
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(main(["--offline", "--check", "--output", str(path)]), 1)
            preview = Path(temporary) / "preview.json"
            source = Path(temporary) / "source-catalog.json"
            source.write_text(json.dumps({
                "mix": "Phoenix2", "songs": [
                    {"name": "Transmission", "type": "Arcade", "bpm": {"min": 100, "max": 225}},
                    {"name": "Moment Day", "type": "Arcade", "bpm": {"min": 155, "max": 155}},
                    {"name": "Phantom", "type": "Arcade", "bpm": {"min": 140, "max": 140}},
                ], "charts": [
                    {"id": "transmission", "songName": "Transmission", "songType": "Arcade", "type": "Double"},
                    {"id": "moment-day", "songName": "Moment Day", "songType": "Arcade", "type": "Single"},
                    {"id": "phantom", "songName": "Phantom", "songType": "Arcade", "type": "CoOp"},
                ],
            }), encoding="utf-8")
            write_preview(preview, source, build(ROOT / "config"))
            output = json.loads(preview.read_text(encoding="utf-8"))
            self.assertEqual(output["summary"], {"songs": 675, "charts": 3, "displayChangedCharts": 2})


if __name__ == "__main__":
    unittest.main()
