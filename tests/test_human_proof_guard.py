"""Per-route acceptance matrix for the human-proof guard (MC-995,
docs/HUMAN_PROOF_GUARD_SPEC.md). Ron's decision, 2026-09-28: every route
in the spec's sections 1a/1b gets the same pattern already shipped for the
vault lock (secrets_routes.py `_require_human_passcode`) — the LOCAL
DASHBOARD PASSCODE re-typed in the request body and verified server-side,
on EVERY call, no unlock window.

Every functional test file that exercises one of these routes for its own
reasons (test_workflows.py, test_character_routes.py, test_settings_routes.py,
test_system_update_stash.py, test_distiller_promote_unattended_gate.py)
bypasses this gate with `monkeypatch.setattr(<mod>, '_require_human_passcode',
lambda data: None)` — see each file's own comment. THIS file is where the
real gate is exercised, once per route, against `_require_human_passcode`
itself: (a) a forged-Origin request with no passcode field -> 403, (b) a
wrong passcode -> 403, (c) the correct passcode -> past the guard.

"Past the guard" for routes with deep, hard-to-fake business logic (the
guide brainstorm transfer, the three backup mutations, distiller promote,
the system-update stash branch) means the underlying store/backup/distiller
call is monkeypatched to a trivial success stub -- the same isolation
`test_workflows.py`'s own fixture already applies to `_dispatch_agent_internal`
-- so a real 200 proves the request cleared `_require_human_passcode`
without this file also having to reconstruct that subsystem's own test
harness. Routes simple enough for a genuine end-to-end 200 (character CRUD,
config PUT) get one.

Every request below carries a forged `Origin` header (`UI_HEADERS`) --
the pre-MC-995 posture that made `_is_agent_caller()` /
`is_unattended_caller()` alone insufficient. Without it, the OLD Origin
check would refuse first and this file would never reach the passcode
layer it exists to test.

Ground truth for the route inventory below is the live code (every call
site of `_require_human_passcode`, grepped across the blueprints listed in
docs/HUMAN_PROOF_GUARD_SPEC.md section 1), not the spec's own summary
count -- the spec is prose ("SPEC ONLY. Nothing in this document changes
... any route"), written before this build, and its "18 + 9 = 27" arithmetic
does not reconcile line-for-line against the shipped guard call sites (26,
by direct count: 6 workflow + 9 character + 1 guide + 1 system-update-stash
+ 4 secrets-CRUD + 1 config + 1 distiller + 3 backup). The vault-lock
routes (secrets_routes.py ~442-518) predate this task (section 1c, "already
fixed") and already carry their own full matrix in test_secrets_routes.py
-- not repeated here. The dead-code landmine (`attend_session`,
agent_routes.py ~6215) is covered by its own dedicated assertion below: it
must 410 regardless of passcode, proving the `if True` guard, not the
passcode helper underneath it, is what makes it unreachable.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UI_HEADERS = {'Origin': 'http://localhost:5199'}  # forged: a real browser fetch always carries one
PASSCODE = 'unlock1234'
WRONG = 'nope-wrong-code'


def _set_passcode(monkeypatch, passcode=PASSCODE):
    from mc.blueprints import local_auth
    local_auth._local_auth_set_passcode(passcode)
    local_auth._LOCAL_AUTH_FAILS.clear()
    return passcode


@pytest.fixture(autouse=True)
def _isolated_local_auth(tmp_path, monkeypatch):
    """One shared LOCAL_AUTH_PATH for every test below. `local_auth` is a
    singleton module -- every blueprint's `_require_human_passcode` call
    goes through `mc.blueprints.local_auth`, so patching it here reaches
    all of them regardless of which blueprint registers the route."""
    from mc.blueprints import local_auth
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()


def _assert_refused(resp, expected_error):
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert resp.get_json()['error'] == expected_error


# ── shared cross-route assertions ───────────────────────────────────────────

def test_no_passcode_configured_at_all_fails_closed_on_an_arbitrary_gated_route(monkeypatch):
    """Fresh install, no passcode ever set: every gated route must refuse
    outright (spec: 'nothing else on this local, unauthenticated API
    surface proves a human... is the one asking'). Picks PUT /api/config as
    the representative -- sharpest single route in the inventory -- rather
    than re-running this once per route; the check is inside
    `_require_human_passcode` itself, shared by all 26."""
    import server
    from mc import state
    monkeypatch.setattr(state, 'CONFIG', {'scheduler_paused': False})
    server.app.config['TESTING'] = True
    client = server.app.test_client()
    resp = client.put('/api/config', json={'scheduler_paused': True}, headers=UI_HEADERS)
    _assert_refused(resp, 'passcode_required')


def test_dead_attend_route_410s_regardless_of_passcode(monkeypatch):
    """agent_routes.py ~6215 `attend_session`: hard-refused by `if True`
    before the forgeable-then-passcode-gated check underneath ever runs
    (MC-995 landmine fix). Proves the 410 comes from the dead-code guard,
    not from `_require_human_passcode` -- sending the CORRECT passcode must
    not matter."""
    import server
    _set_passcode(monkeypatch)
    server.app.config['TESTING'] = True
    client = server.app.test_client()
    resp = client.post('/api/project/p1/agent/s1/attend',
                       json={'passcode': PASSCODE}, headers=UI_HEADERS)
    assert resp.status_code == 410, resp.get_data(as_text=True)


# ── workflow routes (6): mc/blueprints/workflow_routes.py, shared
#    `_refuse_if_agent_caller()` choke point ─────────────────────────────────

@pytest.fixture()
def wf_client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import workflows as m
    from mc.blueprints import workflow_routes as wr

    monkeypatch.setattr(m, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(m, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    # Isolate the guard from workflow-store/runner semantics (own coverage:
    # test_workflows.py) -- stub each mutating call to a trivial success.
    monkeypatch.setattr(wr._wf, 'create_workflow', lambda doc: {'id': 'w1', **doc})
    monkeypatch.setattr(wr._wf, 'update_workflow', lambda wid, doc: {'id': wid, **doc})
    monkeypatch.setattr(wr._wf, 'delete_workflow', lambda wid: True)
    monkeypatch.setattr(wr._wf, 'draft_workflow', lambda desc, pid: {'ok': True, 'definition': {}})
    monkeypatch.setattr(wr._wf, 'resolve_decision', lambda rid, choice: {'id': rid, 'status': 'done'})
    monkeypatch.setattr(wr._wf, 'cancel_run', lambda rid: {'id': rid, 'status': 'cancelled', 'left_running': []})
    server.app.config['TESTING'] = True
    return server.app.test_client()


_WORKFLOW_DOC = {'name': 'x', 'nodes': [], 'edges': [], 'trigger': {'type': 'manual'}}

WORKFLOW_ROUTES = [
    ('post', '/api/workflows', _WORKFLOW_DOC),
    ('put', '/api/workflows/w1', _WORKFLOW_DOC),
    ('delete', '/api/workflows/w1', {}),
    ('post', '/api/workflows/draft', {'description': 'do x', 'project_id': 'p1'}),
    ('post', '/api/workflow-runs/r1/decision', {'choice': 'approve'}),
    ('post', '/api/workflow-runs/r1/cancel', {}),
]


@pytest.mark.parametrize('method,path,body', WORKFLOW_ROUTES)
def test_workflow_route_no_passcode_refused(wf_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(wf_client, method)(path, json=dict(body), headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', WORKFLOW_ROUTES)
def test_workflow_route_wrong_passcode_refused(wf_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(wf_client, method)(path, json={**body, 'passcode': WRONG}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', WORKFLOW_ROUTES)
def test_workflow_route_correct_passcode_succeeds(wf_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(wf_client, method)(path, json={**body, 'passcode': PASSCODE}, headers=UI_HEADERS)
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ── character routes (9): mc/blueprints/character_routes.py, shared
#    `_refuse_if_agent_caller()` choke point ─────────────────────────────────

@pytest.fixture(autouse=True)
def _character_model_double(monkeypatch):
    """generate_voice_route is the one character route that reaches the
    model (name/avatar/identity all take an explicit value or fall back
    deterministically). Route the seam to a canned double so its success
    case doesn't need a live CLI -- same technique as
    test_character_routes.py's own autouse fixture."""
    from mc.blueprints import character_routes as cr
    monkeypatch.setattr(cr, '_scribe_call', lambda model, prompt, stdin: '## Voice\n\n- **Terse.** Says "done" and stops.', raising=False)

    def transform(provider, *, prompt, model='', stdin_text=None, **kw):
        fn = getattr(cr, '_scribe_call', None)
        return fn(model, prompt, stdin_text or '') if fn else ''
    monkeypatch.setattr(cr._agent_runtime, 'run_text_transform', transform)


@pytest.fixture()
def char_client(tmp_path, monkeypatch):
    import server
    from mc import characters as ch
    from mc.blueprints import character_routes as cr
    from mc.blueprints import skills_routes as sr

    global_dir = tmp_path / 'agents-global'
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    proj = {'id': 'tchar', 'name': 'Char Test', 'project_path': str(proj_path)}

    def _load(pid):
        return {'tchar': proj}.get(pid)
    monkeypatch.setattr(cr, 'load_project', _load)
    monkeypatch.setattr(cr, 'load_projects', lambda: [proj])
    monkeypatch.setattr(sr, 'load_project', _load)

    # Precondition most of the 9 routes need: an existing global character.
    ch.write_character('global', 'guardtest', 'a test persona', 'be terse.',
                       project_path=None)

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    return c


CHARACTER_ROUTES = [
    ('post', '/api/characters',
     {'name': 'newguard', 'description': 'd', 'body': 'b', 'scope': 'global'}),
    ('post', '/api/characters/team',
     {'members': [{'mode': 'new', 'name': 'teamguard', 'description': 'd', 'body': 'b', 'scope': 'global'}]}),
    ('post', '/api/characters/voice', {'description': 'a role', 'body': 'be terse'}),
    ('post', '/api/characters/identity', {'description': 'a role', 'body': 'be terse'}),
    ('put', '/api/characters/global/guardtest', {'description': 'updated', 'scope': 'global'}),
    ('post', '/api/characters/global/guardtest/name', {'agent_name': 'Newname'}),
    ('post', '/api/characters/global/guardtest/avatar', {'avatar': '\U0001F9E9'}),
    ('post', '/api/characters/global/guardtest/move', {'to_scope': 'project', 'to_project_id': 'tchar'}),
    ('delete', '/api/characters/global/guardtest', {}),
]


@pytest.mark.parametrize('method,path,body', CHARACTER_ROUTES)
def test_character_route_no_passcode_refused(char_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(char_client, method)(path, json=dict(body), headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', CHARACTER_ROUTES)
def test_character_route_wrong_passcode_refused(char_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(char_client, method)(path, json={**body, 'passcode': WRONG}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', CHARACTER_ROUTES)
def test_character_route_correct_passcode_succeeds(char_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(char_client, method)(path, json={**body, 'passcode': PASSCODE}, headers=UI_HEADERS)
    # create/team answer 201 (a resource was made); everything else 200 --
    # either way, past the guard and into a real write.
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)


# ── guide brainstorm transfer (1): mc/blueprints/guide_routes.py ───────────

@pytest.fixture()
def guide_client(tmp_path, monkeypatch):
    import server
    from mc.blueprints import guide_routes as gr
    from mc.blueprints import agent_routes as ar

    proj_path = tmp_path / 'guideproj'
    proj_path.mkdir()
    proj = {'id': 'gproj', 'name': 'Guide Test', 'project_path': str(proj_path)}
    monkeypatch.setattr(gr, 'load_project', lambda pid: {'gproj': proj}.get(pid))

    # Guard fires before any transcript/backlog work -- stub
    # resolve_transcript_messages so wrong/no-passcode tests never reach it,
    # and so the correct-passcode case can assert "past the guard" without
    # this file reconstructing the transcript-brief-parsing harness.
    monkeypatch.setattr(ar, 'resolve_transcript_messages',
                        lambda *a, **kw: ({'error': 'no transcript in this stub'}, 404))

    server.app.config['TESTING'] = True
    return server.app.test_client()


GUIDE_BODY = {'claude_session_id': 'sess-1', 'version': 1,
              'destination': {'mode': 'existing', 'project_id': 'gproj'}}


def test_guide_transfer_no_passcode_refused(guide_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = guide_client.post('/api/project/gproj/brainstorm/transfer',
                             json=dict(GUIDE_BODY), headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_guide_transfer_wrong_passcode_refused(guide_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = guide_client.post('/api/project/gproj/brainstorm/transfer',
                             json={**GUIDE_BODY, 'passcode': WRONG}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_guide_transfer_correct_passcode_reaches_past_the_guard(guide_client, monkeypatch):
    """Business logic is stubbed to 404 ('no transcript in this stub') --
    the assertion is that the correct passcode reaches THAT 404, not the
    guard's 403. A stubbed 404 proves `_require_human_passcode` let the
    request through; asserting a full 200 here would mean re-building
    guide_routes' own transcript/backlog-write test harness, out of scope
    for a guard-matrix file."""
    _set_passcode(monkeypatch)
    resp = guide_client.post('/api/project/gproj/brainstorm/transfer',
                             json={**GUIDE_BODY, 'passcode': PASSCODE}, headers=UI_HEADERS)
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert resp.get_json()['error'] == 'no transcript in this stub'


# ── system update stash branch (1): mc/blueprints/system_routes.py ────────

def _run(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"{args} failed in {cwd}: {r.stdout}{r.stderr}"
    return r.stdout.strip()


@pytest.fixture()
def sys_client(tmp_path, monkeypatch):
    import server
    from mc.blueprints import system_routes as sr

    upstream = tmp_path / 'upstream.git'
    work = tmp_path / 'work'
    checkout = tmp_path / 'Clayrune'
    _run(['git', 'init', '--bare', '-b', 'master', str(upstream)], tmp_path)
    _run(['git', 'init', '-b', 'master', str(work)], tmp_path)
    _run(['git', 'config', 'user.email', 'test@example.com'], work)
    _run(['git', 'config', 'user.name', 'Test'], work)
    (work / 'server.py').write_text('v1\n')
    _run(['git', 'add', 'server.py'], work)
    _run(['git', 'commit', '-m', 'v1'], work)
    _run(['git', 'remote', 'add', 'origin', str(upstream)], work)
    _run(['git', 'push', 'origin', 'master'], work)
    _run(['git', 'clone', str(upstream), str(checkout)], tmp_path)
    (checkout / 'server.py').write_text('local edit\n')  # dirty tracked file

    monkeypatch.setattr(sr, '_APP_DIR', checkout)
    server.app.config['TESTING'] = True
    return server.app.test_client()


def test_system_update_stash_no_passcode_refused(sys_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = sys_client.post('/api/system/update', json={'stash': True}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_system_update_stash_wrong_passcode_refused(sys_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = sys_client.post('/api/system/update', json={'stash': True, 'passcode': WRONG}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_system_update_stash_correct_passcode_succeeds(sys_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = sys_client.post('/api/system/update', json={'stash': True, 'passcode': PASSCODE}, headers=UI_HEADERS)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['ok'] is True


# ── secrets CRUD (4): mc/blueprints/secrets_routes.py — reuses the same
#    minimal-app fixture shape as tests/test_secrets_routes.py ─────────────

@pytest.fixture()
def secrets_client(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    from mc.blueprints import secrets_routes
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    app = Flask(__name__)
    app.register_blueprint(secrets_routes.bp)
    return app.test_client()


def _totp_migration_uri():
    from tests.test_totp import _make_migration_uri
    return _make_migration_uri([(b'12345678901234567890', 'ron', 'GitHub', 2)])


SECRETS_ROUTES = [
    ('post', '/api/secrets', {'name': 'guard.one', 'value': 'v'}),
    ('patch', '/api/secrets/guard.one', {'description': 'updated'}),
    ('delete', '/api/secrets/guard.one', {}),
    ('post', '/api/secrets/import-authenticator',
     {'uri': _totp_migration_uri(), 'commit': True,
      'names': {'github.ron.totp': 'guard.totp'}}),
]


def _seed_secret(client, monkeypatch):
    """PATCH/DELETE need an existing secret; create one with a valid
    passcode first (outside the loop under test)."""
    _set_passcode(monkeypatch)
    client.post('/api/secrets', json={'name': 'guard.one', 'value': 'v', 'passcode': PASSCODE})


@pytest.mark.parametrize('method,path,body', SECRETS_ROUTES)
def test_secrets_crud_route_no_passcode_refused(secrets_client, monkeypatch, method, path, body):
    _seed_secret(secrets_client, monkeypatch)
    resp = getattr(secrets_client, method)(path, json=dict(body))
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', SECRETS_ROUTES)
def test_secrets_crud_route_wrong_passcode_refused(secrets_client, monkeypatch, method, path, body):
    _seed_secret(secrets_client, monkeypatch)
    resp = getattr(secrets_client, method)(path, json={**body, 'passcode': WRONG})
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', SECRETS_ROUTES)
def test_secrets_crud_route_correct_passcode_succeeds(secrets_client, monkeypatch, method, path, body):
    _seed_secret(secrets_client, monkeypatch)
    resp = getattr(secrets_client, method)(path, json={**body, 'passcode': PASSCODE})
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ── config PUT (1): mc/blueprints/settings_routes.py ────────────────────────

@pytest.fixture()
def config_client(monkeypatch):
    import server
    from mc import state
    monkeypatch.setattr(state, 'CONFIG', {'scheduler_paused': False})
    server.app.config['TESTING'] = True
    return server.app.test_client()


def test_config_put_no_passcode_refused(config_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = config_client.put('/api/config', json={'scheduler_paused': True}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_config_put_wrong_passcode_refused(config_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = config_client.put('/api/config', json={'scheduler_paused': True, 'passcode': WRONG}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_config_put_correct_passcode_succeeds(config_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = config_client.put('/api/config', json={'scheduler_paused': True, 'passcode': PASSCODE}, headers=UI_HEADERS)
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ── distiller promote (1): mc/blueprints/distiller_routes.py ──────────────

def _make_artifact(skills_root: Path, slug: str):
    d = skills_root / '_proposed' / 'global' / f"2026-06-05T00-00-00-aaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'SKILL.md').write_text(
        "---\nkind: skill\nname: " + slug + "\n"
        "extraction_fingerprint_exact: deadbeefdeadbeef\n"
        "extraction_scope: cross-project\ncreated_at: 2026-06-05T00:00:00Z\n---\n\n"
        "# A real title\n\nSome real reusable procedure body content here.\n",
        encoding='utf-8')
    return d


@pytest.fixture()
def distiller_client(tmp_path, monkeypatch):
    from mc import distiller
    distiller._skills_root = tmp_path / 'skills'
    distiller._data_root = tmp_path / 'projects'
    (tmp_path / 'projects').mkdir(parents=True, exist_ok=True)
    distiller._atomic_write_text = lambda p, t: Path(p).write_text(t, encoding='utf-8')
    distiller._now_iso = lambda: '2026-06-05T00:00:00Z'

    import mc.skills as _skills
    monkeypatch.setattr(_skills, 'GLOBAL_SKILLS_DIR', tmp_path / 'global_skills')

    from mc.blueprints import distiller_routes
    from mc.state import agent_sessions
    agent_sessions.clear()

    app = Flask(__name__)
    app.register_blueprint(distiller_routes.bp)
    c = app.test_client()
    c.skills_root = distiller._skills_root  # type: ignore[attr-defined]
    return c


def _distiller_body(client):
    d = _make_artifact(client.skills_root, 'guardslug')
    return {'directory': str(d), 'scope': 'global'}


def test_distiller_promote_no_passcode_refused(distiller_client, monkeypatch):
    _set_passcode(monkeypatch)
    resp = distiller_client.post('/api/distiller/promote', json=_distiller_body(distiller_client), headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_distiller_promote_wrong_passcode_refused(distiller_client, monkeypatch):
    _set_passcode(monkeypatch)
    body = {**_distiller_body(distiller_client), 'passcode': WRONG}
    resp = distiller_client.post('/api/distiller/promote', json=body, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


def test_distiller_promote_correct_passcode_succeeds(distiller_client, monkeypatch):
    _set_passcode(monkeypatch)
    body = {**_distiller_body(distiller_client), 'passcode': PASSCODE}
    resp = distiller_client.post('/api/distiller/promote', json=body, headers=UI_HEADERS)
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ── backup restore/import/rollback (3): mc/blueprints/backup_routes.py ────

@pytest.fixture()
def backup_client(monkeypatch):
    from mc import backup as _backup
    from mc.blueprints import backup_routes
    monkeypatch.setattr(_backup, 'restore_backup', lambda path, categories=None: {'restored_categories': ['x']})
    monkeypatch.setattr(_backup, 'import_project', lambda path, **kw: {'status': 'ok'})
    monkeypatch.setattr(_backup, 'rollback', lambda pid, sid, **kw: {'restored': True})
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    return app.test_client()


BACKUP_ROUTES = [
    ('post', '/api/backup/restore', {'path': '/tmp/fake-backup.zip'}),
    ('post', '/api/backup/import', {'path': '/tmp/fake-import.zip', 'apply': True}),
    ('post', '/api/backup/rollback/p1/snap1', {}),
]


@pytest.mark.parametrize('method,path,body', BACKUP_ROUTES)
def test_backup_route_no_passcode_refused(backup_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(backup_client, method)(path, json=dict(body), headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', BACKUP_ROUTES)
def test_backup_route_wrong_passcode_refused(backup_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(backup_client, method)(path, json={**body, 'passcode': WRONG}, headers=UI_HEADERS)
    _assert_refused(resp, 'bad_passcode')


@pytest.mark.parametrize('method,path,body', BACKUP_ROUTES)
def test_backup_route_correct_passcode_succeeds(backup_client, monkeypatch, method, path, body):
    _set_passcode(monkeypatch)
    resp = getattr(backup_client, method)(path, json={**body, 'passcode': PASSCODE}, headers=UI_HEADERS)
    assert resp.status_code == 200, resp.get_data(as_text=True)
