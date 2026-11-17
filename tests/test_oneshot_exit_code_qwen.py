"""Q1 (MC-1179): the Qwen `oneshot` never read the exit code.

`run_text_transform` raises only when `oneshot` returns None, so a non-zero
exit, an `is_error` result or an aborted run came back as a completed
transform, and with no assistant text the whole raw JSON event stream was
returned as the answer. These tests use a fake `subprocess.run`; no Qwen CLI
is launched, so the real CLI's failure output is NOT exercised here.
"""
import json
import subprocess

import pytest

from mc import agent_runtime as ar
from mc import oneshot_exit_check as oec


def _cp(rc, out='', err=''):
    return subprocess.CompletedProcess(args=['x'], returncode=rc, stdout=out, stderr=err)


def _qwen(monkeypatch, result):
    rt = ar.QwenRuntime()
    monkeypatch.setattr(rt, 'resolve_binary', lambda: 'qwen')
    monkeypatch.setattr(rt, '_transform_argv', lambda **k: ['qwen'])
    monkeypatch.setattr(rt, '_settings_auth_env', lambda: {})

    def fake_run(*a, **k):
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(ar.subprocess, 'run', fake_run)
    return rt


INIT = json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's'})
ASSISTANT = json.dumps({'type': 'assistant', 'session_id': 's',
                        'message': {'content': [{'type': 'text', 'text': 'the answer'}]}})
ERR_RESULT = json.dumps({'type': 'result', 'is_error': True,
                         'error': {'message': 'quota exceeded'}})
OK_RESULT = json.dumps({'type': 'result', 'is_error': False})
LIBUV = ('Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), '
         'file src\\win\\async.c, line 76')


def test_error_result_stream_is_never_returned_as_the_answer(monkeypatch):
    rt = _qwen(monkeypatch, _cp(1, '\n'.join([INIT, ERR_RESULT]), 'API Error: 429 quota'))
    assert rt.oneshot(prompt='p') is None
    assert 'rc=1' in rt.last_error and '429 quota' in rt.last_error


def test_nonzero_exit_with_partial_text_is_a_failure(monkeypatch):
    rt = _qwen(monkeypatch, _cp(55, '\n'.join([INIT, ASSISTANT]), 'FatalBudgetExceededError'))
    assert rt.oneshot(prompt='p') is None
    assert 'rc=55' in rt.last_error and 'FatalBudgetExceededError' in rt.last_error


def test_error_result_with_exit_zero_is_a_failure(monkeypatch):
    rt = _qwen(monkeypatch, _cp(0, '\n'.join([INIT, ERR_RESULT])))
    assert rt.oneshot(prompt='p') is None
    assert 'quota exceeded' in rt.last_error


def test_plain_text_stdout_is_not_an_answer(monkeypatch):
    rt = _qwen(monkeypatch, _cp(0, 'Error: something went wrong'))
    assert rt.oneshot(prompt='p') is None
    assert rt.last_error == 'empty output'


def test_clean_run_still_returns_the_answer(monkeypatch):
    rt = _qwen(monkeypatch, _cp(0, '\n'.join([INIT, ASSISTANT, OK_RESULT])))
    rt.last_error = 'stale reason from an earlier call'
    r = rt.oneshot(prompt='p')
    assert r is not None and r.text == 'the answer'
    assert rt.last_error == ''


def test_known_teardown_crash_after_a_full_reply_is_still_an_answer(monkeypatch):
    rt = _qwen(monkeypatch, _cp(3221226505, '\n'.join([INIT, ASSISTANT, OK_RESULT]), LIBUV))
    r = rt.oneshot(prompt='p')
    assert r is not None and r.text == 'the answer'


def test_teardown_crash_signature_with_an_error_result_is_a_failure(monkeypatch):
    rt = _qwen(monkeypatch, _cp(3221226505, '\n'.join([INIT, ERR_RESULT]), LIBUV))
    assert rt.oneshot(prompt='p') is None


def test_timeout_sets_a_timeout_reason(monkeypatch):
    rt = _qwen(monkeypatch, subprocess.TimeoutExpired(['qwen'], 180))
    assert rt.oneshot(prompt='p') is None
    assert rt.last_error.startswith('timeout')


def test_spawn_failure_is_recorded_not_swallowed(monkeypatch):
    rt = _qwen(monkeypatch, OSError('boom'))
    assert rt.oneshot(prompt='p') is None
    assert 'spawn failed' in rt.last_error and 'boom' in rt.last_error


def test_failed_transform_raises_transform_failure_with_the_reason(monkeypatch):
    """Through the shared `run_text_transform` contract: None from oneshot
    becomes a TransformFailure carrying stderr."""
    rt = _qwen(monkeypatch, _cp(1, '', 'auth expired'))
    monkeypatch.setitem(ar._RUNTIMES, 'qwen', rt)
    monkeypatch.setattr(ar, '_authorize_text_transform', lambda *a, **k: None)
    with pytest.raises(ar.TransformFailure) as ei:
        ar.run_text_transform('qwen', prompt='p')
    assert 'auth expired' in str(ei.value.detail)


def test_helper_unit():
    assert oec.failure_detail(0, '', 'x') is None
    assert oec.failure_detail(1, 'e', 'x') == 'rc=1: e'
    assert oec.failure_detail(1, 'e', 'x', benign_exit=True) is None
    assert oec.failure_detail(0, '', '') == 'empty output'
    assert oec.failure_detail(0, '', 'x', error_text='bad').startswith('error: bad')
    assert oec.is_qwen_teardown_crash('x', LIBUV)
    assert not oec.is_qwen_teardown_crash('x', None, 'rate limit')
