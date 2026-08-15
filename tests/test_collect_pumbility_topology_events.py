from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from scripts.collect_pumbility_topology_events import (
    CollectionError,
    FIRST_DEPLOYMENT_ENV,
    LOCAL_DATA,
    SECOND_DEPLOYMENT_ENV,
    collect_topology_events,
    event_from_message,
    validate_event,
)


class CollectorTests(unittest.TestCase):
    def test_exact_json_event_is_allowlisted(self) -> None:
        event = {
            "kind": "queue",
            "label": "iad1",
            "topic": "analysis",
            "stage": "published",
            "identitySha256": "a" * 64,
            "attempt": 1,
        }
        self.assertEqual(
            event_from_message(json.dumps(event), expected_label="iad1"), event
        )

    def test_recognized_event_with_extra_field_fails_closed(self) -> None:
        with self.assertRaises(CollectionError):
            validate_event(
                {
                    "kind": "capacity",
                    "label": "iad1",
                    "activeConnections": 4,
                    "connectionLimit": 12,
                    "connectionErrors": 0,
                    "deadlineErrors": 0,
                    "deploymentId": "private",
                },
                expected_label="iad1",
            )

    def test_safe_rollout_log_is_reduced_to_telemetry_schema(self) -> None:
        message = (
            "WARNING:pumbility.rollout:pumbility_store operation=get_json "
            "domain=tier-list outcome=mismatch-fallback authoritative_ms=1.0 "
            "raw_url=https://private.invalid"
        )
        self.assertEqual(
            event_from_message(message, expected_label="cle1"),
            {
                "kind": "telemetry",
                "label": "cle1",
                "domain": "tier-list",
                "outcome": "mismatch",
                "count": 1,
            },
        )

    def test_non_event_messages_are_ignored(self) -> None:
        self.assertIsNone(
            event_from_message(
                '{"event":"application","url":"https://private.invalid"}',
                expected_label="iad1",
            )
        )

    def test_saturated_windows_split_and_platform_ids_are_deduplicated(self) -> None:
        references = {"iad-private-ref": "iad1", "cle-private-ref": "cle1"}
        calls: list[tuple[str, str, str]] = []

        def runner(command, **_kwargs):
            deployment = command[command.index("--deployment") + 1]
            since = command[command.index("--since") + 1]
            until = command[command.index("--until") + 1]
            label = references[deployment]
            calls.append((deployment, since, until))
            queue = {
                "kind": "queue",
                "label": label,
                "topic": "analysis",
                "stage": "published",
                "identitySha256": ("a" if label == "iad1" else "b") * 64,
                "attempt": 1,
            }
            telemetry = (
                "WARNING:pumbility.rollout:pumbility_store operation=get_json "
                "domain=analysis outcome=candidate-served authoritative_ms=1.0 "
                "raw_url=https://private.invalid"
            )
            records = {
                "a": {
                    "id": f"{label}-a",
                    "message": "request summary",
                    "logs": [
                        {"level": "info", "message": json.dumps(queue)},
                        {"level": "info", "message": json.dumps(queue)},
                    ],
                },
                "b": {"id": f"{label}-b", "message": telemetry},
                "c": {
                    "id": f"{label}-c",
                    "message": '{"event":"ignored","deploymentId":"private"}',
                },
            }
            if since.endswith("00.000Z") and until.endswith("10.000Z"):
                selected = [records["a"], records["b"], records["c"]]
            elif until.endswith("05.000Z"):
                selected = [records["a"], records["b"]]
            else:
                selected = [records["b"], records["c"]]
            encoded = "".join(json.dumps(item) + "\n" for item in selected).encode()
            return SimpleNamespace(returncode=0, stdout=encoded, stderr=b"")

        LOCAL_DATA.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=LOCAL_DATA) as temporary:
            output = Path(temporary) / "events.jsonl"
            counts = collect_topology_events(
                first_label="iad1",
                second_label="cle1",
                started=datetime(2026, 8, 15, 0, 0, 0, tzinfo=timezone.utc),
                ended=datetime(2026, 8, 15, 0, 0, 10, tzinfo=timezone.utc),
                output=output,
                environment={
                    FIRST_DEPLOYMENT_ENV: "iad-private-ref",
                    SECOND_DEPLOYMENT_ENV: "cle-private-ref",
                },
                limit=3,
                command_runner=runner,
            )
            encoded = output.read_text(encoding="utf-8")
            events = [json.loads(line) for line in encoded.splitlines()]

        self.assertEqual(counts, {"iad1": 2, "cle1": 2})
        self.assertEqual(len(events), 4)
        self.assertEqual(len(calls), 6)
        self.assertNotIn("iad-private-ref", encoded)
        self.assertNotIn("cle-private-ref", encoded)
        self.assertNotIn("private.invalid", encoded)
        self.assertNotIn("deploymentId", encoded)
        self.assertTrue(all(set(event) in [
            {"kind", "label", "topic", "stage", "identitySha256", "attempt"},
            {"kind", "label", "domain", "outcome", "count"},
        ] for event in events))

    def test_minimum_window_saturation_fails_without_writing(self) -> None:
        def runner(_command, **_kwargs):
            encoded = (
                json.dumps({"id": "one", "message": "ignored"})
                + "\n"
                + json.dumps({"id": "two", "message": "ignored"})
                + "\n"
            ).encode()
            return SimpleNamespace(returncode=0, stdout=encoded, stderr=b"")

        LOCAL_DATA.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=LOCAL_DATA) as temporary:
            output = Path(temporary) / "events.jsonl"
            with self.assertRaises(CollectionError):
                collect_topology_events(
                    first_label="iad1",
                    second_label="cle1",
                    started=datetime(2026, 8, 15, tzinfo=timezone.utc),
                    ended=datetime(2026, 8, 15, tzinfo=timezone.utc)
                    + timedelta(milliseconds=1),
                    output=output,
                    environment={
                        FIRST_DEPLOYMENT_ENV: "iad-private-ref",
                        SECOND_DEPLOYMENT_ENV: "cle-private-ref",
                    },
                    limit=2,
                    minimum_window=timedelta(milliseconds=1),
                    command_runner=runner,
                )
            self.assertFalse(output.exists())

    def test_deployment_references_are_required_only_from_environment(self) -> None:
        with self.assertRaises(CollectionError):
            collect_topology_events(
                first_label="iad1",
                second_label="cle1",
                started=datetime(2026, 8, 15, tzinfo=timezone.utc),
                ended=datetime(2026, 8, 15, tzinfo=timezone.utc)
                + timedelta(seconds=1),
                output=LOCAL_DATA / "missing.jsonl",
                environment={},
            )


if __name__ == "__main__":
    unittest.main()
