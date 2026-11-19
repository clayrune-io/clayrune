"""G1 (MC-1179): the Gemini `oneshot` never read the exit code.

`run_text_transform` raises only when `oneshot` returns None, so a Gemini
transform that exited non-zero came back as `''` or a fragment, as a completed
transform, with `last_error` left holding another call's reason. These tests
use a fake `subprocess.run`; no Gemini CLI is launched, so the real CLI's
failure output is NOT exercised here.
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from mc import agent_runtime as ar


def _cp(rc, out='', err=''):
    return subprocess.CompletedProcess(args=['x'], returncode=rc, stdout=out, stderr=err)


def _gemini(monkeypatch, result):
    rt = ar.GeminiRuntime()
    monkeypatch.setattr(rt, 'resolve_binary', lambda: 'gemini')
    monkeypatch.setattr(rt, '_transform_argv', lambda **k: ['gemini'])
    monkeypatch.setattr(rt, '_transform_env', lambda: {})

    def fake_run(*a, **k):
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(ar.subprocess, 'run', fake_run)
    return rt


def test_nonzero_exit_with_empty_stdout_is_a_failure_not_an_empty_answer(monkeypatch):
    rt = _gemini(monkeypatch, _cp(1, '', 'RESOURCE_EXHAUSTED: quota'))
    assert rt.oneshot(prompt='p') is None
    assert 'rc=1' in rt.last_error and 'RESOURCE_EXHAUSTED' in rt.last_error


def test_nonzero_exit_with_a_fragment_is_a_failure(monkeypatch):
    rt = _gemini(monkeypatch, _cp(1, 'half an ans', 'network reset'))
    assert rt.oneshot(prompt='p') is None
    assert 'network reset' in rt.last_error


def test_empty_stdout_with_exit_zero_is_a_failure(monkeypatch):
    rt = _gemini(monkeypatch, _cp(0, '   \n', ''))
    assert rt.oneshot(prompt='p') is None
    assert rt.last_error == 'empty output'


def test_clean_run_still_returns_the_answer(monkeypatch):
    rt = _gemini(monkeypatch, _cp(0, ' the answer \n', ''))
    rt.last_error = 'stale reason from an earlier call'
    r = rt.oneshot(prompt='p')
    assert r is not None and r.text == 'the answer'
    assert rt.last_error == ''


def test_timeout_sets_a_timeout_reason(monkeypatch):
    rt = _gemini(monkeypatch, subprocess.TimeoutExpired(['gemini'], 180))
    assert rt.oneshot(prompt='p') is None
    assert rt.last_error.startswith('timeout')


def test_spawn_failure_is_recorded_not_swallowed(monkeypatch):
    rt = _gemini(monkeypatch, OSError('boom'))
    assert rt.oneshot(prompt='p') is None
    assert 'spawn failed' in rt.last_error and 'boom' in rt.last_error


def test_failed_transform_raises_transform_failure_with_the_reason(monkeypatch):
    """Through the shared `run_text_transform` contract: None from oneshot
    becomes a TransformFailure carrying stderr."""
    rt = _gemini(monkeypatch, _cp(1, '', 'auth expired'))
    monkeypatch.setitem(ar._RUNTIMES, 'gemini', rt)
    monkeypatch.setattr(ar, '_authorize_text_transform', lambda *a, **k: None)
    with pytest.raises(ar.TransformFailure) as ei:
        ar.run_text_transform('gemini', prompt='p')
    assert 'auth expired' in str(ei.value.detail)


def test_shared_runtime_keeps_concurrent_transform_errors_separate(monkeypatch):
    rt = _gemini(monkeypatch, _cp(1, '', 'first transform failed'))
    first_set = Event()
    allow_first_read = Event()

    def first_transform():
        assert rt.oneshot(prompt='first') is None
        first_set.set()
        assert allow_first_read.wait(5)
        return rt.last_error

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_transform)
        try:
            assert first_set.wait(5)
            monkeypatch.setattr(ar.subprocess, 'run', lambda *a, **kw: _cp(1, '', 'second failed'))
            assert rt.oneshot(prompt='second') is None
            assert 'second failed' in rt.last_error
        finally:
            allow_first_read.set()
        assert 'first transform failed' in future.result()
