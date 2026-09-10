"""`PUT /api/config` must refuse an unattended caller — F5 of
docs/_review/2026-09-10_security.md: every key in `_CONFIG_EDITABLE_KEYS`
(`agent_permission_mode`, `exploration_readback_enabled`, `scheduler_paused`,
...) is an operator knob the Settings UI exposes, enforced only by "nothing
but the SPA calls this route" — the same unauthenticated localhost surface
every dispatched agent shares.

Same fixture shape as tests/test_settings_routes.py (registers on the real
`server.app`, snapshots + restores state.CONFIG / agent_sessions) kept in its
own file so this file's fixture doesn't grow another concern.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state
    from mc.blueprints import local_auth as la
    from mc.blueprints import settings_routes as sr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    settings_path = tmp_path / 'settings.json'
    config_path = tmp_path / 'config.json'
    projects_base = tmp_path / 'projects_base'
    projects_base.mkdir()
    monkeypatch.setattr(sr, 'SETTINGS_PATH', settings_path)
    monkeypatch.setattr(sr, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(sr, 'PROJECTS_BASE', projects_base)

    cfg_snapshot = dict(state.CONFIG)
    sess_snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.state = state
    yield c

    state.CONFIG.clear()
    state.CONFIG.update(cfg_snapshot)
    state.agent_sessions.clear()
    state.agent_sessions.update(sess_snapshot)


def test_unattended_session_cannot_change_settings(ctx):
    ctx.state.agent_sessions['scheduled-1'] = {
        'status': 'running', 'trigger_type': 'schedule', 'project_id': 'p'}
    before = ctx.state.CONFIG.get('agent_permission_mode')
    res = ctx.client.put('/api/config', json={'agent_permission_mode': 'bypassPermissions'})
    assert res.status_code == 403
    assert ctx.state.CONFIG.get('agent_permission_mode') == before


def test_unidentifiable_running_session_fails_closed(ctx):
    ctx.state.agent_sessions['unidentifiable-1'] = {
        'status': 'running', 'trigger_type': '', 'project_id': None}
    before = ctx.state.CONFIG.get('scheduler_paused')
    res = ctx.client.put('/api/config', json={'scheduler_paused': True})
    assert res.status_code == 403
    assert ctx.state.CONFIG.get('scheduler_paused') == before


def test_manual_session_and_no_session_still_succeed(ctx):
    res = ctx.client.put('/api/config', json={'agent_name': 'Vector'})
    assert res.status_code == 200
    assert ctx.state.CONFIG['agent_name'] == 'Vector'

    ctx.state.agent_sessions['manual-1'] = {
        'status': 'running', 'trigger_type': 'manual', 'project_id': 'p'}
    res = ctx.client.put('/api/config', json={'log_level': 'warn'})
    assert res.status_code == 200
    assert ctx.state.CONFIG['log_level'] == 'warn'
