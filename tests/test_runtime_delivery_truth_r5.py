"""MC-1168/MC-1167 merge: typed refusals and delivery failures coexist."""
import pytest

from mc import agent_runtime, resume_cwd
from test_runtime_delivery_truth import app_env, revive_env  # noqa: F401
from test_runtime_delivery_truth_r2 import (
    real_reader_env, _early_terminal_proc, _result, _revive,
)  # noqa: F401


@pytest.mark.parametrize('endpoint', ['send', 'followup'])
def test_same_cold_request_preserves_cwd_refusal_then_delivery_failure(
        app_env, monkeypatch, endpoint):
    """Drive both HTTP handlers through the real non-Claude revival helper."""
    ar = app_env['ar']
    sid, native = 'mergedcodex1', 'original-native-thread'
    entry = {'session_id': sid, 'provider': 'codex',
             'provider_session_id': native, 'task': 'original conversation'}
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [entry])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_find_transcript_file', lambda *a: None)
    retained = []
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, fn: fn(retained))
    calls = []

    def dispatch(pid, message, **kwargs):
        calls.append((message, kwargs))
        assert kwargs.get('reuse_session_id') == sid, 'fresh fallback is forbidden'
        if len(calls) == 1:
            raise resume_cwd.ResumeCwdMissing('/missing/original/tree', native, 'codex')
        raise agent_runtime.PromptDeliveryError('BrokenPipeError: prompt rejected')

    monkeypatch.setattr(ar, '_dispatch_agent_internal', dispatch)
    payload = {'session_id': sid, 'message': 'the same addressed request'}
    response = app_env['client'].post(f'/api/project/p1/agent/{endpoint}', json=payload)
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body['code'] == 'resume_cwd_missing' and body['session_id'] == sid
    assert body['request_saved'] is True
    assert retained[0]['resume_refusals'][0]['message'] == payload['message']
    assert len(calls) == 1

    response = app_env['client'].post(f'/api/project/p1/agent/{endpoint}', json=payload)
    assert response.status_code == 502, response.get_json()
    body = response.get_json()
    assert body['ok'] is False and body['delivered'] is False
    assert body['session_id'] == sid and 'BrokenPipeError' in body['error']
    assert len(calls) == 2
    assert all(message == payload['message'] and kwargs['resume_id'] == native
               and kwargs['provider_override'] == 'codex' for message, kwargs in calls)


@pytest.mark.parametrize('fail', [False, True])
def test_credit_result_and_exit_replay_wait_for_delivery(real_reader_env, monkeypatch, fail):
    """The merged billing hooks are terminal work, including a billing stop."""
    env = real_reader_env
    observed = env['observed']
    proc = _early_terminal_proc(env, lines=[_result('billing-stop')], fail=fail)
    monkeypatch.setattr(proc, 'terminate', lambda: observed.append('terminate'), raising=False)

    def result(*args):
        observed.append('billing-result')
        return True

    def replay(*args):
        observed.append('billing-replay')
        return True

    monkeypatch.setattr(env['ar']._credits_session, 'record_result', result)
    monkeypatch.setattr(env['ar']._credits_session, 'replay_after_exit', replay)
    if fail:
        with pytest.raises(env['ar'].ReviveDeliveryError):
            _revive(env)
        assert observed == []
    else:
        _revive(env)
        assert observed == ['billing-result', 'terminate', 'billing-replay']
