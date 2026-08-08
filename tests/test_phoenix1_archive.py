from __future__ import annotations

import unittest

from scripts.verify_phoenix1_archive import verify_archive


class Phoenix1ArchiveTests(unittest.TestCase):
    def test_frozen_archive_matches_manifest_and_is_privacy_safe(self) -> None:
        manifest = verify_archive()
        self.assertEqual(manifest["mix"], "phoenix1")
        self.assertEqual(manifest["measuredCharts"], 2464)


if __name__ == "__main__":
    unittest.main()
