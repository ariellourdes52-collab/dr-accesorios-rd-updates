"""Offline verification: Android preliminary EMSC notifications remain independent."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/dr-radar-webpush"))
import early_fcm

NOW = 1793800000000


def event(event_id="emsc-1", time_ms=NOW-10000, mag=4.5):
    return {"id": event_id, "eventTime": time_ms, "place": "DOMINICAN REPUBLIC",
            "latitude": 18.7, "longitude": -70.16, "magnitude": mag}


def official(time_ms=NOW-10000):
    return {"type": "earthquake", "earthquake": {
        "source": "USGS", "eventTime": time_ms, "latitude": 18.7,
        "longitude": -70.16, "magnitude": 4.5}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.meta = {}
        self.sent = []
        self.sender = self.sent.append
        self.get = self.meta.get
        self.set = lambda k, v: self.meta.__setitem__(k, v)
        self.config = patch.dict(os.environ, {
            "NODOPIN_EMSC_PRELIMINARY_FCM_ENABLED": "true",
            "NODOPIN_RAILWAY_FCM_ENABLED": "true",
            "NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT": "test-placeholder",
        })
        self.config.start()
        self.addCleanup(self.config.stop)

    def poll(self, events, official_alerts=None, sender=None):
        return early_fcm.relay_preliminary(events, official_alerts or [],
                       self.get, self.set, sender or self.sender, NOW)

    def test_payload_provisional_fcm(self):
        message = early_fcm.build_message(event())["message"]
        self.assertEqual(message["topic"], "nodopin_dr_seismic_v1")
        self.assertEqual(message["data"]["kind"], "nodopin_dr_emsc_preliminary_v1")
        self.assertEqual(message["data"]["source"], "EMSC-RTS")
        self.assertNotEqual(message["data"]["kind"], "nodopin_dr_public_v1")
        # Data-only FCM is required for onMessageReceived in the background.
        # A top-level notification would bypass the NodoPin SOS alarm player.
        self.assertNotIn("notification", message)
        self.assertNotIn("notification", message["android"])
        self.assertEqual(message["data"]["category"], "seismic_preliminary")
        self.assertTrue(message["data"]["id"].startswith("earthquake-emsc-"))
        self.assertEqual(message["data"]["eventTimeMs"], str(NOW - 10000))
        self.assertIn("PRELIMINAR", message["data"]["title"])
        self.assertIn("EMSC", message["data"]["body"])
        self.assertTrue(all(isinstance(v, str) for v in message["data"].values()))
        self.assertEqual(message["android"]["priority"], "high")
        self.assertEqual(message["android"]["ttl"], "300s")
        self.assertEqual(message["android"]["restricted_package_name"], "org.nodaviva.mesh")

    def test_no_retrospective_fcm_and_exactly_once(self):
        self.assertTrue(self.poll([event("before")])["baseline"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.poll([event("before"), event("after")])["accepted"], 1)
        self.assertEqual(self.poll([event("after")])["accepted"], 0)
        self.assertEqual([x["id"] for x in self.sent], ["after"])

    def test_suppress_late_preliminary_after_usgs(self):
        self.poll([])
        self.assertEqual(self.poll([event()], [official()])["accepted"], 0)
        self.assertEqual(self.sent, [])

    def test_retry_failure(self):
        self.poll([])
        with self.assertRaises(RuntimeError):
            self.poll([event()], sender=lambda x: (_ for _ in ()).throw(RuntimeError("FCM down")))
        self.assertEqual(self.poll([event()])["accepted"], 1)

    def test_disabled_not_sending(self):
        with patch.dict(os.environ, {"NODOPIN_EMSC_PRELIMINARY_FCM_ENABLED":"false"}):
            self.assertFalse(self.poll([event()])["enabled"])
            self.assertEqual(self.sent, [])

    def test_bad_event_or_ledger_fail_closed(self):
        self.poll([])
        with self.assertRaises(ValueError):
            self.poll([event("same"), event("same")])
        self.assertEqual(self.sent, [])
        self.meta[early_fcm.STATE_KEY] = "not json"
        with self.assertRaises(RuntimeError):
            self.poll([event()])
        self.assertEqual(self.sent, [])

    def test_missing_sender_credentials(self):
        with patch.dict(os.environ, {"NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":""}):
            with self.assertRaises(RuntimeError):
                self.poll([event()])
            self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
