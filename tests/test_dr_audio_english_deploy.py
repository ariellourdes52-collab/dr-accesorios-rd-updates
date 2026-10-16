"""Deployment regression tests: isolated English Firebase Hosting SITE."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy_dr_audio_english as deploy
import generate_dr_audio_english as en


class EnglishDeploySafetyTests(unittest.TestCase):
    def setUp(self):
        self.old = {
            "schemaVersion": 1, "language": "en",
            "entries": [{
                "url": "https://blog.example/old",
                "title": "Previous English story",
                "audioEnglishPath": "/dr-audio/old.m4a",
                "audioEnglishUrl": en.ENGLISH_PUBLIC_BASE + "/dr-audio/old.m4a",
                "englishLanguage": "en",
            }]
        }
        self.updated = copy.deepcopy(self.old)
        self.updated["entries"].insert(0, {
            "url": "https://blog.example/new",
            "title": "Latest English story",
            "audioEnglishPath": "/dr-audio/new.m4a",
            "audioEnglishUrl": en.ENGLISH_PUBLIC_BASE + "/dr-audio/new.m4a",
            "englishLanguage": "en",
        })

    def test_site_is_dedicated_to_english_never_spanish(self):
        deploy.validate_site_identity()
        self.assertNotEqual(deploy.SITE_ID, deploy.SPANISH_SITE_ID)
        self.assertNotEqual(deploy.SITE, "sites/dr-accesorios-rd")
        self.assertEqual(deploy.SITE_ID, en.ENGLISH_SITE_ID)
        self.assertEqual(deploy.SITE_LOOKUP, "projects/-/sites/" + en.ENGLISH_SITE_ID)
        self.assertNotEqual(deploy.SITE_LOOKUP, "projects/-/sites/" + deploy.SPANISH_SITE_ID)

    def test_incremental_deploy_preserves_existing_english_history(self):
        deploy.validate_english_catalog(self.old, self.updated)
        self.assertEqual(len(self.old["entries"]), 1)
        self.assertEqual(len(self.updated["entries"]), 2)

    def test_refuse_history_removal_or_reorder(self):
        changed = copy.deepcopy(self.updated)
        changed["entries"][1]["title"] = "Modified"
        with self.assertRaisesRegex(RuntimeError, "changed or reordered"):
            deploy.validate_english_catalog(self.old, changed)
        changed = copy.deepcopy(self.updated)
        changed["entries"].pop()
        with self.assertRaises(RuntimeError):
            deploy.validate_english_catalog(self.old, changed)

    def test_refuse_spanish_audio_fields_and_wrong_site_url(self):
        bad = copy.deepcopy(self.updated)
        bad["entries"][0]["audioFemaleUrl"] = "https://spanish.example/file.m4a"
        with self.assertRaisesRegex(RuntimeError, "Spanish"):
            deploy.validate_english_catalog(self.old, bad)
        bad = copy.deepcopy(self.updated)
        bad["entries"][0]["audioEnglishUrl"] = "https://dr-accesorios-rd.web.app/overwrite.m4a"
        with self.assertRaisesRegex(RuntimeError, "separate English site"):
            deploy.validate_english_catalog(self.old, bad)

    def test_refuse_changes_to_metadata(self):
        modified = copy.deepcopy(self.updated)
        modified["schemaVersion"] = 99
        with self.assertRaises(RuntimeError):
            deploy.validate_english_catalog(self.old, modified)
        modified = copy.deepcopy(self.updated)
        modified["otherMetadata"] = "unexpected"
        with self.assertRaisesRegex(RuntimeError, "metadata"):
            deploy.validate_english_catalog(self.old, modified)

    def test_deploy_payload_contains_only_english_manifest_and_new_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            (audio_dir / "new.m4a").write_bytes(b"A" * 2048)
            files = deploy.plan_assets(
                {"/dr-audio/old.m4a": "existing-sha", "/keep.txt": "keep-sha"},
                audio_dir, self.updated,
            )
        self.assertEqual(set(files), {"/dr-audio/index.json", "/dr-audio/new.m4a"})
        self.assertNotIn("/dr-audio/old.m4a", files)

    def test_immutable_old_audio_name_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "new.m4a").write_bytes(b"B" * 2048)
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                deploy.plan_assets({"/dr-audio/new.m4a": "sha"}, path, self.updated)


if __name__ == "__main__":
    unittest.main()
