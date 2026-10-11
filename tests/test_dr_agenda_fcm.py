import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from send_dr_agenda_fcm import TOPIC, build_message, send_confirmed
from publish_dr_agenda_telegram import fcm_requested
from dr_agenda_publisher_core import AgendaError


class AgendaFCMTests(unittest.TestCase):
    def setUp(self):
        self.event = {"id": "evt-20261016-agenda-test", "status": "confirmed"}

    def test_private_topic_only(self):
        obj = build_message(self.event, 15)
        self.assertEqual(obj["message"]["topic"], TOPIC)
        self.assertEqual(obj["message"]["data"]["type"], "agenda_event")
        self.assertEqual(obj["message"]["data"]["kind"], "published")
        self.assertEqual(obj["message"]["data"]["revision"], "15")
        self.assertEqual(obj["message"]["android"]["priority"], "NORMAL")
        self.assertNotIn("notification", obj["message"])
        self.assertNotEqual(TOPIC, "blog_updates")

    def test_test_token_targets_one_device_only(self):
        obj = build_message(self.event, 1, test_token="FAKE_TOKEN")
        self.assertEqual(obj["message"]["token"], "FAKE_TOKEN")
        self.assertNotIn("topic", obj["message"])

    def test_invalid_event_rejected(self):
        for value in ("", "agenda://event/x", "evt-!", "../radar.json"):
            with self.assertRaises(ValueError):
                build_message({**self.event, "id": value}, 1)
        with self.assertRaises(ValueError):
            build_message(self.event, 0)

    def test_per_event_opt_in_is_required(self):
        self.assertFalse(fcm_requested({}), "Legacy event must not push without opt-in")
        self.assertFalse(fcm_requested({"notifyFcm": False}))
        self.assertTrue(fcm_requested({"notifyFcm": True}))
        for invalid in ("true", 1, None, [], {}):
            with self.assertRaises(AgendaError):
                fcm_requested({"notifyFcm": invalid})

    def test_gate_off_never_reads_secret_or_sends(self):
        with patch.dict(os.environ, {"DR_AGENDA_FCM_ENABLED": "false"}, clear=True):
            self.assertEqual(send_confirmed(self.event, 1), "disabled")


if __name__ == "__main__":
    unittest.main()
