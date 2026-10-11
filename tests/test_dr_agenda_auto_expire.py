"""Offline contracts: DR Agenda expiry must never change unrelated services."""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_agenda_publisher_core as core
import expire_dr_agenda_events as job

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
def iso(dt):
    return dt.isoformat(timespec="seconds")


class FakeHosting:
    def __init__(self, events):
        self.release = "sites/dr-accesorios-rd-agenda/releases/live"
        self.version = "sites/dr-accesorios-rd-agenda/versions/existing"
        self.catalog = {
            "schemaVersion": 1, "revision": 7, "updatedAt": iso(NOW),
            "events": copy.deepcopy(events),
        }
        self.raw = core.json_bytes(self.catalog)
        self.manifest = {p: "hash-" + str(i) for i, p in enumerate(sorted(job.BASE_FILES))}
        self.manifest[core.CATALOG_PATH] = core.compress_digest(self.raw)[1]
        for e in events:
            if e.get("imageUrl"):
                self.manifest["/agenda/images/" + e["id"] + ".webp"] = "image-" + e["id"]
        self.config = {"headers": [
            {"glob": "/agenda/events.json", "headers": {
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            }}
        ]}
        self.calls = []

    def active(self):
        return self.release, self.version

    def files(self, version):
        assert version == self.version
        return self.manifest.copy()

    def api(self, method, path, **kwargs):
        self.calls.append((method, path))
        assert method == "GET" and path == self.version
        return {"config": copy.deepcopy(self.config), "fileCount": len(self.manifest)}


def event(eid, start, end, status="confirmed", photo=False):
    return {
        "id": "evt-" + eid, "title": eid,
        "status": status, "startAt": iso(start), "endAt": iso(end) if end else "",
        "imageUrl": ("https://dr-accesorios-rd-agenda.web.app/agenda/images/evt-" + eid + ".webp") if photo else "",
        "description": "Conservar descripción", "category": "tech",
        "location": {"type": "online", "online": True},
    }


class ExpireAgendaTests(unittest.TestCase):
    def setUp(self):
        self.old = event("expired", NOW - timedelta(hours=4), NOW, photo=True)
        self.future = event("upcoming", NOW + timedelta(days=2), NOW + timedelta(days=2, hours=2), photo=True)
        self.postponed = event("postponed", NOW - timedelta(days=1),
                               NOW + timedelta(days=3), status="postponed")
        self.api = FakeHosting([self.old, self.future, self.postponed])

    def plan(self, hosting=None):
        h = hosting or self.api
        with patch.object(job, "read_remote", return_value=h.raw):
            return job.prepare_plan(h, NOW)

    def test_remove_only_ended_and_its_image(self):
        plan = self.plan()
        after = json.loads(plan["new_raw"])
        self.assertEqual(after["revision"], 8)
        self.assertEqual(after["events"], [self.future, self.postponed])
        self.assertEqual(plan["expired"], [self.old])
        self.assertEqual(plan["removed_images"], ["/agenda/images/evt-expired.webp"])
        self.assertNotIn("/agenda/images/evt-expired.webp", plan["expected"])
        self.assertEqual(plan["expected"]["/agenda/images/evt-upcoming.webp"],
                         self.api.manifest["/agenda/images/evt-upcoming.webp"])
        self.assertEqual(plan["config"], self.api.config)
        for p, digest in plan["expected"].items():
            if p != core.CATALOG_PATH:
                self.assertEqual(self.api.manifest[p], digest)

    def test_end_at_exact_now_is_expired(self):
        keep, expired = job.partition_events(self.api.catalog, NOW)
        self.assertEqual([x["id"] for x in expired], [self.old["id"]])
        self.assertEqual(len(keep), 2)

    def test_without_end_date_uses_start(self):
        prior = event("no-end", NOW - timedelta(minutes=1), None)
        keep, expired = job.partition_events({
            "schemaVersion": 1, "revision": 1, "events": [prior]}, NOW)
        self.assertEqual(keep, [])
        self.assertEqual(expired, [prior])

    def test_invalid_or_duplicated_events_abort(self):
        for events in [
            [self.old, self.old],
            [dict(self.old, endAt="fecha incorrecta")],
            [dict(self.old, endAt=iso(NOW - timedelta(hours=5)))],
            [dict(self.old, id="../suspicious")],
        ]:
            with self.subTest(events=events):
                with self.assertRaises((job.AgendaError, TypeError, ValueError)):
                    job.partition_events({"schemaVersion": 1, "revision": 1, "events": events}, NOW)

    def test_no_expired_is_read_only(self):
        h = FakeHosting([self.future])
        p = self.plan(h)
        self.assertFalse(p["changed"])
        self.assertEqual(h.calls, [("GET", h.version)])

    def test_auditar_never_writes_or_notifies(self):
        with patch.object(job, "read_remote", return_value=self.api.raw), patch.object(job, "save_backup") as backup:
            self.assertEqual(job.expire(mode="auditar", api=self.api, now=NOW), 1)
            backup.assert_not_called()
        self.assertEqual(self.api.calls, [("GET", self.api.version)])

    def test_schedule_write_requires_main_repo_and_actions(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(job.AgendaError):
                job.expire(mode="aplicar", api=self.api, now=NOW)
        self.assertEqual(self.api.calls, [])

    def test_remote_hash_mismatch_aborts(self):
        self.api.manifest[core.CATALOG_PATH] = "old-wrong-hash"
        with self.assertRaises(job.AgendaError):
            self.plan()


if __name__ == "__main__":
    unittest.main()
