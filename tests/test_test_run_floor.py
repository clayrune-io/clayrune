"""Floor acceptance through canonical storage and the real Flask endpoint."""
from __future__ import annotations

import copy
import json
from typing import Any

from flask import Flask
import pytest

from mc import state, test_run_floor as projection
from mc.blueprints import floor_routes as floor, test_run_routes as routes
from mc.test_run_store import OUTCOMES, TERMINAL_STATES, TestRunStore as RunStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    sessions = {}
    projects = [{'id': 'a', 'name': 'Alpha'}, {'id': 'b', 'name': 'Beta'}]
    monkeypatch.setattr(state, 'CONFIG', {projection.FLAG: True})
    monkeypatch.setattr(routes, '_storage', lambda: store)
    floor.wire(agent_sessions_ref=sessions, load_projects_fn=lambda: projects,
               list_characters_fn=lambda **kw: [])
    monkeypatch.setattr(floor, '_figure_subagents', lambda *args: [])
    monkeypatch.setattr(floor._delegation_waiting, 'waiting_by_parent', lambda: {})
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(floor.bp)
    return store, sessions, projects, app.test_client()


def owner(env, sid='s', pid='a', **extra) -> dict[str, Any]:
    value: dict[str, Any] = dict(session_id=sid, project_id=pid, status='idle',
                 activity_state='writing', task='Original task', **extra)
    env[1][sid] = value
    return value


def run(env, *, pid='a', sid: str | None = 's', total: int | None = 10, completed=2, runner='pytest',
        unit='test', capture='complete', parent=None, at=1):
    store = env[0]
    snapshot = store.create(pid, observed_at=at, owner_session_id=sid,
                            runner=runner, unit=unit, parent_run_id=parent,
                            cwd='/private/worktree', process_identity='private-process',
                            display_label='Untrusted <script>')
    sequence = 0

    def emit(kind, payload):
        nonlocal snapshot, sequence
        sequence += 1
        snapshot = store.apply(pid, snapshot['run_id'], dict(
            schema=1, run_id=snapshot['run_id'], producer_id='fixture',
            producer_seq=sequence, observed_at=at + sequence, type=kind, payload=payload))

    if total is not None:
        emit('collection', dict(selected_total=total, revision=1))
    for index in range(completed):
        emit('unit_report', dict(unit_id=f'case-{index}', attempt=1, phase='call',
                                 outcome='pass', revision=1, final=True))
    if capture != 'complete':
        emit('diagnostic', dict(code='reporter_unavailable', capture=capture))
    return snapshot


def board(env):
    response = env[3].get('/api/floor')
    assert response.status_code == 200
    return response.get_json()


def room(env, pid='a'):
    return next(r for r in board(env)['rooms'] if r['id'] == pid)


def test_idle_owner_has_progress_without_model_activity(env):
    session = owner(env)
    before = copy.deepcopy(session)
    snapshot = run(env)
    figure = room(env)['figures'][0]
    assert (figure['state'], figure['activity'], figure['task']) == ('idle', '', 'Original task')
    assert figure['test_runs']['percent'] == 20
    assert figure['test_runs']['runs'][0]['run_id'] == snapshot['run_id']
    assert session == before


@pytest.mark.parametrize('status', ['completed', 'error', 'stopped', 'exited'])
def test_ended_owner_is_retained_only_until_its_run_finishes(env, status):
    session = owner(env)
    session['status'] = status
    snapshot = run(env)
    assert room(env)['figures'][0]['state'] == 'idle'
    env[0].finish('a', snapshot['run_id'], state='failed', exit_code=1, observed_at=100)
    result = board(env)
    assert result['rooms'] == []
    assert result['counts']['figures'] == 0
    assert {r['id'] for r in result['quiet']} == {'a', 'b'}
    assert env[0].get('a', snapshot['run_id'])['state'] == 'failed'


@pytest.mark.parametrize('wait,reason', [('waiting_for_question', 'question'),
                                       ('waiting_for_plan_approval', 'plan')])
def test_asking_still_outranks_testing_and_working(env, wait, reason):
    owner(env, 'idle')
    asking = owner(env, 'ask', 'b')
    asking[wait] = True
    owner(env, 'work')['status'] = 'running'
    run(env, sid='idle')
    run(env, sid='ask', pid='b')
    result = board(env)
    assert [r['id'] for r in result['rooms']] == ['b', 'a']
    figure = result['rooms'][0]['figures'][0]
    assert (figure['state'], figure['reason']) == ('asking', reason)
    assert figure['test_runs']['active_count'] == 1


def test_active_turn_keeps_original_tool_activity(env):
    session = owner(env)
    session.update(status='running', activity_state='tool')
    run(env)
    figure = room(env)['figures'][0]
    assert (figure['state'], figure['activity']) == ('working', 'tool')


def test_compatible_totals_are_weighted_and_counts_stay_exact(env):
    owner(env)
    run(env, total=10, completed=2)
    run(env, total=90, completed=9)
    summary = room(env)['figures'][0]['test_runs']
    assert (summary['active_count'], summary['completed'], summary['selected_total'], summary['percent']) == (2, 11, 100, 11)
    assert summary['counts'] == {k: 11 if k == 'pass' else 0 for k in OUTCOMES}
    assert len(summary['runs']) == 2


@pytest.mark.parametrize('other', [dict(unit='script'), dict(runner='node'),
                                  dict(total=None), dict(capture='partial'),
                                  dict(capture='lost')])
def test_incompatible_or_incomplete_concurrent_runs_have_no_aggregate(env, other):
    owner(env)
    run(env)
    run(env, **other)
    summary = room(env)['test_runs']
    assert summary['phase'] == 'multiple'
    assert summary['active_count'] == 2
    assert summary['percent'] is summary['completed'] is summary['selected_total'] is summary['counts'] is None
    assert len(summary['runs']) == 2


@pytest.mark.parametrize('runner,phase', [('pytest', 'collecting'), ('generic', 'unknown')])
def test_unknown_totals_do_not_invent_counts_or_percentage(env, runner, phase):
    run(env, sid=None, total=None, completed=0, runner=runner)
    summary = room(env)['test_runs']
    assert summary['phase'] == phase
    assert summary['percent'] is None and summary['selected_total'] is None


def test_zero_denominator_and_pending_exit_do_not_become_success(env):
    empty = run(env, sid=None, total=0, completed=0)
    assert room(env)['test_runs']['percent'] is None
    env[0].finish('a', empty['run_id'], state='no_tests', exit_code=5, observed_at=20)
    run(env, sid=None, total=2, completed=2)
    summary = room(env)['test_runs']
    assert (summary['phase'], summary['percent']) == ('finalizing', 100)
    assert summary['active_count'] == 1
    assert 'state' not in summary  # Test completion never declares process success.


def test_ownerless_and_absent_owners_are_project_work_without_fake_figures(env):
    run(env, sid=None)
    absent = run(env, sid='missing')
    result = room(env)
    assert result['figures'] == []
    assert result['test_runs']['ownerless_count'] == 1
    assert result['test_runs']['unavailable_owner_count'] == 1
    assert absent['owner_session_id'] == 'missing'
    assert env[1] == {}
    assert board(env)['counts']['figures'] == 0


def test_absent_owner_does_not_transfer_to_another_chat(env):
    owner(env, 'other')
    run(env, sid='missing')
    result = room(env)
    assert result['figures'][0]['test_runs']['active_count'] == 0
    assert result['test_runs']['unavailable_owner_count'] == 1


def test_owner_with_wrong_project_is_not_retained_or_attached(env):
    owner(env, 's', 'b')['status'] = 'completed'
    run(env)
    result = board(env)
    assert result['counts']['figures'] == 0
    assert result['rooms'][0]['id'] == 'a'
    assert result['rooms'][0]['test_runs']['unavailable_owner_count'] == 1


def test_project_filter_rejects_cross_project_snapshots(env):
    a, b = run(env), run(env, pid='b')
    projected = projection.project('a', [a, b], {})
    assert [r['run_id'] for r in projected['summary']['runs']] == [a['run_id']]


@pytest.mark.parametrize('terminal', sorted(TERMINAL_STATES))
def test_every_terminal_state_removes_the_testing_pin(env, terminal):
    snapshot = run(env, sid=None)
    code = 0 if terminal == 'passed' else 5 if terminal == 'no_tests' else 1
    env[0].finish('a', snapshot['run_id'], state=terminal, exit_code=code, observed_at=100)
    assert board(env)['rooms'] == []
    assert env[0].get('a', snapshot['run_id'])['state'] == terminal


@pytest.mark.parametrize('flag', [False, None, 1, 'true'])
def test_flag_off_does_no_storage_reads_and_preserves_payload(env, monkeypatch, flag):
    owner(env)
    run(env)
    monkeypatch.setitem(state.CONFIG, projection.FLAG, flag)
    monkeypatch.setattr(routes, '_storage', lambda: pytest.fail('dark feature read storage'))
    result = board(env)
    assert 'test_runs' not in result['rooms'][0]
    assert 'test_runs' not in result['rooms'][0]['figures'][0]
    assert 'test_runs' not in result['quiet'][0]
    assert result['poll_seconds'] == 5


@pytest.mark.parametrize('privacy', [dict(incognito=True), dict(_incognito=True),
                                    dict(_codex_unattended_sandbox=True), dict(housekeeping=True)])
def test_restricted_or_hidden_owner_cannot_expose_test_data(env, privacy):
    owner(env, **privacy)['status'] = 'completed'
    run(env)
    assert board(env)['rooms'] == []


def test_hivemind_housekeeping_is_displayable(env):
    owner(env, housekeeping=True, hivemind_id='hm_test')['status'] = 'completed'
    run(env)
    assert room(env)['figures'][0]['test_runs']['active_count'] == 1


def test_private_projects_are_not_read(env, monkeypatch):
    env[2].extend([dict(id='_incognito', name='Private'),
                   dict(id='secret', _is_incognito_project=True)])
    called = []
    original = env[0].list_runs

    def listing(pid, **kw):
        called.append(pid)
        return original(pid, **kw)

    monkeypatch.setattr(env[0], 'list_runs', listing)
    board(env)
    assert called == ['a', 'b']


def test_manifest_and_active_children_do_not_double_count_units(env):
    owner(env)
    parent = run(env, runner='manifest', unit='script', total=4, completed=1)
    run(env, parent=parent['run_id'], total=100, completed=50)
    summary = room(env)['test_runs']
    assert (summary['active_count'], summary['unit'], summary['percent']) == (1, 'script', 25)
    assert summary['runs'][0]['run_id'] == parent['run_id']


def test_active_child_remains_visible_after_parent_ends(env):
    parent = run(env, sid=None, runner='manifest', unit='script', total=4, completed=1)
    child = run(env, sid=None, parent=parent['run_id'], total=10, completed=5)
    env[0].finish('a', parent['run_id'], state='failed', exit_code=1, observed_at=100)
    summary = room(env)['test_runs']
    assert summary['percent'] == 50
    assert summary['runs'][0]['run_id'] == child['run_id']


def test_same_name_different_runs_and_sessions_remain_separate(env):
    owner(env, 'one')
    owner(env, 'two')
    first, second = run(env, sid='one'), run(env, sid='two')
    figures = room(env)['figures']
    assert {f['test_runs']['runs'][0]['run_id'] for f in figures} == {first['run_id'], second['run_id']}


def test_older_active_run_is_found_after_more_than_one_page_of_history(env):
    old = run(env, sid=None, total=None, completed=0, at=0)
    for at in range(1, 102):
        snapshot = run(env, sid=None, total=None, completed=0, at=at)
        env[0].finish('a', snapshot['run_id'], state='passed', exit_code=0, observed_at=200)
    assert room(env)['test_runs']['runs'][0]['run_id'] == old['run_id']


def test_detail_cap_discloses_omissions_without_capping_totals(env):
    template = run(env, sid=None)
    snapshots = [{**template, 'run_id': str(i)} for i in range(105)]
    summary = projection.project('a', snapshots, {})['summary']
    assert summary['active_count'] == 105
    assert summary['selected_total'] == 1050 and summary['completed'] == 210
    assert len(summary['runs']) == 100 and summary['runs_omitted'] == 5


def test_projection_is_pure_and_does_not_expose_private_metadata(env):
    snapshot = run(env)
    before = copy.deepcopy(snapshot)
    output = projection.project('a', [snapshot], {})
    assert snapshot == before
    text = json.dumps(output)
    for secret in ('/private/worktree', 'private-process', 'Untrusted', 'display_label', 'cwd', 'process_identity'):
        assert secret not in text
    output['summary']['runs'][0]['counts']['pass'] = 999
    assert snapshot == before, 'mutating a projection must not mutate source snapshots'


def test_corrupt_storage_is_unavailable_and_does_not_break_other_rooms(env, monkeypatch):
    broken = run(env, sid=None)
    run(env, pid='b', sid=None)
    env[0]._path('a', broken['run_id']).write_text('broken', encoding='utf-8')
    messages = []
    monkeypatch.setattr(projection, '_log', lambda msg, **kw: messages.append(msg))
    result = board(env)
    rooms = {r['id']: r for r in result['rooms']}
    assert rooms['a']['test_runs']['available'] is False
    assert rooms['a']['test_runs']['error'] == 'test_runs_unavailable'
    assert rooms['b']['test_runs']['active_count'] == 1
    assert len(messages) == 1 and 'project read failed' in messages[0]


def test_testing_rooms_sort_with_work_above_idle_and_below_questions(env):
    env[2].extend([dict(id='z', name='Zulu'), dict(id='c', name='Charlie')])
    owner(env)
    owner(env, 'question', 'z')['waiting_for_question'] = True
    owner(env, 'idle', 'b')
    run(env, pid='c', sid=None)
    assert [r['id'] for r in board(env)['rooms']] == ['z', 'c', 'a', 'b']


def test_reporter_finished_cannot_remove_an_active_testing_figure(env):
    owner(env)['status'] = 'completed'
    snapshot = run(env)
    env[0].apply('a', snapshot['run_id'], dict(
        schema=1, run_id=snapshot['run_id'], producer_id='finish-reporter', producer_seq=1,
        observed_at=99, type='run_finished', payload=dict(state='passed', exit_code=0)))
    assert room(env)['figures'][0]['test_runs']['active_count'] == 1


def test_total_revision_changes_recalculate_progress_without_clamping(env):
    run_snapshot = run(env, sid=None, total=10, completed=2)
    assert room(env)['test_runs']['percent'] == 20
    env[0].apply('a', run_snapshot['run_id'], dict(
        schema=1, run_id=run_snapshot['run_id'], producer_id='recollection', producer_seq=1,
        observed_at=99, type='collection', payload=dict(selected_total=20, revision=2)))
    summary = room(env)['test_runs']
    assert summary['percent'] == 10
    assert summary['total_recalculated'] is True
    assert summary['runs'][0]['total_revision'] == 2


@pytest.mark.parametrize('changes', [dict(selected_total=True), dict(selected_total=-1),
                                    dict(completed=20), dict(completed=True)])
def test_invalid_measurements_cannot_mint_a_percentage(env, changes):
    snapshot = run(env)
    summary = projection.project('a', [{**snapshot, **changes}], {})['summary']
    assert summary['percent'] is None and summary['selected_total'] is None


def test_active_work_in_another_room_is_not_rewritten_on_terminal_cleanup(env):
    owner(env, 's', 'a')['status'] = 'completed'
    owner(env, 'other', 'b')['status'] = 'running'
    snapshot = run(env)
    env[0].finish('a', snapshot['run_id'], state='timeout', exit_code=1, observed_at=100)
    result = board(env)
    assert [r['id'] for r in result['rooms']] == ['b']
    assert result['rooms'][0]['figures'][0]['state'] == 'working'
