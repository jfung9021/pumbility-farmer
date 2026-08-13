from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from piu_misgrade_analyzer import AnalysisConfig
from scripts.analyze_pumbility_supabase import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    AnalysisOutput,
    DatabaseInput,
    _publish_latest,
    _persist_analysis,
    _analyze,
    _validate_output,
    build_parser,
    main,
)


def _output(mix: str = "phoenix2") -> AnalysisOutput:
    player_hash = "a" * 64
    short_hash = player_hash[:16]
    database_input = DatabaseInput(
        mix_key=mix,
        mix_id="mix-db-id",
        snapshot={"players": [], "charts": [], "scores": []},
        player_by_short_hash={short_hash: ("player-db-id", player_hash)},
        chart_ids={"chart-upstream-id": "chart-db-id"},
    )
    payload = {
        "generatedAtUtc": "2026-08-13T00:00:00+00:00",
        "mix": {"key": mix, "apiValue": "Phoenix2", "label": "Phoenix 2"},
        "summary": {"coverage": {}, "modes": {}},
        "singles": [],
        "doubles": [],
    }
    return AnalysisOutput(
        database_input=database_input,
        config=SimpleNamespace(),  # type: ignore[arg-type]
        started_at=SimpleNamespace(),  # type: ignore[arg-type]
        payload=payload,
        baselines=[{"playerHash": short_hash, "mode": "Singles"}],
        contributions=[
            {
                "playerHash": short_hash,
                "chartId": "chart-upstream-id",
                "mode": "Singles",
            }
        ],
        chart_results=[{"chartId": "chart-upstream-id", "mode": "Singles"}],
        source_hash="b" * 64,
        output_hash="c" * 64,
    )


class AnalyzeSupabaseConfigurationTests(unittest.TestCase):
    def test_bootstrap_default_is_production_equivalent_and_override_is_explicit(self) -> None:
        self.assertEqual(build_parser().parse_args([]).bootstrap_samples, DEFAULT_BOOTSTRAP_SAMPLES)
        self.assertEqual(
            build_parser().parse_args(["--bootstrap-samples", "0"]).bootstrap_samples,
            0,
        )

    def test_output_validation_requires_resolvable_private_foreign_keys(self) -> None:
        value = _output()
        _validate_output(value)
        invalid = AnalysisOutput(
            **{
                **value.__dict__,
                "chart_results": [{"chartId": "missing", "mode": "Singles"}],
            }
        )
        with self.assertRaisesRegex(ValueError, "canonical row"):
            _validate_output(invalid)

    @patch("scripts.analyze_pumbility_supabase.analyze_snapshot")
    @patch("scripts.analyze_pumbility_supabase.analyzer_input")
    def test_typed_analysis_uses_the_runtime_eligibility_projection(
        self, analyzer_input: Mock, analyze_snapshot: Mock
    ) -> None:
        database_input = _output().database_input
        analyzer_input.return_value = ([{"userId": "eligible"}], [], [])
        analyze_snapshot.return_value = (
            SimpleNamespace(to_json=lambda **kwargs: "[]"),
            SimpleNamespace(to_json=lambda **kwargs: "[]"),
            {"coverage": {}, "modes": {}},
            SimpleNamespace(to_json=lambda **kwargs: "[]"),
        )
        with patch(
            "scripts.analyze_pumbility_supabase.build_web_payload",
            return_value=_output().payload,
        ):
            _analyze(database_input, 500)

        analyzer_input.assert_called_once_with(
            database_input.snapshot,
            minimum_scores_per_mode=30,
            eligible_only=True,
        )
        analyze_snapshot.assert_called_once_with(
            [{"userId": "eligible"}], [], [], ANY
        )


class LatestPromotionTests(unittest.TestCase):
    def test_all_selected_mix_pointers_use_one_atomic_bundle(self) -> None:
        phoenix1 = _output("phoenix1")
        phoenix1.payload["mix"] = {
            "key": "phoenix1",
            "apiValue": "Phoenix",
            "label": "Phoenix 1",
        }
        phoenix2 = _output("phoenix2")
        store = Mock()
        _publish_latest(store, [phoenix1, phoenix2])
        payloads = store.put_json_bundle.call_args.args[0]
        self.assertEqual(
            sorted(payloads),
            ["analysis/phoenix1/latest.json", "analysis/phoenix2/latest.json"],
        )


class TypedPersistenceTests(unittest.TestCase):
    def test_persists_all_typed_tables_with_full_player_hash_in_shadow_run(self) -> None:
        value = _output()
        value = AnalysisOutput(
            **{
                **value.__dict__,
                "config": AnalysisConfig(mix="phoenix2", bootstrap_samples=0),
                "started_at": datetime(2026, 8, 13, tzinfo=timezone.utc),
                "baselines": [
                    {
                        **value.baselines[0],
                        "validScoreCount": 30,
                        "baselinePumbility": 10.0,
                        "baselineStd": 1.0,
                        "baselineMin": 9.0,
                        "baselineMax": 11.0,
                        "baselineCount": 20,
                    }
                ],
                "contributions": [
                    {
                        **value.contributions[0],
                        "pumbility": 12.0,
                        "baselinePumbility": 10.0,
                        "residualPb": 2.0,
                        "playerRank": 1,
                    }
                ],
                "chart_results": [
                    {
                        **value.chart_results[0],
                        "nContributors": 1,
                        "nPlayersScored": 1,
                    }
                ],
            }
        )
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.side_effect = [("method-id",), ("run-id",)]
        connection = Mock()
        connection.transaction.return_value = nullcontext()
        connection.cursor.return_value = cursor

        fake_json_module = SimpleNamespace(Jsonb=lambda payload: payload)
        with patch.dict("sys.modules", {"psycopg.types.json": fake_json_module}):
            self.assertEqual(_persist_analysis(connection, value), "run-id")

        statements = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("'shadow'", statements)
        typed_statements = "\n".join(call.args[0] for call in cursor.executemany.call_args_list)
        for table in (
            "analysis_mode_results",
            "player_mode_features",
            "chart_contributions",
            "chart_results",
        ):
            self.assertIn(f"pumbility.{table}", typed_statements)
        player_rows = cursor.executemany.call_args_list[1].args[1]
        contribution_rows = cursor.executemany.call_args_list[2].args[1]
        self.assertEqual(player_rows[0][2], "a" * 64)
        self.assertEqual(contribution_rows[0][3], "a" * 64)


class AnalyzeSupabaseMainSafetyTests(unittest.TestCase):
    @patch("scripts.analyze_pumbility_supabase._assert_schema")
    @patch("scripts.analyze_pumbility_supabase._publish_latest")
    @patch("scripts.analyze_pumbility_supabase._persist_analysis")
    @patch("scripts.analyze_pumbility_supabase._analyze")
    @patch("scripts.analyze_pumbility_supabase._read_database_input")
    @patch("scripts.analyze_pumbility_supabase.require_loopback_database_url")
    def test_analysis_failure_never_changes_latest(
        self,
        guard: Mock,
        read_input: Mock,
        analyze: Mock,
        persist: Mock,
        publish: Mock,
        schema: Mock,
    ) -> None:
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        fake_psycopg = SimpleNamespace(connect=Mock(return_value=connection))
        read_input.return_value = _output().database_input
        analyze.side_effect = RuntimeError("analysis failed")
        with patch.dict("sys.modules", {"psycopg": fake_psycopg}), patch.dict(
            "os.environ", {"PUMBILITY_DATABASE_URL": "postgresql://localhost/local"}
        ):
            with self.assertRaisesRegex(RuntimeError, "analysis failed"):
                main(["--mix", "phoenix2", "--bootstrap-samples", "0"])

        guard.assert_called_once()
        schema.assert_called_once()
        persist.assert_not_called()
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
