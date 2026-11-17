"""MC-1179 TL1: start a test unit as its own process group and end the whole group.

`proc.kill()` ends one PID. A shell wrapper, npm, pytest-xdist or a smoke that
launches a browser leaves its descendants running after a timeout or cancel,
still holding ports, files and the output pipes. The launcher therefore starts
every unit in a group it owns:

- POSIX: `start_new_session=True`; the group id is the child's PID and is
  signalled with `os.killpg`. The group is only signalled while the launcher
  still holds the unreaped child, so the id cannot have been recycled.
- Windows: a Job Object with kill-on-job-close. The child is created suspended,
  assigned to the job, then resumed, so it cannot start a grandchild outside
  the job. For argv units the job allows explicit breakaway
  (CREATE_BREAKAWAY_FROM_JOB) but not silent breakaway; shell units get a strict
  job. A job can only ever contain processes this launcher started, and no
  PID is looked up after the fact (Windows reuses PIDs).

A unit that exits normally is left as it was: `release()` forgets the group
without ending stragglers (on Windows it clears kill-on-close first).
If a group cannot be set up (e.g. a parent job that forbids nesting) the unit
runs ungrouped and `kill()` falls back to the direct child; `contained` says so.
"""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_SET_QUOTA, _PROCESS_TERMINATE, _PROCESS_SUSPEND_RESUME = 0x0100, 0x0001, 0x0800

    class _Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class _Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _Basic), ('IoInfo', _IoCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _ntdll = ctypes.WinDLL('ntdll')
    _ntdll.NtResumeProcess.restype = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    def _set_limit(job: int, flags: int) -> bool:
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = flags
        return bool(_k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                                 ctypes.byref(info), ctypes.sizeof(info)))


class Group:
    """The group this launcher created for one child."""

    def __init__(self, proc: subprocess.Popen, job: int | None = None, contained: bool = True) -> None:
        self.proc = proc
        self.job = job
        self.contained = contained

    def kill(self) -> None:
        """End every process in the group. Raises OSError if that fails."""
        if not self.contained:
            self.proc.kill()
            return
        if os.name == 'nt':
            if not _k32.TerminateJobObject(self.job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            # Call only while the child is unreaped (the launcher has not seen
            # poll() return), which keeps the group id ours.
            os.killpg(self.proc.pid, signal.SIGKILL)

    def release(self) -> None:
        """Forget the group without ending anything still running in it."""
        if os.name == 'nt' and self.job:
            _set_limit(self.job, 0)  # otherwise closing the handle would kill stragglers
            _k32.CloseHandle(self.job)
        self.job = None


def start(argv: Any, breakaway_ok: bool = False, **popen_kwargs: Any) -> tuple[subprocess.Popen, Group]:
    """Popen(argv, **popen_kwargs) in a new group. Raises OSError as Popen does.

    `breakaway_ok` (Windows only) lets a descendant that passes
    CREATE_BREAKAWAY_FROM_JOB leave the job, and so outlive a timeout kill.
    """
    if os.name != 'nt':
        proc = subprocess.Popen(argv, start_new_session=True, **popen_kwargs)
        return proc, Group(proc)
    job = _k32.CreateJobObjectW(None, None)
    # BREAKAWAY_OK (never SILENT_BREAKAWAY_OK): only a descendant that explicitly
    # passes CREATE_BREAKAWAY_FROM_JOB (the agent shim launch,
    # mc/agent_shim_client.py) may leave the job; everything else stays in it and
    # is ended with the group. Off for shell units: bash tries that flag on its
    # own forks, so allowing it lets bash grandchildren outlive a timeout.
    limits = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | (_JOB_OBJECT_LIMIT_BREAKAWAY_OK if breakaway_ok else 0)
    if job and not _set_limit(job, limits):
        _k32.CloseHandle(job)
        job = None
    if not job:
        return (proc := subprocess.Popen(argv, **popen_kwargs)), Group(proc, None, False)
    try:
        proc = subprocess.Popen(argv, creationflags=popen_kwargs.pop('creationflags', 0) | _CREATE_SUSPENDED,
                                **popen_kwargs)
    except OSError:
        _k32.CloseHandle(job)
        raise
    # Popen still holds the suspended process's handle, so this PID is ours.
    handle = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SUSPEND_RESUME,
                              False, proc.pid)
    if not handle:
        # Cannot resume it, so it must not be left suspended.
        error = ctypes.get_last_error()
        proc.kill()
        proc.wait()
        _k32.CloseHandle(job)
        raise OSError(error, 'cannot open the started child process')
    assigned = bool(_k32.AssignProcessToJobObject(job, handle))
    # Always resume: a child that could not be contained must still run.
    _ntdll.NtResumeProcess(handle)
    _k32.CloseHandle(handle)
    if not assigned:
        _k32.CloseHandle(job)
        return proc, Group(proc, None, False)
    return proc, Group(proc, job)
