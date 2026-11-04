"""No-network regression tests for DR Accesorios RD Android preliminary FCM."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/dr-radar-webpush"))
import dr_android_early_fcm as dr

NOW = 1793800000000

def early(event_id="emsc-first", at=NOW-12000):
    return {"id": event_id, "eventTime": at, "place": "DOMINICAN REPUBLIC",
            "latitude": 18.73, "longitude": -70.16, "magnitude": 4.6}

def usgs(at=NOW-12000):
    return {"earthquake": {"source": "USGS", "eventTime": at, "magnitude": 4.6,
                           "latitude": 18.73, "longitude": -70.16}}

class Session:
    pass

class AndroidPreliminary(unittest.TestCase):
    def setUp(self):
        self.state, self.sent = {}, []
        self.get = self.state.get
        self.set = lambda key,val: self.state.__setitem__(key,val)
        self.config = patch.dict(os.environ, {"DR_ANDROID_EMSC_FCM_MODE":"live",
                 "DR_ANDROID_EMSC_SERVICE_ACCOUNT":"fake-test-secret"})
        self.config.start()
        self.addCleanup(self.config.stop)

    def call(self, rows, official=None, sender=None):
        with patch.object(dr, "fetch_emsc", return_value=rows):
            return dr.relay_once(Session(),self.get,self.set,official or [],
                        sender or self.sent.append, now_ms=NOW)

    def test_sender_uses_only_dr_dedicated_credential(self):
        with patch.dict(os.environ, {"DR_ANDROID_EMSC_SERVICE_ACCOUNT":"",
                 "NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT":"unrelated"}):
            with self.assertRaises(RuntimeError):
                dr.relay_once(Session(), self.get, self.set, [], now_ms=NOW)

    def test_cross_project_identity_rejected_before_oauth(self):
        wrong={"type":"service_account","project_id":"nexlink-3d017",
               "client_email":"nodopin-dr-fcm-sender@dr-accesorios-rd.iam.gserviceaccount.com",
               "private_key":"not-a-real-private-key"}
        with patch.dict(os.environ, {"DR_ANDROID_EMSC_SERVICE_ACCOUNT":json.dumps(wrong)}):
            with self.assertRaisesRegex(RuntimeError, "sender identity mismatch"):
                dr._get_access_token()

    def test_payload_uses_dr_android_topic_only(self):
        data=dr.payload(early())["message"]
        self.assertEqual(data["topic"],"radar_seismic_v231")
        self.assertEqual(data["data"]["source"],"EMSC-RTS")
        self.assertEqual(data["data"]["kind"],"dr_radar_emsc_preliminary_v1")
        self.assertNotEqual(data["topic"],"nodopin_dr_seismic_v1")
        self.assertEqual(data["android"]["priority"],"high")
        self.assertIn("PRELIMINAR",data["notification"]["title"])
        self.assertIn("No es una predicción",data["notification"]["body"])
        self.assertNotIn("restricted_package_name",data["android"])

    def test_existing_dr_usgs_flow_unchanged(self):
        self.assertEqual(dr.TOPIC,"radar_seismic_v231")
        self.assertEqual(dr.FCM_URL,
            "https://fcm.googleapis.com/v1/projects/dr-accesorios-rd/messages:send")

    def test_baseline_silent_and_deduplicated(self):
        a,b=early("old"),early("new")
        self.assertTrue(self.call([a])["baseline"])
        self.assertEqual(self.sent,[])
        self.assertEqual(self.call([a,b])["sent"],1)
        self.assertEqual(self.call([a,b])["sent"],0)
        self.assertEqual([x["id"] for x in self.sent],["new"])

    def test_no_preliminary_after_usgs(self):
        self.call([])
        self.assertEqual(self.call([early()], [usgs()])["sent"],0)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.call([early()], [])["sent"],0)

    def test_failed_send_retry(self):
        self.call([])
        def failure(_):
            raise RuntimeError("FCM temporarily unavailable")
        with self.assertRaises(RuntimeError):
            self.call([early()],sender=failure)
        self.assertEqual(self.call([early()])["sent"],1)

    def test_invalid_ledger_fails_closed(self):
        self.state[dr.STATE_KEY]="invalid"
        with self.assertRaises(ValueError):
            self.call([early()])
        self.assertEqual(self.sent,[])

    def test_off_is_default_and_no_traffic(self):
        with patch.dict(os.environ, {"DR_ANDROID_EMSC_FCM_MODE":"off"}):
            with patch.object(dr,"fetch_emsc",side_effect=AssertionError("no network")):
                self.assertEqual(self.call if False else dr.relay_once(
                    Session(),self.get,self.set,[],now_ms=NOW)["sent"],0)

    def test_validate_only_never_sends_to_users(self):
        seen=[]
        def validation():
            seen.append("validation")
        with patch.dict(os.environ,{"DR_ANDROID_EMSC_FCM_MODE":"validate"}):
            with patch.object(dr,"validate_transport",side_effect=validation):
                self.assertTrue(dr.relay_once(Session(),self.get,self.set,[],now_ms=NOW)["verified"])
                self.assertTrue(dr.relay_once(Session(),self.get,self.set,[],now_ms=NOW)["verified"])
        self.assertEqual(seen,["validation"])
        self.assertEqual(self.sent,[])

    def test_validation_rate_limits_errors(self):
        with patch.dict(os.environ,{"DR_ANDROID_EMSC_FCM_MODE":"validate"}):
            with patch.object(dr,"validate_transport",side_effect=RuntimeError("HTTP 403")) as check:
                with self.assertRaises(RuntimeError):
                    dr.relay_once(Session(),self.get,self.set,[],now_ms=NOW)
                self.assertFalse(dr.relay_once(Session(),self.get,self.set,[],now_ms=NOW+1000)["verified"])
                self.assertEqual(check.call_count,1)

    def test_validate_only_wire_flag(self):
        self.assertTrue(dr.payload(early(),validate_only=True)["validate_only"])
        self.assertFalse(dr.payload(early())["validate_only"])

    def test_different_source_rejected(self):
        with self.assertRaises(ValueError):
            dr.payload({**early(),"place":"PUERTO RICO"})
        with self.assertRaises(ValueError):
            dr.payload({**early(),"id":"../../attack"})

if __name__=="__main__":
    unittest.main()
