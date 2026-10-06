"""The MCP panel's write routes need a human (backlog 34aac480, MC-1053).

POST /api/mcp, PUT /api/mcp/<scope>/<name> and POST /api/mcp/url/install add or change what an agent
runs: an unattended session gets 403, a missing or wrong passcode gets 403 and writes nothing, the
right passcode writes. DELETE and PUT /api/project/<id>/mcp-enabled only remove or re-select: an
unattended session gets 403, no passcode is asked of a human. Reuses the Desk Connect fixture
(`tests.test_desk_connect_custom.env`): a real passcode, a temp ~/.claude.json and project folder.
"""
from __future__ import annotations

import json
import sys

import pytest

sys.path.insert(0, '.')
from tests.test_desk_connect_custom import PASSCODE, PID, env, servers  # noqa: E402,F401

CFG = {'command': 'node', 'args': ['x.js']}


def _agent():
    from mc.state import agent_sessions
    agent_sessions['sched-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}


def _post(env, passcode=PASSCODE, **over):
    body = {'name': 'plain', 'transport': 'stdio', 'scope': 'global', 'config': CFG}
    if passcode is not None:
        body['passcode'] = passcode
    body.update(over)
    return env.client.post('/api/mcp', json=body)


def _put(env, passcode=PASSCODE, name='plain'):
    body = {'transport': 'stdio', 'config': {'command': 'node', 'args': ['y.js']}}
    if passcode is not None:
        body['passcode'] = passcode
    return env.client.put(f'/api/mcp/global/{name}', json=body)


def _install(env, passcode=PASSCODE):
    body = {'name': 'inst', 'scope': 'global', 'config': CFG}
    if passcode is not None:
        body['passcode'] = passcode
    return env.client.post('/api/mcp/url/install', json=body)


def _global(env):
    return servers(env.glob_cfg) if env.glob_cfg.exists() else {}


# ── POST ─────────────────────────────────────────────────────────────────────

def test_post_by_an_unattended_agent_is_403_and_writes_nothing(env):
    _agent()
    r = _post(env)                                   # even with the right passcode in hand
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']
    assert _global(env) == {}


@pytest.mark.parametrize('passcode', [None, '', 'wrong', 123])
def test_post_without_the_right_passcode_is_403_and_writes_nothing(env, passcode):
    r = _post(env, passcode)
    assert r.status_code == 403
    assert _global(env) == {}


def test_post_with_the_right_passcode_writes(env):
    r = _post(env)
    assert r.status_code == 201 and 'plain' in _global(env)


def test_post_project_scope_with_the_right_passcode_writes_the_project_file(env):
    assert _post(env, scope='project', project_id=PID).status_code == 201
    assert 'plain' in servers(env.proj_cfg)


def test_post_with_a_bad_scope_is_400_before_the_passcode_is_spent(env):
    r = _post(env, passcode='wrong', scope='nowhere')
    assert r.status_code == 400


# ── PUT ──────────────────────────────────────────────────────────────────────

def test_put_by_an_unattended_agent_is_403_and_changes_nothing(env):
    assert _post(env).status_code == 201
    before = _global(env)
    _agent()
    assert _put(env).status_code == 403
    assert _global(env) == before


@pytest.mark.parametrize('passcode', [None, 'wrong'])
def test_put_without_the_right_passcode_is_403_and_changes_nothing(env, passcode):
    assert _post(env).status_code == 201
    before = _global(env)
    assert _put(env, passcode).status_code == 403
    assert _global(env) == before


def test_put_with_the_right_passcode_changes_the_server(env):
    assert _post(env).status_code == 201
    assert _put(env).status_code == 200
    assert _global(env)['plain']['args'] == ['y.js']


# ── url/install ──────────────────────────────────────────────────────────────

def test_install_by_an_unattended_agent_is_403_and_writes_nothing(env):
    _agent()
    r = _install(env)
    assert r.status_code == 403 and r.mimetype == 'application/json'
    assert _global(env) == {}


@pytest.mark.parametrize('passcode', [None, 'wrong'])
def test_install_without_the_right_passcode_is_a_403_before_the_stream_starts(env, passcode):
    r = _install(env, passcode)
    assert r.status_code == 403 and r.mimetype == 'application/json'
    assert _global(env) == {}


def test_install_with_the_right_passcode_streams_and_writes(env):
    r = _install(env)
    assert r.status_code == 200 and r.mimetype == 'text/event-stream'
    events = [json.loads(l[6:]) for l in r.get_data(as_text=True).split('\n') if l.startswith('data: ')]
    assert events[-1]['type'] == 'done' and 'inst' in _global(env)


# ── DELETE and the loadout PUT: unattended refusal only ──────────────────────

def test_delete_by_an_unattended_agent_is_403_and_keeps_the_server(env):
    assert _post(env).status_code == 201
    _agent()
    assert env.client.delete('/api/mcp/global/plain').status_code == 403
    assert 'plain' in _global(env)


def test_delete_by_a_human_needs_no_passcode(env):
    assert _post(env).status_code == 201
    assert env.client.delete('/api/mcp/global/plain').status_code == 200
    assert 'plain' not in _global(env)


def test_the_loadout_put_by_an_unattended_agent_is_403(env):
    _agent()
    r = env.client.put(f'/api/project/{PID}/mcp-enabled', json={'enabled': []})
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']


def test_a_manual_chat_session_is_not_unattended_but_still_needs_the_passcode(env):
    from mc.state import agent_sessions
    agent_sessions['chat-1'] = {'status': 'running', 'trigger_type': 'manual', 'project_id': 'p'}
    assert _post(env, None).status_code == 403
    assert _post(env).status_code == 201


def test_every_write_route_of_the_panel_is_gated(env):
    """A new write route in mcp_routes that skips the gate fails here by name."""
    from mc.blueprints import mcp_routes
    import inspect
    writers = {'create_mcp_route', 'update_mcp_route', 'delete_mcp_route',
               'set_project_mcp_enabled', 'mcp_url_install'}
    for fn_name in writers:
        src = inspect.getsource(getattr(mcp_routes, fn_name))
        assert 'refuse_unattended(' in src, fn_name
    for fn_name in ('create_mcp_route', 'update_mcp_route', 'mcp_url_install'):
        assert 'require_passcode(' in inspect.getsource(getattr(mcp_routes, fn_name)), fn_name
