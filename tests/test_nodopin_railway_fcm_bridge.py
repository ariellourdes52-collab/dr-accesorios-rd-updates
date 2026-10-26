"""Offline validation for NodoPin's optional Railway feed relay."""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/dr-radar-webpush"))
from nodopin_fcm_bridge import candidate, relay_once

NOW = 1793000000000

def alert(event_id, origin="main", kind="rain"):
    return {"id": event_id, "_origin": origin, "type": kind, "active": True,
            "expiresAt": datetime.fromtimestamp((NOW+3600000)/1000, timezone.utc).isoformat()}

def seismic(event_id="earthquake-usgs-abc", event_time=NOW-30000):
    item = alert(event_id, "seismic", "earthquake")
    item["earthquake"] = {"status":"reviewed", "eventTime": event_time}
    return item

class Tests(unittest.TestCase):
    def setUp(self):
        self.meta = {}
        self.sent = []
        self.get_meta = self.meta.get
        self.set_meta = lambda k,v: self.meta.__setitem__(k,v)
        self.sender = lambda c,i: self.sent.append((c,i))
    def relay(self, rows):
        return relay_once(rows, self.get_meta, self.set_meta, self.sender, NOW)

    def test_no_sending_while_disabled(self):
        with patch.dict(os.environ, {"NODOPIN_RAILWAY_FCM_ENABLED":"false","NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":"dummy"}):
            self.assertFalse(self.relay([seismic()])["enabled"])
            self.assertFalse(self.meta)

    def test_safe_first_baseline_and_dedup(self):
        with patch.dict(os.environ, {"NODOPIN_RAILWAY_FCM_ENABLED":"true","NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":"dummy"}):
            self.assertTrue(self.relay([alert("old")])["baseline"])
            self.assertEqual(self.sent,[])
            self.assertEqual(self.relay([alert("old"), seismic()])["accepted"],1)
            self.assertEqual(self.sent,[("seismic","earthquake-usgs-abc")])
            self.assertEqual(self.relay([seismic()])["accepted"],0)

    def test_skip_test_and_stale_quakes(self):
        self.assertIsNone(candidate(seismic("earthquake-test-123"),NOW))
        self.assertIsNone(candidate(seismic("earthquake-usgs-old",NOW-3601000),NOW))
        self.assertEqual(candidate(seismic(),NOW)[0],"seismic")

    def test_no_duplicate_main_feed_seismic(self):
        self.assertIsNone(candidate(alert("earthquake-usgs-abc","main","earthquake"),NOW))
        self.assertEqual(candidate(alert("rain-new"),NOW)[0],"radar_general")

    def test_failed_send_can_retry_without_marking_delivered(self):
        with patch.dict(os.environ, {"NODOPIN_RAILWAY_FCM_ENABLED":"true","NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":"dummy"}):
            self.relay([])
            with self.assertRaises(RuntimeError):
                relay_once([seismic()], self.get_meta, self.set_meta,
                           lambda c,i: (_ for _ in ()).throw(RuntimeError("FCM offline")), NOW)
            self.assertEqual(self.relay([seismic()])["accepted"],1)

    def test_fail_closed_without_secret(self):
        with patch.dict(os.environ, {"NODOPIN_RAILWAY_FCM_ENABLED":"true","NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":""}):
            with self.assertRaises(RuntimeError): self.relay([seismic()])

    def test_bad_ledger_does_not_send(self):
        with patch.dict(os.environ, {"NODOPIN_RAILWAY_FCM_ENABLED":"true","NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":"dummy"}):
            self.meta["nodopin_railway_fcm_known_v1"] = "not-json"
            with self.assertRaises(RuntimeError): self.relay([seismic()])
            self.assertEqual(self.sent,[])

if __name__ == "__main__":
    unittest.main()
