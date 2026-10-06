"""The Settings toggle for `codex_unattended_sandbox` (backlog 627a4961, MC-975 Part A).

The toggle is a client of PUT /api/config, so the human-only gate is the one every key on
`_CONFIG_EDITABLE_KEYS` already has (`is_unattended_caller()` then the retyped dashboard
passcode). These tests pin that for THIS key, against the real passcode check: an agent cannot
turn its own sandbox off, and the value only changes for a human who typed the passcode.
"""
from __future__ import annotations

import json

import pytest

PASSCODE = 'unlock1234'
HUMAN = {'Origin': 'http://localhost:5199'}  # a browser fetch always carries one


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state
    from mc.blueprints import local_auth as la
    from mc.blueprints import settings_routes as sr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(sr, 'SETTINGS_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(sr, 'CONFIG_PATH', tmp_path / 'config.json')
    la._local_auth_set_passcode(PASSCODE)
    la._LOCAL_AUTH_FAILS.clear()

    cfg_snapshot = dict(state.CONFIG)
    sess_snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()
    state.CONFIG['codex_unattended_sandbox'] = True
    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.state = state
    c.sr = sr
    c.config_path = tmp_path / 'config.json'
    yield c

    state.CONFIG.clear()
    state.CONFIG.update(cfg_snapshot)
    state.agent_sessions.clear()
    state.agent_sessions.update(sess_snapshot)


def _put(ctx, value, headers=None, passcode=PASSCODE):
    body = {'codex_unattended_sandbox': value}
    if passcode is not None:
        body['passcode'] = passcode
    return ctx.client.put('/api/config', json=body, headers=headers or {})


def _running(ctx, sid, trigger_type):
    ctx.state.agent_sessions[sid] = {'status': 'running', 'trigger_type': trigger_type,
                                     'project_id': 'p'}


def test_key_is_editable_and_get_config_reports_it(ctx):
    assert 'codex_unattended_sandbox' in ctx.sr._CONFIG_EDITABLE_KEYS
    ctx.state.CONFIG['codex_unattended_sandbox'] = False
    assert ctx.client.get('/api/config').get_json()['codex_unattended_sandbox'] is False


def test_unattended_agent_cannot_turn_the_sandbox_off_even_with_the_passcode(ctx):
    _running(ctx, 'sched-1', 'schedule')
    r = _put(ctx, False)                              # no Origin header: an agent's curl
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']
    assert ctx.state.CONFIG['codex_unattended_sandbox'] is True
    assert not ctx.config_path.exists()               # nothing persisted


def test_unattended_agent_cannot_turn_the_sandbox_on_either(ctx):
    ctx.state.CONFIG['codex_unattended_sandbox'] = False
    _running(ctx, 'wf-1', 'workflow')
    r = _put(ctx, True)
    assert r.status_code == 403
    assert ctx.state.CONFIG['codex_unattended_sandbox'] is False


@pytest.mark.parametrize('passcode', [None, '', 'nope-wrong-code'])
def test_human_without_the_right_passcode_changes_nothing(ctx, passcode):
    r = _put(ctx, False, headers=HUMAN, passcode=passcode)
    assert r.status_code in (400, 401, 403, 429)
    assert ctx.state.CONFIG['codex_unattended_sandbox'] is True
    assert not ctx.config_path.exists()


def test_human_with_the_right_passcode_flips_and_persists_it(ctx):
    r = _put(ctx, False, headers=HUMAN)
    assert r.status_code == 200 and r.get_json()['updated'] == ['codex_unattended_sandbox']
    assert ctx.state.CONFIG['codex_unattended_sandbox'] is False
    assert json.loads(ctx.config_path.read_text())['codex_unattended_sandbox'] is False


def test_manual_chat_session_is_still_a_human_caller(ctx):
    _running(ctx, 'chat-1', 'manual')                 # an interactive chat is not unattended
    r = _put(ctx, False, headers=HUMAN)
    assert r.status_code == 200
    assert ctx.state.CONFIG['codex_unattended_sandbox'] is False
