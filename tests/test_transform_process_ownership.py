"""Kernel ownership seams: dead roots, empty groups and shared job handles."""
from types import SimpleNamespace

import pytest

from mc import proc_kill
from mc import transform_process_deadline as process
from mc import windows_process_job as jobs


def test_job_cancellation_reaches_descendants_after_root_exit(monkeypatch):
    calls = []
    proc = SimpleNamespace(poll=lambda: pytest.fail('job must not resolve stale root'))
    job = SimpleNamespace(stop=lambda: calls.append('terminate-close'))
    monkeypatch.setattr(process.subprocess, 'run', lambda *a, **kw: pytest.fail('PID tree kill'))
    process._stop_owned(proc, job=job)
    assert calls == ['terminate-close']


@pytest.mark.parametrize('reader_alive', [True, False])
def test_recorded_group_is_used_only_with_live_pipe_holder(monkeypatch, reader_alive):
    calls = []
    monkeypatch.setattr(proc_kill, 'safe_killpg_recorded',
                        lambda pgid, **kw: calls.append((pgid, kw)))
    proc = SimpleNamespace(poll=lambda: 0)
    process._stop_owned(proc, pgid=12345,
                        reader=SimpleNamespace(is_alive=lambda: reader_alive))
    assert calls == [(12345, dict(holder_alive=reader_alive))]


def test_finished_reader_can_only_clean_up_its_still_live_root(monkeypatch):
    calls = []
    monkeypatch.setattr(proc_kill, 'safe_killpg_recorded',
                        lambda pgid, **kw: calls.append(('group', kw['holder_alive'])))
    proc = SimpleNamespace(poll=lambda: None, kill=lambda: calls.append('root'))
    process._stop_owned(proc, pgid=12345, reader=SimpleNamespace(is_alive=lambda: False))
    assert calls == [('group', False), 'root']


@pytest.mark.parametrize('pgid,holder,expected', [
    (12345, True, True), (12345, False, False), (7654, True, False),
    (0, True, False), (1, True, False),
])
def test_recorded_group_refuses_empty_and_server_groups(monkeypatch, pgid, holder, expected):
    calls = []
    monkeypatch.setattr(proc_kill, '_IS_WIN', False)
    monkeypatch.setattr(proc_kill.os, 'getpgrp', lambda: 7654, raising=False)
    monkeypatch.setattr(proc_kill.os, 'getpgid', lambda pid: pytest.fail('stale PID lookup'), raising=False)
    monkeypatch.setattr(proc_kill.os, 'killpg', lambda *a: calls.append(a), raising=False)
    assert proc_kill.safe_killpg_recorded(pgid, holder_alive=holder) == expected
    assert calls == ([(pgid, proc_kill._SIGKILL)] if expected else [])


def test_missing_recorded_group_is_benign(monkeypatch):
    monkeypatch.setattr(proc_kill, '_IS_WIN', False)
    monkeypatch.setattr(proc_kill.os, 'getpgrp', lambda: 7654, raising=False)

    def gone(*a):
        raise ProcessLookupError()

    monkeypatch.setattr(proc_kill.os, 'killpg', gone, raising=False)
    assert not proc_kill.safe_killpg_recorded(12345, holder_alive=True)


class ApiCall:
    def __init__(self, name, calls, value):
        self.name, self.calls, self.value = name, calls, value

    def __call__(self, *args):
        self.calls.append((self.name, args))
        return self.value


def fake_kernel(monkeypatch, **results):
    calls = []
    kernel = SimpleNamespace(**{name: ApiCall(name, calls, results.get(name, default))
        for name, default in dict(CreateJobObjectW=100, OpenProcess=200,
                                  AssignProcessToJobObject=1, CloseHandle=1,
                                  TerminateJobObject=1, SetInformationJobObject=1).items()})
    monkeypatch.setattr(jobs.ctypes, 'windll', SimpleNamespace(kernel32=kernel), raising=False)
    return calls


def test_job_enables_kill_on_close_before_assigning_root(monkeypatch):
    calls = fake_kernel(monkeypatch)
    assert jobs.create_for(12345, kill_on_close=True) == 100
    names = [name for name, args in calls]
    assert names == ['CreateJobObjectW', 'SetInformationJobObject', 'OpenProcess',
                     'AssignProcessToJobObject', 'CloseHandle']
    assert calls[3][1] == (100, 200)
    assert calls[1][1][2]._obj.BasicLimitInformation.LimitFlags == 0x2000


@pytest.mark.parametrize('failure', [
    'CreateJobObjectW', 'SetInformationJobObject', 'OpenProcess', 'AssignProcessToJobObject',
])
def test_job_setup_failure_closes_only_acquired_handles(monkeypatch, failure):
    calls = fake_kernel(monkeypatch, **{failure: 0})
    assert jobs.create_for(12345, kill_on_close=True) is None
    closed = [args[0] for name, args in calls if name == 'CloseHandle']
    assert closed == ([] if failure == 'CreateJobObjectW' else
                      [200, 100] if failure == 'AssignProcessToJobObject' else [100])


def test_terminate_then_close_happens_once_with_no_pid_lookup(monkeypatch):
    calls = fake_kernel(monkeypatch)
    job = jobs.OwnedJob(100)
    job.stop()
    job.close()
    job.stop()
    assert calls == [('TerminateJobObject', (100, 1)), ('CloseHandle', (100,))]


def test_normal_finalization_closes_kill_on_close_handle_once(monkeypatch):
    calls = fake_kernel(monkeypatch)
    job = jobs.OwnedJob(100)
    job.close()
    job.stop()
    assert calls == [('CloseHandle', (100,))]


def test_failed_termination_still_closes_kill_on_close_handle(monkeypatch):
    calls = fake_kernel(monkeypatch, TerminateJobObject=0)
    monkeypatch.setattr(jobs.ctypes, 'WinError', lambda: OSError('synthetic'), raising=False)
    job = jobs.OwnedJob(100)
    with pytest.raises(OSError):
        job.stop()
    job.close()
    assert calls == [('TerminateJobObject', (100, 1)), ('CloseHandle', (100,))]


@pytest.mark.parametrize('reader_error', [False, True])
def test_run_releases_job_on_success_and_reader_error(monkeypatch, reader_error):
    calls = []

    def communicate(data):
        if reader_error:
            raise ValueError('synthetic pipe error')
        return 'result', ''

    proc = SimpleNamespace(pid=12345, communicate=communicate, returncode=0)
    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: proc)
    monkeypatch.setattr(jobs, 'create_for', lambda pid, **kw: 100)
    monkeypatch.setattr(jobs, 'OwnedJob', lambda h: SimpleNamespace(
        close=lambda: calls.append('close'), stop=lambda: calls.append('stop')))
    if reader_error:
        with pytest.raises(ValueError):
            process.run(['synthetic'], input='payload', timeout=2,
                        deadline=process.time.monotonic() + 2)
        assert calls == ['stop', 'close']
    else:
        result = process.run(['synthetic'], input='payload', timeout=2,
                             deadline=process.time.monotonic() + 2)
        assert result.stdout == 'result'
        assert calls == ['close']


def test_failed_job_assignment_refuses_result_and_closes_pipes(monkeypatch):
    calls = []
    proc = SimpleNamespace(pid=12345, stdin=SimpleNamespace(close=lambda: calls.append('stdin')),
                           stdout=SimpleNamespace(close=lambda: calls.append('stdout')),
                           stderr=SimpleNamespace(close=lambda: calls.append('stderr')))
    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: proc)
    monkeypatch.setattr(jobs, 'create_for', lambda pid, **kw: None)
    monkeypatch.setattr(process, '_stop_owned', lambda owned: calls.append('stop'))
    with pytest.raises(OSError, match='ownership could not be established'):
        process.run(['synthetic'], input='payload', timeout=2,
                    deadline=process.time.monotonic() + 2)
    assert calls == ['stop', 'stdin', 'stdout', 'stderr']


def test_secrets_callers_share_the_extracted_helpers():
    from mc import secrets_exec_stream as stream
    from mc.blueprints import secrets_routes as routes
    assert routes._win_job_object_for is jobs.create_for
    assert stream._win_job_object_for is jobs.create_for
    assert stream._set_kill_on_close is jobs.set_kill_on_close


@pytest.mark.parametrize('has_job', [False, True])
def test_secrets_fallback_never_tree_kills_exited_root(monkeypatch, has_job):
    from mc.blueprints import secrets_routes as routes
    calls = []
    monkeypatch.setattr(routes, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(routes, 'ctypes', SimpleNamespace(windll=SimpleNamespace(
        kernel32=SimpleNamespace(TerminateJobObject=lambda *a: calls.append(a)))))
    monkeypatch.setattr(routes.subprocess, 'run', lambda *a, **kw: pytest.fail('stale PID tree kill'))
    routes._kill_process_tree(SimpleNamespace(poll=lambda: 0), 100 if has_job else None)
    assert calls == ([(100, 1)] if has_job else [])
