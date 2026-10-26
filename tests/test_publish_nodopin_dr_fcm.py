import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notify", ROOT / "scripts/publish_nodopin_dr_fcm.py")
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class Checks(unittest.TestCase):
    def test_general(self):
        data = {"schemaVersion": 1, "alerts": [
            {"id": "radar-1", "type": "general", "active": True,
             "createdAt": iso(NOW - 10), "expiresAt": iso(NOW + 600)}
        ]}
        result = notify.match_catalog("radar_general", "radar-1", data, NOW)
        self.assertEqual(result["topic"], "nodopin_dr_radar_general_v1")
        self.assertEqual(notify.payload(result)["message"]["android"]["priority"], "high")
        data["alerts"][0]["active"] = False
        with self.assertRaises(ValueError):
            notify.match_catalog("radar_general", "radar-1", data, NOW)

    def test_seismic_no_test_and_expired(self):
        event = {"id": "earthquake-usgs-a1", "type": "earthquake", "active": True,
                 "expiresAt": iso(NOW + 600),
                 "earthquake": {"status": "reviewed", "source": "USGS",
                                "eventTime": int((NOW - 12) * 1000)}}
        data = {"schemaVersion": 1, "enabled": True, "alerts": [event]}
        self.assertEqual(notify.match_catalog("seismic", event["id"], data, NOW)["ttl"], 300)
        event["earthquake"]["status"] = "test"
        with self.assertRaises(ValueError):
            notify.match_catalog("seismic", event["id"], data, NOW)

    def test_agenda_future(self):
        data = {"schemaVersion": 1, "events": [
            {"id": "agenda_event-1", "status": "confirmed",
             "startAt": iso(NOW + 500), "endAt": iso(NOW + 3600)}
        ]}
        self.assertEqual(notify.match_catalog("agenda", "agenda_event-1", data, NOW)["ttl"], 3600)
        data["events"][0]["status"] = "draft"
        with self.assertRaises(ValueError):
            notify.match_catalog("agenda", "agenda_event-1", data, NOW)

    def test_test_mode_does_not_broadcast(self):
        checked = {"category": "seismic", "id": "earthquake-usgs-a1",
                   "topic": "nodopin_dr_seismic_v1", "ttl": 300}
        message = notify.payload(checked, test_token="test-device-token")["message"]
        self.assertEqual(message["token"], "test-device-token")
        self.assertNotIn("topic", message)
        self.assertEqual(message["data"]["kind"], "nodopin_dr_public_v1")

    def test_no_random_ids(self):
        with self.assertRaises(ValueError):
            notify.match_catalog("agenda", "../../bad", {"schemaVersion": 1, "events": []}, NOW)


if __name__ == "__main__":
    unittest.main()
