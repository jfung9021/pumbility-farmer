from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.provision_pumbility_production import (
    _executable,
    _provision_login,
    _service_role_key,
)


class ProvisionProductionTests(unittest.TestCase):
    def test_windows_command_shims_are_resolved(self) -> None:
        with patch("scripts.provision_pumbility_production.os.name", "nt"):
            self.assertEqual(_executable("npx"), "npx.cmd")
            self.assertEqual(_executable("vercel"), "vercel.cmd")

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
