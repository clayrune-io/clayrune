"""SR-1 contract and route boundaries; no server/provider imports or live data."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import caller_attribution as ca, characters, state
from mc import steward_role_model as m, steward_role_writes as w
from mc.blueprints import local_auth as la, project_routes as pr
from mc.blueprints import scheduler_routes as sr, steward_routes as st

UI = {'Origin': 'http://localhost:5199'}
PROOF = 'test-human-pass'


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    projects = tmp_path / 'projects'
    projects.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    global_agents = tmp_path / 'agents'
    global_agents.mkdir()
    monkeypatch.setattr(characters, 'GLOBAL_AGENTS_DIR', global_agents)
    characters.write_character('global', 'owner', 'Owns work', 'Voice stays here')
    project = {'id': 'p1', 'name': 'One', 'project_path': str(workspace),
               'default_character': 'global:owner', 'steward_mode': 'off',
               'steward_objective': 'obsolete objective',
               'roster': [{'character': 'global:owner', 'removed_at': None}],
               'backlog': [{'id': 'charter', 'text': 'Maintain the test project', 'status': 'open'},
                           {'id': 'ordinary', 'text': 'Small task', 'status': 'open'}]}
    (projects / 'p1.json').write_text(json.dumps(project), encoding='utf-8')
    monkeypatch.setattr(pr, 'DATA_DIR', projects)
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', tmp_path / 'schedules.json')
    monkeypatch.setattr(sr, 'load_project', pr.load_project)
    monkeypatch.setattr(sr, 'save_project', pr.save_project)
    monkeypatch.setattr(sr, 'load_projects', lambda: [pr.load_project('p1')])
    monkeypatch.setattr(st, 'load_project', pr.load_project)
    monkeypatch.setattr(st, 'save_project', pr.save_project)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a: ca.Attribution(ca.UNATTRIBUTED))
    # Exercise the real shared human-proof helper, with its verifier isolated.
    monkeypatch.setattr(la, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(la, '_local_auth_try_passcode', lambda p: p == PROOF)
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(state, 'tracked_processes', {})
    monkeypatch.setattr(sr, '_dispatch_agent_internal', lambda *a, **kw: pytest.fail('no role dispatch in SR-1'))
    monkeypatch.setattr(st._core, 'remove_fence_from_project', lambda *a: pytest.fail('must retain guards'))
    app = Flask(__name__)
    app.config['TESTING'] = True
    for blueprint in (pr.bp, sr.bp, st.bp):
        app.register_blueprint(blueprint)
    return SimpleNamespace(app=app, client=app.test_client(), project=project,
                           root=tmp_path, projects=projects, workspace=workspace)


def spec(**over):
    return {'project_id': 'p1', 'steward_role': {'version': 1, 'charter_item_id': 'charter',
            'journal_path': 'docs/_journal/charter-test.md'},
            'schedule_type': 'interval', 'interval_minutes': 180, **over}


def create(ctx, **over):
    return ctx.client.post('/api/schedules', json=spec(passcode=PROOF, **over), headers=UI)


def rows():
    return json.loads(sr.SCHEDULES_PATH.read_text(encoding='utf-8')) if sr.SCHEDULES_PATH.exists() else []


def snapshots(ctx):
    return sr.SCHEDULES_PATH.read_bytes(), (ctx.projects / 'p1.json').read_bytes()


def test_roundtrip_pins_owner_and_compatibility_is_read_only(ctx):
    response = create(ctx)
    assert response.status_code == 201, response.json
    row = response.json
    assert row['character'] == 'global:owner'
    assert row['steward_role'] == spec()['steward_role']
    p = pr.load_project('p1')
    assert p['steward_assignment'] == {'schedule_id': row['id']}
    assert p['steward_objective'] == 'obsolete objective'
    before = snapshots(ctx)
    assert m.objective(p, rows()) == 'Maintain the test project'
    assert m.journal_path(p, row['steward_role']['journal_path']) == ctx.workspace / 'docs/_journal/charter-test.md'
    assert ctx.client.get('/api/project/p1/steward').json['objective'] == 'Maintain the test project'
    assert snapshots(ctx) == before
    assert sorted(p.name for p in ctx.projects.iterdir()) == ['p1.json']
    assert 'passcode' not in str(rows())


@pytest.mark.parametrize('role', [None, {}, 'steward', {'version': 2},
    {'version': True, 'charter_item_id': 'charter', 'journal_path': 'docs/_journal/a.md'},
    {'version': 1, 'charter_item_id': 'charter', 'journal_path': 'docs/_journal/a.md', 'owner': 'x'}])
def test_bad_role_schema_has_no_side_effect(ctx, role):
    before = (ctx.projects / 'p1.json').read_bytes()
    response = create(ctx, steward_role=role)
    assert response.status_code == 400
    assert rows() == []
    assert (ctx.projects / 'p1.json').read_bytes() == before


@pytest.mark.parametrize('change', [{'task': 'broader task'}, {'workflow_id': 'workflow'},
                                    {'delete_after_run': True}, {'enabled': 'false'}])
def test_exclusive_targets_and_lifecycle(ctx, change):
    assert create(ctx, **change).status_code == 400
    assert rows() == []


@pytest.mark.parametrize('path', ['../other.md', '/docs/_journal/a.md', 'C:/docs/_journal/a.md',
    'docs/_journal/../../a.md', 'docs/_journal/a.txt', 'docs/_journal/a.md:stream',
    'docs\\_journal\\a.md', 'docs/_journal/../a.md', 'docs/_journal/./a.md'])
def test_journal_escapes_refused(ctx, path):
    role = spec()['steward_role'] | {'journal_path': path}
    assert create(ctx, steward_role=role).status_code == 400
    assert rows() == []


def test_symlink_escape_refused(ctx):
    outside = ctx.root / 'other-project'
    outside.mkdir()
    (ctx.workspace / 'docs/_journal').mkdir(parents=True)
    link = ctx.workspace / 'docs/_journal/escape'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'host does not permit symlinks: {exc}')
    role = spec()['steward_role'] | {'journal_path': 'docs/_journal/escape/a.md'}
    assert create(ctx, steward_role=role).status_code == 400


@pytest.mark.parametrize('edit', [lambda p: p.update(backlog=[]),
    lambda p: p['backlog'][0].update(text=' '),
    lambda p: p['backlog'][0].update(status='done'),
    lambda p: p.update(roster=[]),
    lambda p: p['roster'][0].update(removed_at='today')])
def test_missing_closed_empty_charter_or_unhired_owner(ctx, edit):
    p = pr.load_project('p1')
    edit(p)
    pr.save_project('p1', p)
    assert create(ctx).status_code == 400


def test_deleted_owner_and_foreign_charter(ctx):
    (characters.GLOBAL_AGENTS_DIR / 'owner.md').unlink()
    assert create(ctx).status_code == 400
    role = spec()['steward_role'] | {'charter_item_id': 'foreign'}
    assert create(ctx, steward_role=role).status_code == 400


def test_owner_not_inherited_after_activation_and_merged_checks(ctx):
    row = create(ctx).json
    p = pr.load_project('p1')
    p['default_character'] = 'global:missing'
    pr.save_project('p1', p)
    response = ctx.client.put('/api/schedules/' + row['id'],
                              json={'description': 'edited', 'passcode': PROOF}, headers=UI)
    assert response.status_code == 200
    assert response.json['character'] == 'global:owner'
    before = snapshots(ctx)
    for patch in ({'task': 'broader'}, {'workflow_id': 'x'}, {'project_id': 'foreign'},
                  {'character': 'global:missing'}, {'steward_role': None}):
        response = ctx.client.put('/api/schedules/' + row['id'],
                                  json=patch | {'passcode': PROOF}, headers=UI)
        assert response.status_code == 400, response.json
        assert snapshots(ctx) == before


def test_one_active_includes_legacy_steward(ctx):
    assert create(ctx).status_code == 201
    before = snapshots(ctx)
    assert create(ctx).status_code == 400
    assert snapshots(ctx) == before
    disabled = create(ctx, enabled=False)
    assert disabled.status_code == 201
    response = ctx.client.put('/api/schedules/' + disabled.json['id'],
                              json={'enabled': True, 'passcode': PROOF}, headers=UI)
    assert response.status_code == 400
    sr._save_schedules([{'id': 'legacy', 'project_id': 'p1', 'steward': True, 'enabled': True}])
    assert create(ctx).status_code == 400


def test_concurrent_assignments_cannot_both_activate(ctx):
    def save(_):
        with ctx.app.test_client() as client:
            return client.post('/api/schedules', json=spec(passcode=PROOF), headers=UI).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = sorted(pool.map(save, range(2)))
    assert result == [201, 400]
    assert len(rows()) == 1
    assert pr.load_project('p1')['steward_assignment']['schedule_id'] == rows()[0]['id']


@pytest.mark.parametrize('origin', [False, True])
def test_attributed_agent_cannot_create_even_with_valid_proof(ctx, monkeypatch, origin):
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a: ca.Attribution(ca.ATTRIBUTED, 'agent'))
    response = ctx.client.post('/api/schedules', json=spec(passcode=PROOF, source='ui'),
                               headers=UI if origin else {})
    assert response.status_code == 403
    assert rows() == []


@pytest.mark.parametrize('payload', [{}, {'passcode': 'wrong'}, {'human': True, 'source': 'ui'}])
def test_forged_browser_needs_actual_proof(ctx, payload):
    assert ctx.client.post('/api/schedules', json=spec(**payload), headers=UI).status_code == 403
    assert rows() == []


@pytest.mark.parametrize('payload', [{'enabled': True}, {'enabled': False}, {'steward_role': None},
    {'steward_role': spec()['steward_role']}, {'character': 'global:owner'},
    {'interval_minutes': 5}, {'task': 'wider', 'source': 'ui'}, {'description': 'same'}])
def test_agent_cannot_change_bound_schedule_or_mixed_body(ctx, monkeypatch, payload):
    row = create(ctx).json
    before = snapshots(ctx)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a: ca.Attribution(ca.ATTRIBUTED, 'agent'))
    assert ctx.client.put('/api/schedules/' + row['id'], json=payload, headers=UI).status_code == 403
    assert snapshots(ctx) == before
    assert ctx.client.delete('/api/schedules/' + row['id'], headers=UI).status_code == 403
    assert snapshots(ctx) == before


def test_only_attributed_own_schedule_session_can_suspend(ctx, monkeypatch):
    row = create(ctx).json
    state.agent_sessions['own'] = {'project_id': 'p1', 'trigger_type': 'schedule', 'trigger_id': row['id']}
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a: ca.Attribution(ca.ATTRIBUTED, 'own'))
    response = ctx.client.put('/api/schedules/' + row['id'], json={'enabled': False})
    assert response.status_code == 200
    assert rows()[0]['enabled'] is False
    assert rows()[0]['steward_role'] == row['steward_role']
    assert ctx.client.put('/api/schedules/' + row['id'], json={'enabled': True}).status_code == 403
    assert ctx.client.put('/api/schedules/' + row['id'],
                          json={'enabled': False, 'description': 'changed'}).status_code == 403
    assert ctx.client.put('/api/schedules/' + row['id'], json={'enabled': 0}).status_code == 403


@pytest.mark.parametrize('payload', [{'text': 'widen'}, {'text': None},
                                    {'status': 'open'}, {'status': None},
                                    {'text': 'Maintain the test project', 'priority': 'high'}])
def test_attached_charter_text_protected_even_disabled(ctx, payload):
    create(ctx, enabled=False)
    before = snapshots(ctx)
    assert ctx.client.patch('/api/project/p1/backlog/charter', json=payload).status_code == 403
    assert ctx.client.delete('/api/project/p1/backlog/charter').status_code == 403
    assert snapshots(ctx) == before


def test_human_can_edit_charter_and_agent_can_update_ordinary_item_state(ctx):
    create(ctx)
    response = ctx.client.patch('/api/project/p1/backlog/charter',
                                json={'text': 'Narrower charter', 'passcode': PROOF}, headers=UI)
    assert response.status_code == 200
    assert m.objective(pr.load_project('p1'), rows()) == 'Narrower charter'
    assert ctx.client.patch('/api/project/p1/backlog/ordinary', json={'text': 'Progress'}).status_code == 200
    assert ctx.client.patch('/api/project/p1/backlog/ordinary', json={'status': 'blocked'}).status_code == 200
    assert ctx.client.patch('/api/project/p1/backlog/charter', json={'status': 'open'}).status_code == 403


@pytest.mark.parametrize('payload', [{'steward_assignment': None}, {'steward_assignment': {}},
    {'steward_assignment': {'schedule_id': 'foreign'}}, {'steward_mode': 'on'},
    {'steward_objective': 'wide'}, {'steward_cadence_minutes': 2}, {'roster': []}, {'backlog': []}])
def test_project_route_cannot_bypass_role_gate(ctx, payload):
    create(ctx)
    before = snapshots(ctx)
    response = ctx.client.post('/api/project/p1', json=payload | {'name': 'not applied'}, headers=UI)
    assert response.status_code == 403
    assert snapshots(ctx) == before


def test_human_pointer_checked_and_legacy_fields_not_competing_authority(ctx):
    row = create(ctx).json
    before = snapshots(ctx)
    for payload in ({'steward_assignment': None}, {'steward_assignment': {'schedule_id': 'missing'}},
                    {'steward_objective': 'wide'}, {'steward_cadence_minutes': 2}):
        response = ctx.client.post('/api/project/p1', json=payload | {'passcode': PROOF}, headers=UI)
        assert response.status_code == 400
        assert snapshots(ctx) == before
    response = ctx.client.post('/api/project/p1', headers=UI,
        json={'steward_assignment': {'schedule_id': row['id']}, 'passcode': PROOF})
    assert response.status_code == 200


def test_disable_retains_guard_and_binding_enable_does_not_seed_legacy(ctx):
    row = create(ctx).json
    assert ctx.client.post('/api/project/p1/steward/enable', json={'passcode': PROOF}, headers=UI).status_code == 200
    response = ctx.client.post('/api/project/p1/steward/disable', json={'passcode': PROOF}, headers=UI)
    assert response.status_code == 200
    assert len(rows()) == 1 and rows()[0]['id'] == row['id']
    assert pr.load_project('p1')['steward_mode'] == 'off'
    assert pr.load_project('p1')['steward_assignment'] == {'schedule_id': row['id']}
    assert ctx.client.post('/api/project/p1/steward/enable',
                           json={'objective': 'widen', 'passcode': PROOF}, headers=UI).status_code == 400
    assert ctx.client.post('/api/project/p1/steward/enable').status_code == 403


def test_run_now_is_blocked_pending_guarded_runner(ctx):
    row = create(ctx).json
    response = ctx.client.post('/api/schedule/' + row['id'] + '/run-now')
    assert response.status_code == 409
    assert response.json['error'] == w.EXECUTION_ERROR


def test_stale_timer_snapshot_cannot_restore_reassigned_or_deleted_binding(ctx):
    row = create(ctx).json
    stale = copy.deepcopy(rows())
    assert ctx.client.put('/api/schedules/' + row['id'], headers=UI,
        json={'interval_minutes': 999, 'passcode': PROOF}).status_code == 200
    w.save_runtime_schedules(stale)
    assert rows()[0]['interval_minutes'] == 999
    assert ctx.client.delete('/api/schedules/' + row['id'], headers=UI,
                              json={'passcode': PROOF}).status_code == 200
    w.save_runtime_schedules(stale)
    assert rows() == []
    assert 'steward_assignment' not in pr.load_project('p1')


def test_corrupt_schedule_store_does_not_get_reset(ctx):
    create(ctx)
    sr.SCHEDULES_PATH.write_text('{broken', encoding='utf-8')
    before = snapshots(ctx)
    assert create(ctx).status_code == 500
    assert snapshots(ctx) == before
    response = ctx.client.post('/api/schedules', json={'project_id': 'p1', 'task': 'ordinary'})
    assert response.status_code == 500
    assert snapshots(ctx) == before


def test_ordinary_schedule_and_ordinary_backlog_behavior_unchanged(ctx):
    response = ctx.client.post('/api/schedules', json={'project_id': 'p1', 'task': 'ordinary'})
    assert response.status_code == 201
    row = response.json
    assert 'steward_role' not in row
    assert ctx.client.put('/api/schedules/' + row['id'], json={'task': 'edited'}).status_code == 200
    assert ctx.client.delete('/api/schedules/' + row['id']).status_code == 200
    assert ctx.client.patch('/api/project/p1/backlog/ordinary', json={'text': 'Updated'}).status_code == 200


def test_foreign_project_pointer_and_inactive_pointer_cannot_replace_active(ctx):
    active = create(ctx).json
    inactive = create(ctx, enabled=False).json
    p = pr.load_project('p1')
    for pointer in ({'schedule_id': inactive['id']}, {'schedule_id': 'foreign'}):
        with pytest.raises(m.RoleError):
            m.assignment(p | {'steward_assignment': pointer}, rows())
    foreign = copy.deepcopy(active) | {'id': 'foreign', 'project_id': 'p2'}
    with pytest.raises(m.RoleError, match='same-project'):
        m.assignment(p | {'steward_assignment': {'schedule_id': 'foreign'}}, [foreign])


def test_closed_charter_visible_and_human_suspension_still_works(ctx):
    row = create(ctx).json
    p = pr.load_project('p1')
    p['backlog'][0]['status'] = 'done'
    pr.save_project('p1', p)
    status = ctx.client.get('/api/project/p1/steward').json
    assert status['enabled'] is False and status['role_error'] == 'charter is closed'
    response = ctx.client.put('/api/schedules/' + row['id'], headers=UI,
                              json={'enabled': False, 'passcode': PROOF})
    assert response.status_code == 200
    assert response.json['enabled'] is False


def test_orphan_role_pointer_cannot_bootstrap_legacy_and_kill_switch_keeps_guard(ctx):
    row = create(ctx).json
    sr._save_schedules([])  # interrupted human deletion between the two stores
    before = (ctx.projects / 'p1.json').read_bytes()
    result, rc = st._do_enable('p1', 'must not become legacy', 5)
    assert rc == 409
    assert (ctx.projects / 'p1.json').read_bytes() == before
    assert ctx.client.post('/api/project/p1/steward/enable', headers=UI,
                           json={'passcode': PROOF}).status_code == 400
    response = ctx.client.post('/api/project/p1/steward/disable', headers=UI,
                               json={'passcode': PROOF})
    assert response.status_code == 200
    assert pr.load_project('p1')['steward_assignment'] == {'schedule_id': row['id']}


def test_role_cannot_be_shed_by_agent_project_deletion(ctx):
    create(ctx)
    before = snapshots(ctx)
    assert ctx.client.delete('/api/project/p1', headers=UI).status_code == 403
    assert snapshots(ctx) == before


def test_legacy_bootstrap_refuses_even_disabled_binding(ctx):
    create(ctx, enabled=False)
    before = snapshots(ctx)
    _, rc = st._do_enable('p1', 'not another mandate', 60)
    assert rc == 409
    assert snapshots(ctx) == before


def test_ordinary_run_now_cannot_clobber_concurrent_assignment(ctx, monkeypatch):
    ordinary = ctx.client.post('/api/schedules', json={'project_id': 'p1', 'task': 'ordinary'}).json
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda *a: '')
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda *a: '')
    def dispatch(*a, **kw):
        with ctx.app.test_client() as other:
            response = other.post('/api/schedules', json=spec(passcode=PROOF), headers=UI)
            assert response.status_code == 201
        return 'fake-session'
    monkeypatch.setattr(sr, '_dispatch_agent_internal', dispatch)
    response = ctx.client.post('/api/schedule/' + ordinary['id'] + '/run-now')
    assert response.status_code == 200
    assert len(rows()) == 2
    assert m.assignment(pr.load_project('p1'), rows())['steward_role'] == spec()['steward_role']


def test_pointer_save_failure_leaves_blocked_repairable_binding(ctx, monkeypatch):
    original_save = sr.save_project
    def fail(*a):
        raise OSError('simulated disk error')
    monkeypatch.setattr(sr, 'save_project', fail)
    assert create(ctx).status_code == 500
    assert len(rows()) == 1
    assert 'steward_assignment' not in pr.load_project('p1')
    status = ctx.client.get('/api/project/p1/steward').json
    assert status['enabled'] is False and 'pointer' in status['role_error']
    assert create(ctx).status_code == 400  # cannot add a second active binding
    monkeypatch.setattr(sr, 'save_project', original_save)
    response = ctx.client.post('/api/project/p1', headers=UI,
        json={'steward_assignment': {'schedule_id': rows()[0]['id']}, 'passcode': PROOF})
    assert response.status_code == 200


def test_human_detach_retains_schedule_identity_and_cannot_be_resurrected(ctx):
    row = create(ctx, enabled=False).json
    stale = copy.deepcopy(rows())
    response = ctx.client.put('/api/schedules/' + row['id'], headers=UI,
        json={'steward_role': None, 'task': 'ordinary now', 'passcode': PROOF})
    assert response.status_code == 200
    assert response.json['id'] == row['id']
    assert response.json['enabled'] is False
    assert 'steward_role' not in response.json
    assert 'steward_assignment' not in pr.load_project('p1')
    w.save_runtime_schedules(stale)
    assert len(rows()) == 1 and 'steward_role' not in rows()[0]


def test_no_passcode_configured_cannot_activate_role(ctx, monkeypatch):
    monkeypatch.setattr(la, '_local_auth_is_configured', lambda: False)
    assert create(ctx).status_code == 403
    assert rows() == []


def test_timer_cannot_dispatch_role_even_with_poisoned_cached_task(ctx, monkeypatch):
    row = create(ctx).json
    row.update(task='must never run', next_run='2020-01-01T00:00:00Z')
    sr._save_schedules([row])
    class StopAfterOne:
        checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 1
        def wait(self, timeout):
            return True
    monkeypatch.setattr(sr, '_scheduler_stop', StopAfterOne())
    monkeypatch.setattr(sr, 'all_managers', lambda: [])
    monkeypatch.setattr(sr._wf, 'resume_due_waits', lambda: None)
    sr._scheduler_loop()
    assert rows()[0]['last_run'] is None
    assert ctx.client.get('/api/schedules').json[0]['execution_error'] == w.EXECUTION_ERROR


def test_project_owner_is_resolved_only_in_its_project(ctx):
    other = ctx.root / 'other-project'
    other.mkdir()
    characters.write_character('project', 'local', 'Other', 'Other voice', project_path=str(other))
    p = pr.load_project('p1')
    p['roster'].append({'character': 'project:local', 'removed_at': None})
    pr.save_project('p1', p)
    assert create(ctx, character='project:local').status_code == 400
    characters.write_character('project', 'local', 'Local', 'Local voice', project_path=str(ctx.workspace))
    response = create(ctx, character='project:local')
    assert response.status_code == 201
    assert response.json['character'] == 'project:local'


@pytest.mark.parametrize('patch', [{'text': None}, {'text': ' '}, {'status': None}])
def test_human_charter_edit_is_validated_before_any_mutation(ctx, patch):
    create(ctx)
    before = snapshots(ctx)
    response = ctx.client.patch('/api/project/p1/backlog/charter', headers=UI,
                                json=patch | {'passcode': PROOF, 'priority': 'high'})
    assert response.status_code == 400
    assert snapshots(ctx) == before


@pytest.mark.parametrize('kind,bound', [
    ('project', False), ('project', True),
    ('charter', False), ('charter', True),
    ('delete', False), ('delete', True), ('legacy-control', False),
])
def test_validated_handlers_allow_manager_guarded_project_saves(ctx, kind, bound):
    """A handler must not inherit role locks and invert manager -> project."""
    from threading import RLock

    if bound:
        assert create(ctx).status_code == 201
    manager_lock = RLock()

    def save_from_manager():
        # This is the order used by project create/delete and delivery paths.
        with manager_lock:
            if not w.WRITE_LOCK.acquire(timeout=1):
                return False
            try:
                project_lock = pr._project_write_lock('p1')
                if not project_lock.acquire(timeout=1):
                    return False
                try:
                    project = pr.load_project('p1')
                    project['description'] = 'manager save completed'
                    pr.save_project('p1', project)
                finally:
                    project_lock.release()
            finally:
                w.WRITE_LOCK.release()
        return True

    def handler(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            saved = pool.submit(save_from_manager).result(timeout=5)
        assert saved, 'role validation locks leaked into the handler'
        return 'handler completed'

    decorator = {'project': w.project_write, 'charter': w.charter_write,
                 'delete': w.project_delete, 'legacy-control': w.steward_control}[kind]
    path = '/api/project/p1/steward/disable' if kind == 'legacy-control' else '/api/project/p1'
    with ctx.app.test_request_context(path, method='POST',
                                     json={'passcode': PROOF}, headers=UI):
        args = ('p1', 'charter') if kind == 'charter' else ('p1',)
        assert decorator(handler)(*args) == 'handler completed'
    assert pr.load_project('p1')['description'] == 'manager save completed'
