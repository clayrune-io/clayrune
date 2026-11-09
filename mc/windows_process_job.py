"""Shared Windows Job Object ownership for exec and transform children.

Assignment follows Popen, as in the original secrets exec precedent. A child
can spawn before assignment; that residual window is not eliminated here.
"""
from __future__ import annotations

import ctypes
import threading
from typing import Any

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def set_kill_on_close(job) -> bool:
    """Windows: make closing the job handle kill every process in the job. The
    handle is owned by this (server) process, so however the server exits the OS
    closes it and the child tree goes with it."""
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
                    ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
                    ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class _Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _Basic), ('IoInfo', _IoCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]

    kernel32 = ctypes.windll.kernel32
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return bool(kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)))


_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def create_for(pid: int, *, kill_on_close: bool = False):
    """Windows only: create a job object, put ``pid`` in it, return the job
    HANDLE (an int) — or ``None`` if setup fails. Callers choose whether to
    refuse launch results or fall back to their existing live-root cleanup.

    Why a job object and not just ``taskkill /T /PID <pid>`` (MC-981 review
    finding, reproduced with a probe script): ``/T`` walks the process tree
    from ``pid`` by PPID at the moment it's invoked. A command that forks a
    grandchild and then EXITS ITSELF (the ordinary daemon/fork-and-detach
    shape — confirmed with a script whose child's only job is to spawn a
    sleeper and return) leaves nothing for `/T` to walk from: `pid` is
    already gone by the time the timeout fires, so `taskkill` reports
    "not found" and the grandchild — which kept the stdout pipe open, which
    is *why* ``communicate()`` timed out in the first place — survives
    forever. A job object doesn't have this hole: once a process is a
    member, every process IT creates automatically joins too (as long as
    membership happens before that grandchild is spawned), and the
    membership persists independent of whether the original member is still
    alive. ``TerminateJobObject`` then kills everyone still in the job in one
    call, dead parent or not.

    Assigned as early as possible after ``Popen`` returns to keep the window
    where the child could fork before joining the job as small as possible —
    not zero (that needs suspended/atomic process creation rather than this
    existing Popen precedent). A descendant created before assignment remains
    outside the job even if the root joins afterward. There is no guarantee
    that a particular executable cannot reach that window.
    """
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    # Declare HANDLE types: ctypes defaults to a 32-bit int, which can
    # truncate a 64-bit handle on the way out and back in.
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    if kill_on_close and not set_kill_on_close(job):
        kernel32.CloseHandle(job)
        return None
    proc_handle = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not proc_handle:
        kernel32.CloseHandle(job)
        return None
    try:
        ok = kernel32.AssignProcessToJobObject(job, proc_handle)
    finally:
        kernel32.CloseHandle(proc_handle)
    if not ok:
        kernel32.CloseHandle(job)
        return None
    return job


class OwnedJob:
    """Transfer the handle once so cancellation and finalization cannot double-close."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._lock = threading.Lock()

    def _take(self) -> Any:
        with self._lock:
            handle, self._handle = self._handle, None
            return handle

    def stop(self) -> None:
        handle = self._take()
        if handle:
            try:
                if not ctypes.windll.kernel32.TerminateJobObject(handle, 1):
                    raise ctypes.WinError()
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)

    def close(self) -> None:
        handle = self._take()
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
