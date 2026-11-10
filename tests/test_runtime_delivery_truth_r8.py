"""R8 writer audit: stale callbacks and startup snapshots cannot take a row."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import threading
import runpy

import pytest

from mc import agent_log_maintenance as maintenance
from mc.delivery_attempt import DeliveryAttempt, pending_fields
from test_runtime_completion_log import env, _dispatch, _log_rows  # noqa: F401
from test_runtime_delivery_truth import app_env, revive_env, _Proc, _Stdin  # noqa: F401
from test_runtime_delivery_truth_r4 import scheduled  # noqa: F401


@pytest.mark.parametrize('writer', ['pending-identity', 'native-init', 'start-failure'])
@pytest.mark.parametrize('token', [None, '', 'successor'])
def test_runtime_metadata_and_failure_writers_refuse_unowned_rows(env, writer, token):
    sid, handle = _dispatch(env)
    session, ar = handle.session_dict, env['ar']
    session.update(provider_session_id='late-native', log_lines=['late failure'])
    ar._update_agent_log('proj1', lambda rows: rows[0].update(delivery_attempt_id=token))
    expected = _log_rows(env)
    if writer == 'pending-identity':
        ar._log_agent_dispatch_pending(session, identity_only=True)
    elif writer == 'native-init':
        ar._runtime_note_init(None, session)
    else:
        ar._persist_runtime_start_failure(session)
    assert _log_rows(env) == expected


@pytest.mark.parametrize('writer', ['native-init', 'start-failure'])
def test_writer_cannot_borrow_a_new_token_from_the_same_live_dictionary(env, monkeypatch, writer):
    sid, handle = _dispatch(env)
    ar, session = env['ar'], handle.session_dict
    session.update(provider_session_id='old-native', log_lines=['old failure'])
    original = ar._update_agent_log
    expected = []

    def update(pid, mutate):
        if mutate.__name__ in ('backfill', 'failed'):
            # Neither callback holds the manager or leaf lock here. A new
            # prelaunch claim can land before the callback takes the leaf.
            DeliveryAttempt.capture(session)
            expected[:] = _log_rows(env)
        return original(pid, mutate)

    monkeypatch.setattr(ar, '_update_agent_log', update)
    if writer == 'native-init':
        ar._runtime_note_init(None, session)
    else:
        ar._persist_runtime_start_failure(session)
    assert _log_rows(env) == expected


def test_same_dictionary_reclaimed_while_callback_waits_is_not_completed(env, monkeypatch):
    sid, handle = _dispatch(env)
    ar = env['ar']
    entered, release = threading.Event(), threading.Event()

    def pause(*args):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(ar._delegation_settlement, 'note_runtime_exit', pause)
    worker = threading.Thread(target=lambda: env['runtime'].run_turn(handle, ['old answer']))
    worker.start()
    try:
        assert entered.wait(3)
        DeliveryAttempt.capture(handle.session_dict)
        expected = _log_rows(env)
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert _log_rows(env) == expected
    finally:
        release.set()
        worker.join(5)


@pytest.mark.parametrize('token', [None, ''])
def test_completion_cannot_adopt_untagged_current_row(env, token):
    sid, handle = _dispatch(env)
    ar = env['ar']
    ar._update_agent_log('proj1', lambda rows: rows[0].update(delivery_attempt_id=token))
    expected = _log_rows(env)
    env['runtime'].run_turn(handle, ['unowned answer'])
    assert _log_rows(env) == expected


@pytest.mark.parametrize('change', ['new-attempt', 'same-attempt-completed', 'removed'])
def test_startup_snapshot_cannot_undo_intervening_run_activity(env, change):
    sid, handle = _dispatch(env)
    before = maintenance.snapshot('proj1')
    after = deepcopy(before)
    after[0].update(status='interrupted', summary='stale maintenance', scribed=True)
    if change == 'new-attempt':
        DeliveryAttempt.capture(handle.session_dict)
    elif change == 'same-attempt-completed':
        env['runtime'].run_turn(handle, ['new answer'])
    else:
        env['ar']._update_agent_log('proj1', lambda rows: rows.clear())
    expected = _log_rows(env)
    maintenance.commit('proj1', before, after)
    assert _log_rows(env) == expected


def test_legacy_startup_claim_and_revival_have_distinct_owners(env):
    ar = env['ar']
    ar._save_agent_log('proj1', [{'session_id': 'legacy', 'status': 'in_progress'}])
    before = maintenance.snapshot('proj1')
    assert before[0]['delivery_attempt_id']
    after = deepcopy(before)
    after[0]['status'] = 'interrupted'
    maintenance.commit('proj1', before, after)
    assert _log_rows(env)[0]['status'] == 'interrupted'
    next_attempt = DeliveryAttempt.capture({'session_id': 'legacy', 'project_id': 'proj1'})
    assert next_attempt.attempt_id != before[0]['delivery_attempt_id']
    maintenance.commit('proj1', before, after)
    assert _log_rows(env)[0]['delivery_attempt_id'] == next_attempt.attempt_id


def test_transcript_import_does_not_duplicate_concurrently_claimed_native_id(env):
    before = maintenance.snapshot('proj1')
    after = [{'session_id': '', 'claude_session_id': 'native', 'status': 'interrupted'}]
    env['ar']._save_agent_log('proj1', [{'session_id': 'live', 'claude_session_id': 'native',
                                      'status': 'in_progress', **pending_fields({})}])
    expected = _log_rows(env)
    maintenance.commit('proj1', before, after)
    assert _log_rows(env) == expected


def test_live_scheduler_claim_publishes_pending_task_and_records_pipe_failure(scheduled, monkeypatch):
    e = scheduled
    written = threading.Event()
    ar = e['ar']
    original = e['sr']._delivery_failure.record_failure

    def record(*args):
        try:
            original(*args)
        finally:
            written.set()

    monkeypatch.setattr(e['sr']._delivery_failure, 'record_failure', record)
    monkeypatch.setattr(e['sr'], '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(e['sr'], '_record_usage_breakdown_turn_started', lambda s: None)
    session = {'session_id': e['sid'], 'project_id': 'p1', 'mode': 'B', 'status': 'idle',
               'process_alive': True, 'proc': _Proc(_Stdin(fail_on='write')),
               'log_lines': [], 'trigger_id': 's1'}
    e['sessions'][e['sid']] = session
    prior = DeliveryAttempt.capture(session)
    assert e['sr']._scheduled_continue(e['project'], 'p1', e['sid'], 'new task') == 'appended'
    assert written.wait(3)
    assert session['_delivery_attempt_id'] != prior.attempt_id
    assert e['history'][0]['delivery_attempt_id'] == session['_delivery_attempt_id']
    assert e['history'][0]['task'] == 'new task'
    assert e['history'][0]['status'] == 'error'


def test_superseded_reviewer_task_writer_refuses_launch(env):
    from mc.hivemind_integration_cycle import _ReviewLaunchAPI
    sid, handle = _dispatch(env)
    api = _ReviewLaunchAPI(SimpleNamespace(_hm_runtime_dispatch=lambda **kw: pytest.fail('launched')),
                           Path('brief.md'), 'digest')
    predecessor = dict(handle.session_dict)
    DeliveryAttempt.capture(handle.session_dict)
    expected = _log_rows(env)
    with pytest.raises(RuntimeError, match='superseded'):
        api._hm_runtime_dispatch(project_id='proj1', session_id=sid,
                                 session_dict=predecessor, task='review')
    assert _log_rows(env) == expected


@pytest.mark.parametrize('tool', ['repair-agent-log-personas', 'backfill-human-joined'])
def test_offline_repair_cannot_save_its_old_snapshot_over_a_new_attempt(env, monkeypatch, tool):
    sid, handle = _dispatch(env, trigger_type='schedule', trigger_id='s1')
    ar = env['ar']
    ar._update_agent_log('proj1', lambda rows: rows[0].update(
        character=None, claude_session_id='native', human_joined=False))
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools' / (tool + '.py')))
    repair = module['repair' if tool.startswith('repair') else 'run']
    monkeypatch.setitem(repair.__globals__, 'project_path_for', lambda *a: 'project')
    expected = []

    def takeover(*args):
        DeliveryAttempt.capture(handle.session_dict)
        ar._update_agent_log('proj1', lambda rows: rows[0].update(
            task='successor task', status='completed', summary='successor answer'))
        expected[:] = _log_rows(env)
        return {'scope': 'global', 'name': 'persona'} if tool.startswith('repair') else ['human text']

    if tool.startswith('repair'):
        monkeypatch.setattr(module['_memory'], 'persona_ref_for_session', takeover)
        repair(ar.DATA_DIR, 'proj1', True)
    else:
        monkeypatch.setattr(module['_memory'], '_find_transcript_file', lambda *a: Path('transcript'))
        monkeypatch.setitem(repair.__globals__, 'genuine_human_turns', takeover)
        repair(ar.DATA_DIR, 'proj1', '', True)
    assert expected
    assert _log_rows(env) == expected


def test_plan_reference_scrub_preserves_an_intervening_claim(env, monkeypatch, tmp_path):
    from flask import Flask
    sid, handle = _dispatch(env)
    ar = env['ar']
    plans = tmp_path / '.claude' / 'plans'
    plans.mkdir(parents=True)
    plan = plans / 'own-test-plan.md'
    plan.write_text('test plan', encoding='utf-8')
    ar._update_agent_log('proj1', lambda rows: rows[0].update(plan_file=str(plan), plan_files=[str(plan)]))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    original = maintenance.commit
    expected = []

    def takeover(pid, before, after):
        DeliveryAttempt.capture(handle.session_dict)
        expected[:] = _log_rows(env)
        original(pid, before, after)

    monkeypatch.setattr(maintenance, 'commit', takeover)
    with Flask(__name__).test_request_context(json={'paths': [str(plan)]}):
        response = ar.delete_plans()
    assert response.get_json()['deleted'] == 1
    assert not plan.exists()
    assert _log_rows(env) == expected


def test_server_and_offline_writer_share_the_transaction(env):
    from mc.agent_log_file import transaction
    sid, handle = _dispatch(env)
    ar = env['ar']
    entered, finished = threading.Event(), threading.Event()

    def write():
        entered.set()
        ar._update_agent_log('proj1', lambda rows: rows[0].update(summary='serialized update'))
        finished.set()

    with transaction(ar._agent_log_path('proj1')):
        worker = threading.Thread(target=write)
        worker.start()
        assert entered.wait(2)
        assert not finished.wait(0.1)
    worker.join(5)
    assert finished.is_set() and not worker.is_alive()
    assert _log_rows(env)[0]['summary'] == 'serialized update'
