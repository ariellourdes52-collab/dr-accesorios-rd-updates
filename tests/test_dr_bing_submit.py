import importlib.util
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
spec = importlib.util.spec_from_file_location('bing', Path(__file__).parents[1] / 'scripts/dr_bing_submit.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
URL = b.SITE + '2026/10/noticia.html'
HEADERS = {'Content-Type': 'text/html; charset=utf-8'}
HTML = ('<link rel="canonical" href="%s"><meta name="robots" content="index,follow">' % URL).encode()

class BingTests(unittest.TestCase):
    def test_rotation_reaches_beyond_first_fifty(self):
        urls = ['url%d' % i for i in range(121)]
        seen = []
        for run in range(3):
            with patch.dict(b.os.environ, {'GITHUB_RUN_NUMBER': str(run)}):
                selected = b.candidates(urls)
                self.assertLessEqual(len(selected), 50)
                seen.extend(selected)
        self.assertEqual(seen, urls)

    def test_missing_key_is_failure(self):
        with patch.dict(b.os.environ, {'BING_WEBMASTER_API_KEY': ''}), patch.object(b, 'inventory', return_value={URL:'v1'}):
            with self.assertRaisesRegex(b.SafeError, 'Missing BING'):
                b.run()

    @patch.dict(b.os.environ, {'GITHUB_REPOSITORY':'owner/repo', 'GITHUB_TOKEN':'dummy'})
    def test_save_timeout_after_success_is_not_repeated(self):
        with patch.object(b.urllib.request, 'urlopen', side_effect=TimeoutError) as put, patch.object(b, 'read_state', return_value=({URL:'v2'}, 'new')):
            b.save_state({URL:'v2'}, 'old')
            self.assertEqual(put.call_count, 1)

    @patch.dict(b.os.environ, {'GITHUB_REPOSITORY':'owner/repo', 'GITHUB_TOKEN':'dummy'})
    def test_save_retries_without_resubmitting_to_bing(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(b.urllib.request, 'urlopen', side_effect=[TimeoutError, response]) as put, patch.object(b, 'read_state', return_value=({URL:'v1'}, 'old')), patch.object(b.time, 'sleep'), patch.object(b, 'bing') as api:
            b.save_state({URL:'v2'}, 'old')
            self.assertEqual(put.call_count, 2)
            api.assert_not_called()

    @patch.dict(b.os.environ, {'GITHUB_REPOSITORY':'owner/repo', 'GITHUB_TOKEN':'dummy'})
    def test_save_never_overwrites_concurrent_change(self):
        with patch.object(b.urllib.request, 'urlopen', side_effect=TimeoutError) as put, patch.object(b, 'read_state', return_value=({URL:'v3'}, 'new')):
            with self.assertRaisesRegex(b.SafeError, 'concurrently'):
                b.save_state({URL:'v2'}, 'old')
            self.assertEqual(put.call_count, 1)

    @patch.dict(b.os.environ, {'BING_WEBMASTER_API_KEY':'dummy'})
    def test_fetch_failure_is_not_green(self):
        with patch.object(b, 'inventory', return_value={URL:'v2'}), patch.object(b, 'bing', return_value={'DailyQuota':5,'MonthlyQuota':10}), patch.object(b, 'read_state', return_value=({URL:'v1'},'sha')), patch.object(b, 'save_state') as save, patch.object(b, 'request', side_effect=[(200,b.SITE+'robots.txt',{},b'User-agent: *\nAllow: /'),b.SafeError('HTTP 503')]):
            with self.assertRaisesRegex(b.SafeError, 'page fetches failed'):
                b.run()
            save.assert_not_called()

    @patch.dict(b.os.environ, {'BING_WEBMASTER_API_KEY':'dummy'})
    def test_receipt_logged_before_save_failure(self):
        messages = []
        def fail_save(*args):
            self.assertTrue(any('Bing accepted 1 URLs' in m and URL in m for m in messages))
            raise b.SafeError('State save failed')
        with patch.object(b,'inventory',return_value={URL:'v2'}), patch.object(b,'bing',side_effect=[{'DailyQuota':5,'MonthlyQuota':10},None]), patch.object(b,'read_state',return_value=({URL:'v1'},'sha')), patch.object(b,'save_state',side_effect=fail_save), patch.object(b,'report',side_effect=messages.append), patch.object(b,'request',side_effect=[(200,b.SITE+'robots.txt',{},b'User-agent: *\nAllow: /'),(200,URL,HEADERS,HTML)]):
            with self.assertRaisesRegex(b.SafeError, 'State save failed'):
                b.run()

    def test_sitemap_404_identifies_source(self):
        with patch.object(b, 'critical_get', side_effect=b.SafeError('HTTP 404')):
            with self.assertRaisesRegex(b.SafeError, r'Blogger sitemap sitemap.xml: HTTP 404'):
                b.inventory()
    def test_bing_404_identifies_method_without_exposing_key(self):
        with patch.object(b, 'critical_get', side_effect=b.SafeError('HTTP 404')):
            with self.assertRaises(b.SafeError) as cm:
                b.bing('GetUrlSubmissionQuota', 'secret-test-key')
        self.assertIn('Bing API GetUrlSubmissionQuota: HTTP 404', str(cm.exception))
        self.assertNotIn('secret-test-key', str(cm.exception))
    def test_critical_get_retries_only_transient_failures(self):
        expected = (200, b.SITE+'sitemap.xml', {}, b'<xml/>')
        with patch.object(b, 'request', side_effect=[b.SafeError('HTTP 404'), expected]) as req, patch.object(b.time, 'sleep') as sleep:
            self.assertEqual(b.critical_get(b.SITE+'sitemap.xml'), expected)
            self.assertEqual(req.call_count, 2)
            sleep.assert_called_once_with(1)
        with patch.object(b, 'request', side_effect=b.SafeError('HTTP 403')) as req, patch.object(b.time, 'sleep') as sleep:
            with self.assertRaisesRegex(b.SafeError, 'HTTP 403'):
                b.critical_get(b.SITE+'sitemap.xml')
            req.assert_called_once()
            sleep.assert_not_called()
    def test_bing_post_does_not_retry(self):
        with patch.object(b, 'request', side_effect=b.SafeError('HTTP 503')) as req, patch.object(b, 'critical_get') as retry:
            with self.assertRaisesRegex(b.SafeError, 'Bing API SubmitUrlBatch: HTTP 503'):
                b.bing('SubmitUrlBatch', 'secret-test-key', {'siteUrl': b.SITE, 'urlList': [URL]})
            req.assert_called_once()
            retry.assert_not_called()
    def test_url_filters(self):
        self.assertTrue(b.eligible(URL))
        for u in [URL+'?m=1', URL+'#comments', URL.replace('https:', 'http:'),
                  b.SITE+'search/label/IA', b.SITE+'p/politica-de-privacidad-dr-accesorios-rd.html',
                  URL.replace('blogspot.com', 'blogspot.com.evil.test')]:
            self.assertFalse(b.eligible(u), u)
    def test_page_checks(self):
        self.assertTrue(b.valid_page(URL, URL, HEADERS, HTML))
        self.assertFalse(b.valid_page(URL, URL+'?m=1', HEADERS, HTML))
        self.assertFalse(b.valid_page(URL, URL, HEADERS, HTML.replace(b'index,follow', b'noindex')))
        self.assertFalse(b.valid_page(URL, URL, HEADERS, HTML+HTML))
        self.assertFalse(b.valid_page(URL, URL, {**HEADERS, 'X-Robots-Tag':'noindex'}, HTML))
    def test_dedup_and_update(self):
        self.assertEqual(b.pending({URL:'v1'}, {URL:'v1'}), [])
        self.assertEqual(b.pending({URL:'v2'}, {URL:'v1'}), [URL])
        self.assertEqual(b.pending({}, {URL:'v1'}), [])
    def test_sitemap_dedup(self):
        node = '<url><loc>%s</loc><lastmod>2026-10-25</lastmod></url>' % URL
        xml = '<urlset xmlns="%s">%s</urlset>' % (b.NS['s'], node+node)
        self.assertEqual(b.sitemap_entries(xml), {URL:'2026-10-25'})
        with self.assertRaises(b.SafeError):
            b.sitemap_entries('<urlset xmlns="%s"/>' % b.NS['s'])
    def test_quota(self):
        self.assertEqual(b.quota_limit({'DailyQuota':50, 'MonthlyQuota':2}), 2)
        self.assertEqual(b.quota_limit({'DailyQuota':0, 'MonthlyQuota':50}), 0)
        for q in [{}, {'DailyQuota':-1,'MonthlyQuota':10}]:
            with self.assertRaises(b.SafeError): b.quota_limit(q)
    @patch.dict(b.os.environ, {'BING_WEBMASTER_API_KEY':'dummy'})
    def test_bootstrap_sends_nothing(self):
        with patch.object(b,'inventory',return_value={URL:'v1'}), patch.object(b,'bing',return_value={'DailyQuota':5,'MonthlyQuota':10}) as api, patch.object(b,'read_state',return_value=(None,None)), patch.object(b,'save_state') as save:
            b.run()
            api.assert_called_once_with('GetUrlSubmissionQuota','dummy')
            save.assert_called_once_with({URL:'v1'},None)
    @patch.dict(b.os.environ, {'BING_WEBMASTER_API_KEY':'dummy'})
    def test_failed_submit_does_not_advance(self):
        with patch.object(b,'inventory',return_value={URL:'v2'}), patch.object(b,'bing',side_effect=[{'DailyQuota':5,'MonthlyQuota':10},b.SafeError('HTTP 429')]), patch.object(b,'read_state',return_value=({URL:'v1'},'sha')), patch.object(b,'save_state') as save, patch.object(b,'request',side_effect=[(200,b.SITE+'robots.txt',{},b'User-agent: *\nAllow: /'),(200,URL,HEADERS,HTML)]):
            with self.assertRaises(b.SafeError): b.run()
            save.assert_not_called()
    @patch.dict(b.os.environ, {'BING_WEBMASTER_API_KEY':'dummy'})
    def test_success_advances_only_accepted(self):
        with patch.object(b,'inventory',return_value={URL:'v2'}), patch.object(b,'bing',side_effect=[{'DailyQuota':5,'MonthlyQuota':10},None]) as api, patch.object(b,'read_state',return_value=({URL:'v1'},'sha')), patch.object(b,'save_state') as save, patch.object(b,'request',side_effect=[(200,b.SITE+'robots.txt',{},b'User-agent: *\nAllow: /'),(200,URL,HEADERS,HTML)]):
            b.run()
            self.assertEqual(api.call_args.args[2]['urlList'], [URL])
            save.assert_called_once_with({URL:'v2'},'sha')
    def test_dry_run_never_calls_bing_or_writes(self):
        with patch.object(b,'inventory',return_value={URL:'v1'}), patch.object(b,'bing') as api, patch.object(b,'save_state') as save:
            b.run(True)
            api.assert_not_called()
            save.assert_not_called()

if __name__ == '__main__': unittest.main()

