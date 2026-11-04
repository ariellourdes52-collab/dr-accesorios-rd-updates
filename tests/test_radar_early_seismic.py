"""Offline tests: EMSC provisional report, independent USGS confirmation."""
import os
import sys
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/dr-radar-webpush"))
import early_seismic as early

NOW = 1793812500000


def emsc(event_id="20261104_0000012", mag=4.5, time_ms=NOW - 15000,
         place="DOMINICAN REPUBLIC", lat=18.7357, lon=-70.1627, kind="ke"):
    return {"type": "Feature", "id": event_id, "geometry": {"coordinates": [lon, lat, 10]},
            "properties": {"unid": event_id, "source_catalog": "EMSC-RTS",
                           "evtype": kind, "flynn_region": place, "mag": mag,
                           "time": datetime.fromtimestamp(time_ms / 1000, timezone.utc).isoformat()}}


def usgs(time_ms=NOW - 15000, mag=4.5, lat=18.7357, lon=-70.1627):
    return {"type": "earthquake", "earthquake": {"source": "USGS", "eventTime": time_ms,
            "magnitude": mag, "latitude": lat, "longitude": lon}}


class Response:
    status_code = 200
    content = b"{}"
    def __init__(self, features):
        self.features = features
    def raise_for_status(self):
        pass
    def json(self):
        return {"type": "FeatureCollection", "features": self.features}


class Session:
    def __init__(self, features):
        self.features = features
    def get(self, url, **kwargs):
        assert url.startswith("https://www.seismicportal.eu/fdsnws/event/1/query")
        assert kwargs["params"]["catalog"] == "EMSC-RTS"
        return Response(self.features)


class Tests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.sent = []
        self.set = lambda k, v: self.state.__setitem__(k, v)
        self.send = lambda payload: (self.sent.append(payload) or (2, 0))

    def test_filters(self):
        self.assertIsNotNone(early.normalize_feature(emsc(), NOW))
        self.assertIsNone(early.normalize_feature(emsc(place="PUERTO RICO REGION"), NOW))
        self.assertIsNone(early.normalize_feature(emsc(kind="ex"), NOW))
        self.assertIsNone(early.normalize_feature(emsc(mag=3.9), NOW))
        self.assertIsNone(early.normalize_feature(emsc(time_ms=NOW - 2500000), NOW))
        self.assertIsNone(early.normalize_feature(emsc(event_id="../x"), NOW))
        self.assertIsNone(early.normalize_feature(emsc(lat=0, lon=0), NOW))

    def test_baseline_and_single_preliminary(self):
        poll = early.poll_preliminary
        session = Session([emsc("first")])
        self.assertTrue(poll(session, NOW, [], self.state.get, self.set, self.send)["baseline"])
        self.assertEqual(self.sent, [])
        session.features = [emsc("first"), emsc("second")]
        self.assertEqual(poll(session, NOW, [], self.state.get, self.set, self.send)["sent"], 1)
        self.assertEqual(poll(session, NOW, [], self.state.get, self.set, self.send)["sent"], 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("PRELIMINAR", self.sent[0]["title"])
        self.assertTrue(early.previously_preliminary(usgs(), self.state[early.STATE_KEY]))

    def test_no_late_preliminary_when_usgs_earlier(self):
        early.poll_preliminary(Session([]), NOW, [], self.state.get, self.set, self.send)
        result = early.poll_preliminary(Session([emsc()]), NOW, [usgs()],
                                        self.state.get, self.set, self.send)
        self.assertEqual(result["sent"], 0)
        self.assertFalse(early.previously_preliminary(usgs(), self.state[early.STATE_KEY]))

    def test_aftershock_not_same_event(self):
        e = early.normalize_feature(emsc(), NOW)
        self.assertFalse(early.matches_usgs(e, usgs(time_ms=NOW - 130000)))
        self.assertFalse(early.matches_usgs(e, usgs(lat=19.8, lon=-72)))
        self.assertFalse(early.matches_usgs(e, usgs(mag=6.0)))

    def test_failed_send_can_retry(self):
        early.poll_preliminary(Session([]), NOW, [], self.state.get, self.set, self.send)
        def fail(_payload):
            raise RuntimeError("Push temporarily unavailable")
        with self.assertRaises(RuntimeError):
            early.poll_preliminary(Session([emsc()]), NOW, [], self.state.get, self.set, fail)
        self.assertEqual(early.poll_preliminary(Session([emsc()]), NOW, [],
                          self.state.get, self.set, self.send)["sent"], 1)

    def test_invalid_ledger_no_send(self):
        self.state[early.STATE_KEY] = "invalid"
        with self.assertRaises(ValueError):
            early.poll_preliminary(Session([emsc()]), NOW, [], self.state.get, self.set, self.send)
        self.assertEqual(self.sent, [])

    def test_clear_non_prediction(self):
        p = early.preliminary_payload(early.normalize_feature(emsc(), NOW))
        self.assertEqual(p["category"], "earthquake")
        self.assertIn("no es una predicción", p["body"])


if __name__ == "__main__":
    unittest.main()
