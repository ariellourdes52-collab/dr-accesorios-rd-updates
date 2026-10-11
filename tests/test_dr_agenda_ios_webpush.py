"""DR Agenda Web Push iOS: opt-in is explicit, never inferred from FCM or Telegram."""
import ast
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from publish_dr_agenda_telegram import validated_request
from dr_agenda_publisher_core import AgendaError


def request(**changes):
    # Publisher accepts RFC3339 seconds with timezone, never fractional seconds.
    start = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    end = start + timedelta(hours=2)
    item = {
        "requestId": "0f00ae66-741c-4ed6-9747-56305ea8153b",
        "id": "evt-20261018-webpushios",
        "title": "Prueba de DR Agenda en iOS",
        "description": "Publicacion de prueba para un evento real futuro.",
        "category": "tech", "startAt": start.isoformat(),
        "endAt": end.isoformat(), "type": "online",
        "telegramPhotoFileId": "", "officialUrl": "",
    }
    item.update(changes)
    return json.dumps(item)


def load_server_gate():
    module = ast.parse((ROOT / "services/dr-radar-webpush/app.py").read_text())
    func = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                and n.name == "agenda_push_allowed")
    compiled = compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])),
                       "agenda_push_allowed", "exec")
    from typing import Any
    scope = {"Any": Any}
    exec(compiled, scope)
    return scope["agenda_push_allowed"]


class IOSAgendaNoticeTests(unittest.TestCase):
    def test_legacy_events_do_not_push(self):
        event, _ = validated_request(request())
        self.assertIs(event["notifyWebPush"], False)

    def test_webpush_is_independent_of_android(self):
        event, _ = validated_request(request(notifyFcm=False, notifyWebPush=True))
        self.assertIs(event["notifyWebPush"], True)
        event2, _ = validated_request(request(notifyFcm=True, notifyWebPush=False))
        self.assertIs(event2["notifyWebPush"], False)

    def test_invalid_opt_in_fails_closed(self):
        for value in ("true", "false", 1, None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(AgendaError):
                    validated_request(request(notifyWebPush=value))

    def test_railway_requires_explicit_opt_in(self):
        allows = load_server_gate()
        self.assertFalse(allows({"status":"confirmed"}))
        self.assertFalse(allows({"status":"confirmed","notifyWebPush":1}))
        self.assertFalse(allows({"status":"confirmed","notifyWebPush":"true"}))
        self.assertFalse(allows({"status":"cancelled","notifyWebPush":True}))
        self.assertTrue(allows({"status":"confirmed","notifyWebPush":True}))

    def test_pwa_handler_scoped(self):
        sw = (ROOT / "landing/radar-alertas/sw.js").read_text()
        self.assertIn("data.category === 'agenda'", sw)
        self.assertIn("self.registration.showNotification(title, options)", sw)
        self.assertIn("/radar-alertas/agenda.html", sw)
        page = (ROOT / "landing/radar-alertas/agenda.html").read_text()
        self.assertIn("dr-agenda-push-v1", page)
        self.assertIn("https://dr-accesorios-rd-agenda.web.app/agenda/", page)


if __name__ == "__main__":
    unittest.main()
