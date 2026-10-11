import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import delete_dr_agenda_event as module


EVENT = "evt-20261016-delete-test"
OTHER = "evt-20261020-other-test"
TITLE = "Presentación de DR Agenda"


class FakeHosting:
    def __init__(self):
        self.manifest = {
            p: "hash-" + p for p in module.BASE_FILES
        }
        self.manifest["/agenda/images/" + EVENT + ".webp"] = "hash-image-selected"
        self.manifest["/agenda/images/" + OTHER + ".webp"] = "hash-image-other"
        self.calls = []

    def active(self):
        return ("sites/dr-accesorios-rd-agenda/releases/live",
                "sites/dr-accesorios-rd-agenda/versions/xyz")

    def files(self, version):
        return self.manifest.copy()

    def api(self, method, path):
        self.calls.append((method, path))
        assert method == "GET"
        return {"config": {"rewrites": []}, "fileCount": len(self.manifest)}


class DeleteAgendaTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeHosting()
        self.raw = json.dumps({
            "schemaVersion": 1, "revision": 7,
            "updatedAt": "2026-10-11T04:00:00Z",
            "events": [{"id": EVENT, "title": TITLE, "imageUrl": ""},
                       {"id": OTHER, "title": "Otro evento", "imageUrl": ""}]
        }).encode()

    def plan(self, *, revision=7, title=TITLE, event_id=EVENT):
        with patch.object(module, "read_remote", return_value=self.raw), \
             patch.object(module, "verify_remote_against_live"):
            return module.deletion_plan(self.api, event_id, revision, title)

    def test_only_selected_event_and_image_removed(self):
        plan = self.plan()
        data = json.loads(plan["new_raw"])
        self.assertEqual(data["revision"], 8)
        self.assertEqual([e["id"] for e in data["events"]], [OTHER])
        self.assertNotIn("/agenda/images/" + EVENT + ".webp", plan["expected"])
        self.assertEqual(plan["expected"]["/agenda/images/" + OTHER + ".webp"],
                         self.api.manifest["/agenda/images/" + OTHER + ".webp"])
        self.assertEqual(set(plan["expected"]) ^ (set(self.api.manifest) - {"/agenda/images/" + EVENT + ".webp"}), set())
        self.assertTrue(plan["photo_removed"])

    def test_wrong_title_revision_or_id_refused(self):
        for kw in [{"revision": 8}, {"title": "Otro evento"}, {"event_id": OTHER}]:
            with self.assertRaises(module.AgendaError):
                self.plan(**kw)

    def test_audit_does_not_write_or_send(self):
        with patch.object(module, "deletion_plan", return_value={
            "old_version": "previous", "photo_removed": False
        }), patch.object(module, "backup") as backup:
            module.delete_event(EVENT, 7, TITLE, "auditar", "", api=self.api)
        backup.assert_not_called()
        self.assertEqual(self.api.calls, [])

    def test_delete_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(module.AgendaError):
                module.delete_event(EVENT, 7, TITLE, "eliminar",
                                    module.CONFIRM, api=self.api)
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
