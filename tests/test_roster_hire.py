"""Request-level tests for the drag-to-hire roster endpoints
(mc/blueprints/project_routes.py: POST .../roster/hire, DELETE .../roster/<ref>).

docs/DRAG_TO_HIRE_SPEC.md §3.1/§4/§5. Membership is a field on the EXISTING
project record (`roster`) — these tests assert that field's shape directly,
never a sidecar file, mirroring the DATA_DIR-pollution regression style of
test_load_projects_sidecar_exclusions.py.
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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint on first import)
    from mc import characters as _characters
    from mc import state as mc_state
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    mem_dir = tmp_path / 'memory'
    mem_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'SHARED_RULES_PATH', tmp_path / 'SHARED_RULES.md')
    monkeypatch.setattr(pr, '_get_memory_path', lambda p: mem_dir / f"{p['id']}.md")
    monkeypatch.setattr(pr, '_resolve_claude', lambda: 'claude-stub')
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    monkeypatch.setattr(pr, '_unregister_process', lambda pid: None)
    monkeypatch.setattr(pr, '_kill_terminal_session', lambda pid: None)

    # A real global characters dir with one character on disk — the hire
    # route resolves through mc.characters.read_character for real, not a
    # stub, so a typo in the ref format or the scope/name split shows up here.
    global_agents = tmp_path / 'global_agents'
    global_agents.mkdir()
    (global_agents / 'code-reviewer.md').write_text(
        '---\nname: code-reviewer\ndescription: reviews diffs\n---\nBody.\n',
        encoding='utf-8')
    monkeypatch.setattr(_characters, 'GLOBAL_AGENTS_DIR', global_agents)

    # A project-local character, living under its OWN project's path — used
    # to prove a project: ref from a DIFFERENT project 404s (spec §6 case 4).
    proj_a_path = tmp_path / 'proj_a'
    (proj_a_path / '.claude' / 'agents').mkdir(parents=True)
    (proj_a_path / '.claude' / 'agents' / 'local-writer.md').write_text(
        '---\nname: local-writer\ndescription: writes copy for proj_a only\n---\nBody.\n',
        encoding='utf-8')

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    import server as srv
    srv.app.config['TESTING'] = True
    c = srv.app.test_client()
    c.pr = pr                          # type: ignore[attr-defined]
    c.state = mc_state                 # type: ignore[attr-defined]
    c.data_dir = data_dir              # type: ignore[attr-defined]
    c.proj_a_path = proj_a_path        # type: ignore[attr-defined]
    yield c

    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update(sess_snapshot)


def _seed(client, pid='tproj', **extra):
    rec = {'id': pid, 'name': 'Test Project', 'status': 'active',
           'backlog': [], **extra}
    (client.data_dir / f'{pid}.json').write_text(json.dumps(rec), encoding='utf-8')
    return rec


def _roster_of(client, pid):
    return json.loads((client.data_dir / f'{pid}.json').read_text(encoding='utf-8')).get('roster')


# ── hire ─────────────────────────────────────────────────────────────────────

def test_hire_writes_roster_field_on_existing_record_not_a_sidecar(client):
    _seed(client)
    r = client.post('/api/project/tproj/roster/hire',
                    json={'character': 'global:code-reviewer'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['already_hired'] is False
    assert len(body['roster']) == 1
    entry = body['roster'][0]
    assert entry['character'] == 'global:code-reviewer'
    assert entry['removed_at'] is None
    assert entry['hired_at']
    # No new file appeared in DATA_DIR — this is a field, not a sidecar.
    assert sorted(p.name for p in client.data_dir.glob('*.json')) == ['tproj.json']
    assert _roster_of(client, 'tproj') == body['roster']


def test_hire_unknown_character_404s(client):
    _seed(client)
    r = client.post('/api/project/tproj/roster/hire',
                    json={'character': 'global:nope-does-not-exist'})
    assert r.status_code == 404
    assert not _roster_of(client, 'tproj')


def test_hire_malformed_ref_400s(client):
    _seed(client)
    r = client.post('/api/project/tproj/roster/hire',
                    json={'character': 'not-a-scoped-ref'})
    assert r.status_code == 400


def test_hire_missing_project_404s(client):
    r = client.post('/api/project/ghost/roster/hire',
                    json={'character': 'global:code-reviewer'})
    assert r.status_code == 404


def test_hire_twice_is_idempotent_and_already_hired(client):
    _seed(client)
    client.post('/api/project/tproj/roster/hire',
               json={'character': 'global:code-reviewer'})
    r = client.post('/api/project/tproj/roster/hire',
                    json={'character': 'global:code-reviewer'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['already_hired'] is True
    assert len(body['roster']) == 1   # no duplicate row


def test_hire_project_local_character_into_its_own_project(client):
    _seed(client, pid='proj_a', project_path=str(client.proj_a_path))
    r = client.post('/api/project/proj_a/roster/hire',
                    json={'character': 'project:local-writer'})
    assert r.status_code == 200
    assert r.get_json()['already_hired'] is False


def test_hire_project_local_character_into_a_different_project_404s(client):
    """Spec §6 case 4: a project: ref only resolves inside its OWN project."""
    _seed(client, pid='proj_a', project_path=str(client.proj_a_path))
    _seed(client, pid='proj_b', project_path=str(client.data_dir / 'proj_b_ws'))
    r = client.post('/api/project/proj_b/roster/hire',
                    json={'character': 'project:local-writer'})
    assert r.status_code == 404
    assert not _roster_of(client, 'proj_b')


# ── un-hire ──────────────────────────────────────────────────────────────────

def test_unhire_sets_removed_at_never_deletes(client):
    _seed(client)
    client.post('/api/project/tproj/roster/hire',
               json={'character': 'global:code-reviewer'})
    r = client.delete('/api/project/tproj/roster/global:code-reviewer')
    assert r.status_code == 200
    roster = _roster_of(client, 'tproj')
    assert len(roster) == 1              # row kept, not removed
    assert roster[0]['removed_at'] is not None


def test_unhire_not_hired_404s(client):
    _seed(client)
    r = client.delete('/api/project/tproj/roster/global:code-reviewer')
    assert r.status_code == 404


def test_rehire_after_unhire_revives_the_same_row(client):
    _seed(client)
    client.post('/api/project/tproj/roster/hire',
               json={'character': 'global:code-reviewer'})
    client.delete('/api/project/tproj/roster/global:code-reviewer')
    r = client.post('/api/project/tproj/roster/hire',
                    json={'character': 'global:code-reviewer'})
    assert r.status_code == 200
    roster = r.get_json()['roster']
    assert len(roster) == 1              # revived, not a second row
    assert roster[0]['removed_at'] is None


def test_unhire_refused_while_character_has_a_live_session(client):
    _seed(client)
    client.post('/api/project/tproj/roster/hire',
               json={'character': 'global:code-reviewer'})
    client.state.agent_sessions['s1'] = {
        'session_id': 's1', 'project_id': 'tproj', 'status': 'running',
        'character': {'scope': 'global', 'name': 'code-reviewer'},
    }
    r = client.delete('/api/project/tproj/roster/global:code-reviewer')
    assert r.status_code == 409
    roster = _roster_of(client, 'tproj')
    assert roster[0]['removed_at'] is None   # refusal did not mutate anything
