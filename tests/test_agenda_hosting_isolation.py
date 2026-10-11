"""Comprobar que DR Agenda jamás utiliza el Hosting principal."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_agenda_publisher_core as core

class IsolationTests(unittest.TestCase):
    def test_site_is_secondary_only(self):
        self.assertEqual(core.SITE, "sites/dr-accesorios-rd-agenda")
        self.assertNotEqual(core.SITE_ID, core.PRIMARY_SITE_ID)
        self.assertEqual(core.CATALOG_URL, "https://dr-accesorios-rd-agenda.web.app/agenda/events.json")
        self.assertEqual(core.SITE_LOOKUP, "projects/dr-accesorios-rd/sites/dr-accesorios-rd-agenda")
        self.assertEqual(core.CRITICAL, ())

    def test_no_environment_override_to_main(self):
        with patch.dict(os.environ, {"DR_AGENDA_SITE_ID": "dr-accesorios-rd"}):
            self.assertEqual(core.SITE_ID, "dr-accesorios-rd-agenda")

    def test_missing_site_fails_closed(self):
        session = Mock()
        reply = Mock(status_code=404, ok=False, text="Not Found", content=b"{}")
        session.request.return_value = reply
        with patch.object(core, "make_auth_session", return_value=session):
            with self.assertRaises(core.AgendaError):
                core.Hosting()
        self.assertEqual(session.request.call_args.args[:2], ("GET", core.BASE + core.SITE_LOOKUP))

    def test_fresh_site_without_live_release(self):
        session = Mock()
        found = Mock(status_code=200, ok=True, content=b"{}")
        found.json.return_value = {"name": core.SITE_LOOKUP, "defaultUrl": core.PUBLIC}
        session.request.return_value = found
        session.get.return_value = Mock(status_code=404, ok=False)
        with patch.object(core, "make_auth_session", return_value=session):
            self.assertEqual(core.Hosting().active(), (None, None))
        self.assertEqual(session.get.call_args.args[0], core.BASE + core.SITE + "/channels/live")

    def test_wrong_domain_is_rejected_before_deploy(self):
        session = Mock()
        found = Mock(status_code=200, ok=True, content=b"{}")
        found.json.return_value = {"name": core.SITE_LOOKUP, "defaultUrl": "https://dr-accesorios-rd.web.app"}
        session.request.return_value = found
        with patch.object(core, "make_auth_session", return_value=session):
            with self.assertRaisesRegex(core.AgendaError, "otro sitio o dominio"):
                core.Hosting()
        self.assertEqual(session.request.call_args.args[:2], ("GET", core.BASE + core.SITE_LOOKUP))

    def test_firebase_reserved_files_do_not_break_agenda_manifest(self):
        expected = {"/agenda/index.html": "hash-html", "/agenda/events.json": "hash-json"}
        actual = {**expected, "/__/firebase/init.js": "reserved-js",
                  "/__/firebase/init.json": "reserved-json"}
        self.assertTrue(core.verify_agenda_inventory(actual, expected))

    def test_firebase_unknown_file_is_rejected(self):
        expected = {"/agenda/events.json": "safe"}
        with self.assertRaisesRegex(core.AgendaError, "no autorizado"):
            core.verify_agenda_inventory({**expected, "/radar.json": "other"}, expected)

    def test_firebase_changed_agenda_hash_is_rejected(self):
        expected = {"/agenda/events.json": "safe"}
        with self.assertRaisesRegex(core.AgendaError, "Hash incorrecto"):
            core.verify_agenda_inventory({
                "/agenda/events.json": "tampered", "/__/firebase/init.js": "auto"}, expected)

    def test_manifest_pending_does_not_allow_finalize(self):
        expected = {"/agenda/events.json": "safe"}
        api = Mock()
        api.files.side_effect = [
            {}, {"/agenda/events.json": "safe"},
            {}, {"/agenda/events.json": "safe"}
        ]
        with patch.object(core.time, "sleep") as sleep:
            with self.assertRaisesRegex(core.AgendaError, "Inventario incompleto"):
                core.wait_for_agenda_inventory(
                    api, core.SITE + "/versions/draft", expected,
                    max_checks=2, pause_seconds=0)
        sleep.assert_called_once()
        self.assertEqual(api.files.call_count, 4)

    def test_manifest_complete_allows_reserved_files(self):
        expected = {"/agenda/events.json": "safe"}
        api = Mock()
        api.files.side_effect = [
            {"/agenda/events.json": "safe", "/__/firebase/init.js": "auto"},
            {}
        ]
        core.wait_for_agenda_inventory(api, core.SITE + "/versions/draft", expected,
                                       max_checks=1, pause_seconds=0)
        self.assertEqual(api.files.call_count, 2)

    def test_image_must_not_point_to_main_site(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc) + timedelta(days=2)
        event = {
          "id": "evt-20270115-example", "title": "Evento de prueba",
          "description": "Descripción de evento ficticio", "category": "tech", "status": "confirmed",
          "startAt": now.isoformat(timespec="seconds"),
          "endAt": (now + timedelta(hours=1)).isoformat(timespec="seconds"),
          "imageUrl": "https://dr-accesorios-rd.web.app/agenda/images/evt-20270115-example.webp",
          "officialUrl": "", "articleUrl": "",
          "location": {"type": "online", "online": True, "name": "", "address": "", "city": "", "province": "", "lat": None, "lng": None}
        }
        with self.assertRaises(core.AgendaError):
            core.validate({"schemaVersion": 1, "baseRevision": 0, "events": [event]}, Path("/tmp"))

if __name__ == "__main__":
    unittest.main()
