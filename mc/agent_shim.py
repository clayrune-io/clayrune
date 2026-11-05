"""Agent supervisor shim (MC-1150 P1). One shim per engine process.

Why it exists: an agent CLI spawned by the Clayrune server dies with the
server (the stop-all in the restart path, the startup reaper, a tree kill).
The shim is the process that does NOT: it is spawned detached (see
mc/agent_shim_tramp.py and mc/agent_shim_client.py), starts the CLI with its
stdout/stderr pointed straight at `out.log` (the shim is not in the output
path, so it cannot lose or stall output), and holds the CLI's stdin pipe open
so a restarted server can keep talking to it. Design and the measured
survival matrix: docs/AGENT_SURVIVE_RESTART_DESIGN.md.

HARD RULE: stdlib only and NO `mc` imports. It is started by a bare base
interpreter (no venv site-packages) and, in a frozen build, by
`<app> --clayrune-shim run <dir>`. Importing the `mc` package here would
drag server state into a process that must stay tiny. `psutil` is used for
the orphan idle test only when importable (see `extra_sys_path` in spec.json);
without it the idle test is skipped and only the hard `orphan_max` ceiling
applies.

Files under <session_dir> (all JSON writes are atomic):
  spec.json    in   argv, cwd, mode, timers ... written by the server. Env
                    VALUES never appear here; the environment travels on the
                    spawn call itself, so no credential ever touches disk.
  prompt.txt   in   optional stdin payload; deleted once delivered.
  out.log      out  raw merged stdout+stderr, written by the CLI itself.
  shim.json    out  pids + create times, port, token, last_seq, owner.
  exit.json    out  CLI return code, written the moment the CLI exits.
  shim.log     out  one line per shim event, for debugging.

Wire protocol (interactive mode only, loopback TCP, one line per command):
  HELLO <token> <owner_pid> <boot_id>   -> OK <last_seq> <cli_pid> <cli_alive>
                                           | REFUSED <live_owner_pid> | ERR token
  SEND <seq> <nbytes>\\n<bytes>          -> ACK <seq> | ERR stdin_closed | ERR seq_gap <n>
  CLOSE_STDIN                           -> ACK
  STATUS                                -> OK <json>
There is deliberately no kill command: the server kills by PID, identity
checked, so the shim exposes no remote-kill surface.
"""
from __future__ import annotations

import errno
import hmac
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

SCHEMA = 1
MODE_ONESHOT = 'oneshot'
MODE_INTERACTIVE = 'interactive'

# Orphan-lifetime verdicts returned by LifetimePolicy.tick().
A_NONE = 'none'
A_CLOSE_STDIN = 'close_stdin'
A_EXPIRE = 'expire'
A_FINISH = 'finish'

_HELLO_TIMEOUT_S = 10.0
_MAX_SEND_BYTES = 64 * 1024 * 1024
_CREATE_NO_WINDOW = 0x08000000

# Spec defaults (seconds). The server overrides them from the config keys
# shim_orphan_grace_min / shim_exit_grace_min / shim_orphan_max_hours.
DEFAULT_TIMERS = {
    'grace_s': 20 * 60,
    'exit_grace_s': 60 * 60,
    'orphan_max_s': 12 * 3600,
    'tick_s': 5.0,
}


# ───────────────────────── process identity (stdlib) ─────────────────────────
def _win_create_time(pid: int) -> Optional[float]:
    """Epoch creation time of a LIVE process, or None. Uses a private WinDLL
    handle so argtypes set here cannot leak into other ctypes users."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    ft_p = ctypes.POINTER(wintypes.FILETIME)
    k.GetProcessTimes.argtypes = [wintypes.HANDLE, ft_p, ft_p, ft_p, ft_p]
    h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        return _win_create_time_from_handle(k, h)
    finally:
        k.CloseHandle(h)


def _win_create_time_from_handle(k: Any, h: Any) -> Optional[float]:
    """Creation time of the process behind an OPEN handle, or None if it has
    exited. `k` is the private WinDLL from the caller, argtypes already set."""
    import ctypes
    from ctypes import wintypes
    code = wintypes.DWORD()
    if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:
        return None                               # 259 == STILL_ACTIVE
    c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
    if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                             ctypes.byref(kt), ctypes.byref(ut)):
        return None
    ft = (c.dwHighDateTime << 32) | c.dwLowDateTime
    return (ft - 116444736000000000) / 1e7


def _linux_create_time(pid: int) -> Optional[float]:
    try:
        with open(f'/proc/{int(pid)}/stat') as f:
            rest = f.read().rsplit(')', 1)[1].split()
        if rest[0] == 'Z':
            return None                           # zombie: not running
        start_ticks = int(rest[19])
        with open('/proc/stat') as f:
            btime = next(int(ln.split()[1]) for ln in f if ln.startswith('btime'))
        return btime + start_ticks / getattr(os, 'sysconf')('SC_CLK_TCK')   # POSIX-only attr
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def _ps_create_time(pid: int) -> Optional[float]:
    """macOS / other POSIX: `ps -o lstart=` (1 s resolution; the identity
    tolerance is 2 s). Unmeasured on a real Mac (design section 14)."""
    try:
        out = subprocess.run(['ps', '-o', 'lstart=', '-p', str(int(pid))],
                             capture_output=True, text=True, encoding='utf-8', errors='replace',
                             timeout=5).stdout.strip()
        if not out:
            return None
        return time.mktime(time.strptime(out, '%a %b %d %H:%M:%S %Y'))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def proc_create_time(pid: Optional[int]) -> Optional[float]:
    """Creation time (epoch seconds) of a live process; None if it is gone,
    is a zombie, or cannot be read. Never raises."""
    if not pid or pid <= 0:
        return None
    try:
        if sys.platform == 'win32':
            return _win_create_time(pid)
        if sys.platform.startswith('linux'):
            return _linux_create_time(pid)
        return _ps_create_time(pid)
    except Exception:
        return None


def _tolerance_for(platform: str) -> float:
    """How far a live creation time may differ from the one recorded earlier
    and still be the same process. Stored and live values come from the SAME
    reader (`proc_create_time`), so the tolerance only has to absorb that
    reader's own jitter, and every second of slack is a second in which a
    process that reused the PID passes as the old one.
      Windows  0.01 s  FILETIME is exact; same call both times.
      Linux    0.05 s  jiffies are 10 ms; btime is read from /proc/stat.
      other    2.0 s   macOS `ps -o lstart` has 1 s resolution. REMAINING RISK:
                       a process that reuses the PID within ~2 s of the
                       recorded one reads as ours. Unmeasured on a real Mac."""
    if platform == 'win32':
        return 0.01
    if platform.startswith('linux'):
        return 0.05
    return 2.0


IDENTITY_TOLERANCE_S = _tolerance_for(sys.platform)


def same_process(pid: Optional[int], create_time: Optional[float],
                 tol: float = IDENTITY_TOLERANCE_S) -> bool:
    """The PID-reuse guard: True only if `pid` is alive AND started within
    `tol` seconds of the recorded `create_time`. A missing recorded identity is
    never a match (same posture as process_ledger._should_reap_entry)."""
    if create_time is None:
        return False
    live = proc_create_time(pid)
    return live is not None and abs(live - float(create_time)) <= tol


def _pid_absent(pid: int) -> bool:
    """True only on POSITIVE evidence that no live process owns `pid` (not found,
    exited, or a zombie). Any read failure or access-denied is False: unknown is
    not absent. Never raises."""
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            h = k.OpenProcess(0x1000, False, int(pid))
            if not h:
                return ctypes.get_last_error() == 87      # ERROR_INVALID_PARAMETER: no such pid
            try:
                code = wintypes.DWORD()
                return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value != 259
            finally:
                k.CloseHandle(h)
        # POSIX: only ESRCH is proof of absence. EPERM means the process exists
        # (another user's, or hidden from /proc by hidepid=2), so a missing
        # /proc entry is NOT evidence on its own.
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if sys.platform.startswith('linux'):
            try:                                        # signalable but a zombie = exited
                with open(f'/proc/{int(pid)}/stat') as f:
                    return f.read().rsplit(')', 1)[1].split()[0] == 'Z'
            except OSError:
                return False                            # unreadable stat: no extra signal
        return False
    except Exception:
        return False


def process_state(pid: Optional[int], create_time: Optional[float],
                  tol: float = IDENTITY_TOLERANCE_S) -> str:
    """Three-valued liveness of the process recorded as (pid, create_time):
    'ours'    -- alive and started within `tol` of the recorded create_time;
    'gone'    -- positively verified absent (or the pid now belongs to a process
                 with a different start time, so the recorded one is gone);
    'unknown' -- cannot tell (live lookup failed, or alive with no recorded
                 identity to compare). Callers that must not assume a process
                 is dead treat 'unknown' as alive."""
    if not pid or pid <= 0:
        return 'gone'
    live = proc_create_time(pid)
    if live is not None:
        if create_time is None:
            return 'unknown'
        return 'ours' if abs(live - float(create_time)) <= tol else 'gone'
    return 'gone' if _pid_absent(int(pid)) else 'unknown'


def kill_tree(pid: int) -> None:
    """Kill a process tree. The shim only ever calls this on its OWN child."""
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(int(pid))],
                           capture_output=True, creationflags=_CREATE_NO_WINDOW, timeout=30)
            return
        try:
            pgid = os.getpgid(pid)
            if pgid == os.getpgrp():
                # Never signal the shim's own group (the mc.proc_kill bright
                # line; this module may not import mc). The child leads its
                # own session, so this only fires if that ever stops being so.
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _win_kill_tree_if_ours(pid: int, create_time: float, tol: float) -> bool:
    """Windows half of kill_tree_if_ours. The handle is opened first and held
    across the kill: Windows will not reuse a PID while any handle to the
    process object is open, so the identity read from THIS handle is the
    identity taskkill acts on."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    ft_p = ctypes.POINTER(wintypes.FILETIME)
    k.GetProcessTimes.argtypes = [wintypes.HANDLE, ft_p, ft_p, ft_p, ft_p]
    # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    h = k.OpenProcess(0x1000 | 0x00100000, False, int(pid))
    if not h:
        return False                              # gone, or access denied: unknown
    try:
        live = _win_create_time_from_handle(k, h)
        if live is None or abs(live - float(create_time)) > tol:
            return False
        r = subprocess.run(['taskkill', '/F', '/T', '/PID', str(int(pid))],
                           capture_output=True, creationflags=_CREATE_NO_WINDOW, timeout=30)
        return r.returncode == 0
    finally:
        k.CloseHandle(h)


# pidfd_open failures that mean "this syscall cannot be used here", not "the
# target is gone/denied": ENOSYS (kernel older than 5.3 under a Python that
# exposes the wrapper) and EPERM (pidfd_open performs no permission check on
# the target, so EPERM can only come from a seccomp/LSM filter on the syscall).
# ESRCH and everything else still refuse.
_PIDFD_UNUSABLE_ERRNOS = frozenset({errno.ENOSYS, errno.EPERM})


def _posix_kill_tree_if_ours(pid: int, create_time: float, tol: float) -> bool:
    """POSIX half of kill_tree_if_ours. With pidfd_open (Linux 5.3+) the fd pins
    the process object: a signal sent through it can only reach the process it
    was opened on, so the identity check and the kill cannot straddle a PID
    reuse. Without it, identity is re-read immediately before os.kill/killpg;
    the window left is the few microseconds between that read and the signal
    (REMAINING RISK on macOS and old Linux kernels, not closable from stdlib)."""
    pidfd_open = getattr(os, 'pidfd_open', None)
    send = getattr(signal, 'pidfd_send_signal', None)
    sigkill = getattr(signal, 'SIGKILL', 9)       # POSIX-only attribute
    fd = None
    if pidfd_open is not None and send is not None:
        try:
            fd = pidfd_open(int(pid))
        except OSError as e:
            if e.errno not in _PIDFD_UNUSABLE_ERRNOS:
                return False                      # ESRCH (gone), EINVAL, other: not provably ours
            # The syscall itself is unusable here (Python exposes pidfd_open on a
            # kernel that lacks it, or a filter blocks it) -- no evidence about the
            # target. Fall through to the identity-checked fallback below, which
            # restores what kill_tree's killpg path did before the identity check.
    if fd is not None and send is not None:
        try:
            # pgid is read BEFORE the identity check: if the pid was reused
            # in between, the check (made after) reads the new process and
            # fails; read after, it could be the new process's group.
            try:
                pgid = os.getpgid(int(pid))       # type: ignore[attr-defined]
            except OSError:
                pgid = None
            if not same_process(pid, create_time, tol):
                return False
            send(fd, sigkill)
            # A group whose id equals the pid is the one the child made for
            # itself; no other process can take that id while the group has
            # members, so this reaches only its descendants. Left open: a
            # root with no descendants whose pid is recycled into a new group
            # leader in the microseconds after the signal.
            if pgid == int(pid) and pgid != os.getpgrp():   # type: ignore[attr-defined]
                try:
                    os.killpg(pgid, sigkill)      # type: ignore[attr-defined]
                except OSError:
                    pass
            return True
        except OSError:
            return False
        finally:
            os.close(fd)
    try:
        pgid = os.getpgid(int(pid))               # type: ignore[attr-defined]
        if process_state(pid, create_time, tol) != 'ours':
            return False
        if pgid == int(pid) and pgid != os.getpgrp():       # type: ignore[attr-defined]
            os.killpg(pgid, sigkill)              # type: ignore[attr-defined]
        else:
            os.kill(int(pid), sigkill)
        return True
    except OSError:
        return False


def kill_tree_if_ours(pid: Optional[int], create_time: Optional[float],
                      tol: float = IDENTITY_TOLERANCE_S) -> bool:
    """Kill the tree rooted at `pid` ONLY IF it is still the process recorded as
    (pid, create_time); identity is re-checked inside this call, immediately
    before the signal, from live state. Nothing is read from a cache, so a PID
    reused between an earlier look and this call is not killed. Never kills on
    'unknown' (lookup failed) or 'gone'. True = a kill was delivered. Never raises."""
    if not pid or pid <= 0 or create_time is None:
        return False
    try:
        if sys.platform == 'win32':
            return _win_kill_tree_if_ours(int(pid), float(create_time), tol)
        return _posix_kill_tree_if_ours(int(pid), float(create_time), tol)
    except Exception:
        return False


# ───────────────────────────── atomic JSON ──────────────────────────────────
def write_json_atomic(path: str, obj: Any) -> None:
    """tmp-in-same-dir + fsync + os.replace (stdlib twin of
    mc.atomic_json.write_json_atomic). The tmp name matches the orphan-tmp
    sweep pattern `.<name>.tmp<pid>`. A reader holding the target makes the
    Windows replace fail transiently, hence the retry."""
    d, name = os.path.split(path)
    tmp = os.path.join(d, f'.{name}.tmp{os.getpid()}')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    for _ in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(tmp, path)


# ───────────────────────────── lifetime policy ──────────────────────────────
class LifetimePolicy:
    """Pure state machine for the section-6.4 timers. No I/O and no real
    clock: the caller supplies `clock` and the observations, so the timers are
    testable without sleeping.

      attached   an owner is present: nothing happens.
      orphaned   no owner for `grace_s`: run the idle test (log silent for
                 `grace_s` AND the CLI tree shows no CPU/new children) and, if
                 idle, close the CLI's stdin once (an interactive CLI exits on
                 EOF). Log silence alone is NOT enough: a 25-minute silent
                 pytest in a Bash tool prints nothing (MC-1149 incident).
      expired    no owner for `orphan_max_s`: tree-kill the CLI.
      draining   CLI exited: wait up to `exit_grace_s` for an owner to
                 collect, then finish.
    """

    def __init__(self, *, grace_s: float, exit_grace_s: float, orphan_max_s: float,
                 clock: Callable[[], float], idle_close: bool = True):
        self._grace = float(grace_s)
        self._exit_grace = float(exit_grace_s)
        self._orphan_max = float(orphan_max_s)
        self._clock = clock
        self._idle_close = idle_close
        self._orphan_since: Optional[float] = None
        self._last_size: Optional[int] = None
        self._last_growth = clock()
        self._exit_t: Optional[float] = None
        self._stdin_closed = False

    def tick(self, *, owner_attached: bool, cli_exited: bool, collected: bool,
             log_size: int, tree_busy: Optional[bool]) -> str:
        now = self._clock()
        if log_size != self._last_size:
            self._last_size = log_size
            self._last_growth = now
        if cli_exited:
            if self._exit_t is None:
                self._exit_t = now
            if collected or now - self._exit_t >= self._exit_grace:
                return A_FINISH
            return A_NONE
        if owner_attached:
            self._orphan_since = None
            return A_NONE
        if self._orphan_since is None:
            self._orphan_since = now
        orphaned = now - self._orphan_since
        if orphaned >= self._orphan_max:
            return A_EXPIRE
        if (self._idle_close and not self._stdin_closed and orphaned >= self._grace
                and now - self._last_growth >= self._grace and tree_busy is False):
            self._stdin_closed = True
            return A_CLOSE_STDIN
        return A_NONE


class TreeProbe:
    """Is the CLI's process tree doing anything? Needs psutil; returns None
    (unknown) without it, which the policy treats as "not provably idle"."""

    def __init__(self, cli_pid: int):
        self._pid = cli_pid
        self._prev_cpu: Optional[float] = None
        self._prev_kids: Optional[frozenset] = None
        try:
            import psutil  # type: ignore
            self._psutil: Any = psutil
        except Exception:
            self._psutil = None

    def busy(self) -> Optional[bool]:
        if self._psutil is None:
            return None
        try:
            root = self._psutil.Process(self._pid)
            procs = [root] + root.children(recursive=True)
            cpu = 0.0
            for p in procs:
                try:
                    t = p.cpu_times()
                    cpu += t.user + t.system
                except Exception:
                    pass
            kids = frozenset(p.pid for p in procs)
        except Exception:
            return None
        first = self._prev_cpu is None
        grew = (not first) and (cpu - (self._prev_cpu or 0.0)) > 0.05
        changed = (not first) and kids != self._prev_kids
        self._prev_cpu, self._prev_kids = cpu, kids
        return True if first else (grew or changed)


# ──────────────────────────────── the shim ──────────────────────────────────
class _Owner:
    def __init__(self, pid: int, create_time: Optional[float], boot_id: str,
                 conn: Optional[socket.socket]):
        self.pid = pid
        self.create_time = create_time
        self.boot_id = boot_id
        self.conn = conn


class Shim:
    def __init__(self, session_dir: str, *, clock: Callable[[], float] = time.monotonic):
        self.dir = os.path.abspath(session_dir)
        self._clock = clock
        self.spec: Dict[str, Any] = {}
        self.mode = MODE_ONESHOT
        self.token = secrets.token_hex(16)            # 128-bit
        self.cli: Optional[subprocess.Popen] = None
        self.cli_ct: Optional[float] = None
        self.port: Optional[int] = None
        self.last_seq = 0
        self.owner: Optional[_Owner] = None
        self._lock = threading.RLock()                # owner + manifest state
        self._send_lock = threading.Lock()            # serialises stdin writes
        self._cli_lock = threading.Lock()             # reap flag vs expiry kill (see _expire_cli)
        self._cli_reaped = False
        self._stdin_closed = False
        self._exited = threading.Event()
        self._wake = threading.Event()
        self._collected = False
        self._exit_reason = 'exit'
        self._listener: Optional[socket.socket] = None

    # ── paths / logging ──
    def _p(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _slog(self, msg: str) -> None:
        try:
            with open(self._p('shim.log'), 'a', encoding='utf-8') as f:
                f.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")} pid={os.getpid()} {msg}\n')
        except OSError:
            pass                                       # cosmetic diagnostics only

    # ── startup ──
    def _load_spec(self) -> None:
        with open(self._p('spec.json'), 'r', encoding='utf-8') as f:
            self.spec = json.load(f)
        if self.spec.get('schema') != SCHEMA:
            raise ValueError(f'spec schema {self.spec.get("schema")!r} != {SCHEMA}')
        self.mode = self.spec.get('mode', MODE_ONESHOT)
        if self.mode not in (MODE_ONESHOT, MODE_INTERACTIVE):
            raise ValueError(f'bad mode {self.mode!r}')
        for extra in self.spec.get('extra_sys_path') or []:
            if extra and extra not in sys.path:
                sys.path.append(extra)                 # only so TreeProbe can import psutil

    def _spawn_cli(self) -> None:
        argv = list(self.spec['argv'])
        has_prompt = os.path.exists(self._p('prompt.txt'))
        stdin = subprocess.PIPE if (self.mode == MODE_INTERACTIVE or has_prompt) else subprocess.DEVNULL
        kw: Dict[str, Any] = {}
        if sys.platform == 'win32':
            kw['creationflags'] = _CREATE_NO_WINDOW    # a detached shim has no console to share
        else:
            kw['start_new_session'] = True             # CLI leads its own group: killpg-able
        log = open(self._p('out.log'), 'ab', buffering=0)
        try:
            self.cli = subprocess.Popen(argv, cwd=self.spec.get('cwd') or None, stdin=stdin,
                                        stdout=log, stderr=subprocess.STDOUT, **kw)
        finally:
            log.close()                                # the CLI holds its own handle
        self.cli_ct = proc_create_time(self.cli.pid)
        if stdin is subprocess.PIPE:
            threading.Thread(target=self._feed_prompt, daemon=True).start()

    def _feed_prompt(self) -> None:
        """Deliver prompt.txt (if any) to the CLI's stdin. oneshot: then close
        so the CLI sees EOF; interactive: leave stdin open for SEND."""
        cli = self.cli
        assert cli is not None and cli.stdin is not None
        path = self._p('prompt.txt')
        try:
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    data = f.read()
                cli.stdin.write(data)
                cli.stdin.flush()
                try:
                    os.unlink(path)
                except OSError:
                    pass
            if self.mode == MODE_ONESHOT:
                self._close_stdin()
        except (OSError, ValueError) as e:
            self._slog(f'prompt delivery failed: {e}')
            self._close_stdin()

    def _close_stdin(self) -> None:
        with self._send_lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
            try:
                if self.cli is not None and self.cli.stdin is not None:
                    self.cli.stdin.close()
            except (OSError, ValueError):
                pass

    # ── manifest ──
    def _manifest(self) -> Dict[str, Any]:
        with self._lock:
            o = self.owner
            return {
                'schema': SCHEMA,
                'launch_id': self.spec.get('launch_id'),
                'session_id': self.spec.get('session_id'),
                'mode': self.mode,
                'shim_pid': os.getpid(),
                'shim_ct': proc_create_time(os.getpid()),
                'cli_pid': self.cli.pid if self.cli else None,
                'cli_ct': self.cli_ct,
                'port': self.port,
                'token': self.token if self.port else None,
                'last_seq': self.last_seq,
                'owner_pid': o.pid if o else None,
                'owner_boot_id': o.boot_id if o else None,
            }

    def _write_manifest(self) -> None:
        try:
            write_json_atomic(self._p('shim.json'), self._manifest())
        except OSError as e:
            self._slog(f'shim.json write failed: {e}')

    # ── owner bookkeeping ──
    def _owner_alive(self, o: _Owner) -> bool:
        return same_process(o.pid, o.create_time)

    def _attached(self) -> bool:
        with self._lock:
            o = self.owner
            return o is not None and (o.conn is not None or self._owner_alive(o))

    def _claim_owner(self, pid: int, boot_id: str, conn: socket.socket) -> Optional[int]:
        """Returns None on success, or the pid of the LIVE owner that blocks us.
        A dead owner is displaced; a live different owner is refused (the
        documented Windows port-sharing hole can start two servers)."""
        with self._lock:
            cur = self.owner
            if cur is not None and cur.pid != pid and self._owner_alive(cur):
                return cur.pid
            if cur is not None and cur.conn is not None and cur.conn is not conn:
                try:
                    cur.conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.owner = _Owner(pid, proc_create_time(pid), boot_id, conn)
        self._write_manifest()
        return None

    def _release_owner(self, conn: socket.socket) -> None:
        with self._lock:
            if self.owner is not None and self.owner.conn is conn:
                self.owner.conn = None
                if self._exited.is_set():
                    self._collected = True             # an owner saw the run out
        self._wake.set()

    # ── socket service (interactive mode) ──
    def _start_listener(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))                       # loopback only
        s.listen(8)
        self._listener = s
        self.port = s.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _reply(conn: socket.socket, text: str) -> bool:
        try:
            conn.sendall(text.encode('utf-8') + b'\n')
            return True
        except OSError:
            return False

    def _serve(self, conn: socket.socket) -> None:
        claimed = False
        try:
            conn.settimeout(_HELLO_TIMEOUT_S)
            rf = conn.makefile('rb')
            parts = rf.readline(4096).decode('ascii', 'replace').split()
            if len(parts) != 4 or parts[0] != 'HELLO':
                self._reply(conn, 'ERR protocol')
                return
            if not hmac.compare_digest(parts[1].encode(), self.token.encode()):
                self._slog('HELLO rejected: bad token')
                self._reply(conn, 'ERR token')
                return
            try:
                pid = int(parts[2])
            except ValueError:
                self._reply(conn, 'ERR protocol')
                return
            blocker = self._claim_owner(pid, parts[3], conn)
            if blocker is not None:
                self._slog(f'HELLO from {pid} refused: live owner {blocker}')
                self._reply(conn, f'REFUSED {blocker}')
                return
            claimed = True
            self._slog(f'owner {pid} attached')
            self._reply(conn, f'OK {self.last_seq} {self.cli.pid if self.cli else 0} '
                              f'{0 if self._exited.is_set() else 1}')
            conn.settimeout(None)
            while True:
                line = rf.readline(4096)
                if not line:
                    return
                cmd = line.decode('ascii', 'replace').split()
                if not cmd:
                    continue
                if not self._is_owner(conn):
                    self._reply(conn, 'ERR not_owner')
                    return
                if cmd[0] == 'SEND' and len(cmd) == 3:
                    if not self._do_send(conn, rf, cmd):
                        return
                elif cmd[0] == 'CLOSE_STDIN':
                    self._close_stdin()
                    self._reply(conn, 'ACK')
                elif cmd[0] == 'STATUS':
                    self._reply(conn, 'OK ' + json.dumps(self._status()))
                else:
                    self._reply(conn, 'ERR protocol')
        except (OSError, ValueError):
            pass
        finally:
            if claimed:
                self._release_owner(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _is_owner(self, conn: socket.socket) -> bool:
        with self._lock:
            return self.owner is not None and self.owner.conn is conn

    def _do_send(self, conn: socket.socket, rf: Any, cmd: List[str]) -> bool:
        """Returns False when the connection must be dropped (framing lost)."""
        try:
            seq, n = int(cmd[1]), int(cmd[2])
        except ValueError:
            self._reply(conn, 'ERR protocol')
            return False
        if n < 0 or n > _MAX_SEND_BYTES:
            self._reply(conn, 'ERR protocol')
            return False
        with self._lock:
            o = self.owner
            sender_pid = o.pid if (o is not None and o.conn is conn) else None
        data = rf.read(n)
        if len(data) != n:
            return False
        with self._send_lock:
            # Re-check ownership HERE, under the lock that serialises the write.
            # The check in _serve ran before the payload was read; a takeover in
            # between would let a displaced owner's buffered SEND consume the
            # next seq, and the new owner's payload would then be ACKed as a
            # duplicate and dropped.
            if sender_pid is None or not self._is_owner(conn):
                self._reply(conn, 'ERR not_owner')
                return False
            if seq <= self.last_seq:
                self._reply(conn, f'ACK {seq}')        # resend after reconnect: already written
                return True
            if seq != self.last_seq + 1:
                self._reply(conn, f'ERR seq_gap {self.last_seq + 1}')
                return True
            if self._stdin_closed or self.cli is None or self.cli.stdin is None:
                self._reply(conn, 'ERR stdin_closed')
                return True
            try:
                self.cli.stdin.write(data)
                self.cli.stdin.flush()
            except (OSError, ValueError):
                self._stdin_closed = True
                self._reply(conn, 'ERR stdin_closed')
                return True
            with self._lock:
                cur = self.owner
                still_ours = cur is not None and cur.pid == sender_pid
                if still_ours:
                    self.last_seq = seq
            if not still_ours:
                # The write blocked (full pipe) and a different owner took over
                # meanwhile. The bytes are out, but this seq belongs to the old
                # owner's numbering: advancing last_seq would make the new
                # owner's next SEND read as a duplicate and be dropped.
                self._slog(f'SEND {seq} written by displaced owner {sender_pid}; seq not advanced')
                self._reply(conn, 'ERR not_owner')
                return False
        self._reply(conn, f'ACK {seq}')
        return True

    def _status(self) -> Dict[str, Any]:
        m = self._manifest()
        m.pop('token', None)
        m['cli_alive'] = not self._exited.is_set()
        m['stdin_closed'] = self._stdin_closed
        return m

    # ── CLI exit ──
    def _wait_cli(self) -> None:
        assert self.cli is not None
        rc = self.cli.wait()
        with self._cli_lock:
            self._cli_reaped = True                    # from here the pid may be reused by anyone
        info: Dict[str, Any] = {
            'schema': SCHEMA, 'launch_id': self.spec.get('launch_id'), 'rc': rc,
            'signal': (-rc if (rc is not None and rc < 0 and sys.platform != 'win32') else None),
            'reason': self._exit_reason, 'ended_at': time.time(),
        }
        try:
            write_json_atomic(self._p('exit.json'), info)
        except OSError as e:
            self._slog(f'exit.json write failed: {e}')
        with self._lock:
            self._exited.set()
            # An owner connection that is open when the CLI exits is the one
            # that will collect; if none is open, wait for a HELLO + close.
        self._slog(f'cli exited rc={rc} reason={self._exit_reason}')
        self._wake.set()

    def _expire_cli(self) -> bool:
        """Tree-kill the CLI at orphan_max, but only while the pid is still the
        CLI we spawned. Once the wait thread has reaped it (POSIX) the pid is
        free for reuse, and exit.json can take arbitrarily long to write, so
        the main loop may still see `_exited` unset: killing then would take
        out an unrelated process group. Held under `_cli_lock` so the reap
        flag cannot flip between the check and the kill."""
        cli = self.cli
        with self._cli_lock:
            if cli is None or self._cli_reaped:
                self._slog('orphan_max reached: CLI already reaped; not killing')
                return False
            if self.cli_ct is not None and not same_process(cli.pid, self.cli_ct):
                self._slog(f'orphan_max reached: pid {cli.pid} is no longer the CLI; not killing')
                return False
            self._exit_reason = 'expired'
            kill_tree(cli.pid)
            return True

    # ── main ──
    def run(self) -> int:
        try:
            self._load_spec()
        except (OSError, ValueError, KeyError) as e:
            self._slog(f'bad spec: {e}')
            return 2
        t = {**DEFAULT_TIMERS, **(self.spec.get('timers') or {})}
        owner_pid = self.spec.get('owner_pid')
        if owner_pid:
            self.owner = _Owner(int(owner_pid), proc_create_time(int(owner_pid)), '', None)
        try:
            self._spawn_cli()
        except OSError as e:
            self._slog(f'cli spawn failed: {e}')
            write_json_atomic(self._p('exit.json'), {
                'schema': SCHEMA, 'launch_id': self.spec.get('launch_id'), 'rc': None,
                'signal': None, 'reason': 'spawn_failed', 'error': str(e), 'ended_at': time.time()})
            return 1
        assert self.cli is not None
        if self.mode == MODE_INTERACTIVE:
            self._start_listener()
        self._write_manifest()
        self._slog(f'started mode={self.mode} cli={self.cli.pid} port={self.port}')
        threading.Thread(target=self._wait_cli, daemon=True).start()

        policy = LifetimePolicy(grace_s=t['grace_s'], exit_grace_s=t['exit_grace_s'],
                                orphan_max_s=t['orphan_max_s'], clock=self._clock,
                                idle_close=self.mode == MODE_INTERACTIVE)
        probe = TreeProbe(self.cli.pid)
        tick = float(t['tick_s'])
        while True:
            exited = self._exited.is_set()
            if exited and self.mode == MODE_ONESHOT:
                break                                  # nothing to collect from a socketless shim
            try:
                size = os.path.getsize(self._p('out.log'))
            except OSError:
                size = 0
            attached = self._attached()
            action = policy.tick(owner_attached=attached, cli_exited=exited,
                                 collected=self._collected, log_size=size,
                                 tree_busy=(probe.busy() if (not exited and not attached) else None))
            if action == A_FINISH:
                break
            if action == A_EXPIRE:
                self._slog('orphan_max reached: killing CLI')
                self._expire_cli()
            elif action == A_CLOSE_STDIN:
                self._slog('orphaned and idle: closing CLI stdin')
                self._exit_reason = 'stdin_idle'
                self._close_stdin()
            self._wake.wait(tick)
            self._wake.clear()
        self._write_manifest()                         # final last_seq
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        return 0


def main(argv: List[str]) -> int:
    if len(argv) != 1:
        return 2
    return Shim(argv[0]).run()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
