"""`POST /api/setup/complete` — backlog c9c82caf.

First run re-showed on every login because `setup_completed` could only be
written through the passcode-gated `PUT /api/config` (MC-995). On a fresh
install (no passcode yet) "Not now" raised a "set a passcode" form; closing it
dropped the write, `setup_completed` stayed false, and every new browser or
origin ran setup again. The narrow endpoint records ONLY that flag, with no
passcode, because the flag grants no capability: it hides a wizard.

What must keep holding:
  * it writes `setup_completed: true` to state.CONFIG and to config.json, and
    leaves every other key in config.json alone;
  * it is idempotent;
  * an unattended agent session is refused (same gate as PUT /api/config);
  * `PUT /api/config` is STILL passcode-gated for setup_completed (the MC-995
    gate is not weakened by this route existing).

Same fixture shape as tests/test_settings_routes_unattended_gate.py.
"""
from __future__ import annotations

import json
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

    # No passcode configured: the fresh-install case this route exists for.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    la._LOCAL_AUTH_FAILS.clear()

    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps({'setup_completed': False, 'agent_name': 'Kept'}), encoding='utf-8')
    monkeypatch.setattr(sr, 'SETTINGS_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(sr, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(sr, 'PROJECTS_BASE', tmp_path)

    cfg_snapshot = dict(state.CONFIG)
    sess_snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()
    state.CONFIG['setup_completed'] = False

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.state = state
    c.config_path = config_path
    yield c

    state.CONFIG.clear()
    state.CONFIG.update(cfg_snapshot)
    state.agent_sessions.clear()
    state.agent_sessions.update(sess_snapshot)


def test_marks_setup_completed_without_a_passcode(ctx):
    res = ctx.client.post('/api/setup/complete', headers={'Origin': 'http://localhost:5199'})
    assert res.status_code == 200
    assert res.get_json() == {'ok': True, 'setup_completed': True}
    assert ctx.state.CONFIG['setup_completed'] is True
    on_disk = json.loads(ctx.config_path.read_text(encoding='utf-8'))
    assert on_disk['setup_completed'] is True
    assert on_disk['agent_name'] == 'Kept'  # read-merge-write: other keys untouched


def test_idempotent(ctx):
    for _ in range(2):
        assert ctx.client.post('/api/setup/complete').status_code == 200
    assert ctx.state.CONFIG['setup_completed'] is True


def test_unattended_session_is_refused(ctx):
    ctx.state.agent_sessions['scheduled-1'] = {
        'status': 'running', 'trigger_type': 'schedule', 'project_id': 'p'}
    res = ctx.client.post('/api/setup/complete')
    assert res.status_code == 403
    assert ctx.state.CONFIG['setup_completed'] is False
    assert json.loads(ctx.config_path.read_text(encoding='utf-8'))['setup_completed'] is False


def test_put_config_still_requires_the_passcode(ctx):
    # The MC-995 gate on PUT /api/config must not have been loosened: with no
    # passcode configured, writing setup_completed through it is still refused.
    res = ctx.client.put('/api/config', json={'setup_completed': True},
                         headers={'Origin': 'http://localhost:5199'})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'passcode_required'
    assert ctx.state.CONFIG['setup_completed'] is False
