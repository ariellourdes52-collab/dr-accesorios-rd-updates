"""Sin red: demostrar que cambiar CSS nunca reemplaza eventos, fotos ni Hosting principal."""
import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_dr_agenda_style as target
from dr_agenda_publisher_core import AgendaError, SITE


class AgendaCSSIsolationTests(unittest.TestCase):
    def setUp(self):
        self.live_version = SITE + "/versions/live-css-previous"
        self.live_release = SITE + "/releases/live"
        self.old = {p: "existing-hash-" + str(i)
                    for i, p in enumerate(sorted(target.BASE_FILES))}
        self.old["/agenda/images/evt-20261011-f63104291e81.webp"] = "photo-hash"
        self.old["/__/firebase/init.js"] = "firebase-js"
        self.old["/__/firebase/init.json"] = "firebase-json"
        self.api = MagicMock()
        self.api.active.return_value = (self.live_release, self.live_version)
        self.api.files.return_value = dict(self.old)
        self.api.api.return_value = {"fileCount": len(self.old), "config": {"headers": []}}

    def test_css_matches_complete_images_on_mobile_and_desktop(self):
        target.validate_css(target.CSS_FILE.read_bytes())

    def test_copies_all_existing_events_images_logo_and_scripts(self):
        expected = target.expected_manifest(self.old, "new-style-hash")
        self.assertEqual(expected[target.CSS_PATH], "new-style-hash")
        for path, digest in self.old.items():
            if path not in target.FIREBASE_RESERVED_PATHS and path != target.CSS_PATH:
                self.assertEqual(expected[path], digest)
        self.assertNotIn("/__/firebase/init.js", expected)
        self.assertEqual(expected["/agenda/images/evt-20261011-f63104291e81.webp"], "photo-hash")

    def test_unexpected_paths_abort_without_writes(self):
        with self.assertRaisesRegex(AgendaError, "Rutas desconocidas"):
            target.expected_manifest({**self.old, "/radar.json": "oops"}, "new-hash")
        with self.assertRaisesRegex(AgendaError, "Rutas desconocidas"):
            target.expected_manifest({**self.old, "/agenda/misc-suspicious.js": "oops"}, "new-hash")
        self.api.session.post.assert_not_called()

    def test_auditar_does_not_write_or_deploy(self):
        result = target.update(mode="auditar", hosting=self.api)
        self.assertEqual(result, {"mode": "auditar", "changed": True})
        self.api.api.assert_called_once_with("GET", self.live_version)
        self.api.session.post.assert_not_called()

    def test_aplicar_fails_without_manual_confirmation(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(AgendaError, "confirmación exacta"):
                target.update(mode="aplicar", hosting=self.api)
        self.api.api.assert_called_once_with("GET", self.live_version)
        self.api.session.post.assert_not_called()

    def test_aplicar_fails_outside_actions_even_with_confirmation(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": ""}):
            with self.assertRaisesRegex(AgendaError, "GitHub Actions"):
                target.update(mode="aplicar", confirm=target.CONFIRM, hosting=self.api)
        self.api.api.assert_called_once_with("GET", self.live_version)
        self.api.session.post.assert_not_called()

    def test_already_updated_is_idempotent(self):
        digest = target.compress_digest(target.CSS_FILE.read_bytes())[1]
        self.old[target.CSS_PATH] = digest
        self.api.files.return_value = dict(self.old)
        result = target.update(mode="auditar", hosting=self.api)
        self.assertEqual(result, {"mode": "auditar", "changed": False})
        self.api.session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
