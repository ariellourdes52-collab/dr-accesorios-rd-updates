import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/dr-radar-webpush"))
import web_seismic_dedup as d
import early_seismic as e

NOW = 1795600000000

def alert(source, id, t=NOW, lat=18.7, lon=-70.1, mag=4.5, **aliases):
    return {"id": "earthquake-" + ("usgs" if source=="USGS" else "emsc") + "-" + id,
            "earthquake": {"source":source, "sourceEventId":id, "eventTime":t,
                           "latitude":lat,"longitude":lon,"magnitude":mag, **aliases}}

class TestDedup(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.sent = []
    def run_feed(self, rows, seen=(), baseline=False):
        d.dispatch_feed(rows, set(seen), lambda a:a["id"],
                        lambda a:(self.sent.append(a["id"]) or (1,0)),
                        self.state.get, self.state.__setitem__, NOW, baseline)
    def test_same_batch(self):
        self.run_feed([alert("EMSC-RTS","e1"), alert("USGS","u1")])
        self.assertEqual(self.sent, ["earthquake-usgs-u1"])
    def test_direct_first(self):
        self.state[e.STATE_KEY] = json.dumps({"version":1,"events":[
            {"id":"e1","sent":True,"eventTime":NOW,"latitude":18.7,"longitude":-70.1,"magnitude":4.5}]})
        self.run_feed([alert("EMSC-RTS","e1"),alert("USGS","u1")])
        self.assertEqual(self.sent,[])
    def test_feed_first_and_restart(self):
        self.run_feed([alert("USGS","u1")])
        self.assertTrue(d.skip_direct({"id":"e1","eventTime":NOW,"latitude":18.7,"longitude":-70.1,"magnitude":4.5},self.state.get,NOW))
        self.run_feed([alert("EMSC-RTS","e1")])
        self.assertEqual(len(self.sent),1)
    def test_distinct_same_source(self):
        self.run_feed([alert("USGS","u1"),alert("USGS","u2")])
        self.assertEqual(len(self.sent),2)
    def test_ambiguous_cross_source(self):
        self.run_feed([alert("USGS","u1"),alert("USGS","u2"),alert("EMSC-RTS","e1")])
        self.assertEqual(len(self.sent),3)
    def test_distinct_time(self):
        self.run_feed([alert("USGS","u1"),alert("EMSC-RTS","e1",t=NOW-120000)])
        self.assertEqual(len(self.sent),2)
    def test_explicit_alias(self):
        self.run_feed([alert("EMSC-RTS","e1"),alert("USGS","u1",t=NOW-120000,emscEventId="e1")])
        self.assertEqual(len(self.sent),1)
    def test_baseline(self):
        self.run_feed([alert("USGS","u1")],baseline=True)
        self.run_feed([alert("EMSC-RTS","e1")])
        self.assertEqual(self.sent,[])
    def test_non_seismic(self):
        self.run_feed([{"id":"tech-1"}])
        self.assertEqual(self.sent,["tech-1"])

if __name__ == "__main__":
    unittest.main()
