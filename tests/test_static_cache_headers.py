"""Cache headers for static + brand assets (server.py after_request hooks).

Measured 2026-08-06: /static/js was the largest remaining group of a page load
(1,289 KB over 41 requests) purely because Flask's default `no-cache` forced a
revalidation round trip for every JS/CSS file on every load. On localhost that
is ~1.7 ms each and invisible; over the Cloudflare tunnel, with the dev
server's `Connection: close`, it is the dominant cost.

The safety property these tests pin is the one that makes a long max-age
survivable at all: `immutable` is granted ONLY when the request carries the
`?v=<asset_version>` that `/` injects into every /static reference. Drop that
condition and a bare URL could pin a client to an asset it can never re-fetch.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


def test_versioned_static_is_immutable(client):
    r = client.get('/static/js/render-core.js?v=123456')
    assert r.status_code == 200
    assert r.headers['Cache-Control'] == 'public, max-age=31536000, immutable'


def test_unversioned_static_is_not_immutable(client):
    """No cache-bust → no long cache. This is the guard, not an edge case:
    an immutable response on a bare URL is unreachable by any future deploy."""
    r = client.get('/static/js/render-core.js')
    assert r.status_code == 200
    assert 'immutable' not in r.headers.get('Cache-Control', '')


@pytest.mark.parametrize('path', ['/static/sw.js', '/static/manifest.json'])
def test_service_worker_and_manifest_never_immutable(client, path):
    """Both gate app-update delivery — caching them long-term strands users on
    an old shell, which is exactly the failure the ?v= scheme exists to avoid."""
    r = client.get(path + '?v=123456')
    if r.status_code == 404:
        pytest.skip(f'{path} not present in this checkout')
    assert 'immutable' not in r.headers.get('Cache-Control', '')


def test_index_is_never_cached(client):
    """index.html carries the ?v= token, so it must stay no-store or a stale
    copy pins the whole SPA to an old asset version."""
    r = client.get('/')
    assert r.status_code == 200
    cc = r.headers.get('Cache-Control', '')
    assert 'no-store' in cc and 'immutable' not in cc


def test_brand_assets_cached_but_not_immutable(client):
    """/assets/* has no ?v=, so it gets a bounded max-age — long enough to stop
    the same image being fetched twice per boot, short enough to self-heal."""
    r = client.get('/assets/claydo-idle.webp')
    if r.status_code == 404:
        pytest.skip('mascot asset not present in this checkout')
    cc = r.headers.get('Cache-Control', '')
    assert 'max-age=3600' in cc and 'immutable' not in cc


def test_app_contributes_no_date_on_static(client):
    """Static responses went out with TWO Date headers — a malformed header set.

    Only one was ours: `send_file` sets a Date on the Response and the WSGI
    server adds its own BELOW the WSGI layer, out of reach of any after_request
    hook. So the app must contribute none and let the server's stand alone —
    which is what this asserts. Under the test client there is no server, so
    zero Date headers here is the correct expectation, not a missing one.
    """
    r = client.get('/static/js/render-core.js?v=123456')
    assert r.headers.getlist('Date') == []
    # API responses never had the duplicate — Flask sets no Date on them.
    assert len(client.get('/api/version').headers.getlist('Date')) <= 1


@pytest.mark.parametrize('filename,expected', [
    ('js/walkthrough.js', 'text/javascript'),
    ('js/composer-extras.js', 'text/javascript'),
    ('js/project-actions.js', 'text/javascript'),
    ('css/app.css', 'text/css'),
])
def test_static_types_ignore_bad_windows_registry(client, monkeypatch, filename, expected):
    import mimetypes
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.js', 'text/plain')
    monkeypatch.setitem(mimetypes.types_map, '.css', 'application/octet-stream')
    response = client.get('/static/' + filename + '?v=bad-registry')
    assert response.status_code == 200
    assert response.mimetype == expected
    assert response.headers['Cache-Control'].endswith('immutable')
    response.close()


def test_missing_module_remains_html_error(client):
    response = client.get('/static/js/does-not-exist.js')
    assert response.status_code == 404
    assert response.mimetype == 'text/html'


def test_mime_fix_busts_previously_immutable_assets(client):
    import server
    assert server._asset_version().endswith('-mime1')
    assert ('?v=' + server._asset_version()).encode() in client.get('/').data


def test_chromium_accepts_actual_tour_response_with_bad_registry(client, monkeypatch):
    import json
    import mimetypes
    import shutil
    import subprocess
    node = shutil.which('node')
    smoke_dir = PROJECT_ROOT / 'tools' / 'smoke'
    if not node or not (smoke_dir / 'node_modules' / 'playwright').exists():
        pytest.skip('local Playwright installation required')
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.js', 'text/plain')
    response = client.get('/static/js/walkthrough.js')
    payload = json.dumps({'body': response.get_data(as_text=True),
                          'contentType': response.headers['Content-Type']})
    response.close()
    script = '''
import {chromium} from 'playwright';
let input = ''; for await (const chunk of process.stdin) input += chunk;
const asset = JSON.parse(input);
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage();
  await page.route('http://mime.test/**', route => {
    if (route.request().url().endsWith('.js')) return route.fulfill(asset);
    return route.fulfill({contentType:'text/html', body:'<script type="module" src="/walkthrough.js"></script>'});
  });
  await page.goto('http://mime.test/');
  await page.waitForFunction(() => typeof window.startWalkthrough === 'function', {timeout:5000});
} finally { await browser.close(); }
'''
    result = subprocess.run([node, '--input-type=module', '-e', script],
                            input=payload, text=True, capture_output=True,
                            cwd=smoke_dir, timeout=15)
    assert result.returncode == 0, result.stderr
