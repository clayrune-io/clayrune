"""Session billing phase 2: no real vault reads, processes, or paid calls."""
import json
import math
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask, request, jsonify

from mc import api_credits_budget as budget
from mc import api_credits_session as billing
from mc import launch_marker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, '_data_dir', tmp_path)
    monkeypatch.setattr(budget, '_now_fn', lambda: datetime(2026, 11, 9, tzinfo=timezone.utc))
    notices = []
    monkeypatch.setattr(budget, '_notifier', lambda *args: notices.append(args))
    from mc import secrets_store
    read = MagicMock(return_value='unit-test-key')
    monkeypatch.setattr(secrets_store, 'get_secret_value', read)
    budget.save_settings({'mode': 'on', 'budget_usd': 100, 'secret_name': 'unit-key'})
    return SimpleNamespace(read=read, notices=notices)


def session(**over):
    return {'project_id': 'p', 'session_id': 'sid', 'trigger_type': 'schedule',
            'trigger_id': 'sched', 'model': 'claude-haiku-5-5', **over}


def record(cost=1, caller='background'):
    budget.record(caller=caller, model='claude-haiku-5-5', usage={}, cost=cost, estimated=False)


@pytest.mark.parametrize('trigger,expected', [
    ('manual', False), ('', False), (None, False), ('schedule', True),
    ('steward', True), ('night_review', True), ('night-review', True),
    ('dispatch', True), ('workflow', True), ('hivemind_worker', True),
    ('hivemind_orchestrator', True), ('unknown', False)])
def test_eligibility_matrix(trigger, expected, isolated):
    s = session(trigger_type=trigger)
    env = billing.env_for_session(s, {'A': 'B'})
    assert (s['billing'] == 'credits') is expected
    assert ('ANTHROPIC_API_KEY' in env) is expected
    assert isolated.read.call_count == int(expected)


@pytest.mark.parametrize('provider', ['codex', 'gemini', 'qwen'])
def test_other_engines_never_get_key(provider, isolated):
    s = session(provider=provider)
    assert billing.env_for_session(s, {}) == {}
    isolated.read.assert_not_called()


def test_explicit_spawner_and_attended_override():
    assert billing.eligible(session(trigger_type='', _notify_session='parent'))
    assert not billing.eligible(session(trigger_type='manual', _notify_session='parent'))


def test_child_only_and_after_provider_scoping(isolated, monkeypatch):
    from mc import provider_env_scope as scope
    monkeypatch.setattr(scope, '_saved', {'goose': {'ANTHROPIC_API_KEY': 'foreign'}})
    monkeypatch.setattr(scope, '_ORIGINAL_ENV', {})
    base = {'ANTHROPIC_API_KEY': 'foreign', 'anthropic_auth_token': 'foreign-token', 'A': '1'}
    before = dict(os.environ)
    child = launch_marker.launch_env('schedule', base=base, session=session())
    assert child['ANTHROPIC_API_KEY'] == 'unit-test-key'
    assert 'anthropic_auth_token' not in child
    assert base['ANTHROPIC_API_KEY'] == 'foreign'
    assert dict(os.environ) == before
    assert 'ANTHROPIC_API_KEY' not in launch_marker.launch_env('manual', base=base, session=session(trigger_type='manual'))
    assert 'ANTHROPIC_API_KEY' not in launch_marker.launch_env('manual', base=base)
    isolated.read.assert_called_once_with('unit-key', consumer='api-credits-session', unattended=True)


@pytest.mark.parametrize('changes,reason', [
    ({'mode': 'off'}, 'mode_not_on'), ({'mode': 'measure'}, 'mode_not_on'),
    ({'secret_name': ''}, 'no_key_selected'), ({'budget_usd': 0}, 'no_budget'),
    ({'budget_usd': 3}, 'insufficient_session_reserve'),
    ({'session_reserve_usd': 91}, 'insufficient_session_reserve')])
def test_initial_gates(changes, reason, isolated):
    budget.save_settings({**budget.load_settings(), **changes})
    s = session()
    assert billing.env_for_session(s, {}) == {}
    assert s['billing_reason'] == reason
    isolated.read.assert_not_called()


def test_reserve_boundary_and_sticky_repeated_spawn():
    record(87)
    s = session()
    assert billing.env_for_session(s, {})['ANTHROPIC_API_KEY']
    record(1)
    assert billing.env_for_session(s, {})['ANTHROPIC_API_KEY']
    assert s['billing'] == 'credits'  # reserve only applies to first spawn
    restored = {**session(), **billing.snapshot(s)}
    assert billing.env_for_session(restored, {})['ANTHROPIC_API_KEY']
    record(2)
    assert billing.env_for_session(restored, {}) == {}
    assert restored['billing_reason'] == 'stop_point_reached'


def test_subscription_remains_sticky_after_settings_change():
    s = session(billing_preference='subscription')
    assert billing.env_for_session(s, {}) == {}
    assert s['billing_reason'] == 'schedule_subscription'
    s['billing_preference'] = 'auto'
    assert billing.env_for_session(s, {}) == {}


def test_unknown_model_vault_empty_and_vault_exception(isolated):
    s = session(model='unpriced-model')
    assert billing.env_for_session(s, {}) == {}
    assert s['billing_reason'] == 'price_unknown'
    for value in ('', None):
        isolated.read.return_value = value
        s = session()
        assert billing.env_for_session(s, {}) == {}
        assert s['billing_reason'] == 'vault_secret_empty'
    isolated.read.side_effect = RuntimeError('unit-test-key')
    s = session()
    assert billing.env_for_session(s, {}) == {}
    assert s['billing_reason'] == 'vault_unavailable'
    assert 'unit-test-key' not in json.dumps(billing.snapshot(s))


def test_live_child_must_respawn_after_halt_and_not_before():
    s = session()
    billing.env_for_session(s, {})
    assert not billing.needs_respawn(s)
    budget.halt('stopped', 'credit_low')
    assert billing.needs_respawn(s)
    assert s['billing'] == 'subscription'


def test_scheduler_reaps_only_idle_owned_child():
    proc = MagicMock()
    proc.poll.return_value = None
    s = session(billing='credits', proc=proc, process_alive=True)
    record(90)
    assert billing.end_idle_child_if_stopped(s)
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=10)
    assert not s['process_alive']


def test_failed_teardown_keeps_respawn_required_until_new_spawn():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = TimeoutError('not exited')
    s = session(billing='credits', proc=proc, process_alive=True)
    record(90)
    with pytest.raises(TimeoutError):
        billing.end_idle_child_if_stopped(s)
    assert billing.needs_respawn(s)
    proc.wait.side_effect = None
    assert billing.end_idle_child_if_stopped(s)
    assert billing.env_for_session(s, {}) == {}
    assert not billing.needs_respawn(s)


def event(total=.25, **over):
    return {'type': 'result', 'is_error': False, 'total_cost_usd': total,
            'model': 'claude-haiku-5-5', 'usage': {'input_tokens': 20,
            'output_tokens': 3, 'cache_read_input_tokens': 10}, **over}


def test_result_fixture_exact_cost_usage_and_per_turn_deltas():
    s = session(billing='credits')
    counters = {}
    assert not billing.record_result(s, event(.25), counters)
    assert not billing.record_result(s, event(.35), counters)
    st = budget.status()['cycle']
    assert st['session_spent_usd'] == pytest.approx(.35)
    assert st['background_spent_usd'] == 0
    ledger = json.loads(budget.ledger_path().read_text())
    entries = next(iter(ledger['cycles'].values()))['entries']
    assert len(entries) == 2
    assert entries[0]['caller'] == 'session:p:sched'
    assert entries[0]['input_tokens'] == 20
    assert entries[0]['cache_read_input_tokens'] == 10
    assert entries[1]['cost_usd'] == pytest.approx(.1)
    assert entries[0]['estimated'] is False


def test_resume_baseline_and_new_process_counter():
    s = session(billing='credits', trigger_type='dispatch')
    counters = {'cli_version': '2.1.277', 'cli_saved_total': 5.0}
    billing.record_result(s, event(5.25), counters)
    billing.record_result(s, event(.1), {})  # older CLI starts fresh per process
    assert budget.status()['cycle']['session_spent_usd'] == pytest.approx(.35)


def test_group_totals_survive_ledger_entry_cap(monkeypatch):
    monkeypatch.setattr(budget, 'ENTRIES_PER_CYCLE_CAP', 1)
    record(2, 'session:p:dispatch')
    record(3)
    record(4, 'session:p:dispatch')
    cycle = budget.status()['cycle']
    assert cycle['background_spent_usd'] == 3
    assert cycle['session_spent_usd'] == 6
    assert cycle['spent_usd'] == 9


@pytest.mark.parametrize('text,status,kind', [
    ('Your credit balance is too low', 400, 'credit_low'),
    ('You have reached your specified API usage limits', 400, 'spend_limit'),
    ('Unauthorized', 401, 'auth'), ('Invalid API key', 0, 'auth'),
    ('API Error: 401 authentication_error', 0, 'auth')])
def test_fallback_stops_once_replays_same_message_once(text, status, kind, isolated):
    s = session(billing='credits', status='running')
    billing.note_turn(s, 'exact original task')
    proc = MagicMock()
    s['proc'] = proc
    assert billing.record_result(s, event(0, is_error=True, result=text, status_code=status), {})
    assert s['billing'] == 'subscription'
    assert len(isolated.notices) == 1
    app = Flask(__name__)
    delivered = []
    def followup(pid):
        delivered.append((pid, request.get_json(), request.environ.get('clayrune.api_credits_retry')))
        assert billing.env_for_session(s, {'ANTHROPIC_API_KEY': 'old'}) == {}
        s['status'] = 'running'
        return jsonify(ok=True)
    routes = SimpleNamespace(_delegation_app=app, agent_followup=followup)
    assert billing.replay_after_exit(s, proc, routes)
    assert delivered[0][0] == 'p'
    assert delivered[0][1]['message'] == 'exact original task'
    assert delivered[0][2] is True
    assert not billing.record_result(s, event(0, is_error=True, result=text, status_code=status), {})
    assert not billing.replay_after_exit(s, proc, routes)
    assert len(delivered) == 1
    ledger = json.loads(budget.ledger_path().read_text())
    assert ledger['halt']['kind'] == kind


def test_non_billing_failure_and_success_discussion_never_replay():
    for msg in [event(result='credit balance is too low'), event(is_error=True, result='rate limit', status_code=429)]:
        s = session(billing='credits')
        assert not billing.record_result(s, msg, {})
        assert s['billing'] == 'credits'


def test_billing_fallback_survives_ledger_write_failure(monkeypatch):
    monkeypatch.setattr(budget, 'record', MagicMock(side_effect=OSError('disk')))
    monkeypatch.setattr(budget, 'halt', MagicMock(side_effect=OSError('disk')))
    s = session(billing='credits')
    assert billing.record_result(s, event(is_error=True, result='Credit balance is too low'), {})
    assert s['billing'] == 'subscription' and s['_api_credits_retry_pending']


@pytest.mark.parametrize('value', [None, 'bad', True, math.inf, math.nan, -1])
def test_missing_or_invalid_actual_cost_is_not_invented(value):
    s = session(billing='credits')
    billing.record_result(s, event(value), {})
    assert budget.status()['cycle']['calls'] == 0


def test_lowering_reserve_requires_human_proof():
    from mc.blueprints.api_credits_routes import _widens_spend
    old = budget.load_settings()
    assert _widens_spend(old, {**old, 'session_reserve_usd': 2})
    assert not _widens_spend(old, {**old, 'session_reserve_usd': 4})


@pytest.mark.parametrize('value', [-1, '3', True, math.inf, math.nan])
def test_bad_reserve_rejected(value):
    assert budget.validate_settings({'session_reserve_usd': value})[0] is None


def test_replay_failure_and_supersession_are_observable():
    s = session(billing='subscription', status='running', proc=object(),
                _api_credits_retry_pending=True, _api_credits_turn_message='task')
    assert not billing.replay_after_exit(s, object(), SimpleNamespace())
    assert s['_api_credits_retry_pending']
    assert not billing.replay_after_exit(s, s['proc'], SimpleNamespace(_delegation_app=None))
    assert s['status'] == 'error'
    assert 'replay failed' in s['log_lines'][0]
