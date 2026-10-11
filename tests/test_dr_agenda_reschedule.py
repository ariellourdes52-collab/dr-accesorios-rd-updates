"""Safe, offline, side-effect-free contract tests for DR Agenda date changes."""
import copy
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

THIS = Path(__file__).resolve()
SCRIPT = THIS.parents[1] / 'scripts' / 'reschedule_dr_agenda_event.py'

# Only the offline tests use a small stub of the already-tested publisher core.
# The actual script imports dr_agenda_publisher_core on GitHub Actions.
core = types.ModuleType('dr_agenda_publisher_core')
core.AgendaError = type('AgendaError', (Exception,), {})
core.SITE = 'sites/dr-accesorios-rd-agenda'
core.SITE_ID = 'dr-accesorios-rd-agenda'
core.PRIMARY_SITE_ID = 'dr-accesorios-rd'
core.PUBLIC = 'https://dr-accesorios-rd-agenda.web.app'
core.CATALOG_PATH = '/agenda/events.json'
core.FIREBASE_RESERVED_PATHS = frozenset({'/__/firebase/init.js', '/__/firebase/init.json'})
core.json_bytes = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()
core.compress_digest = lambda raw: (gzip.compress(raw, mtime=0), hashlib.sha256(gzip.compress(raw, mtime=0)).hexdigest())
core.read_remote = lambda: None
core.fingerprint = lambda raw: hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()
core.verify_remote_against_live = lambda *_a, **_kw: None
core.wait_for_agenda_inventory = lambda *_a, **_kw: None
core.verify_agenda_inventory = lambda *_a, **_kw: None
core.require = lambda condition, message: None if condition else (_ for _ in ()).throw(core.AgendaError(message))
core.parse_iso = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00'))
core.Hosting = object
sys.modules['dr_agenda_publisher_core'] = core
spec = importlib.util.spec_from_file_location('reschedule_tested', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeHosting:
    def __init__(self, raw):
        self.raw = raw
        self.release = 'sites/dr-accesorios-rd-agenda/releases/old'
        self.version = 'sites/dr-accesorios-rd-agenda/versions/old'
        self.files_live = {
            '/index.html': 'a', '/robots.txt': 'b', '/sitemap.xml': 'c',
            '/agenda/index.html': 'd', '/agenda/app.js': 'e',
            '/agenda/styles.css': 'f', '/agenda/logo-oficial.webp': 'g',
            '/agenda/events.json': 'h', '/agenda/images/evt-alpha.webp': 'i'
        }
    def active(self):
        return self.release, self.version
    def files(self, version):
        return copy.deepcopy(self.files_live)
    def api(self, method, path, **kw):
        if method == 'GET' and path == self.version:
            return {'fileCount': len(self.files_live), 'config': {'headers': []}}
        raise AssertionError('Unexpected network operation ' + str((method, path)))


class RescheduleTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.start = (now + timedelta(days=8)).replace(microsecond=0).isoformat()
        self.end = (now + timedelta(days=8, hours=2)).replace(microsecond=0).isoformat()
        self.new_start = (now + timedelta(days=16)).replace(microsecond=0).isoformat()
        self.new_end = (now + timedelta(days=16, hours=2)).replace(microsecond=0).isoformat()
        self.original = {
            'schemaVersion': 1, 'revision': 31, 'updatedAt': '2026-10-10T00:00:00Z',
            'events': [
                {'id': 'evt-alpha', 'title': 'Evento uno', 'startAt': self.start,
                 'endAt': self.end, 'description': 'Descripción original',
                 'imageUrl': 'https://dr-accesorios-rd-agenda.web.app/agenda/images/evt-alpha.webp',
                 'officialUrl': 'https://example.org', 'status': 'confirmed',
                 'location': {'city': 'Santo Domingo'}, 'notifyWebPush': True},
                {'id': 'evt-beta', 'title': 'Evento dos', 'startAt': self.start,
                 'endAt': self.end, 'description': 'Sin cambios', 'status': 'confirmed'},
            ],
        }
        self.raw = json.dumps(self.original, ensure_ascii=False)
        self.api = FakeHosting(self.raw)
    def prepare(self, **changes):
        args = dict(event_id='evt-alpha', revision=31, title='Evento uno',
                    expected_start=self.start, expected_end=self.end,
                    new_start=self.new_start, new_end=self.new_end, kind='postpone', reason='Aplazado por logística')
        args.update(changes)
        with patch.object(mod, 'read_remote', return_value=self.raw):
            return mod.prepare_reschedule(self.api, **args)
    def test_two_events_same_time_preserved(self):
        p = self.prepare()
        result = json.loads(p['new_data'])
        self.assertEqual(len(result['events']), 2)
        self.assertEqual(result['events'][1], self.original['events'][1])
        self.assertEqual(result['events'][0]['id'], 'evt-alpha')
        self.assertEqual(result['events'][0]['imageUrl'], self.original['events'][0]['imageUrl'])
        self.assertTrue(result['events'][0]['notifyWebPush'])
        self.assertEqual(result['events'][0]['startAt'], self.new_start)
        self.assertIn('Aplazado por logística', result['events'][0]['description'])
        self.assertEqual(result['revision'], 32)
        self.assertEqual(set(p['expected_files']), set(self.api.files_live))
        for name, digest in p['expected_files'].items():
            if name != '/agenda/events.json':
                self.assertEqual(digest, self.api.files_live[name])
    def test_second_reschedule_without_reason_removes_old_note(self):
        self.original['events'][0]['description'] = 'Descripción original\n\n📅 Evento pospuesto: logística anterior'
        self.raw = json.dumps(self.original, ensure_ascii=False)
        p = self.prepare(reason='', kind='change')
        e = json.loads(p['new_data'])['events'][0]
        self.assertEqual(e['description'], 'Descripción original')

    def test_invalid_end_rejected(self):
        with self.assertRaises(core.AgendaError):
            self.prepare(new_end=self.new_start)

    def test_prevent_stale_revision(self):
        with self.assertRaises(core.AgendaError):
            self.prepare(revision=30)
    def test_prevent_wrong_id(self):
        with self.assertRaises(core.AgendaError):
            self.prepare(event_id='evt-gamma')
    def test_prevent_earlier_postponement(self):
        with self.assertRaises(core.AgendaError):
            self.prepare(new_start=self.start)
    def test_fail_closed_production_without_gate(self):
        with patch.dict(os.environ, {'DR_AGENDA_RESCHEDULE_ENABLED':'false', 'GITHUB_ACTIONS':'true'}):
            with self.assertRaises(core.AgendaError):
                mod.reschedule(event_id='evt-alpha', revision=31, title='Evento uno',
                    expected_start=self.start, expected_end=self.end,
                    new_start=self.new_start, new_end=self.new_end, kind='postpone',
                    reason='', mode='publicar', confirm=mod.CONFIRM, api=self.api)
    def test_audit_mode_does_not_write(self):
        with patch.object(mod, 'read_remote', return_value=self.raw):
            mod.reschedule(event_id='evt-alpha', revision=31, title='Evento uno',
                expected_start=self.start, expected_end=self.end,
                new_start=self.new_start, new_end=self.new_end, kind='postpone',
                reason='', mode='auditar', confirm='', api=self.api)

if __name__ == '__main__':
    unittest.main()
