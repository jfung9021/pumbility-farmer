from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.provision_pumbility_production import (
    _executable,
    _install_vercel_value,
    _provision_login,
    _service_role_key,
    build_parser,
)


class ProvisionProductionTests(unittest.TestCase):
    def test_reconcile_only_is_explicit(self) -> None:
        self.assertFalse(build_parser().parse_args([]).reconcile_only)
        self.assertTrue(build_parser().parse_args(["--reconcile-only"]).reconcile_only)

    def test_windows_command_shims_are_resolved(self) -> None:
        with patch("scripts.provision_pumbility_production.os.name", "nt"):
            self.assertEqual(_executable("npx"), "npx.cmd")
            self.assertEqual(_executable("vercel"), "vercel.cmd")

    @patch("scripts.provision_pumbility_production._run")
    def test_vercel_stdin_value_has_required_line_terminator(self, run) -> None:
        _install_vercel_value("PUMBILITY_DATA_BACKEND", "vercel")
        self.assertEqual(run.call_args.kwargs["stdin"], "vercel\n")

    @patch("scripts.provision_pumbility_production._run")
    def test_login_sql_is_narrow_and_password_stays_on_stdin(self, run) -> None:
        password = "a" * 64
        _provision_login(password)
        args = run.call_args.args[0]
        sql = run.call_args.kwargs["stdin"]
        self.assertNotIn(password, args)
        self.assertIn("nosuperuser", sql)
        self.assertIn("nobypassrls", sql)
        self.assertIn("grant pumbility_worker", sql)
        self.assertIn("connection limit 12", sql)

    @patch("scripts.provision_pumbility_production._run")
    def test_service_key_requires_one_service_role_row(self, run) -> None:
        run.return_value = '[{"name":"service_role","api_key":"private"}]'
        self.assertEqual(_service_role_key(), "private")
        run.return_value = "[]"
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            _service_role_key()


if __name__ == "__main__":
    unittest.main()
