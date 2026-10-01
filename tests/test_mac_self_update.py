"""MC-1026: in-place self-update of the frozen macOS Clayrune.app.

Pins, in order:
  1. every verification gate aborts (unsigned, Gatekeeper reject, not
     notarized, Team ID mismatch, unsigned running app, bundle id mismatch)
     and the gates run cheapest-first;
  2. every fallback condition hands back the download link instead of updating;
  3. the route refuses agents and an unproven human, takes the release from the
     server's own GitHub fetch (never the request body), and 409s on running
     agents;
  4. the job aborts without touching the old app or quitting, and on success
     writes the helper and quits through the shutdown path;
  5. the generated swap helper, run under a real sh with stubbed open/curl:
     swaps and cleans up on success, rolls back when the new app never answers,
     and leaves the old app alone when it never quit.

codesign/spctl are mocked: they exist only on macOS. The gate logic, not
Apple's tools, is what is under test.
"""
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server
from mc import mac_update as mu
from mc.blueprints import system_routes as sr

TEAM = 'ABCDE12345'
BUNDLE_ID = 'io.clayrune.app'
NOTARIZED = 'Clayrune.app: accepted\nsource=Notarized Developer ID\norigin=Developer ID Application: X (ABCDE12345)'


def make_app(root: Path, name='Clayrune.app', bundle_id=BUNDLE_ID) -> Path:
    app = root / name
    (app / 'Contents' / 'MacOS').mkdir(parents=True)
    with open(app / 'Contents' / 'Info.plist', 'wb') as f:
        plistlib.dump({'CFBundleIdentifier': bundle_id, 'CFBundleExecutable': 'Clayrune'}, f)
    return app


class FakeTools:
    """Stands in for mu._run. `teams` maps app path -> TeamIdentifier text."""

    def __init__(self, new_app, running_app, *, verify=(0, ''), spctl=(0, NOTARIZED),
                 new_team=TEAM, running_team=TEAM):
        self.calls = []
        self.verify, self.spctl = verify, spctl
        self.teams = {str(new_app): new_team, str(running_app): running_team}

    def __call__(self, cmd, timeout=180):
        self.calls.append(cmd[0] + ' ' + ' '.join(c for c in cmd[1:] if c.startswith('-')))
        if cmd[0] == 'codesign' and '--verify' in cmd:
            return self.verify
        if cmd[0] == 'spctl':
            return self.spctl
        if cmd[0] == 'codesign' and '-dv' in cmd:
            team = self.teams[cmd[-1]]
            return 0, f'Identifier={BUNDLE_ID}\nTeamIdentifier={team}'
        raise AssertionError(f'unexpected command {cmd}')


@pytest.fixture()
def apps(tmp_path):
    return make_app(tmp_path / 'new_dl'), make_app(tmp_path / 'installed')


def _patch(monkeypatch, tools):
    monkeypatch.setattr(mu, '_run', tools)


class TestVerificationGates:
    def test_all_gates_pass(self, apps, monkeypatch):
        new, old = apps
        _patch(monkeypatch, FakeTools(new, old))
        assert mu.verify_bundle(new, old) == {'team': TEAM, 'bundle_id': BUNDLE_ID}

    def test_unsigned_bundle_aborts(self, apps, monkeypatch):
        new, old = apps
        tools = FakeTools(new, old, verify=(1, f'{new}: code object is not signed at all'))
        _patch(monkeypatch, tools)
        with pytest.raises(mu.UpdateAbort, match='code-signature'):
            mu.verify_bundle(new, old)
        assert all('spctl' not in c for c in tools.calls)  # cheapest gate failed first

    def test_gatekeeper_rejection_aborts(self, apps, monkeypatch):
        new, old = apps
        _patch(monkeypatch, FakeTools(new, old, spctl=(3, f'{new}: rejected\nsource=no usable signature')))
        with pytest.raises(mu.UpdateAbort, match='Gatekeeper rejected'):
            mu.verify_bundle(new, old)

    def test_signed_but_not_notarized_aborts(self, apps, monkeypatch):
        new, old = apps
        _patch(monkeypatch, FakeTools(new, old, spctl=(0, f'{new}: accepted\nsource=Developer ID')))
        with pytest.raises(mu.UpdateAbort, match='not notarized'):
            mu.verify_bundle(new, old)

    def test_team_mismatch_aborts(self, apps, monkeypatch):
        new, old = apps
        _patch(monkeypatch, FakeTools(new, old, new_team='ZZZZZ99999'))
        with pytest.raises(mu.UpdateAbort, match='different team'):
            mu.verify_bundle(new, old)

    def test_running_app_without_team_aborts(self, apps, monkeypatch):
        new, old = apps
        _patch(monkeypatch, FakeTools(new, old, running_team='not set'))
        with pytest.raises(mu.UpdateAbort, match='not Developer-ID signed'):
            mu.verify_bundle(new, old)

    def test_new_app_without_team_aborts(self, apps, monkeypatch):
        new, old = apps
        _patch(monkeypatch, FakeTools(new, old, new_team='not set'))
        with pytest.raises(mu.UpdateAbort, match='no Team ID'):
            mu.verify_bundle(new, old)

    def test_bundle_identifier_mismatch_aborts(self, tmp_path, monkeypatch):
        new = make_app(tmp_path / 'new_dl', bundle_id='com.someone.else')
        old = make_app(tmp_path / 'installed')
        _patch(monkeypatch, FakeTools(new, old))
        with pytest.raises(mu.UpdateAbort, match='bundle identifier'):
            mu.verify_bundle(new, old)

    def test_missing_info_plist_aborts(self, tmp_path, monkeypatch):
        new = make_app(tmp_path / 'new_dl')
        (new / 'Contents' / 'Info.plist').unlink()
        old = make_app(tmp_path / 'installed')
        _patch(monkeypatch, FakeTools(new, old))
        with pytest.raises(mu.UpdateAbort, match='bundle identifier'):
            mu.verify_bundle(new, old)


class TestFallbackConditions:
    @pytest.fixture()
    def bundle(self, tmp_path, monkeypatch):
        b = make_app(tmp_path / 'Applications')
        monkeypatch.setattr(mu, 'is_frozen_mac', lambda: True)
        return b

    def test_eligible_install_has_no_blocker(self, bundle):
        assert mu.in_place_blocker(bundle) is None

    def test_not_frozen_mac(self, bundle, monkeypatch):
        monkeypatch.setattr(mu, 'is_frozen_mac', lambda: False)
        assert mu.in_place_blocker(bundle) == mu.NOT_FROZEN_MAC

    def test_bundle_not_locatable(self, bundle, monkeypatch):
        monkeypatch.setattr(mu, 'running_bundle', lambda: None)
        assert mu.in_place_blocker() == mu.NO_BUNDLE

    def test_app_translocation_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mu, 'is_frozen_mac', lambda: True)
        translocated = make_app(tmp_path / 'private' / 'var' / 'folders' / 'x' / 'AppTranslocation' / 'UUID' / 'd')
        assert mu.in_place_blocker(translocated) == mu.TRANSLOCATED

    def test_parent_folder_not_writable(self, bundle, monkeypatch):
        parent = bundle.parent
        real_access = mu.os.access
        monkeypatch.setattr(mu.os, 'access',
                            lambda p, mode: False if Path(p) == parent else real_access(p, mode))
        assert mu.in_place_blocker(bundle) == mu.PARENT_NOT_WRITABLE

    def test_running_bundle_walks_up_from_executable(self, tmp_path, monkeypatch):
        app = make_app(tmp_path)
        exe = app / 'Contents' / 'MacOS' / 'Clayrune'
        exe.write_text('')
        monkeypatch.setattr(mu.sys, 'executable', str(exe))
        assert mu.running_bundle() == app.resolve()


# ── route ───────────────────────────────────────────────────────────────────

MANIFEST_URL = 'https://example.invalid/releases/download/v9.9.9/Clayrune-macOS.build.json'
ZIP_URL = 'https://example.invalid/releases/download/v9.9.9/Clayrune-macOS.zip'
UI_HEADERS = {'Origin': 'http://localhost:5199'}
PASSCODE = 'unlock1234'


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    from mc.blueprints import local_auth
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    mu._reset_for_tests()
    yield
    mu._reset_for_tests()


@pytest.fixture()
def route(tmp_path, monkeypatch):
    """Frozen install whose update is newer, eligible for in-place update, with
    start_job recorded instead of run."""
    app_dir = tmp_path / 'meipass'
    app_dir.mkdir()
    (app_dir / 'build_info.json').write_text(json.dumps({
        'commit': 'aaa1111', 'commit_full': 'a' * 40, 'built_at': '2026-09-01T00:00:00+00:00'}))
    monkeypatch.setattr(sr, '_APP_DIR', app_dir)
    monkeypatch.setattr(sr, '_DATA_ROOT', tmp_path / 'dataroot')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(mu, 'in_place_blocker', lambda bundle=None: None)

    manifest = {'commit': 'bbb2222', 'commit_full': 'b' * 40,
                'built_at': '2026-09-14T00:00:00+00:00'}
    release = json.dumps({'tag_name': 'v9.9.9', 'body': '', 'assets': [
        {'name': 'Clayrune-macOS.build.json', 'browser_download_url': MANIFEST_URL},
        {'name': 'Clayrune-macOS.zip', 'browser_download_url': ZIP_URL,
         'digest': 'sha256:' + 'f' * 64, 'size': 4242}]}).encode()

    class _Resp:
        def __init__(self, b): self._b = b
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=8):
        url = req.full_url
        return _Resp(release if url == sr._MACOS_RELEASE_API else json.dumps(manifest).encode())

    monkeypatch.setattr(sr.urllib.request, 'urlopen', urlopen)

    started = []

    def fake_start(**kw):
        started.append(kw)
        return True, ''

    monkeypatch.setattr(mu, 'start_job', fake_start)
    monkeypatch.setattr(sr, '_get_active_restart_blockers',
                        lambda: {'active_sessions': [], 'active_hiveminds': []})
    server.app.config['TESTING'] = True
    return {'client': server.app.test_client(), 'started': started, 'app_dir': app_dir}


def _set_passcode():
    from mc.blueprints import local_auth
    local_auth._local_auth_set_passcode(PASSCODE)


class TestRoute:
    def test_agent_without_origin_is_refused(self, route):
        _set_passcode()
        resp = route['client'].post('/api/system/update', json={'passcode': PASSCODE})
        assert resp.status_code == 403
        assert 'human-only' in resp.get_json()['error']
        assert route['started'] == []

    def test_forged_origin_without_passcode_is_refused(self, route):
        _set_passcode()
        resp = route['client'].post('/api/system/update', json={}, headers=UI_HEADERS)
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'bad_passcode'
        assert route['started'] == []

    def test_forged_origin_wrong_passcode_is_refused(self, route):
        _set_passcode()
        resp = route['client'].post('/api/system/update', json={'passcode': 'nope-wrong'}, headers=UI_HEADERS)
        assert resp.status_code == 403
        assert route['started'] == []

    def test_no_passcode_configured_fails_closed(self, route):
        resp = route['client'].post('/api/system/update', json={'passcode': 'x'}, headers=UI_HEADERS)
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'passcode_required'
        assert route['started'] == []

    def test_human_with_passcode_starts_job_from_server_side_release(self, route):
        _set_passcode()
        resp = route['client'].post('/api/system/update', headers=UI_HEADERS, json={
            'passcode': PASSCODE, 'download_url': 'https://evil.invalid/x.zip', 'sha256': '0' * 64})
        assert resp.status_code == 202, resp.get_data(as_text=True)
        assert len(route['started']) == 1
        rel = route['started'][0]['release']
        assert rel['download_url'] == ZIP_URL  # not the client's
        assert rel['remote_commit'] == 'bbb2222'  # what the helper will wait to hear from the new app
        assert rel['sha256'] == 'f' * 64 and rel['size'] == 4242
        assert rel['release_tag'] == 'v9.9.9'
        assert route['started'][0]['force'] is False

    def test_running_agents_block_unless_forced(self, route, monkeypatch):
        _set_passcode()
        monkeypatch.setattr(sr, '_get_active_restart_blockers', lambda: {
            'active_sessions': [{'session_id': 's1'}], 'active_hiveminds': []})
        resp = route['client'].post('/api/system/update', json={'passcode': PASSCODE}, headers=UI_HEADERS)
        assert resp.status_code == 409
        assert resp.get_json()['active_sessions'][0]['session_id'] == 's1'
        assert route['started'] == []
        resp = route['client'].post('/api/system/update', headers=UI_HEADERS,
                                    json={'passcode': PASSCODE, 'force': True})
        assert resp.status_code == 202
        assert route['started'][0]['force'] is True

    def test_already_latest_is_409(self, route):
        _set_passcode()
        (route['app_dir'] / 'build_info.json').write_text(json.dumps({
            'commit': 'bbb2222', 'commit_full': 'b' * 40, 'built_at': '2026-09-14T00:00:00+00:00'}))
        resp = route['client'].post('/api/system/update', json={'passcode': PASSCODE}, headers=UI_HEADERS)
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'no_update_available'
        assert route['started'] == []

    @pytest.mark.parametrize('reason', [mu.TRANSLOCATED, mu.PARENT_NOT_WRITABLE, mu.NO_BUNDLE])
    def test_ineligible_install_gets_download_link_not_a_job(self, route, monkeypatch, reason):
        monkeypatch.setattr(mu, 'in_place_blocker', lambda bundle=None: reason)
        resp = route['client'].post('/api/system/update')  # no Origin, no passcode: nothing to guard
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['download_required'] is True and body['ok'] is False
        assert body['download_url'] == ZIP_URL
        assert body['fallback_reason'] == reason
        assert 'download the new build' in body['message']
        assert route['started'] == []

    def test_github_unreachable_falls_back_to_fixed_url(self, route, monkeypatch):
        def boom(req, timeout=8):
            raise OSError('offline')
        monkeypatch.setattr(sr.urllib.request, 'urlopen', boom)
        resp = route['client'].post('/api/system/update')
        body = resp.get_json()
        assert body['download_url'] == sr._MACOS_DOWNLOAD_URL
        assert body['fallback_reason'] == mu.GITHUB_UNREACHABLE
        assert route['started'] == []

    def test_status_reports_in_place_capability(self, route):
        body = route['client'].get('/api/system/update/status').get_json()
        assert body['in_place_update'] is True and body['in_place_blocker'] == ''

    def test_progress_route_returns_job_and_last_result(self, route, tmp_path):
        res = tmp_path / 'dataroot' / 'data'
        res.mkdir(parents=True)
        (res / 'mac_update_result.json').write_text('{"status":"rolled_back","reason":"x","ts":1}')
        body = route['client'].get('/api/system/update/progress').get_json()
        assert body['state'] == mu.IDLE
        assert body['last_result']['status'] == 'rolled_back'


# ── job orchestration ───────────────────────────────────────────────────────

class TestJob:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        bundle = make_app(tmp_path / 'Applications')
        (bundle / 'Contents' / 'MacOS' / 'Clayrune').write_text('old')
        data_dir = tmp_path / 'data'
        calls = {'quit': 0, 'spawn': []}

        def fake_download(url, dest, size, sha, log):
            dest.write_bytes(b'zip')

        def fake_extract(zip_path, into):
            return make_app(into)

        class FakeProc:
            pid = 4242
            def poll(self): return None
            def terminate(self): calls['terminated'] = True

        monkeypatch.setattr(mu, '_download', fake_download)
        monkeypatch.setattr(mu, '_extract', fake_extract)
        monkeypatch.setattr(mu, 'verify_bundle', lambda n, o: {'team': TEAM, 'bundle_id': BUNDLE_ID})
        monkeypatch.setattr(mu, '_spawn_swap', lambda script, log: calls['spawn'].append(script) or FakeProc())
        monkeypatch.setattr(mu, 'team_identifier', lambda app: TEAM)
        return {'bundle': bundle, 'data_dir': data_dir, 'calls': calls}

    def run(self, env, *, blockers=False, force=False):
        def quit_fn():
            env['calls']['quit'] += 1
        mu._set(state=mu.DOWNLOADING)
        mu._run_job(env['bundle'], {'download_url': ZIP_URL, 'remote_commit': 'abc1234'}, 5199, env['data_dir'],
                    quit_fn, lambda: blockers, force, lambda m: None)

    def stages(self, env):
        return [p for p in env['bundle'].parent.iterdir() if p.name.startswith('.clayrune-update-')]

    def test_success_writes_helper_then_quits(self, env):
        self.run(env)
        assert mu.job_snapshot()['state'] == mu.RESTARTING
        assert env['calls']['quit'] == 1
        script = env['calls']['spawn'][0]
        text = script.read_text()
        assert env['bundle'].as_posix() in text and 'mv "$OLD" "$ASIDE"' in text
        assert str(5199) in text and '/api/system/update/status' in text
        assert 'EXPECT=abc1234' in text
        assert (env['bundle'] / 'Contents' / 'MacOS' / 'Clayrune').read_text() == 'old'  # app untouched until it exits

    @pytest.mark.parametrize('stage_that_fails', ['_download', '_extract', 'verify_bundle'])
    def test_any_failing_stage_keeps_old_app_and_never_quits(self, env, monkeypatch, stage_that_fails):
        def boom(*a, **k):
            raise mu.UpdateAbort('refused for test')
        monkeypatch.setattr(mu, stage_that_fails, boom)
        self.run(env)
        snap = mu.job_snapshot()
        assert snap['state'] == mu.FAILED and snap['reason'] == 'refused for test'
        assert env['calls']['quit'] == 0 and env['calls']['spawn'] == []
        assert self.stages(env) == []  # staging removed
        assert (env['bundle'] / 'Contents' / 'MacOS' / 'Clayrune').read_text() == 'old'

    def test_unsigned_running_app_fails_before_downloading(self, env, monkeypatch):
        monkeypatch.setattr(mu, 'team_identifier', lambda app: None)
        monkeypatch.setattr(mu, '_download', lambda *a: pytest.fail('downloaded before the trust check'))
        self.run(env)
        snap = mu.job_snapshot()
        assert snap['state'] == mu.FAILED and 'not Developer-ID signed' in snap['reason']
        assert env['calls']['quit'] == 0
        assert self.stages(env) == []

    def test_release_without_a_commit_aborts_before_downloading(self, env, monkeypatch):
        monkeypatch.setattr(mu, '_download', lambda *a: pytest.fail('downloaded with nothing to confirm against'))
        mu._set(state=mu.DOWNLOADING)
        mu._run_job(env['bundle'], {'download_url': ZIP_URL}, 5199, env['data_dir'],
                    lambda: pytest.fail('quit'), lambda: False, False, lambda m: None)
        snap = mu.job_snapshot()
        assert snap['state'] == mu.FAILED and 'which build' in snap['reason']
        assert env['calls']['spawn'] == [] and self.stages(env) == []

    def test_helper_is_told_the_executable_name_from_the_new_bundle(self, env):
        self.run(env)
        assert 'EXE="$OLD/Contents/MacOS/"Clayrune' in env['calls']['spawn'][0].read_text()

    def test_agents_started_mid_download_abort_unless_forced(self, env):
        self.run(env, blockers=True, force=False)
        assert mu.job_snapshot()['state'] == mu.FAILED
        assert env['calls']['quit'] == 0
        mu._reset_for_tests()
        self.run(env, blockers=True, force=True)
        assert mu.job_snapshot()['state'] == mu.RESTARTING and env['calls']['quit'] == 1

    def test_quit_failure_after_spawn_terminates_helper_and_fails(self, env):
        def bad_quit():
            raise RuntimeError('cannot quit')
        mu._set(state=mu.DOWNLOADING)
        mu._run_job(env['bundle'], {'download_url': ZIP_URL, 'remote_commit': 'abc1234'}, 5199, env['data_dir'],
                    bad_quit, lambda: False, False, lambda m: None)
        assert mu.job_snapshot()['state'] == mu.FAILED
        assert env['calls'].get('terminated') is True
        assert self.stages(env) == []

    def test_second_job_refused_while_one_is_active(self, tmp_path, monkeypatch):
        bundle = make_app(tmp_path)
        monkeypatch.setattr(mu, 'running_bundle', lambda: bundle)
        monkeypatch.setattr(mu, 'in_place_blocker', lambda b=None: None)
        monkeypatch.setattr(mu, '_run_job', lambda *a: None)

        def start():
            return mu.start_job(release={}, port=1, data_dir=tmp_path, quit_fn=lambda: None,
                                blockers_fn=lambda: False, force=False)
        assert start() == (True, '')
        ok, err = start()
        assert ok is False and 'already in progress' in err


class TestDownload:
    def _urlopen(self, monkeypatch, payload):
        import io

        class Resp(io.BytesIO):
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(mu.urllib.request, 'urlopen', lambda req, timeout=60: Resp(payload))

    def test_checksum_mismatch_aborts(self, tmp_path, monkeypatch):
        self._urlopen(monkeypatch, b'hello')
        with pytest.raises(mu.UpdateAbort, match='checksum'):
            mu._download('https://x.invalid/a.zip', tmp_path / 'a.zip', 5, '0' * 64, lambda m: None)

    def test_checksum_match_passes(self, tmp_path, monkeypatch):
        import hashlib
        self._urlopen(monkeypatch, b'hello')
        mu._download('https://x.invalid/a.zip', tmp_path / 'a.zip', 5,
                     hashlib.sha256(b'hello').hexdigest(), lambda m: None)
        assert (tmp_path / 'a.zip').read_bytes() == b'hello'

    def test_short_download_aborts(self, tmp_path, monkeypatch):
        self._urlopen(monkeypatch, b'hel')
        with pytest.raises(mu.UpdateAbort, match='incomplete'):
            mu._download('https://x.invalid/a.zip', tmp_path / 'a.zip', 5, '', lambda m: None)

    def test_oversize_download_aborts(self, tmp_path, monkeypatch):
        self._urlopen(monkeypatch, b'hello world')
        with pytest.raises(mu.UpdateAbort, match='larger than expected'):
            mu._download('https://x.invalid/a.zip', tmp_path / 'a.zip', 5, '', lambda m: None)

    def test_size_matches_but_sha_differs_aborts(self, tmp_path, monkeypatch):
        self._urlopen(monkeypatch, b'hellx')
        import hashlib
        with pytest.raises(mu.UpdateAbort, match='checksum'):
            mu._download('https://x.invalid/a.zip', tmp_path / 'a.zip', 5,
                         hashlib.sha256(b'hello').hexdigest(), lambda m: None)

    def test_non_https_refused(self, tmp_path):
        with pytest.raises(mu.UpdateAbort, match='non-HTTPS'):
            mu._download('http://x.invalid/a.zip', tmp_path / 'a.zip', None, '', lambda m: None)


class TestReleaseIntegrityFields:
    """_fetch_latest_macos_release_info takes size/sha256 from the zip ASSET
    record (GitHub's `digest`), never from the build manifest."""

    HEX = 'ab' * 32

    def _fetch(self, monkeypatch, zip_asset, manifest=None):
        import io
        assets = [{'name': sr._MACOS_BUILD_MANIFEST_ASSET, 'browser_download_url': 'https://x.invalid/m.json',
                   'digest': 'sha256:' + 'cd' * 32, 'size': 7}]
        if zip_asset is not None:
            assets.append({'name': sr._MACOS_ZIP_ASSET, 'browser_download_url': 'https://x.invalid/z.zip',
                           **zip_asset})
        bodies = [json.dumps({'tag_name': 'v9', 'body': 'n', 'assets': assets}).encode(),
                  json.dumps(manifest or {'commit': 'c'}).encode()]

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(sr.urllib.request, 'urlopen', lambda req, timeout=8: Resp(bodies.pop(0)))
        return sr._fetch_latest_macos_release_info()

    def test_digest_and_size_parsed_from_zip_asset(self, monkeypatch):
        r = self._fetch(monkeypatch, {'digest': 'sha256:' + self.HEX.upper(), 'size': 123456})
        assert r['sha256'] == self.HEX and r['size'] == 123456

    def test_missing_digest_is_empty_not_invented(self, monkeypatch):
        r = self._fetch(monkeypatch, {'size': 99})
        assert r['sha256'] == '' and r['size'] == 99

    @pytest.mark.parametrize('digest', ['md5:' + 'a' * 32, 'sha256:xyz', 'sha256:' + 'a' * 63, '', None])
    def test_malformed_digest_is_empty(self, monkeypatch, digest):
        assert self._fetch(monkeypatch, {'digest': digest, 'size': 1})['sha256'] == ''

    def test_no_zip_asset_leaves_both_empty(self, monkeypatch):
        r = self._fetch(monkeypatch, None)
        assert r['sha256'] == '' and r['size'] is None

    def test_manifest_cannot_vouch_for_its_own_zip(self, monkeypatch):
        r = self._fetch(monkeypatch, {'size': 5}, manifest={'sha256': 'f' * 64, 'size': 1})
        assert r['sha256'] == '' and r['size'] == 5


# ── the generated swap helper, run under a real sh ──────────────────────────

SH = shutil.which('sh')
needs_sh = pytest.mark.skipif(SH is None, reason='no sh on PATH')


class TestSwapHelper:
    """Runs the generated script under a real sh. External commands (open,
    curl, ps, kill, mv) are stubs injected as shell FUNCTIONS naming the stub by
    absolute path: a function beats a PATH lookup, so Git Bash's own /usr/bin
    curl can never override the stub and reach a live server."""

    COMMIT = 'abc1234'

    @staticmethod
    def make_layout(tmp_path, app_name='Clayrune.app'):
        parent = tmp_path / 'Applications'
        old = parent / app_name
        old.mkdir(parents=True)
        (old / 'marker').write_text('old')
        stage = parent / '.clayrune-update-1'
        new = stage / 'extracted' / app_name
        new.mkdir(parents=True)
        (new / 'marker').write_text('new')
        stubs = tmp_path / 'stubs'
        stubs.mkdir()
        return {'parent': parent, 'old': old, 'new': new, 'stage': stage, 'stubs': stubs,
                'aside': parent / '.Clayrune-previous-1',
                'log': tmp_path / 'logs' / 'u.log', 'result': tmp_path / 'result.json',
                'opened': tmp_path / 'opened.txt', 'curl_log': tmp_path / 'curl.txt',
                'kill_log': tmp_path / 'kill.txt', 'alive': tmp_path / 'alive.txt'}

    @pytest.fixture()
    def layout(self, tmp_path):
        return self.make_layout(tmp_path)

    def stub(self, layout, name, body):
        f = layout['stubs'] / name
        with open(f, 'w', newline='\n') as fh:  # LF even on Windows: CR would end up in ps output
            fh.write('#!/bin/sh\n' + body + '\n')
        f.chmod(0o755)

    def run(self, layout, *, pid=999999, curl_body='{"commit":"abc1234"}', ps_lines=(),
            kill_alive=None, fail_failed_mv=False, expect=COMMIT, wait_exit_s=2, wait_up_s=2):
        layout['log'].parent.mkdir(parents=True, exist_ok=True)
        self.stub(layout, 'open', f'echo "$@" >> "{layout["opened"].as_posix()}"')
        if curl_body is None:
            self.stub(layout, 'curl', f'echo "$@" >> "{layout["curl_log"].as_posix()}"\nexit 22')
        else:
            self.stub(layout, 'curl', f'echo "$@" >> "{layout["curl_log"].as_posix()}"\n'
                                      f"cat <<'EOF'\n{curl_body}\nEOF")
        self.stub(layout, 'ps', 'cat <<\'EOF\'\n' + '\n'.join(ps_lines) + '\nEOF')
        if kill_alive is not None:
            layout['alive'].write_text('\n'.join(str(p) for p in kill_alive) + '\n')
            self.stub(layout, 'kill',
                      f'echo "$@" >> "{layout["kill_log"].as_posix()}"\n'
                      f'if [ "$1" = "-0" ]; then grep -qx "$2" "{layout["alive"].as_posix()}"; exit $?; fi\n'
                      'exit 0')
        if fail_failed_mv:
            self.stub(layout, 'mv', 'case "$2" in */.clayrune-failed-*) exit 1 ;; esac\nexec mv "$@"')
        script = layout['stage'] / 'swap.sh'
        text = mu.build_swap_script(
            bundle=layout['old'], new_app=layout['new'], aside=layout['aside'],
            stage=layout['stage'], pid=pid, port=5199, log_path=layout['log'],
            result_path=layout['result'], expect_commit=expect,
            wait_exit_s=wait_exit_s, wait_up_s=wait_up_s)
        funcs = ''.join(f'{f.name}() {{ "{f.as_posix()}" "$@"; }}\n'
                        for f in sorted(layout['stubs'].iterdir()))
        head, rest = text.split('\n', 1)
        with open(script, 'w', newline='\n') as fh:
            fh.write(head + '\n' + funcs + rest)
        r = subprocess.run([SH, script.as_posix()], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        return r, json.loads(layout['result'].read_text())

    @needs_sh
    def test_success_swaps_and_cleans_up(self, layout):
        r, res = self.run(layout)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (layout['old'] / 'marker').read_text() == 'new'
        assert not layout['aside'].exists() and not layout['stage'].exists()
        assert res['status'] == 'updated'
        assert layout['opened'].read_text().count('Clayrune.app') == 1
        assert 'http://127.0.0.1:5199/api/system/update/status' in layout['curl_log'].read_text()  # the stub ran

    @needs_sh
    @pytest.mark.parametrize('body', [
        '{"commit": "abc1234def5678"}',                    # full sha, spaced JSON
        '{\n  "remote_commit": "zzz9999",\n  "commit": "abc1234"\n}',  # pretty-printed, other keys around it
    ])
    def test_new_build_answering_in_any_json_shape_counts_as_up(self, layout, body):
        r, res = self.run(layout, curl_body=body)
        assert res['status'] == 'updated', r.stdout + r.stderr

    @needs_sh
    def test_new_app_never_answers_rolls_back(self, layout):
        r, res = self.run(layout, curl_body=None)
        assert r.returncode == 1, r.stdout + r.stderr
        assert (layout['old'] / 'marker').read_text() == 'old'
        assert not layout['aside'].exists() and not layout['stage'].exists()
        assert not any(p.name.startswith('.clayrune-failed') for p in layout['parent'].iterdir())
        assert res['status'] == 'rolled_back'
        assert layout['opened'].read_text().count('Clayrune.app') == 2  # new, then old again

    @needs_sh
    @pytest.mark.parametrize('body', [
        '{"commit":"deadbee"}',                 # a different build holds the port
        '{"status":"ok"}',                      # some other server entirely: 200, no commit
        '{"remote_commit":"abc1234"}',          # only a look-alike key
        '<html>hello</html>',
        '',
    ])
    def test_foreign_server_on_the_port_is_not_the_new_app(self, layout, body):
        r, res = self.run(layout, curl_body=body)
        assert res['status'] == 'rolled_back', r.stdout + r.stderr
        assert (layout['old'] / 'marker').read_text() == 'old'
        assert not layout['aside'].exists()  # restored, only now removed

    @needs_sh
    def test_failed_mv_during_rollback_leaves_the_backup_untouched(self, layout):
        r, res = self.run(layout, curl_body=None, fail_failed_mv=True)
        assert r.returncode == 2, r.stdout + r.stderr
        assert res['status'] == 'rollback_failed'
        assert (layout['aside'] / 'marker').read_text() == 'old'   # backup not nested, not moved
        assert not any((layout['aside'] / n).exists() for n in ('Clayrune.app',))
        assert (layout['old'] / 'marker').read_text() == 'new'     # failed app still where it was
        assert layout['aside'].as_posix() in res['reason'] and layout['old'].as_posix() in res['reason']

    @needs_sh
    def test_rollback_stops_only_the_exact_executable(self, tmp_path):
        layout = self.make_layout(tmp_path, 'Clayrune [Beta].app')
        exe = layout['old'].as_posix() + '/Contents/MacOS/Clayrune'
        ps = [
            f'  101 {exe}',                                   # the one to stop (ps pads the pid)
            f'  102 {exe}-helper',                            # same prefix
            f'  103 /elsewhere{exe}',                         # same suffix
            f'  104 {exe} Helper',                            # exe path plus more
            '  105 /usr/bin/vim',
            f'  106 {layout["old"].as_posix()}',             # mentions the path, is not the exe
        ]
        r, res = self.run(layout, curl_body=None, ps_lines=ps, kill_alive=[101, 102, 103])
        assert res['status'] == 'rolled_back', r.stdout + r.stderr
        signals = [ln.split() for ln in layout['kill_log'].read_text().splitlines()
                   if ln.split()[0] in ('-TERM', '-KILL')]
        assert signals == [['-TERM', '101'], ['-KILL', '101']]  # 101 was still alive after the wait
        assert (layout['old'] / 'marker').read_text() == 'old'

    @needs_sh
    @pytest.mark.skipif(sys.platform == 'win32', reason='MSYS pids differ from Windows pids')
    def test_old_app_that_never_quits_is_left_alone(self, layout):
        sleeper = subprocess.Popen(['sleep', '30'], stdin=subprocess.DEVNULL)
        try:
            r, res = self.run(layout, pid=sleeper.pid, wait_exit_s=1)
        finally:
            sleeper.kill()
            sleeper.wait()
        assert (layout['old'] / 'marker').read_text() == 'old'
        assert (layout['new'] / 'marker').read_text() == 'new' or not layout['new'].exists()
        assert res['status'] == 'aborted'

    def test_paths_with_spaces_and_quotes_stay_one_word(self, tmp_path):
        weird = tmp_path / "My Apps" / "it's Clayrune.app"
        text = mu.build_swap_script(
            bundle=weird, new_app=tmp_path / 'n', aside=tmp_path / 'a', stage=tmp_path / 's',
            pid=1, port=1, log_path=tmp_path / 'l', result_path=tmp_path / 'r',
            expect_commit='abc1234')
        import shlex
        old_line = next(ln for ln in text.splitlines() if ln.startswith('OLD='))
        assert shlex.split(old_line)[0] == 'OLD=' + weird.as_posix()

    @pytest.mark.parametrize('bad', ['', 'xyz', 'ABC1234', 'abc12', 'abc1234; rm', 'abc1234"'])
    def test_unusable_expected_commit_is_refused_at_generation(self, tmp_path, bad):
        with pytest.raises(ValueError):
            mu.build_swap_script(
                bundle=tmp_path / 'A.app', new_app=tmp_path / 'n', aside=tmp_path / 'a',
                stage=tmp_path / 's', pid=1, port=1, log_path=tmp_path / 'l',
                result_path=tmp_path / 'r', expect_commit=bad)
