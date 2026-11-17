"""SF22 (MC-1179 pass 11): github/setup and code-sync/enable are human-only.

One POST to github/setup starts publishing backlog items and their notes as
GitHub issues; one POST to code-sync/enable pushes a branch with local commits.
Both used to check only that the project exists. They now refuse an unattended
caller, an attributed agent caller, a request with no browser Origin, and a
browser request without the retyped dashboard passcode, all before anything is
saved, validated or started.

The real `mc.github_sync` runs here against the conftest `fake_gh`, so
"gh never called" is measured at the subprocess seam, not assumed.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import caller_attribution as ca  # noqa: E402

PASSCODE = 'publish-gate-test-proof'
HUMAN = {'Origin': 'http://localhost:5199'}
ENABLE = '/api/project/tproj/code-sync/enable'
SETUP = '/api/project/tproj/github/setup'


@pytest.fixture()
def env(tmp_path, monkeypatch, gs, fake_gh):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    la._local_auth_set_passcode(PASSCODE)
    monkeypatch.setattr(la, '_LOCAL_AUTH_FAILS', {})
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    (tmp_path / 'uploads').mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    (data_dir / 'tproj.json').write_text(json.dumps({
        'id': 'tproj', 'name': 'T', 'status': 'active', 'backlog': [
            {'id': 'a', 'text': 'secret-ish item', 'status': 'open'}],
        'activity_log': [],
    }), encoding='utf-8')

    # Real github_sync (fake gh underneath), a recorder for the thread and for
    # the code-sync module so no real git runs.
    monkeypatch.setattr(pr, '_gh_sync', gs)
    started = []

    class _Thread:
        def __init__(self, *a, **kw):
            started.append(kw.get('target'))

        def start(self):
            pass

    monkeypatch.setattr(pr, 'threading', types.SimpleNamespace(Thread=_Thread))
    ps_calls = []

    class _ProjSync:
        def enable(self, pid):
            ps_calls.append(('enable', pid))
            return True, 'enabled'

        def disable(self, pid):
            ps_calls.append(('disable', pid))
            return True, 'disabled'

    monkeypatch.setattr(pr, '_proj_sync', _ProjSync())

    # Deterministic caller: nobody is attributed unless a test says so.
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(ca.UNATTRIBUTED, detail='test'))
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    fake_gh.calls.clear()   # setting the passcode ran icacls through the same seam
    server.app.config['TESTING'] = True
    yield types.SimpleNamespace(
        client=server.app.test_client(), data_dir=data_dir, fake_gh=fake_gh,
        started=started, ps_calls=ps_calls, state=mc_state, monkeypatch=monkeypatch)
    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update(snapshot)


def _rec(env):
    return json.loads((env.data_dir / 'tproj.json').read_text(encoding='utf-8'))


def _call(env, url, headers=None, passcode=None):
    body = {'repo': 'owner/repo'} if url == SETUP else {}
    if passcode is not None:
        body['passcode'] = passcode
    return env.client.post(url, json=body, headers=headers or {})


def _assert_nothing_happened(env):
    rec = _rec(env)
    assert not rec.get('github_repo') and not rec.get('github_sync_enabled')
    assert env.fake_gh.calls == []        # validate_repo never reached gh
    assert env.started == []              # no initial sync thread
    assert env.ps_calls == []             # code sync never enabled


def _dispatch_session(env):
    env.state.agent_sessions['s1'] = {
        'status': 'running', 'trigger_type': 'dispatch', 'project_id': 'tproj'}


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_unattended_caller_is_refused(env, url):
    _dispatch_session(env)
    r = _call(env, url)
    assert r.status_code == 403
    _assert_nothing_happened(env)


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_unattended_caller_is_refused_even_with_the_passcode(env, url):
    _dispatch_session(env)
    r = _call(env, url, passcode=PASSCODE)
    assert r.status_code == 403
    _assert_nothing_happened(env)


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_attributed_agent_is_refused_with_a_forged_origin_and_the_passcode(env, url):
    env.monkeypatch.setattr(ca, 'attribute_caller',
                            lambda *a, **k: ca.Attribution(ca.ATTRIBUTED, 'sess-abc', 42, 'agent'))
    r = _call(env, url, headers=HUMAN, passcode=PASSCODE)
    assert r.status_code == 403
    assert r.get_json()['error'] == 'human_only'
    _assert_nothing_happened(env)


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_no_origin_is_refused_even_with_the_passcode(env, url):
    r = _call(env, url, passcode=PASSCODE)
    assert r.status_code == 403
    assert r.get_json()['error'] == 'human_only'
    _assert_nothing_happened(env)


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_browser_origin_without_passcode_is_refused(env, url):
    r = _call(env, url, headers=HUMAN)
    assert r.status_code == 403
    _assert_nothing_happened(env)


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_browser_origin_with_wrong_passcode_is_refused(env, url):
    r = _call(env, url, headers=HUMAN, passcode='not-it')
    assert r.status_code == 403
    assert r.get_json()['error'] == 'bad_passcode'
    _assert_nothing_happened(env)


def test_refusal_comes_before_the_project_lookup(env):
    r = env.client.post('/api/project/nope/github/setup', json={'repo': 'o/r'})
    assert r.status_code == 403


def test_setup_accepted_with_origin_and_passcode(env):
    r = _call(env, SETUP, headers=HUMAN, passcode=PASSCODE)
    assert r.status_code == 200
    rec = _rec(env)
    assert rec['github_repo'] == 'owner/repo' and rec['github_sync_enabled']
    assert 'passcode' not in rec
    assert env.fake_gh.count(['repo', 'view']) == 1
    assert len(env.started) == 1


def test_enable_accepted_with_origin_and_passcode(env):
    r = _call(env, ENABLE, headers=HUMAN, passcode=PASSCODE)
    assert r.status_code == 200
    assert env.ps_calls == [('enable', 'tproj')]


@pytest.mark.parametrize('url', [SETUP, ENABLE])
def test_a_human_is_not_blocked_by_an_unrelated_running_agent(env, url):
    _dispatch_session(env)
    r = _call(env, url, headers=HUMAN, passcode=PASSCODE)
    assert r.status_code == 200


def test_disconnect_and_disable_stay_open(env):
    """Backing out of publishing needs no passcode, no Origin."""
    rec = _rec(env)
    rec.update(github_repo='o/r', github_sync_enabled=True)
    (env.data_dir / 'tproj.json').write_text(json.dumps(rec), encoding='utf-8')
    _dispatch_session(env)
    assert env.client.post('/api/project/tproj/github/disconnect').status_code == 200
    assert _rec(env)['github_repo'] == ''
    assert env.client.post('/api/project/tproj/code-sync/disable').status_code == 200
    assert env.ps_calls == [('disable', 'tproj')]
