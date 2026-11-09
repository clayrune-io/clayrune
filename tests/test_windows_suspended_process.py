"""Suspended transform setup: thread selection, handle cleanup and launch order."""
from types import SimpleNamespace

import pytest

from mc import transform_process_deadline as process
from mc import windows_process_job as jobs


class Api:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.mark.parametrize('failure', [None, 'snapshot', 'first', 'next', 'missing',
                                    'multiple', 'open', 'resume', 'zero', 'two'])
def test_primary_thread_resume_closes_all_acquired_handles(monkeypatch, failure):
    calls = []
    entries = iter([(900, 99), (123, 7)] + ([(124, 7)] if failure == 'multiple' else []))
    error = [18]

    def enumerate_thread(snapshot, pointer, *, first=False):
        if failure == ('first' if first else 'next'):
            error[0] = 5
            return 0
        try:
            tid, pid = next(entries)
        except StopIteration:
            return 0
        pointer._obj.th32ThreadID = tid
        pointer._obj.th32OwnerProcessID = 0 if failure == 'missing' else pid
        return 1

    def resume(handle):
        calls.append(('resume', handle))
        return {'resume': 0xFFFFFFFF, 'zero': 0, 'two': 2}.get(failure, 1)

    def opened(access, inherit, tid):
        calls.append(('open', access, inherit, tid))
        return 0 if failure == 'open' else 200

    kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=Api(lambda flags, pid:
            jobs.ctypes.c_void_p(-1).value if failure == 'snapshot' else 100),
        Thread32First=Api(lambda h, p: enumerate_thread(h, p, first=True)),
        Thread32Next=Api(enumerate_thread), GetLastError=Api(lambda: error[0]),
        OpenThread=Api(opened), ResumeThread=Api(resume),
        CloseHandle=Api(lambda h: calls.append(('close', h))))
    monkeypatch.setattr(jobs.ctypes, 'windll', SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setattr(jobs.ctypes, 'WinError', lambda *a: OSError('native failure'), raising=False)
    if failure is None:
        jobs.resume_suspended(7)
    else:
        with pytest.raises(OSError):
            jobs.resume_suspended(7)
    closed = [call[1] for call in calls if call[0] == 'close']
    assert closed == ([] if failure == 'snapshot' else
                      [100, 200] if failure in (None, 'resume', 'zero', 'two') else [100])
    if failure in (None, 'open', 'resume', 'zero', 'two'):
        assert ('open', 0x0002, False, 123) in calls
    for name, api in vars(kernel).items():
        assert hasattr(api, 'argtypes'), name
        assert hasattr(api, 'restype'), name


def test_abort_uses_original_popen_handle_and_waits(monkeypatch):
    calls = []
    terminate = Api(lambda handle, code: calls.append(('terminate', handle, code)) or 1)
    monkeypatch.setattr(jobs.ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(
        TerminateProcess=terminate)), raising=False)
    proc = SimpleNamespace(_handle=456, pid=123,
                           wait=lambda **kw: calls.append(('wait', kw['timeout'])))
    jobs.terminate_suspended(proc, timeout=.4)
    assert calls == [('terminate', 456, 1), ('wait', .4)]


@pytest.mark.parametrize('resume_fails', [False, True])
def test_launch_preserves_flags_assigns_before_resume_and_aborts_without_pid_fallback(
        monkeypatch, resume_fails):
    calls = []
    proc = SimpleNamespace(pid=123, returncode=0, communicate=lambda data: ('ok', ''),
                           stdin=None, stdout=None, stderr=None)

    def spawn(*a, **kw):
        assert kw['creationflags'] == 0x08000000 | jobs.CREATE_SUSPENDED
        calls.append('spawn')
        return proc

    def resume(pid):
        calls.append('resume')
        if resume_fails:
            raise OSError('resume failed')

    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(process.subprocess, 'Popen', spawn)
    monkeypatch.setattr(jobs, 'create_for', lambda *a, **kw: calls.append('assign') or 100)
    monkeypatch.setattr(jobs, 'OwnedJob', lambda handle: SimpleNamespace(
        close=lambda: calls.append('close')))
    monkeypatch.setattr(jobs, 'resume_suspended', resume)
    monkeypatch.setattr(jobs, 'terminate_suspended', lambda owned, **kw:
                        calls.append('terminate') if owned is proc else pytest.fail('wrong root'))
    monkeypatch.setattr(process, '_stop_owned', lambda *a, **kw: pytest.fail('PID fallback'))
    kwargs = dict(input='x', timeout=2, deadline=process.time.monotonic() + 2,
                  creationflags=0x08000000)
    if resume_fails:
        with pytest.raises(OSError, match='resume failed'):
            process.run(['synthetic'], **kwargs)
        assert calls == ['spawn', 'assign', 'resume', 'terminate', 'close']
    else:
        assert process.run(['synthetic'], **kwargs).stdout == 'ok'
        assert calls == ['spawn', 'assign', 'resume', 'close']
