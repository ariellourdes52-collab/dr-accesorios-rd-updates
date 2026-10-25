"""Blogger -> Bing URL Submission API. Standard library only; no IndexNow key."""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

SITE = 'https://draccesoriosrd.blogspot.com/'
STATE_PATH = '.state/bing-url-submission.json'
NS = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
MAX_BATCH = 10

class SafeError(Exception):
    pass

def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.url, r.headers, r.read(8_000_001)
    except urllib.error.HTTPError as e:
        raise SafeError('HTTP %s' % e.code) from None
    except (OSError, ValueError):
        # Never print URL-bearing exceptions: Bing API key is a query parameter.
        raise SafeError('Network request failed') from None

def eligible(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme == 'https' and p.netloc == 'draccesoriosrd.blogspot.com'
            and not p.query and not p.fragment
            and bool(re.fullmatch(r'/(?:20\d{2}/\d{2}/[^/]+\.html|p/[^/]+\.html)', p.path))
            and not p.path.startswith('/p/politica-de-privacidad'))

def sitemap_entries(xml):
    root = ET.fromstring(xml)
    if root.tag != '{%s}urlset' % NS['s']:
        raise SafeError('Unexpected sitemap format')
    result = {}
    for node in root.findall('s:url', NS):
        url = node.findtext('s:loc', '', NS).strip()
        stamp = node.findtext('s:lastmod', '', NS).strip()
        if eligible(url) and stamp:
            result[url] = stamp
    if not result:
        raise SafeError('Empty sitemap; refusing to reset state')
    return result

def inventory():
    entries = {}
    for path in ('sitemap.xml', 'sitemap-pages.xml'):
        status, final, headers, body = request(SITE + path)
        if status != 200 or not final.startswith(SITE) or len(body) > 8_000_000:
            raise SafeError('Invalid sitemap response')
        entries.update(sitemap_entries(body))
    return entries

class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals = []
        self.robots = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'link' and 'canonical' in a.get('rel', '').lower().split():
            self.canonicals.append(a.get('href', ''))
        if tag == 'meta' and a.get('name', '').lower() in ('robots', 'bingbot'):
            self.robots.append(a.get('content', '').lower())

def valid_page(url, final, headers, body):
    if final != url or 'text/html' not in headers.get('Content-Type', '').lower():
        return False
    p = Metadata()
    p.feed(body.decode('utf-8', errors='replace'))
    directives = ','.join(p.robots + [headers.get('X-Robots-Tag', '').lower()])
    return p.canonicals == [url] and not re.search(r'\b(noindex|none)\b', directives)

def pending(entries, state):
    return sorted((u for u, stamp in entries.items() if state.get(u) != stamp),
                  key=lambda u: (entries[u], u))

def bing(method, key, payload=None):
    params = {'apikey': key}
    if payload is None:
        params['siteUrl'] = SITE
    url = 'https://ssl.bing.com/webmaster/api.svc/json/' + method + '?' + urllib.parse.urlencode(params)
    _, _, _, body = request(url, None if payload is None else json.dumps(payload).encode(),
                            {'Content-Type': 'application/json; charset=utf-8'})
    result = json.loads(body)
    if not isinstance(result, dict) or 'd' not in result or 'ErrorCode' in result or 'error' in result:
        raise SafeError('Bing returned an invalid or error response')
    return result['d']

def quota_limit(data):
    if not isinstance(data, dict):
        raise SafeError('Invalid Bing quota')
    values = [data.get('DailyQuota'), data.get('MonthlyQuota')]
    if any(type(v) is not int or v < 0 for v in values):
        raise SafeError('Invalid Bing quota')
    return min(MAX_BATCH, *values)

def state_api():
    repo = os.environ['GITHUB_REPOSITORY']
    return 'https://api.github.com/repos/' + repo + '/contents/' + STATE_PATH

def github_headers():
    return {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}

def read_state():
    try:
        _, _, _, raw = request(state_api() + '?ref=main', headers=github_headers())
    except SafeError as e:
        if str(e) == 'HTTP 404':
            return None, None
        raise
    obj = json.loads(raw)
    state = json.loads(base64.b64decode(obj['content']))
    if state.get('version') != 1 or not isinstance(state.get('urls'), dict):
        raise SafeError('Invalid state; refusing to reinitialize')
    return state['urls'], obj['sha']

def save_state(urls, sha):
    payload = {'message': 'chore: update Bing URL submission state [skip ci]', 'branch': 'main',
               'content': base64.b64encode(json.dumps({'version': 1, 'urls': urls},
                           indent=2, sort_keys=True).encode()).decode()}
    if sha:
        payload['sha'] = sha
    req = urllib.request.Request(state_api(), data=json.dumps(payload).encode(),
                                 headers=github_headers(), method='PUT')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status not in (200, 201):
                raise SafeError('State save failed')
    except (OSError, ValueError):
        raise SafeError('State save failed; accepted URLs may be retried next run') from None

def report(message):
    print(message)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(message + '\n\n')

def run(dry_run=False):
    entries = inventory()
    if dry_run:
        report('Dry run: %d eligible sitemap URLs. No submission or state write.' % len(entries))
        return
    key = os.environ.get('BING_WEBMASTER_API_KEY', '').strip()
    if not key:
        report('Not activated: add the BING_WEBMASTER_API_KEY Actions secret (not the IndexNow key).')
        return
    limit = quota_limit(bing('GetUrlSubmissionQuota', key))
    state, sha = read_state()
    if state is None:
        save_state(entries, None)
        report('Bing credentials verified. Baseline saved: %d URLs; 0 submitted. Future changes will be sent.' % len(entries))
        return
    changes = pending(entries, state)
    if not changes or limit == 0:
        report('Pending changes: %d. Available batch quota: %d. Nothing submitted.' % (len(changes), limit))
        return
    _, final, _, body = request(SITE + 'robots.txt')
    if final != SITE + 'robots.txt':
        raise SafeError('Unexpected robots.txt redirect')
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(body.decode().splitlines())
    batch = []
    # Bound requests even if some URLs are persistently unavailable.
    for url in changes[:50]:
        if not robots.can_fetch('bingbot', url):
            report('Deferred (robots): ' + url)
            continue
        try:
            status, final, headers, body = request(url)
            if status != 200 or len(body) > 8_000_000 or not valid_page(url, final, headers, body):
                report('Deferred (status/canonical/noindex): ' + url)
                continue
        except SafeError:
            report('Deferred (fetch failed): ' + url)
            continue
        batch.append(url)
        if len(batch) == limit:
            break
    if not batch:
        report('No eligible changed pages passed validation.')
        return
    result = bing('SubmitUrlBatch', key, {'siteUrl': SITE, 'urlList': batch})
    if result is not None:
        raise SafeError('Unexpected Bing acknowledgment; state was not advanced')
    for url in batch:
        state[url] = entries[url]
    save_state(state, sha)
    report('Bing accepted %d URLs (receipt only, not confirmed indexing):\n%s' % (len(batch), '\n'.join(batch)))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        run(args.dry_run)
    except Exception as exc:
        # Do not log raw exceptions from network/authentication libraries.
        report('ERROR: ' + (str(exc) if isinstance(exc, SafeError) else type(exc).__name__))
        sys.exit(1)
