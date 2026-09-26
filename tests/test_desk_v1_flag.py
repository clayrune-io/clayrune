"""Desk v1 (MC-977, R0 plan T0a) — the `desk_v1` flag + `user_timezone`.

T0a's whole job is a flag that is OFF by default so the legacy Desk
(`static/js/desk.js`) is byte-identical in behaviour until someone opts in
(`docs/desk_v1_r0_plan.md` "Done when"). This pins the three things that make
that true at the config layer (the JS-side behaviour — `openDesk()` branching,
every v1 route rendering, Back — is covered by
`tools/smoke/desk-v1-harness.mjs`, which actually boots the page):

1. `server.py`'s `_load_config()` defaults `desk_v1` to `False` and
   `user_timezone` to `''` when no config.json exists yet.
2. Both keys are on the Settings panel's editable surface
   (`mc/blueprints/settings_routes.py::_CONFIG_EDITABLE_KEYS`) — otherwise the
   toggle added to `static/js/settings-drill.js` would PUT a key the server
   silently drops.
3. `GET`/`PUT /api/config` round-trip both keys like any other editable
   setting, persisting to config.json.

Reuses the `ctx` fixture shape from `tests/test_settings_routes.py`
(module-only patches — CONFIG_PATH/SETTINGS_PATH/PROJECTS_BASE point at tmp,
`state.CONFIG` + `state.agent_sessions` snapshotted and restored — the
Phase-0 test-port rule: patch `mc.blueprints.settings_routes.*`, never
`server.*`).
"""
import ast
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SERVER = PROJECT_ROOT / 'server.py'


def _server_config_defaults():
    """The literal `defaults` dict from server.py's `_load_config`, parsed
    with `ast` rather than imported — same technique as
    `test_memory_doc_config.py::_server_defaults` — so a test about the
    DEFAULTS dict can't be fooled by a config.json already on disk merging
    something else in ahead of it."""
    tree = ast.parse(SERVER.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_load_config':
            for stmt in ast.walk(node):
                if (isinstance(stmt, ast.Assign)
                        and any(getattr(t, 'id', '') == 'defaults' for t in stmt.targets)
                        and isinstance(stmt.value, ast.Dict)):
                    out = {}
                    for k, v in zip(stmt.value.keys, stmt.value.values):
                        if not isinstance(k, ast.Constant):
                            continue
                        try:
                            out[k.value] = ast.literal_eval(v)
                        except Exception:
                            pass
                    return out
    raise AssertionError('could not find _load_config defaults dict in server.py')


def test_load_config_defaults_desk_v1_off_and_timezone_blank():
    defaults = _server_config_defaults()
    assert defaults['desk_v1'] is False
    assert defaults['user_timezone'] == ''


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

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.sr = sr
    c.state = state
    c.config_path = config_path
    yield c

    state.CONFIG.clear()
    state.CONFIG.update(cfg_snapshot)
    state.agent_sessions.clear()
    state.agent_sessions.update(sess_snapshot)


def test_desk_v1_and_timezone_are_editable_keys(ctx):
    assert 'desk_v1' in ctx.sr._CONFIG_EDITABLE_KEYS
    assert 'user_timezone' in ctx.sr._CONFIG_EDITABLE_KEYS


def test_get_config_reports_desk_v1_off_by_default(ctx):
    # Deterministic regardless of what this box's real config.json holds —
    # ctx.state is the snapshotted/restored live CONFIG, set explicitly here
    # to the documented default before reading the projection back.
    ctx.state.CONFIG['desk_v1'] = False
    ctx.state.CONFIG['user_timezone'] = ''
    body = ctx.client.get('/api/config').get_json()
    assert body['desk_v1'] is False
    assert body['user_timezone'] == ''


def test_put_config_toggles_desk_v1_and_persists(ctx):
    resp = ctx.client.put('/api/config', json={'desk_v1': True})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['updated'] == ['desk_v1']
    assert ctx.state.CONFIG['desk_v1'] is True
    saved = json.loads(ctx.config_path.read_text(encoding='utf-8'))
    assert saved['desk_v1'] is True


def test_put_config_sets_user_timezone_and_persists(ctx):
    resp = ctx.client.put('/api/config', json={'user_timezone': 'America/New_York'})
    assert resp.status_code == 200
    assert ctx.state.CONFIG['user_timezone'] == 'America/New_York'
    saved = json.loads(ctx.config_path.read_text(encoding='utf-8'))
    assert saved['user_timezone'] == 'America/New_York'


# ── wiring the flag actually reaches the two frontend files it must ───────────

def test_settings_drill_has_a_desk_v1_toggle_row():
    src = (PROJECT_ROOT / 'static' / 'js' / 'settings-drill.js').read_text(encoding='utf-8')
    assert "toggle('desk_v1'" in src
    assert "textInput('user_timezone'" in src


def test_open_desk_branches_to_v1_shell_when_flagged():
    """`openDesk()` in the legacy file is T0a's ONE edit to desk.js (ground
    rule 1) — a branch to `window.deskV1Open()` gated on the config flag,
    checked before anything else in the function runs so the legacy modal
    never builds in the same call when v1 is on."""
    src = (PROJECT_ROOT / 'static' / 'js' / 'desk.js').read_text(encoding='utf-8')
    assert 'window.deskV1Open' in src
    assert src.index('desk_v1') < src.index('DESK_MODAL_ID', src.index('async function openDesk'))
