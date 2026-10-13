"""Regression tests: read-only seismic monitoring and ordinary-deploy guard."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from seismic_feed_guard import SEISMIC_PATH, require_seismic_preserved
import check_seismic_feed_public as watcher

FEED = {"schemaVersion": 1, "enabled": True, "updatedAt": "", "alerts": []}
PUBLISHERS = (
    "deploy_download_landing.py",
    "deploy_ios_agenda_pwa.py",
    "deploy_radar_widget.py",
    "deploy_shared_dr_audio.py",
    "deploy_radar_preserving_hosting.py",
    "deploy_version_preserving_hosting.py",
    "expire_radar_alerts.py",
    "manual_clear_radar_alert.py",
    "manual_publish_radar_alert.py",
)


class GuardTests(unittest.TestCase):
    def test_preserves_digest_in_ordinary_deploy(self):
        before = {SEISMIC_PATH: "seismic-original", "/radar.json": "r1"}
        after = {SEISMIC_PATH: "seismic-original", "/radar.json": "r2"}
        require_seismic_preserved(before, after)
        self.assertEqual(before[SEISMIC_PATH], after[SEISMIC_PATH])

    def test_fails_if_original_missing(self):
        with self.assertRaisesRegex(RuntimeError, "falta en versión LIVE"):
            require_seismic_preserved({"/radar.json": "r1"}, {"/radar.json": "r2"})

    def test_fails_if_new_manifest_drops_feed(self):
        with self.assertRaisesRegex(RuntimeError, "eliminar/modificar"):
            require_seismic_preserved({SEISMIC_PATH: "sha"}, {})

    def test_fails_if_digest_changed(self):
        with self.assertRaisesRegex(RuntimeError, "eliminar/modificar"):
            require_seismic_preserved({SEISMIC_PATH: "sha"}, {SEISMIC_PATH: "wrong"})

    def test_all_primary_publishers_have_runtime_guard(self):
        for file in PUBLISHERS:
            with self.subTest(file=file):
                source = (ROOT / "scripts" / file).read_text(encoding="utf-8")
                self.assertIn("from seismic_feed_guard import require_seismic_preserved", source)
                self.assertEqual(source.count("    require_seismic_preserved("), 1)


class MonitorTests(unittest.TestCase):
    def test_empty_seismic_feed_is_valid(self):
        self.assertEqual(watcher.validate_feed(FEED), FEED)

    def test_active_alerts_are_valid(self):
        alerts = [dict(id="earthquake-usgs-test", active=True)]
        self.assertEqual(watcher.validate_feed({**FEED, "alerts": alerts})["alerts"], alerts)

    def test_rejects_html_and_invalid_schema(self):
        for bad in ("<html>404</html>", [], {"alerts": []},
                    {**FEED, "enabled": False}, {**FEED, "alerts": "none"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    watcher.validate_feed(bad)

    def test_retries_transient_failure(self):
        remaining = iter([RuntimeError("404"), FEED])
        def reader():
            value = next(remaining)
            if isinstance(value, Exception):
                raise value
            return value
        with patch.object(watcher.time, "sleep") as sleep:
            self.assertEqual(watcher.check(retries=3, reader=reader), FEED)
        sleep.assert_called_once_with(7)

    def test_persistent_failure_not_silently_accepted(self):
        with patch.object(watcher.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "tras 3 intentos"):
                watcher.check(reader=lambda: (_ for _ in ()).throw(RuntimeError("HTTP 404")))

    def test_email_ping_disabled_without_new_secret(self):
        with patch.dict("os.environ", {}, clear=True):
            watcher.ping_healthchecks()

    def test_untrusted_healthchecks_url_rejected(self):
        with patch.dict("os.environ", {"DR_SEISMIC_HEALTHCHECKS_PING_URL": "https://other.tld/secret"}):
            with self.assertRaisesRegex(ValueError, "URL de Healthchecks inválida"):
                watcher.ping_healthchecks()


if __name__ == "__main__":
    unittest.main()
