import copy
import json
import math
import unittest
from pathlib import Path
from statistics import median

from sss_scoring import payload_sha256
from submitted_sssplus_calibration import (
    METHOD, NAMESPACE, POLICY, STAGE, apply_submitted_sssplus_calibration,
    recover_submitted_sssplus_calibration, validate_submitted_sssplus_calibration,
)


def calibration_fixture():
    """A real recoverable schema-56 fixture with an aggregate-only cached fit."""
    base = json.loads((Path(__file__).parent / "fixtures/source-aware-width.json").read_text(encoding="utf-8"))["candidate"]
    charts = {}
    for row in base["doubles"]:
        if row["level"] >= 20:
            continue
        metric = row["scoringVariants"]["sssplus"]
        effect = metric["scoringChartEffect"]
        charts[row["chartId"]] = {
            "effect": effect, "effectLow": effect-.12, "effectHigh": effect+.14,
            "ratingEligible": True, "calibratedComponent": True,
            "usableContributors": metric["nContributors"], "targetSuccesses": metric["scoringTargetSuccesses"],
            "belowTarget": metric["scoringBelowTarget"], "bootstrapUsableSamples": 200,
            "provisionalReasons": ["weak-target-information"],
        }
    fit = {"charts": charts, "policy": {"scoreTarget": 995000, "bootstrapSamples": 200,
            "selectedRegularization": .01, "modelVersion": "target-rasch-observed-best-v1",
            "weighting": "each-player-total-one-inverse-observed-chart-count-v1"},
        "diagnostics": {"converged": True, "inputPlayers": 30, "eligiblePlayers": 25,
            "observations": 375, "fitObservations": 375, "componentCount": 1,
            "bootstrap": {"requested": 200, "completed": 200, "converged": 200, "failed": 0}}}
    identity = next(f["identity"] for f in base["summary"]["method"]["sourceAwareTiers"]["sourcePolicy"]["targetFits"]
                    if (f["goal"], f["source"], f["mode"]) == ("sssplus", "submitted", "Double"))
    return base, fit, identity


class SubmittedSssplusCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.base, self.fit, self.identity = calibration_fixture()

    def apply(self):
        return apply_submitted_sssplus_calibration(self.base, self.fit, fit_identity=self.identity)

    def lower(self, payload):
        return [r for r in payload["doubles"] if r["level"] < 20]

    def test_exact_predecessor_recovery_and_no_input_mutation(self):
        digest = payload_sha256(self.base)
        fit_digest = payload_sha256(self.fit)
        candidate = self.apply()
        self.assertEqual(candidate["schemaVersion"], 57)
        self.assertEqual(payload_sha256(recover_submitted_sssplus_calibration(candidate)), digest)
        self.assertEqual(payload_sha256(validate_submitted_sssplus_calibration(candidate, self.base, fit=self.fit)), digest)
        self.assertEqual(payload_sha256(self.base), digest)
        self.assertEqual(payload_sha256(self.fit), fit_digest)
        # Recovery patches retain only changed fields of the two target metrics.
        for row in candidate["doubles"]:
            if STAGE in row:
                self.assertEqual(set(row[STAGE]), {"scoring", "pumbility"})

    def test_failed_nonmonotone_calibration_is_restored_from_cached_effects(self):
        for row in self.lower(self.base):
            metric = row["scoringVariants"]["sssplus"]
            metric.update(estimatedDifficulty=None, difficultyDelta=None, evidenceStatus="Unrated",
                          scoringUnratedReason="SSS+ calibration requires strictly increasing reference medians.")
            metric["intervalUnavailableReason"] = "Calibration unavailable."
            metric["difficultyCi95Low"] = metric["difficultyCi95High"] = None
        # Deliberately reverse the whole middle folder; no adjacent-anchor gap is used.
        for row in self.lower(self.base):
            if row["level"] == 17:
                raw = self.fit["charts"][row["chartId"]]
                for key in ("effect", "effectLow", "effectHigh"):
                    raw[key] -= 5
                row["scoringVariants"]["sssplus"]["scoringChartEffect"] = raw["effect"]
        candidate = self.apply()
        for row in self.lower(candidate):
            m = row["scoringVariants"]["sssplus"]
            self.assertTrue(math.isfinite(m["estimatedDifficulty"]))
            self.assertIsNone(m["scoringUnratedReason"])
            self.assertNotIn("intervalUnavailableReason", m)
            self.assertEqual(m["scoringChartEffectCi95Low"], self.fit["charts"][row["chartId"]]["effectLow"])
            self.assertLess(m["difficultyCi95Low"], m["difficultyCi95High"])
            self.assertIn("weak-target-information", m["scoringProvisionalReasons"])
            self.assertEqual(m["evidenceStatus"], "Provisional")

    def test_production_scope_uses_identical_ratings_and_remains_recoverable(self):
        local = self.apply()
        production = apply_submitted_sssplus_calibration(self.base, self.fit,
            fit_identity=self.identity, local_only=False)
        self.assertFalse(production["summary"]["method"][NAMESPACE]["localOnly"])
        self.assertEqual(production["doubles"], local["doubles"])
        self.assertEqual(validate_submitted_sssplus_calibration(production, self.base, fit=self.fit), self.base)

    def test_map_compresses_once_and_centers_every_supported_folder(self):
        candidate = self.apply()
        marker = candidate["summary"]["method"][NAMESPACE]
        self.assertGreater(marker["sharedScale"], 0)
        for level, folder in marker["folders"].items():
            rows = [r for r in self.lower(candidate) if r["level"] == int(level)]
            self.assertAlmostEqual(median(r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in rows), int(level)+.5, places=12)
            for row in rows:
                m = row["scoringVariants"]["sssplus"]
                raw = int(level)+.5+.8*(m["scoringChartEffect"]-folder["centerEffect"])/marker["sharedScale"]
                self.assertEqual(m["initialFit"]["estimatedDifficulty"], raw)
                self.assertEqual(m["scoringSpreadPolicy"]["preSpreadDifficulty"], int(level)+.5+.5*(raw-int(level)-.5))
                self.assertEqual(m["scoringSpreadPolicy"]["midpoint"], int(level)+.5)
                self.assertEqual(m["finalFolderWidth"]["initialCompressionApplications"], 1)
                self.assertEqual(m["scoringSubmittedCalibration"]["method"], METHOD)
        with self.assertRaises(ValueError):
            apply_submitted_sssplus_calibration(candidate, self.fit)

    def test_order_ties_even_median_and_endpoint_order_survive_transformations(self):
        rows = [r for r in self.base["doubles"] if r["level"] == 16]
        extra = copy.deepcopy(rows[-1]); extra["chartId"] = "extra-double-sixteenth"
        self.base["doubles"].append(extra); rows.append(extra)
        self.fit["charts"][extra["chartId"]] = copy.deepcopy(self.fit["charts"][rows[-2]["chartId"]])
        for row, effect in zip(rows, [0., 1., 1., 3., 4., 5.]):
            self.fit["charts"][row["chartId"]].update(effect=effect, effectLow=effect-.1, effectHigh=effect+.2)
            row["scoringVariants"]["sssplus"]["scoringChartEffect"] = effect
        candidate = self.apply()
        vals = [r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in candidate["doubles"] if r["level"] == 16]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(vals[1], vals[2])
        self.assertAlmostEqual(median(vals), 16.5, places=12)

    def test_pumbility_mean_and_protected_components_are_unchanged(self):
        candidate = self.apply()
        self.assertEqual(candidate["singles"], self.base["singles"])
        self.assertEqual(candidate.get("coop"), self.base.get("coop"))
        for row, old in zip(candidate["doubles"], self.base["doubles"]):
            self.assertEqual(row["tierMetrics"], old["tierMetrics"])
            for goal in ("general", "sss"):
                self.assertEqual(row["scoringVariants"][goal], old["scoringVariants"][goal])
                self.assertEqual(row["pumbilityVariants"][goal], old["pumbilityVariants"][goal])
            if row["level"] < 20:
                self.assertEqual(row["pumbilityVariants"]["sssplus"]["estimatedDifficulty"],
                    (row["scoringVariants"]["sssplus"]["estimatedDifficulty"]+row["tierMetrics"]["clearing"]["estimatedDifficulty"])/2)
            else:
                for key in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                    self.assertEqual(row["scoringVariants"]["sssplus"][key], old["scoringVariants"]["sssplus"][key])

    def test_wrong_identity_target_or_cache_is_rejected(self):
        for key, value in (("boards", "f"*64), ("sourceBoards", "e"*64), ("target", 990000)):
            bad = {**self.identity, key: value}
            with self.assertRaises(ValueError):
                apply_submitted_sssplus_calibration(self.base, self.fit, fit_identity=bad)
        first = next(iter(self.fit["charts"]))
        self.fit["charts"][first]["effect"] += .1
        with self.assertRaises(ValueError):
            self.apply()

    def test_future_one_sided_rare_or_unrelated_failures_are_not_rescued(self):
        for kind in ("no-target", "all-target", "rare-target", "unrelated-failure"):
            base, fit, identity = calibration_fixture()
            row = next(r for r in base["doubles"] if r["level"] == 16)
            metric, raw = row["scoringVariants"]["sssplus"], fit["charts"][row["chartId"]]
            metric.update(estimatedDifficulty=None, evidenceStatus="Unrated", scoringUnratedReason="SSS+ calibration unavailable.")
            if kind == "no-target":
                raw["targetSuccesses"] = metric["scoringTargetSuccesses"] = 0
            elif kind == "all-target":
                raw["belowTarget"] = metric["scoringBelowTarget"] = 0
            elif kind == "rare-target":
                raw["targetSuccesses"] = metric["scoringTargetSuccesses"] = 1
            else:
                metric["scoringUnratedReason"] = "Chart is outside the calibrated shared-player component."
            candidate = apply_submitted_sssplus_calibration(base, fit, fit_identity=identity)
            actual = next(r for r in candidate["doubles"] if r["chartId"] == row["chartId"])
            self.assertIsNone(actual["scoringVariants"]["sssplus"]["estimatedDifficulty"])
            self.assertNotIn("scoringSubmittedCalibration", actual["scoringVariants"]["sssplus"])

    def test_degenerate_scale_and_invalid_intervals_fail_closed(self):
        for row in self.lower(self.base):
            row["scoringVariants"]["sssplus"]["scoringChartEffect"] = 1.
            self.fit["charts"][row["chartId"]].update(effect=1., effectLow=.9, effectHigh=1.1)
        with self.assertRaisesRegex(ValueError, "positive shared"):
            self.apply()
        self.base, self.fit, self.identity = calibration_fixture()
        next(iter(self.fit["charts"].values()))["effectHigh"] = -100.
        with self.assertRaisesRegex(ValueError, "ordered"):
            self.apply()

    def test_tampered_points_means_policy_ranks_and_protected_fields_are_rejected(self):
        candidate = self.apply()
        for kind in ("score", "mean", "policy", "rank", "clearing", "endpoint"):
            bad = copy.deepcopy(candidate)
            row = self.lower(bad)[0]
            if kind == "score": row["scoringVariants"]["sssplus"]["estimatedDifficulty"] += .1
            elif kind == "mean": row["pumbilityVariants"]["sssplus"]["estimatedDifficulty"] += .1
            elif kind == "policy": bad["summary"]["method"][NAMESPACE]["policy"]["compressionApplications"] = 2
            elif kind == "rank": row["scoringVariants"]["sssplus"]["modeRank"] = 99999
            elif kind == "clearing": row["tierMetrics"]["clearing"]["estimatedDifficulty"] += .1
            else: row["scoringVariants"]["sssplus"]["scoringSubmittedCalibration"]["effectCi95High"] += .1
            with self.assertRaises(ValueError, msg=kind):
                validate_submitted_sssplus_calibration(bad, self.base, fit=self.fit)


if __name__ == "__main__":
    unittest.main()
