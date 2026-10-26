import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from nodopin_dr_feed_relay import reconcile

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()


def at(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def general(event_id, source="OTHER", active=True):
    return {"id": event_id, "type": "general", "source": source,
            "active": active, "createdAt": at(NOW-40),
            "expiresAt": at(NOW+600)}


def seismic(event_id, real=True):
    return {"id": event_id, "active": True, "type": "earthquake",
            "expiresAt": at(NOW+600),
            "earthquake": {"status": "reviewed" if real else "test",
                           "source": "USGS", "eventTime": int((NOW-40)*1000)}}


class RelayTests(unittest.TestCase):
    def test_first_run_baseline_no_push(self):
        sent, state = [], {"known": {}}
        feed = {"schemaVersion": 1, "alerts": [general("prior")]}
        self.assertEqual(reconcile("radar_general", feed, state, sent.append, NOW), 0)
        self.assertEqual(sent, [])
        self.assertEqual(state["known"]["radar_general"], ["prior"])

    def test_new_general_once_but_manual_uses_existing_hook(self):
        state, sent = {"known": {"radar_general": ["old"]}}, []
        feed = {"schemaVersion": 1, "alerts": [
            general("old"), general("new"), general("manual", "TELEGRAM_ADMIN")]}
        self.assertEqual(reconcile("radar_general", feed, state, sent.append, NOW), 1)
        self.assertEqual(sent[0]["id"], "new")
        self.assertEqual(reconcile("radar_general", feed, state, sent.append, NOW), 0)

    def test_seismic_reject_test_and_notify_only_real_once(self):
        state, sent = {"known": {"seismic": []}}, []
        feed = {"schemaVersion": 1, "enabled": True, "alerts": [
            seismic("earthquake-test-1"), seismic("earthquake-usgs-x1"), seismic("earthquake-usgs-x2", False)]}
        self.assertEqual(reconcile("seismic", feed, state, sent.append, NOW), 1)
        self.assertEqual(sent[0]["id"], "earthquake-usgs-x1")
        self.assertEqual(reconcile("seismic", feed, state, sent.append, NOW), 0)

    def test_error_does_not_mark_new_as_sent(self):
        state = {"known": {"radar_general": []}}
        feed = {"schemaVersion": 1, "alerts": [general("new")]}
        def fail(_):
            raise RuntimeError("FCM refused")
        with self.assertRaises(RuntimeError):
            reconcile("radar_general", feed, state, fail, NOW)
        self.assertEqual(state["known"]["radar_general"], [])

    def test_refuse_duplicate_alert_ids(self):
        state = {"known": {"radar_general": []}}
        feed = {"schemaVersion": 1, "alerts": [general("x"), general("x")]}
        with self.assertRaises(ValueError):
            reconcile("radar_general", feed, state, lambda _: None, NOW)


if __name__ == "__main__":
    unittest.main()
