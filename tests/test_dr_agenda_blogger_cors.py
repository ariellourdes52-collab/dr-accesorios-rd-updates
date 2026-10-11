import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import enable_dr_agenda_blogger_cors as cors


class HostingStub:
    def __init__(self):
        self.calls = []
        self.release = 'sites/dr-accesorios-rd-agenda/releases/r0'
        self.version = 'sites/dr-accesorios-rd-agenda/versions/v0'
        self.files_map = {key: 'digest-' + str(i)
                          for i, key in enumerate(sorted(cors.BASE_FILES))}
        self.original = {'headers': [
            {'glob': '/agenda/**', 'headers': {'Cache-Control': 'public,max-age=180'}},
            {'glob': '/agenda/events.json',
             'headers': {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}},
        ]}

    def active(self):
        return self.release, self.version

    def files(self, version):
        return copy.deepcopy(self.files_map)

    def api(self, method, path, **kw):
        self.calls.append((method, path))
        if method == 'GET' and path == self.version:
            return {'config': copy.deepcopy(self.original),
                    'fileCount': len(self.files_map)}
        raise AssertionError('Unexpected write or API')


class BloggerCorsTests(unittest.TestCase):
    def test_public_catalog_only_and_idempotent(self):
        original = {'headers': [
            {'glob': '/agenda/events.json',
             'headers': {'Cache-Control': 'no-store'}},
            {'glob': '/agenda/**', 'headers': {'Cache-Control': 'public'}},
        ], 'redirects': []}
        clean = copy.deepcopy(original)
        result = cors.updated_config(original)
        self.assertEqual(original, clean)
        self.assertEqual(result['headers'][0]['headers']['Access-Control-Allow-Origin'], '*')
        self.assertEqual(result['headers'][0]['headers']['Cache-Control'], 'no-store')
        self.assertEqual(result['headers'][1], original['headers'][1])
        self.assertEqual(result['redirects'], [])
        self.assertEqual(cors.updated_config(result), result)

    def test_create_single_rule_if_absent(self):
        result = cors.updated_config({'headers': []})
        self.assertEqual(result['headers'], [
            {'glob': cors.CATALOG_PATH,
             'headers': {'Access-Control-Allow-Origin': '*'}}
        ])

    def test_audit_does_not_write(self):
        h = HostingStub()
        self.assertTrue(cors.run(mode='auditar', hosting=h))
        self.assertEqual(h.calls, [('GET', h.version)])

    def test_apply_without_auth_does_not_write(self):
        h = HostingStub()
        with self.assertRaises(cors.AgendaError):
            cors.run(mode='aplicar', confirm='INVALIDO', hosting=h)
        self.assertEqual(h.calls, [('GET', h.version)])

    def test_unknown_files_fail_closed(self):
        h = HostingStub()
        h.files_map['/radar/index.html'] = 'digest'
        with self.assertRaises(cors.AgendaError):
            cors.run(mode='auditar', hosting=h)
        self.assertEqual(h.calls, [])


if __name__ == '__main__':
    unittest.main()
