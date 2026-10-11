import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap_dr_agenda_site as boot
from dr_agenda_publisher_core import AgendaError


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'agenda_site'

    def test_site_pinned_to_secondary(self):
        boot.assert_isolated()
        self.assertEqual(boot.SITE, 'sites/dr-accesorios-rd-agenda')
        self.assertNotEqual(boot.SITE_ID, boot.PRIMARY_SITE_ID)

    def test_bundle_has_only_allowed_files(self):
        assets = boot.payloads(self.root)
        self.assertEqual(set(assets), set(boot.FILES))
        self.assertEqual(len(assets), 8)

    def test_seed_catalog_empty(self):
        import json
        catalog = json.loads(boot.payloads(self.root)['/agenda/events.json'])
        self.assertEqual((catalog['schemaVersion'], catalog['revision'], catalog['events']), (1,1,[]))

    def test_audit_never_writes(self):
        fake=MagicMock()
        fake.active.return_value=(None,None)
        with patch.object(boot,'Hosting',return_value=fake):
            result=boot.initialize(mode='auditar',root=self.root)
        self.assertEqual(result['mode'],'auditar')
        fake.api.assert_called_once_with("GET", boot.SITE + "/versions", params={"pageSize": 20})
        fake.session.post.assert_not_called()

    def test_uploaded_files_ready_requires_all_active(self):
        fake = MagicMock()
        expected = {"/agenda/index.html": "abc123", "/agenda/events.json": "xyz987"}
        def response(method, path, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(path, boot.SITE + "/versions/new/files")
            state = kwargs["params"]["status"]
            return {"files": [{"path": p, "hash": h} for p,h in
                              (expected.items() if state == "ACTIVE" else [])]}
        fake.api.side_effect = response
        with patch.object(boot.time, "sleep") as sleep:
            boot.wait_for_uploaded_files(fake, boot.SITE + "/versions/new", expected, max_checks=2)
        sleep.assert_not_called()

    def test_uploaded_files_pending_waits_and_retries(self):
        fake = MagicMock()
        expected = {"/agenda/events.json": "hash1"}
        states = iter([{"files": []}, {"files": [{"path": "/agenda/events.json", "hash": "hash1"}]},
                       {"files": [{"path": "/agenda/events.json", "hash": "hash1"}]}, {"files": []}])
        fake.api.side_effect = lambda method, path, **kwargs: next(states)
        with patch.object(boot.time, "sleep") as sleep:
            boot.wait_for_uploaded_files(fake, boot.SITE + "/versions/new", expected, max_checks=2)
        sleep.assert_called_once()

    def test_unexpected_hash_never_finalize(self):
        fake = MagicMock()
        fake.api.return_value = {"files": [{"path": "/agenda/index.html", "hash": "tampered"}]}
        with self.assertRaisesRegex(AgendaError, "hash inesperado"):
            boot.wait_for_uploaded_files(fake, boot.SITE + "/versions/new",
                                         {"/agenda/index.html": "safe"}, max_checks=1)
        fake.session.post.assert_not_called()

    def test_permanent_pending_fails_closed(self):
        fake = MagicMock()
        def resp(method, path, **kwargs):
            status = kwargs["params"]["status"]
            if status == "ACTIVE":
                return {"files": []}
            return {"files": [{"path": "/agenda/index.html", "hash": "safe"}]}
        fake.api.side_effect = resp
        with patch.object(boot.time, "sleep") as sleep:
            with self.assertRaisesRegex(AgendaError, "sin publicar"):
                boot.wait_for_uploaded_files(fake, boot.SITE + "/versions/new",
                                             {"/agenda/index.html": "safe"}, max_checks=2)
        sleep.assert_called_once()
        fake.session.post.assert_not_called()

    def test_auditar_reads_existing_drafts_without_writing(self):
        fake = MagicMock()
        name = boot.SITE + "/versions/draft1"
        def resp(method, path, **kwargs):
            self.assertEqual(method, "GET")
            if path == boot.SITE + "/versions":
                return {"versions": [{"name": name, "status": "CREATED"}]}
            if path == name + "/files":
                return {"files": [{"path": "/agenda/index.html", "hash": "safe"}]}
            self.fail("Unexpected API path")
        fake.api.side_effect = resp
        boot.inspect_created_versions(fake)
        fake.session.post.assert_not_called()

    def test_existing_release_aborts_without_writing(self):
        fake=MagicMock()
        fake.active.return_value=('release/1','sites/dr-accesorios-rd-agenda/versions/1')
        with patch.object(boot,'Hosting',return_value=fake):
            with self.assertRaisesRegex(AgendaError, 'ya tiene publicación'):
                boot.initialize(mode='auditar',root=self.root)
        fake.api.assert_not_called()

    def test_install_fails_without_confirm(self):
        fake=MagicMock();fake.active.return_value=(None,None)
        with patch.object(boot,'Hosting',return_value=fake):
            with self.assertRaisesRegex(AgendaError,'confirmación'):
                boot.initialize(mode='instalar',root=self.root)
        fake.api.assert_not_called()

    def test_install_fails_outside_github(self):
        fake=MagicMock();fake.active.return_value=(None,None)
        with patch.object(boot,'Hosting',return_value=fake),patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(AgendaError,'GitHub Actions'):
                boot.initialize(mode='instalar',confirm='INSTALAR-DR-AGENDA-V1',root=self.root)
        fake.api.assert_not_called()

    def test_main_host_denied_if_config_mutates(self):
        with patch.object(boot,'SITE', 'sites/dr-accesorios-rd'):
            with self.assertRaisesRegex(AgendaError,'PROHIBIDO'):
                boot.assert_isolated()

    def test_existing_catalog_never_overwritten(self):
        fake=MagicMock();fake.active.return_value=('old','sites/dr-accesorios-rd-agenda/versions/2')
        with patch.object(boot,'Hosting',return_value=fake),patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}):
            with self.assertRaises(AgendaError):
                boot.initialize(mode='instalar',confirm='INSTALAR-DR-AGENDA-V1',root=self.root)
        fake.api.assert_not_called()

if __name__=='__main__':unittest.main()
