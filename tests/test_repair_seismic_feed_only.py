"""Offline protections for surgical restoration of the missing seismic feed."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repair_seismic_feed_only as r


class SeismicRestoreSafety(unittest.TestCase):
    def test_payload_is_empty_and_deterministic(self):
        import gzip
        import hashlib
        import json
        raw, digest = r.payload()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertEqual(json.loads(gzip.decompress(raw)), r.EMPTY)
        self.assertEqual(r.EMPTY['alerts'], [])

    def test_only_seismic_cache_header_changes(self):
        old = {'headers': [{'glob': '/radar.json', 'headers': {'X-Keep': 'yes'}}],
               'rewrites': [{'source': '**', 'destination': '/index.html'}]}
        new = r.candidate_config(old)
        self.assertEqual(old['headers'], [{'glob': '/radar.json', 'headers': {'X-Keep': 'yes'}}])
        self.assertEqual(new['headers'][0], old['headers'][0])
        self.assertEqual(new['rewrites'], old['rewrites'])
        self.assertEqual(new['headers'][1]['glob'], r.PATH)
        self.assertEqual(new['headers'][1]['headers']['Cache-Control'],
                         'no-cache, no-store, must-revalidate')

    def test_only_one_new_file_and_no_hash_changes(self):
        before = {x: 'sha' + str(i) for i, x in enumerate(r.CRITICAL)}
        after = r.expected_manifest(before, 'new')
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual({x: after[x] for x in before}, before)
        self.assertEqual(after[r.PATH], 'new')

    def test_existing_seismic_alerts_never_overwritten(self):
        before = {x: 'sha' for x in r.CRITICAL}
        before[r.PATH] = 'existing-alerts-sha'
        with self.assertRaisesRegex(RuntimeError, 'ya existe feed'):
            r.expected_manifest(before, 'new')

    def test_aborts_if_unrelated_critical_file_is_missing(self):
        with self.assertRaisesRegex(RuntimeError, 'rutas críticas'):
            r.expected_manifest({'/radar.json': 'sha'}, 'new')

    def test_requires_exact_confirmation_before_using_secret(self):
        with self.assertRaisesRegex(RuntimeError, 'confirmación exacta'):
            r.run('restaurar', 'NO')

    def test_mismatch_between_live_and_public_aborts(self):
        hosting = Mock()
        hosting.active.return_value = ('release', r.SITE + '/versions/one')
        hosting.api.return_value = {'fileCount': len(r.CRITICAL), 'config': {}}
        hosting.files.return_value = {x: 'sha' for x in r.CRITICAL}
        hosting.public.return_value = Mock(status_code=200, ok=True)
        with self.assertRaisesRegex(RuntimeError, 'mismatch'):
            r.audit(hosting)

    def test_audit_only_gets_and_detects_missing_feed(self):
        hosting = Mock()
        hosting.active.return_value = ('release', r.SITE + '/versions/one')
        old = {x: 'sha' for x in r.CRITICAL}
        hosting.api.return_value = {'fileCount': len(old), 'config': {}}
        hosting.files.return_value = old
        hosting.public.side_effect = lambda path: Mock(status_code=404 if path == r.PATH else 200,
                                                       ok=path != r.PATH)
        snapshot = r.audit(hosting)
        self.assertTrue(snapshot['missing'])
        self.assertEqual(snapshot['files'], old)
        hosting.api.assert_called_once_with('GET', r.SITE + '/versions/one')

    def test_any_concurrent_change_aborts_before_writing(self):
        hosting = Mock()
        snap = {'site': r.SITE, 'release': 'release', 'version': r.SITE + '/versions/one',
                'files': {x: 'sha' for x in r.CRITICAL},
                'config': {}, 'publicStatus': 404, 'missing': True}
        with patch.object(r, 'audit', return_value={**snap, 'version': 'changed'}):
            with self.assertRaisesRegex(RuntimeError, 'Hosting cambió'):
                r.restore(hosting, snap)
        hosting.api.assert_not_called()

    def test_existing_feed_repair_is_forbidden(self):
        hosting = Mock()
        with self.assertRaisesRegex(RuntimeError, 'ya existe'):
            r.restore(hosting, {'missing': False})
        hosting.api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
