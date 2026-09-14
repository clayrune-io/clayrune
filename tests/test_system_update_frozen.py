"""Regression tests for the frozen (PyInstaller, no .git) Mac update check.

Before this, `/api/system/update/status` and `/api/system/update` both hard-
required `(_APP_DIR / '.git').exists()` and returned "Install directory is
not a git checkout" otherwise -- the notarized macOS .app is exactly that
kind of install, so the "Check for updates" menu item did nothing useful for
any Mac user.

A frozen build carries its own commit identity in a bundled build_info.json
(baked in by installer/build-macos.spec) and compares it against
Clayrune-macOS.build.json, a manifest published alongside the GitHub release
(tools/notarize-macos.sh) -- NOT the release tag/version, which gets
re-uploaded under the same tag when a same-day build needs a fix (v2.3.0 was
replaced 3 times in one day; comparing tags alone would never notice).

These tests pin:
1. An older bundled commit vs. a newer published manifest -> update_available
   + a usable download_url.
2. An identical commit -> up to date, no update offered.
3. GitHub unreachable -> fails quiet (no exception surfaced, no false
   update_available).
4. A normal dev git checkout is untouched by any of this -- the frozen branch
   is never even consulted when sys.frozen is not set (existing coverage in
   test_system_update_resync.py already exercises the git path itself; this
   file only pins that frozen-vs-git selection is correct).
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server
from mc.blueprints import system_routes as sr


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_urlopen_factory(release_assets, manifest_bodies, release_body=''):
    """Builds a fake urllib.request.urlopen that answers the release-lookup
    request and then the asset-download request(s) it triggers, keyed by URL.
    """
    release_payload = json.dumps({
        'tag_name': 'v9.9.9',
        'body': release_body,
        'assets': release_assets,
    }).encode('utf-8')

    def _urlopen(req, timeout=8):
        url = req.full_url if hasattr(req, 'full_url') else req
        if url == sr._MACOS_RELEASE_API:
            return _FakeResponse(release_payload)
        if url in manifest_bodies:
            return _FakeResponse(json.dumps(manifest_bodies[url]).encode('utf-8'))
        raise AssertionError(f'unexpected URL requested: {url}')

    return _urlopen


@pytest.fixture()
def frozen_app(tmp_path, monkeypatch):
    """A frozen, non-git install dir carrying a bundled build_info.json."""
    app_dir = tmp_path / 'Clayrune.app-Contents-MacOS'
    app_dir.mkdir()
    (app_dir / 'build_info.json').write_text(json.dumps({
        'commit': 'aaa1111',
        'commit_full': 'aaa1111111111111111111111111111111111',
        'branch': 'master',
        'built_at': '2026-09-01T00:00:00+00:00',
    }), encoding='utf-8')

    monkeypatch.setattr(sr, '_APP_DIR', app_dir)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    server.app.config['TESTING'] = True
    return {'app_dir': app_dir, 'client': server.app.test_client()}


MANIFEST_URL = 'https://example.invalid/releases/download/v9.9.9/Clayrune-macOS.build.json'
ZIP_URL = 'https://example.invalid/releases/download/v9.9.9/Clayrune-macOS.zip'
_ASSETS = [
    {'name': 'Clayrune-macOS.build.json', 'browser_download_url': MANIFEST_URL},
    {'name': 'Clayrune-macOS.zip', 'browser_download_url': ZIP_URL},
]


class TestNewerBuildAvailable:
    def test_status_reports_update_available(self, frozen_app, monkeypatch):
        manifest = {
            'commit': 'bbb2222',
            'commit_full': 'bbb2222222222222222222222222222222222222',
            'built_at': '2026-09-14T00:00:00+00:00',  # after the bundled 09-01
            'sha256': 'deadbeef',
            'size': 12345,
        }
        monkeypatch.setattr(
            sr.urllib.request, 'urlopen',
            _fake_urlopen_factory(_ASSETS, {MANIFEST_URL: manifest}, release_body='fixed a bug'),
        )

        resp = frozen_app['client'].get('/api/system/update/status')
        assert resp.status_code == 200
        body = resp.get_json()

        assert body['is_git_repo'] is False
        assert body['frozen'] is True
        assert body['update_available'] is True
        assert body['download_url'] == ZIP_URL
        assert body['remote_commit'] == 'bbb2222'
        assert body['commit'] == 'aaa1111'
        # Must never show the git-checkout error text to a Mac app user.
        assert 'git checkout' not in json.dumps(body)

    def test_post_update_offers_download_instead_of_400(self, frozen_app, monkeypatch):
        manifest = {
            'commit': 'bbb2222',
            'commit_full': 'bbb2222222222222222222222222222222222222',
            'built_at': '2026-09-14T00:00:00+00:00',
        }
        monkeypatch.setattr(
            sr.urllib.request, 'urlopen',
            _fake_urlopen_factory(_ASSETS, {MANIFEST_URL: manifest}),
        )

        resp = frozen_app['client'].post('/api/system/update')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['ok'] is False
        assert body['frozen'] is True
        assert body['download_required'] is True
        assert body['download_url'] == ZIP_URL
        # Must not be the generic dev/Windows "not a git checkout" error text
        # -- Mac users get an actionable message naming the actual next step.
        assert 'automatic updates not available' not in body['message']
        assert 'download the new build' in body['message']


class TestSameCommit:
    def test_status_reports_up_to_date(self, frozen_app, monkeypatch):
        manifest = {
            'commit': 'aaa1111',
            'commit_full': 'aaa1111111111111111111111111111111111',
            'built_at': '2026-09-01T00:00:00+00:00',
        }
        monkeypatch.setattr(
            sr.urllib.request, 'urlopen',
            _fake_urlopen_factory(_ASSETS, {MANIFEST_URL: manifest}),
        )

        resp = frozen_app['client'].get('/api/system/update/status')
        body = resp.get_json()
        assert body['update_available'] is False
        assert body['remote_commit'] == 'aaa1111'


class TestGithubUnreachable:
    def test_status_fails_quiet(self, frozen_app, monkeypatch):
        def _boom(req, timeout=8):
            raise OSError('network unreachable')
        monkeypatch.setattr(sr.urllib.request, 'urlopen', _boom)

        resp = frozen_app['client'].get('/api/system/update/status')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['is_git_repo'] is False
        assert body['frozen'] is True
        assert body['update_available'] is False
        assert 'Could not reach GitHub' in body['message']

    def test_post_update_still_offers_download_url(self, frozen_app, monkeypatch):
        """Even with no network, the POST path must not 400 -- it should
        fall back to the fixed releases/latest/download URL."""
        def _boom(req, timeout=8):
            raise OSError('network unreachable')
        monkeypatch.setattr(sr.urllib.request, 'urlopen', _boom)

        resp = frozen_app['client'].post('/api/system/update')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['ok'] is False
        assert body['download_url'] == sr._MACOS_DOWNLOAD_URL


class TestDevGitCheckoutUnaffected:
    def test_git_checkout_never_takes_frozen_path(self, tmp_path, monkeypatch):
        """A real git checkout must keep answering from the git branch even
        if sys.frozen happens to be set (defense against the frozen check
        being reordered ahead of the .git check in a future edit)."""
        import subprocess

        repo = tmp_path / 'checkout'
        subprocess.run(['git', 'init', '-b', 'master', str(repo)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'a@b.com'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'T'], check=True)
        (repo / 'f.txt').write_text('x')
        subprocess.run(['git', '-C', str(repo), 'add', 'f.txt'], check=True)
        subprocess.run(['git', '-C', str(repo), 'commit', '-m', 'x'], check=True, capture_output=True)

        monkeypatch.setattr(sr, '_APP_DIR', repo)
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        server.app.config['TESTING'] = True
        resp = server.app.test_client().get('/api/system/update/status')
        body = resp.get_json()
        assert body['is_git_repo'] is True
        assert 'frozen' not in body
