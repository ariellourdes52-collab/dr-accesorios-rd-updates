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
