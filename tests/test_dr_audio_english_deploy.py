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


    def test_repeated_english_releases_keep_headers_idempotent(self):
        config = {
            "headers": [
                {"glob": "/keep.txt", "headers": {"X-Unrelated": "stay"}},
                {"glob": "/dr-audio/index.json", "headers": {"Old": "stale"}},
            ],
        }
        deploy.set_english_headers(config)
        initial = copy.deepcopy(config)
        deploy.set_english_headers(config)
        self.assertEqual(config, initial)
        self.assertEqual(len(config["headers"]), 3)
        self.assertEqual(config["headers"][0]["glob"], "/keep.txt")
        self.assertNotIn("Old", config["headers"][1]["headers"])
        self.assertEqual(config["headers"][1]["glob"], "/dr-audio/index.json")
        self.assertEqual(config["headers"][2]["glob"], "/dr-audio/**")

    def test_english_identity_rejects_similar_but_wrong_site(self):
        from unittest.mock import patch
        with patch.object(deploy, "SITE_ID", "dr-accesorios-rd-audio-en-test"):
            with self.assertRaisesRegex(RuntimeError, "EXACT"):
                deploy.validate_site_identity()


    def test_publication_waits_for_new_catalog_visibility_before_success(self):
        from unittest.mock import Mock, patch
        stale = Mock(ok=True)
        stale.json.return_value = self.old
        updated = Mock(ok=True)
        updated.json.return_value = self.updated
        with (patch.object(deploy.requests, "get",
                           side_effect=[stale, updated]) as get,
              patch.object(deploy.requests, "head",
                           return_value=Mock(ok=True)) as head,
              patch.object(deploy.time, "sleep") as sleep):
            deploy.verify_published_english_release(self.updated)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(all(c.args[0] == en.ENGLISH_INDEX
                            for c in get.call_args_list))
        head.assert_called_once_with(
            self.updated["entries"][0]["audioEnglishUrl"],
            timeout=30, allow_redirects=True,
        )
        sleep.assert_called_once_with(2)

    def test_publication_stale_catalog_fails_closed_without_releasing_again(self):
        from unittest.mock import Mock, patch
        stale = Mock(ok=True)
        stale.json.return_value = self.old
        with (patch.object(deploy.requests, "get", return_value=stale) as get,
              patch.object(deploy.requests, "head") as head,
              patch.object(deploy.time, "sleep") as sleep):
            with self.assertRaisesRegex(
                    RuntimeError, "after 5 visibility checks"):
                deploy.verify_published_english_release(self.updated)
        self.assertEqual(get.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        head.assert_not_called()

    def test_english_audio_visibility_can_recover_without_reupload(self):
        from unittest.mock import Mock, patch
        manifest = Mock(ok=True)
        manifest.json.return_value = self.updated
        with (patch.object(deploy.requests, "get", return_value=manifest) as get,
              patch.object(deploy.requests, "head",
                           side_effect=[Mock(ok=False), Mock(ok=True)]) as head,
              patch.object(deploy.time, "sleep") as sleep):
            deploy.verify_published_english_release(self.updated)
        get.assert_called_once()
        self.assertEqual(head.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
