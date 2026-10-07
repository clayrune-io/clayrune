"""End-to-end test for the steward blueprint: enable → status → disable through
the real Flask app, isolated via monkeypatched data paths. Validates the whole
bootstrap: config persisted, charter seeded, schedule created, fence installed
into the project's .claude/settings.json — and that disable reverses all of it.
"""
import json

import pytest


BROWSER = {'Origin': 'http://localhost:5199'}
ENABLE_ROUTES = [
    '/api/project/p1/steward/enable',
    '/api/steward/standalone/enable',
]
ENABLE_BODY = {'name': 'Env Health', 'objective': 'Keep docs synced', 'cadence_minutes': 60}


@pytest.fixture()
def sclient(tmp_path, monkeypatch):
    import server  # noqa: F401 — registers blueprints + runs wire() on import
    from mc.blueprints import project_routes as pr
    from mc.blueprints import scheduler_routes as sr
    from mc.blueprints import local_auth as la
    from mc import caller_attribution as ca, state
    import steward

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', tmp_path / 'schedules.json')
    monkeypatch.setattr(steward.CFG, 'data_root', tmp_path)
    monkeypatch.setattr(steward.CFG, 'notify_push', lambda *a, **k: None)
    monkeypatch.setitem(state.CONFIG, 'auto_workspace_base', str(tmp_path / 'ws'))
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(ca.UNATTRIBUTED))

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    pr.save_project('p1', {'id': 'p1', 'name': 'P1',
                           'project_path': str(proj_path), 'backlog': []})

    server.app.config['TESTING'] = True
    client = server.app.test_client()
    # Existing bootstrap tests represent browser clicks. Agent tests override
    # this with an empty Origin or with managed caller attribution.
    client.environ_base['HTTP_ORIGIN'] = BROWSER['Origin']
    return client, proj_path, pr, sr


def _settings(proj_path):
    f = proj_path / '.claude' / 'settings.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}


def test_enable_requires_objective(sclient):
    client, *_ = sclient
    r = client.post('/api/project/p1/steward/enable', json={})
    assert r.status_code == 400


def test_enable_bootstraps_everything(sclient):
    client, proj_path, pr, sr = sclient
    r = client.post('/api/project/p1/steward/enable',
                    json={'objective': 'Keep docs synced with code',
                          'cadence_minutes': 60})
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] and body['fenced']
    assert body['cadence_minutes'] == 60
    assert body['charter_item_id']

    # config persisted
    p = pr.load_project('p1')
    assert p['steward_mode'] == 'on'
    assert p['steward_objective'] == 'Keep docs synced with code'

    # charter seeded
    charter = next(it for it in p['backlog'] if it['source'] == 'steward-charter')
    assert charter['text'].startswith('STEWARD CHARTER:')

    # schedule created, continues one thread, interval cadence, steward-tagged
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    st = next(s for s in scheds if s.get('steward'))
    assert st['project_id'] == 'p1'
    assert st['continue_session'] is True
    assert st['schedule_type'] == 'interval'
    assert st['interval_minutes'] == 60
    assert '[Steward cycle]' in st['task']

    # fence installed into the project's own settings
    hook = _settings(proj_path)['hooks']['PreToolUse'][0]
    assert 'Bash' in hook['matcher']
    assert 'fence.py' in hook['hooks'][0]['command']


def test_enable_is_idempotent(sclient):
    client, proj_path, pr, sr = sclient
    for _ in range(2):
        client.post('/api/project/p1/steward/enable',
                    json={'objective': 'x', 'cadence_minutes': 90})
    p = pr.load_project('p1')
    charters = [it for it in p['backlog'] if it['source'] == 'steward-charter']
    assert len(charters) == 1                          # no duplicate charter
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    assert sum(1 for s in scheds if s.get('steward')) == 1   # no duplicate schedule
    pre = _settings(proj_path)['hooks']['PreToolUse']
    assert sum(1 for e in pre for h in e['hooks']
               if 'fence.py' in h['command']) == 1     # no duplicate hook


def test_status_reflects_state(sclient):
    client, *_ = sclient
    assert client.get('/api/project/p1/steward').get_json()['enabled'] is False
    client.post('/api/project/p1/steward/enable', json={'objective': 'o'})
    s = client.get('/api/project/p1/steward').get_json()
    assert s['enabled'] and s['objective'] == 'o' and s['schedule_id']


def test_disable_reverses_bootstrap(sclient):
    client, proj_path, pr, sr = sclient
    client.post('/api/project/p1/steward/enable',
                json={'objective': 'o', 'cadence_minutes': 60})
    r = client.post('/api/project/p1/steward/disable')
    assert r.status_code == 200 and r.get_json()['enabled'] is False

    assert pr.load_project('p1')['steward_mode'] == 'off'
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    assert not any(s.get('steward') for s in scheds)        # schedule gone
    pre = _settings(proj_path).get('hooks', {}).get('PreToolUse', [])
    assert not any('fence.py' in h['command'] for e in pre for h in e['hooks'])  # unfenced
    # charter is intentionally preserved as a record
    assert any(it['source'] == 'steward-charter' for it in pr.load_project('p1')['backlog'])


def test_loop_health_endpoint(sclient):
    client, *_ = sclient
    client.post('/api/project/p1/steward/enable', json={'objective': 'o'})
    h = client.get('/api/steward/loop-health').get_json()
    assert h['projects_enabled'] == 1
    assert h['enabled'][0]['project_id'] == 'p1'


def test_standalone_requires_name_and_objective(sclient):
    client, *_ = sclient
    assert client.post('/api/steward/standalone/enable', json={'objective': 'o'}).status_code == 400
    assert client.post('/api/steward/standalone/enable', json={'name': 'X'}).status_code == 400


def test_standalone_bootstraps_workspace(sclient):
    client, proj_path, pr, sr = sclient
    r = client.post('/api/steward/standalone/enable',
                    json={'name': 'Env Health', 'objective': 'Keep my environment healthy',
                          'cadence_minutes': 120})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] and body['enabled'] and body['standalone']
    pid = body['project_id']
    assert pid.startswith('_steward_')

    # workspace pseudo-project provisioned, flagged, hidden-eligible
    ws = pr.load_project(pid)
    assert ws is not None and ws['_is_steward_workspace'] is True
    assert ws['project_path']
    # charter + schedule + fence all bootstrapped on the workspace
    assert any(it['source'] == 'steward-charter' for it in ws['backlog'])
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    assert any(s.get('steward') and s['project_id'] == pid for s in scheds)
    fence = _settings(__import__('pathlib').Path(ws['project_path']))
    assert 'fence.py' in fence['hooks']['PreToolUse'][0]['hooks'][0]['command']


def test_standalone_idempotent_by_name(sclient):
    client, proj_path, pr, sr = sclient
    for _ in range(2):
        client.post('/api/steward/standalone/enable',
                    json={'name': 'Weekly Research', 'objective': 'o'})
    ws_projects = [p for p in pr.load_projects() if p.get('_is_steward_workspace')]
    assert len(ws_projects) == 1  # same slug reused, not duplicated


def test_standalone_shows_in_loop_health(sclient):
    client, *_ = sclient
    client.post('/api/steward/standalone/enable',
                json={'name': 'Dep Watch', 'objective': 'Watch dependencies'})
    h = client.get('/api/steward/loop-health').get_json()
    entry = next(e for e in h['enabled'] if e['standalone'])
    assert entry['objective'] == 'Watch dependencies'


def test_disable_preserves_user_hooks(sclient):
    """The fence removal must not eat a user-authored PreToolUse hook."""
    client, proj_path, pr, sr = sclient
    settings_dir = proj_path / '.claude'
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / 'settings.json').write_text(json.dumps({
        'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
            {'type': 'command', 'command': 'echo user-hook'}]}]}}), encoding='utf-8')
    client.post('/api/project/p1/steward/enable', json={'objective': 'o'})
    client.post('/api/project/p1/steward/disable')
    pre = _settings(proj_path)['hooks']['PreToolUse']
    cmds = [h['command'] for e in pre for h in e['hooks']]
    assert 'echo user-hook' in cmds                    # user hook survived
    assert not any('fence.py' in c for c in cmds)       # steward hook gone


def test_edit_updates_objective_cadence_and_charter(sclient):
    """Re-enabling an existing steward EDITS it in place: objective, cadence, the
    pinned charter item's text, and the schedule interval — without duplicating
    the charter or the schedule. Backs the Automation-modal 'Edit' button."""
    client, proj_path, pr, sr = sclient
    r1 = client.post('/api/project/p1/steward/enable',
                     json={'objective': 'First mandate', 'cadence_minutes': 60})
    assert r1.status_code == 200
    cid1 = r1.get_json()['charter_item_id']

    r2 = client.post('/api/project/p1/steward/enable',
                     json={'objective': 'Second, revised mandate', 'cadence_minutes': 120})
    assert r2.status_code == 200
    body = r2.get_json()
    assert body['cadence_minutes'] == 120
    assert body['charter_item_id'] == cid1          # same charter, not a new one

    p = pr.load_project('p1')
    assert p['steward_objective'] == 'Second, revised mandate'
    assert p['steward_cadence_minutes'] == 120
    charters = [it for it in p['backlog'] if it.get('source') == 'steward-charter']
    assert len(charters) == 1                        # not duplicated
    assert charters[0]['text'] == 'STEWARD CHARTER: Second, revised mandate'

    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    steward_scheds = [s for s in scheds if s.get('steward') and s['project_id'] == 'p1']
    assert len(steward_scheds) == 1                  # schedule updated, not duplicated
    assert steward_scheds[0]['interval_minutes'] == 120


@pytest.mark.parametrize('route', ENABLE_ROUTES)
@pytest.mark.parametrize('origin', ['', BROWSER['Origin']])
@pytest.mark.parametrize('body', [ENABLE_BODY, {}])
def test_agent_enable_refused_before_any_side_effect(sclient, monkeypatch, route, origin, body):
    from mc import caller_attribution as ca
    from mc.blueprints import steward_routes as st

    client, proj_path, pr, sr = sclient
    record = pr.DATA_DIR / 'p1.json'
    before = record.read_bytes()
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))

    def unexpected(*args):
        pytest.fail('agent refusal must precede bootstrap and workspace provision')

    monkeypatch.setattr(st, '_do_enable', unexpected)
    monkeypatch.setattr(st, '_ensure_steward_workspace', unexpected)
    r = client.post(route, json=body, headers={'Origin': origin})
    assert r.status_code == 403
    assert r.get_json()['protected_keys'] == [
        'steward_cadence_minutes', 'steward_mode', 'steward_objective',
    ]
    assert all(key in r.get_json()['error'] for key in r.get_json()['protected_keys'])
    assert record.read_bytes() == before
    assert sorted(p.name for p in pr.DATA_DIR.iterdir()) == ['p1.json']
    assert not sr.SCHEDULES_PATH.exists()
    assert not (proj_path / '.claude').exists()
    assert not (proj_path.parent / 'ws').exists()


@pytest.mark.parametrize('route', ENABLE_ROUTES)
def test_agent_cannot_edit_an_enabled_steward(sclient, monkeypatch, route):
    from mc import caller_attribution as ca

    client, _, pr, sr = sclient
    enabled = client.post(route, json=ENABLE_BODY, headers=BROWSER)
    assert enabled.status_code == 200
    record = pr.DATA_DIR / f"{enabled.get_json()['project_id']}.json"
    before = record.read_bytes(), sr.SCHEDULES_PATH.read_bytes()
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))
    r = client.post(route, json={**ENABLE_BODY, 'objective': 'Agent edit',
                                'cadence_minutes': 5}, headers=BROWSER)
    assert r.status_code == 403
    assert (record.read_bytes(), sr.SCHEDULES_PATH.read_bytes()) == before


@pytest.mark.parametrize('route', ENABLE_ROUTES)
@pytest.mark.parametrize('origin', ['', BROWSER['Origin']])
def test_agent_disable_remains_a_working_kill_switch(sclient, monkeypatch, route, origin):
    from pathlib import Path
    from mc import caller_attribution as ca

    client, _, pr, sr = sclient
    enabled = client.post(route, json=ENABLE_BODY, headers=BROWSER)
    assert enabled.status_code == 200
    pid = enabled.get_json()['project_id']
    charter = enabled.get_json()['charter_item_id']
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))
    r = client.post(f'/api/project/{pid}/steward/disable', headers={'Origin': origin})
    assert r.status_code == 200
    assert r.get_json()['enabled'] is False and r.get_json()['schedule_removed']
    saved = pr.load_project(pid)
    assert saved['steward_mode'] == 'off'
    assert any(item['id'] == charter for item in saved['backlog'])
    assert not any(s.get('steward') and s['project_id'] == pid
                   for s in json.loads(sr.SCHEDULES_PATH.read_text(encoding='utf-8')))
    hooks = _settings(Path(saved['project_path'])).get('hooks', {}).get('PreToolUse', [])
    assert not any('fence.py' in h['command'] for entry in hooks for h in entry['hooks'])
