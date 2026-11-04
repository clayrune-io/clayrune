"""No real CLIs, clocks, processes, credentials or vendor calls."""
import json
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import retry_budgets as budgets, retry_capabilities, state
from mc import qwen_retry_wait as qwen
from mc.launch_marker import launch_env


@pytest.mark.parametrize('trigger', [
    'dispatch', 'schedule', 'workflow', 'hivemind_worker', 'hivemind_orchestrator'])
def test_native_claude_caps_from_one_block(trigger, monkeypatch):
    monkeypatch.setitem(state.CONFIG, budgets.CONFIG_KEY,
                        {'max_wait': 2, 'max_usage_limit_wait': 7, 'usage_limit_recheck': 1})
    env = launch_env(trigger, base={})
    assert env['CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS'] == '120000'
    assert env['CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS'] == '420000'
    assert env['CLAUDE_CODE_RETRY_WATCHDOG_USAGE_LIMIT_RECHECK_MS'] == '60000'


@pytest.mark.parametrize('bad', [None, [], False, {'max_wait': 0}, {'max_wait': -1},
    {'max_wait': True}, {'max_wait': 2.5}, {'max_wait': '30'}, {'max_wait': float('inf')},
    {'max_wait': 2**53}, {'usage_limit_recheck': 181}, {'unknown': 10}])
def test_reject_malformed_block(bad):
    with pytest.raises(ValueError):
        budgets.validate_block(bad)


def test_defaults_and_safe_hand_edited_config_fallback():
    assert budgets.durations({}) == {
        'max_wait': 30, 'max_usage_limit_wait': 120, 'usage_limit_recheck': 5}
    assert budgets.durations({budgets.CONFIG_KEY: {'max_wait': False}}) == budgets.DEFAULTS


@pytest.mark.parametrize('engine', ['claude', 'qwen', 'codex', 'gemini', 'goose', 'aider', 'kiro', 'opencode'])
def test_attended_runs_do_not_gain_retry_settings(engine):
    env = {'PATH': 'test'}
    assert budgets.apply_env(engine, 'manual', env, {}) == {'PATH': 'test'}


def test_qwen_explicit_opt_out_is_preserved():
    env = {'QWEN_CODE_UNATTENDED_RETRY': '0'}
    assert budgets.apply_env('qwen', 'dispatch', env, {}) == env
    assert budgets.apply_env('qwen', 'dispatch', {}, {})['QWEN_CODE_UNATTENDED_RETRY'] == '1'


def session():
    return {'provider': 'qwen', 'trigger_type': 'dispatch', 'status': 'running',
            'proc': SimpleNamespace(poll=lambda: None), 'log_lines': [], 'project_id': 'p'}


def heartbeat(remaining=300):
    return f'[qwen-code] Waiting for API capacity... attempt 3, retry in {remaining}s'


def check(s, now, monkeypatch, kills):
    monkeypatch.setitem(state.CONFIG, budgets.CONFIG_KEY, {'max_wait': 1})
    return qwen.check_session(s, now, threading.RLock(), kills.append)


def test_long_productive_turn_or_silence_has_no_deadline(monkeypatch):
    s, kills = session(), []
    assert not check(s, 9999999999, monkeypatch, kills)
    assert s['status'] == 'running' and not kills


def test_observed_wait_cap_stops_only_current_owned_process(monkeypatch):
    s, kills = session(), []
    assert qwen.observe_line(s, heartbeat(), 10)
    assert s['last_output_time'] == 10
    assert not check(s, 69, monkeypatch, kills)
    assert check(s, 70, monkeypatch, kills)
    assert kills == [s['proc']]
    assert s['status'] == 'error' and 'retry cap' in s['log_lines'][-1]
    assert not check(s, 80, monkeypatch, kills)
    assert len(kills) == 1


def test_retry_heartbeats_do_not_reset_or_double_count_budget(monkeypatch):
    s, kills = session(), []
    qwen.observe_line(s, heartbeat(), 10)
    qwen.observe_line(s, heartbeat(270), 40)
    assert check(s, 70, monkeypatch, kills)


def test_request_time_after_sleep_is_not_wait_time(monkeypatch):
    s, kills = session(), []
    qwen.observe_line(s, heartbeat(20), 10)
    assert not check(s, 10000, monkeypatch, kills)
    qwen.observe_line(s, heartbeat(300), 10001)
    assert not check(s, 10040, monkeypatch, kills)
    assert check(s, 10041, monkeypatch, kills)  # 20 + 40 observed seconds


def test_success_ends_wait_while_keeping_cumulative_budget(monkeypatch):
    s, kills = session(), []
    qwen.observe_line(s, heartbeat(), 10)
    qwen.observe_line(s, json.dumps({'type': 'assistant'}), 30)
    assert not check(s, 10000, monkeypatch, kills)
    qwen.observe_line(s, heartbeat(), 10001)
    assert check(s, 10041, monkeypatch, kills)


@pytest.mark.parametrize('change', [
    {'trigger_type': 'manual'}, {'provider': 'codex'}, {'status': 'idle'},
    {'waiting_for_question': True}, {'waiting_for_plan_approval': True},
    {'guardian_state': 'recovering'},
    {'proc': SimpleNamespace(poll=lambda: None)}])
def test_replaced_process_and_human_gates_are_exempt(change, monkeypatch):
    s, kills = session(), []
    qwen.observe_line(s, heartbeat(), 10)
    s.update(change)
    assert not check(s, 80, monkeypatch, kills)
    assert not kills


def test_mere_quota_words_do_not_start_or_extend_wait():
    s = session()
    assert not qwen.observe_line(s, 'quota rate limit retry wait', 10)
    assert qwen.KEY not in s
    qwen.observe_line(s, heartbeat(20), 10)
    qwen.observe_line(s, 'retrying some other operation', 10000)
    assert s[qwen.KEY]['until'] == 30


def test_settings_payload_has_all_eight_engines_and_honest_partial_labels():
    rows = {r['engine']: r for r in retry_capabilities.settings_rows()}
    assert set(rows) == {'claude', 'codex', 'gemini', 'qwen', 'aider', 'goose', 'kiro', 'opencode'}
    assert rows['qwen']['status'] == 'PARTIAL'
    assert rows['codex']['status'] == 'UNSUPPORTED'
    assert 'unverified' in rows['goose']['detail']


@pytest.fixture
def settings_client(monkeypatch, tmp_path):
    from mc.blueprints import settings_routes as routes
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(routes, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(routes, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr(state, 'CONFIG', {})
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client(), routes


def test_api_rejects_invalid_durations_without_mutation(settings_client):
    client, _ = settings_client
    response = client.put('/api/config', json={budgets.CONFIG_KEY: {'max_wait': 0}})
    assert response.status_code == 400
    assert budgets.CONFIG_KEY not in state.CONFIG


def test_api_roundtrip_and_readonly_capabilities(settings_client):
    client, _ = settings_client
    assert client.get('/api/config').json[budgets.CONFIG_KEY] == budgets.DEFAULTS
    response = client.put('/api/config', json={budgets.CONFIG_KEY: {'max_wait': 60}})
    assert response.status_code == 200
    result = client.get('/api/config').json
    assert result[budgets.CONFIG_KEY]['max_wait'] == 60
    assert result[budgets.CONFIG_KEY]['max_usage_limit_wait'] == 120
    assert len(result['unattended_retry_capabilities']) == 8
    assert 'unattended_retry_capabilities' not in state.CONFIG


def test_api_still_requires_human_passcode(settings_client, monkeypatch):
    client, routes = settings_client
    monkeypatch.setattr(routes, '_require_human_passcode', lambda data: ({'error': 'human required'}, 403))
    assert client.put('/api/config', json={budgets.CONFIG_KEY: {'max_wait': 1}}).status_code == 403
    assert not state.CONFIG


def test_real_qwen_launch_boundaries_enable_persistent_retry_only_unattended(monkeypatch):
    from mc import agent_runtime as ar
    import io

    captured = []
    class NoThread:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
    def popen(cmd, **kwargs):
        captured.append(kwargs.get('env') or {})
        return SimpleNamespace(stdin=io.StringIO(), stdout=iter([]), poll=lambda: 0)
    monkeypatch.setattr(ar.subprocess, 'Popen', popen)
    monkeypatch.setattr(ar.threading, 'Thread', NoThread)
    monkeypatch.setattr(ar, '_inject_guardrail_env', lambda *args: None)
    monkeypatch.setattr(ar, '_inject_qwen_defaults_env', lambda *args: None)
    monkeypatch.setattr(ar, '_pin_qwen_windows_shell', lambda *args: None)
    # BUILTINS-5 fence probe needs a real Qwen CLI; this test is about retry env only.
    monkeypatch.setattr(ar.vendor_fence, 'check_launch', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar.vendor_fence, 'prepare_env', lambda vendor, session, env, **kwargs: env)
    monkeypatch.delenv('QWEN_CODE_UNATTENDED_RETRY', raising=False)
    runtime = ar.QwenRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: 'qwen')
    monkeypatch.setattr(runtime, '_settings_auth_env', lambda: {})
    for trigger in ('dispatch', 'manual'):
        s = {'trigger_type': trigger, 'log_lines': []}
        handle = runtime.dispatch(project_path='.', task='test', session_dict=s)
        runtime.write_followup(handle, 'next')
    assert [env.get('QWEN_CODE_UNATTENDED_RETRY') for env in captured] == ['1', '1', None, None]


def test_real_qwen_reader_consumes_vendor_heartbeat_without_assistant_text(monkeypatch):
    from mc import agent_runtime as ar
    import io
    s = session()
    proc = SimpleNamespace(stdout=io.StringIO(heartbeat() + '\n'), wait=lambda: 0,
                           poll=lambda: 0, pid=999999)
    s.update(proc=proc, session_id='test', project_id='p', mode='A')
    text = []
    handle = ar.SessionHandle(mc_session_id='test', provider='qwen', mode='A',
                              project_path='.', project_id='p', session_dict=s,
                              meta={'callbacks': {'on_text': lambda *args: text.append(args)}})
    ar._mode_a_reader(proc, handle, ar.QwenRuntime())
    assert qwen.KEY in s
    assert not text
    assert s['log_lines'][0].startswith('[qwen-code] Waiting')


def test_existing_guardian_calls_wait_enforcement_before_hang_recovery(monkeypatch):
    from mc.blueprints import agent_routes as routes
    s, kills = session(), []
    monkeypatch.setitem(state.CONFIG, budgets.CONFIG_KEY, {'max_wait': 1})
    monkeypatch.setattr(routes, 'get_manager', lambda pid: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(routes, '_kill_proc_background', kills.append)
    qwen.observe_line(s, heartbeat(), 10)
    routes._guardian_check_session('test', s, 70)
    assert kills == [s['proc']]
    assert s['status'] == 'error'


def test_api_cannot_promote_unsupported_capabilities(settings_client):
    client, _ = settings_client
    response = client.put('/api/config', json={
        'unattended_retry_capabilities': [{'engine': 'goose', 'status': 'NATIVE'}]})
    assert response.status_code == 200
    assert 'unattended_retry_capabilities' not in state.CONFIG
    goose = next(row for row in client.get('/api/config').json['unattended_retry_capabilities']
                 if row['engine'] == 'goose')
    assert goose['status'] == 'UNSUPPORTED'
