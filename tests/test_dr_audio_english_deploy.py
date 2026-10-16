"""Offline safety checks: English deploy must not alter Spanish catalog/history."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy_shared_dr_audio as deploy


class EnglishDeploySafetyTests(unittest.TestCase):
    def setUp(self):
        self.live = {
            "schemaVersion": 3, "generatedAt": "2026-10-15T00:00:00Z",
            "historyCount": 3, "latestMaleReady": 2,
            "entries": [
                {
                    "url": "https://blog.example/a",
                    "title": "Article A",
                    "audioUrl": "https://host/dr-audio/female.m4a",
                    "audioFemaleUrl": "https://host/dr-audio/female.m4a",
                    "audioMaleUrl": "https://host/dr-audio/male.m4a",
                    "femaleContentHash": "originalfemale",
                    "maleContentHash": "originalmale",
                },
                {"url": "https://blog.example/older", "title": "Older female",
                 "audioUrl": "https://host/dr-audio/legacy.m4a"},
                {"url": "https://blog.example/oldest", "title": "Oldest",
                 "audioUrl": "https://host/dr-audio/oldest.m4a"},
            ],
        }

    def test_new_english_fields_only_allowed(self):
        updated = copy.deepcopy(self.live)
        updated["entries"][0]["audioEnglishPath"] = "/dr-audio/english.m4a"
        updated["entries"][0]["audioEnglishUrl"] = "https://host/dr-audio/english.m4a"
        updated["latestEnglishReady"] = 1
        deploy.assert_english_overlay_preserved(self.live, updated)
        self.assertEqual(self.live["historyCount"], 3)

    def test_spanish_male_or_female_link_cannot_change(self):
        for key in ("audioUrl", "audioFemaleUrl", "audioMaleUrl",
                    "femaleContentHash", "maleContentHash"):
            with self.subTest(key=key):
                altered = copy.deepcopy(self.live)
                altered["entries"][0][key] = "overwrite"
                with self.assertRaisesRegex(RuntimeError, "Spanish fields"):
                    deploy.assert_english_overlay_preserved(self.live, altered)

    def test_history_entries_cannot_be_removed_or_reordered(self):
        altered = copy.deepcopy(self.live)
        altered["entries"].pop(1)
        with self.assertRaises(RuntimeError):
            deploy.assert_english_overlay_preserved(self.live, altered)
        altered = copy.deepcopy(self.live)
        altered["entries"].reverse()
        with self.assertRaises(RuntimeError):
            deploy.assert_english_overlay_preserved(self.live, altered)

    def test_previous_english_audio_cannot_be_replaced(self):
        existing = copy.deepcopy(self.live)
        existing["entries"][0]["audioEnglishUrl"] = "https://host/dr-audio/original-en.m4a"
        existing["entries"][0]["audioEnglishPath"] = "/dr-audio/original-en.m4a"
        changed = copy.deepcopy(existing)
        changed["entries"][0]["audioEnglishPath"] = "/dr-audio/replaced-en.m4a"
        with self.assertRaisesRegex(RuntimeError, "replace"):
            deploy.assert_english_overlay_preserved(existing, changed)

    def test_all_old_audio_files_preserved_even_orphans(self):
        old_files = {
            "/dr-audio/index.json": "catalog_old",
            "/dr-audio/female.m4a": "SHA_F",
            "/dr-audio/male.m4a": "SHA_M",
            "/dr-audio/forgotten-archive.m4a": "SHA_ARCHIVE",
            "/robots.txt": "SHA_ROBOTS",
        }
        expected = {"/robots.txt": "SHA_ROBOTS"}
        deploy.preserve_existing_audio_assets(expected, old_files)
        self.assertEqual(expected["/dr-audio/female.m4a"], "SHA_F")
        self.assertEqual(expected["/dr-audio/male.m4a"], "SHA_M")
        self.assertEqual(expected["/dr-audio/forgotten-archive.m4a"], "SHA_ARCHIVE")
        self.assertNotIn("/dr-audio/index.json", expected)
        self.assertEqual(expected["/robots.txt"], "SHA_ROBOTS")


if __name__ == "__main__":
    unittest.main()
