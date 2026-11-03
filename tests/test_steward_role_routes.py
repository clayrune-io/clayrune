"""SR-5 read-only editor support: overview, options and preview. No writes, no server."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import caller_attribution as ca, characters, state
from mc import steward_role_task as composer
from mc.blueprints import local_auth as la, project_routes as pr
from mc.blueprints import scheduler_routes as sr, steward_role_routes as rr, steward_routes as st

UI = {'Origin': 'http://localhost:5199'}
PROOF = 'test-human-pass'


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    projects = tmp_path / 'projects'
    projects.mkdir()
    workspace = tmp_path / 'workspace'
    (workspace / 'docs' / '_journal').mkdir(parents=True)
    agents = tmp_path / 'agents'
    agents.mkdir()
    monkeypatch.setattr(characters, 'GLOBAL_AGENTS_DIR', agents)
    characters.write_character('global', 'owner', 'Owns work', 'Voice stays here')
    characters.write_character('global', 'outsider', 'Not hired', 'Voice')
    project = {'id': 'p1', 'name': 'One', 'project_path': str(workspace),
               'default_character': 'global:owner', 'steward_mode': 'off',
               'roster': [{'character': 'global:owner', 'removed_at': None}],
               'backlog': [
                   {'id': 'charter', 'text': 'Maintain the test project\nSecond line', 'status': 'open'},
                   {'id': 'other', 'text': 'Another standing job', 'status': 'open'},
                   {'id': 'closed', 'text': 'Finished job', 'status': 'done'},
                   {'id': 'empty', 'text': '   ', 'status': 'open'}]}
    (projects / 'p1.json').write_text(json.dumps(project), encoding='utf-8')
    monkeypatch.setattr(pr, 'DATA_DIR', projects)
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', tmp_path / 'schedules.json')
    monkeypatch.setattr(sr, 'load_project', pr.load_project)
    monkeypatch.setattr(sr, 'save_project', pr.save_project)
    monkeypatch.setattr(sr, 'load_projects', lambda: [pr.load_project('p1')])
    monkeypatch.setattr(st, 'load_project', pr.load_project)
    monkeypatch.setattr(st, 'save_project', pr.save_project)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a: ca.Attribution(ca.UNATTRIBUTED))
    monkeypatch.setattr(la, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(la, '_local_auth_try_passcode', lambda p: p == PROOF)
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(state, 'tracked_processes', {})
    app = Flask(__name__)
    app.config['TESTING'] = True
    for bp in (pr.bp, sr.bp, st.bp, rr.bp):
        app.register_blueprint(bp)
    return SimpleNamespace(client=app.test_client(), root=tmp_path, projects=projects,
                           workspace=workspace)


def role(**over):
    return {'version': 1, 'charter_item_id': 'charter',
            'journal_path': 'docs/_journal/charter-test.md', **over}


def create_role(ctx, **over):
    body = {'project_id': 'p1', 'steward_role': role(), 'schedule_type': 'interval',
            'interval_minutes': 180, 'passcode': PROOF, 'enabled': False, **over}
    res = ctx.client.post('/api/schedules', json=body, headers=UI)
    assert res.status_code == 201, res.json
    return res.json


def write_rows(rows):
    sr.SCHEDULES_PATH.write_text(json.dumps(rows), encoding='utf-8')


def legacy_task_row(**over):
    return {'id': 'legacy1', 'enabled': True, 'project_id': 'p1', 'task': 'do the backlog',
            'workflow_id': '', 'character': 'global:owner', 'model': '', 'effort': '',
            'description': '', 'continue_session': True, 'schedule_type': 'interval',
            'time': '09:00', 'days': [], 'interval_minutes': 720, 'run_at': '', 'cron_expr': '',
            'delete_after_run': False, 'run_condition': None, 'last_run': None,
            'next_run': None, 'created_at': 'x', **over}


def snapshot(ctx):
    return {str(p.relative_to(ctx.root)): p.read_bytes() for p in ctx.root.rglob('*') if p.is_file()}


def preview(ctx, **over):
    body = {'project_id': 'p1', 'character': 'global:owner', 'steward_role': role(), **over}
    return ctx.client.post('/api/steward-role/preview', json=body)


# -- blockers -----------------------------------------------------------------

@pytest.fixture()
def unblocked(monkeypatch, tmp_path):
    from steward import core

    def build(project, charter_item, refresh='', *, cycle_snapshot=None):  # patched-core signature
        return ''
    directive = tmp_path / 'CYCLE_DIRECTIVE.md'
    directive.write_text('directive', encoding='utf-8')
    monkeypatch.setattr(core, 'build_cycle_task', build)
    monkeypatch.setattr(composer, 'DIRECTIVE_PATH', directive)
    monkeypatch.setattr(sr, 'steward_role_lifecycle', lambda *a: '', raising=False)
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    return directive


def test_no_blockers_when_every_dependency_is_present(unblocked):
    assert rr.execution_blockers() == []


def test_each_missing_dependency_is_its_own_blocker(unblocked, monkeypatch):
    from steward import core
    unblocked.unlink()
    monkeypatch.setattr(core, 'build_cycle_task', lambda project, charter_item, refresh='': '')
    monkeypatch.delattr(sr, 'steward_role_lifecycle')
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', False)
    assert [b['code'] for b in rr.execution_blockers()] == [
        'core_adapter_unapplied', 'directive_missing', 'lifecycle_adapter_unavailable',
        'unattended_guard_off']


def test_blockers_never_claim_the_role_is_fenced(unblocked, monkeypatch):
    monkeypatch.delattr(sr, 'steward_role_lifecycle')
    assert 'fenced' not in json.dumps(rr.execution_blockers())


# -- assignments --------------------------------------------------------------

def test_assignments_empty_then_lists_a_role_with_charter_text(ctx):
    assert ctx.client.get('/api/steward-role/assignments').json['assignments'] == []
    row = create_role(ctx)
    got = ctx.client.get('/api/steward-role/assignments').json['assignments']
    assert len(got) == 1
    a = got[0]
    assert (a['schedule_id'], a['project_id'], a['character']) == (row['id'], 'p1', 'global:owner')
    assert a['charter_text'].startswith('Maintain the test project')
    assert a['active'] is False and a['enabled'] is False and a['pointed'] is True
    assert a['role_error'] == ''


def test_assignment_with_a_closed_charter_reports_an_error_not_a_crash(ctx):
    row = create_role(ctx)
    p = pr.load_project('p1')
    p['backlog'][0]['status'] = 'done'
    pr.save_project('p1', p)
    a = ctx.client.get('/api/steward-role/assignments').json['assignments'][0]
    assert a['schedule_id'] == row['id'] and 'closed' in a['role_error']
    assert a['charter_text'] == ''


def test_assignment_for_a_deleted_project_is_reported_not_dropped(ctx, monkeypatch):
    create_role(ctx)
    monkeypatch.setattr(sr, 'load_projects', lambda: [])
    a = ctx.client.get('/api/steward-role/assignments').json['assignments'][0]
    assert a['active'] is False and 'no longer exists' in a['role_error']


def test_active_means_enabled_row_and_project_switch_on(ctx):
    create_role(ctx, enabled=True)
    assert ctx.client.get('/api/steward-role/assignments').json['assignments'][0]['active'] is False
    p = pr.load_project('p1')
    p['steward_mode'] = 'on'
    pr.save_project('p1', p)
    assert ctx.client.get('/api/steward-role/assignments').json['assignments'][0]['active'] is True


def test_corrupt_schedule_store_fails_closed_with_a_500(ctx):
    sr.SCHEDULES_PATH.write_text('{not json', encoding='utf-8')
    res = ctx.client.get('/api/steward-role/assignments')
    assert res.status_code == 500 and res.json['assignments'] == []


# -- options ------------------------------------------------------------------

def test_options_list_open_charters_only_with_a_suggested_journal(ctx):
    res = ctx.client.get('/api/steward-role/options?project_id=p1').json
    ids = [c['id'] for c in res['charters']]
    assert ids == ['charter', 'other']          # closed + blank text excluded
    first = res['charters'][0]
    assert first['title'] == 'Maintain the test project'
    assert first['suggested_journal'] == 'docs/_journal/charter-maintain-the-test-project.md'
    assert res['default_character'] == 'global:owner'


def test_options_mark_a_charter_already_bound_to_a_role(ctx):
    row = create_role(ctx)
    charters = {c['id']: c for c in ctx.client.get('/api/steward-role/options?project_id=p1').json['charters']}
    assert charters['charter']['bound_schedule_id'] == row['id']
    assert charters['other']['bound_schedule_id'] is None


@pytest.mark.parametrize('pid,code', [('', 400), ('../x', 400), ('nope', 404)])
def test_options_rejects_bad_project(ctx, pid, code):
    assert ctx.client.get(f'/api/steward-role/options?project_id={pid}').status_code == code


# -- preview ------------------------------------------------------------------

def test_preview_valid_draft_is_ok_and_writes_nothing(ctx):
    before = snapshot(ctx)
    res = preview(ctx)
    assert res.status_code == 200, res.json
    body = res.json
    assert body['ok'] is True
    assert {c['name']: c['ok'] for c in body['checks']} == {
        'charter': True, 'journal': True, 'owner': True, 'assignment': True}
    assert body['owner'] == 'global:owner'
    assert body['charter']['text'].startswith('Maintain the test project')
    assert snapshot(ctx) == before            # byte-identical tree: no journal, no sidecar, no row
    assert not (ctx.workspace / 'docs' / '_journal' / 'charter-test.md').exists()
    assert not (ctx.root / 'steward').exists()
    assert not sr.SCHEDULES_PATH.exists()


def test_preview_never_requests_a_passcode_or_starts_anything(ctx, monkeypatch):
    monkeypatch.setattr(sr, '_dispatch_agent_internal',
                        lambda *a, **k: pytest.fail('preview must not dispatch'), raising=False)
    monkeypatch.setattr(st._core, 'remove_fence_from_project',
                        lambda *a: pytest.fail('preview must not touch the fence'))
    assert preview(ctx).status_code == 200


@pytest.mark.parametrize('journal', ['../x.md', '/abs/a.md', 'docs/_journal/a.txt',
                                     'docs/_journal/../../a.md', 'C:/docs/_journal/a.md'])
def test_preview_reports_journal_escape_by_check(ctx, journal):
    body = preview(ctx, steward_role=role(journal_path=journal)).json
    assert body['ok'] is False
    bad = [c for c in body['checks'] if not c['ok']]
    assert [c['name'] for c in bad] == ['journal'] and bad[0]['detail']


def test_preview_flags_existing_journal_will_be_appended(ctx):
    (ctx.workspace / 'docs' / '_journal' / 'charter-test.md').write_text('old', encoding='utf-8')
    body = preview(ctx).json
    journal = next(c for c in body['checks'] if c['name'] == 'journal')
    assert journal['ok'] and 'appended' in journal['detail']
    assert (ctx.workspace / 'docs' / '_journal' / 'charter-test.md').read_text(encoding='utf-8') == 'old'


def test_preview_refuses_an_owner_not_hired_on_the_project(ctx):
    body = preview(ctx, character='global:outsider').json
    assert body['ok'] is False
    assert [c['name'] for c in body['checks'] if not c['ok']] == ['owner']
    assert 'not hired' in next(c['detail'] for c in body['checks'] if c['name'] == 'owner')


def test_preview_blank_character_shows_the_pinned_default(ctx):
    body = preview(ctx, character='').json
    owner = next(c for c in body['checks'] if c['name'] == 'owner')
    assert body['ok'] and body['owner'] == 'global:owner' and 'pinned on save' in owner['detail']


@pytest.mark.parametrize('item', ['closed', 'empty', 'missing'])
def test_preview_refuses_unusable_charters(ctx, item):
    body = preview(ctx, steward_role=role(charter_item_id=item)).json
    assert body['ok'] is False
    assert body['charter'] is None
    assert [c['name'] for c in body['checks'] if not c['ok']] == ['charter']


def test_preview_reports_the_one_active_assignment_conflict(ctx):
    create_role(ctx, enabled=True)
    body = preview(ctx, steward_role=role(charter_item_id='other',
                                          journal_path='docs/_journal/other.md')).json
    assert body['ok'] is False
    bad = [c for c in body['checks'] if not c['ok']]
    assert [c['name'] for c in bad] == ['assignment']
    assert 'only one active steward assignment' in bad[0]['detail']


def test_preview_of_the_active_row_itself_is_not_a_conflict(ctx):
    row = create_role(ctx, enabled=True)
    assert preview(ctx, schedule_id=row['id']).json['ok'] is True


def test_preview_of_converting_a_task_schedule_clears_the_task(ctx):
    write_rows([legacy_task_row()])
    before = sr.SCHEDULES_PATH.read_bytes()
    body = preview(ctx, schedule_id='legacy1').json
    assert body['ok'] is True, body
    assert sr.SCHEDULES_PATH.read_bytes() == before


def test_preview_refuses_converting_a_legacy_steward_schedule(ctx):
    write_rows([legacy_task_row(steward=True)])
    body = preview(ctx, schedule_id='legacy1').json
    assert body['ok'] is False
    assert 'exactly one of task' in next(c['detail'] for c in body['checks'] if c['name'] == 'assignment')


@pytest.mark.parametrize('body,code', [
    ({}, 400), ({'project_id': 'p1'}, 400), ({'project_id': 'p1', 'steward_role': 'x'}, 400),
    ({'project_id': 'ghost', 'steward_role': {}}, 404),
    ({'project_id': 'p1', 'steward_role': {}, 'schedule_id': 'nope'}, 404)])
def test_preview_rejects_malformed_requests(ctx, body, code):
    assert ctx.client.post('/api/steward-role/preview', json=body).status_code == code


def test_preview_rejects_a_schedule_of_another_project(ctx):
    write_rows([legacy_task_row(project_id='p2')])
    res = ctx.client.post('/api/steward-role/preview', json={
        'project_id': 'p1', 'schedule_id': 'legacy1', 'steward_role': role()})
    assert res.status_code == 400


def test_runnable_now_needs_a_valid_draft_and_no_blockers(ctx, unblocked):
    assert preview(ctx).json['runnable_now'] is True
    assert preview(ctx, character='global:outsider').json['runnable_now'] is False


def test_valid_draft_with_blockers_is_ok_but_not_runnable(ctx, unblocked, monkeypatch):
    monkeypatch.delattr(sr, 'steward_role_lifecycle')
    body = preview(ctx).json
    assert body['ok'] is True
    assert [b['code'] for b in body['blockers']] == ['lifecycle_adapter_unavailable']
    assert body['runnable_now'] is False
