"""Server-side client for the agent supervisor shim (MC-1150 P1).

Nothing in the server calls this yet: P1 ships the building blocks, tested in
isolation; P2/P3 wire them into the spawn sites. With
`agent_survive_restart_enabled` false (the default) none of it runs.

What lives here
  config        flag + timer keys and their defaults
  spawn recipe  `spawn_detached` (trampoline + CREATE_BREAKAWAY_FROM_JOB on
                Windows, double-fork + setsid on POSIX). If Windows refuses
                breakaway the spawn is REFUSED, not worked around: the WMI
                escape loses the caller's environment and secrets must not
                touch disk (design Q4, decided). The branch taken is logged.
  manifest I/O  spec.json (server writes) and shim.json / exit.json (shim
                writes), plus `inspect` with the PID-reuse guard
  ShimClient    the loopback protocol: HELLO, SEND with idempotent sequence
                numbers, CLOSE_STDIN, STATUS
  LogTail       a `for line in proc.stdout` iterator over out.log that holds
                back a trailing partial line
  ShimProc      a Popen-compatible facade over all of the above

Files are documented in mc/agent_shim.py. The session directory lives at
`<data>/agent_shims/<session_id>/`, a SIBLING of data/projects/ (CLAUDE.md
DATA_DIR rule) and gitignored: it carries machine paths and the socket token.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from mc import agent_shim as _shim
from mc.atomic_json import write_json_atomic

# ───────────────────────────────── config ────────────────────────────────────
CONFIG_DEFAULTS: Dict[str, Any] = {
    'agent_survive_restart_enabled': False,   # off until P4 is live-proven (Q6)
    'shim_orphan_grace_min': 20,
    'shim_exit_grace_min': 60,
    'shim_orphan_max_hours': 12,
}


def enabled(cfg: Optional[Mapping[str, Any]]) -> bool:
    return bool((cfg or {}).get('agent_survive_restart_enabled',
                                CONFIG_DEFAULTS['agent_survive_restart_enabled']))


def timers_from_config(cfg: Optional[Mapping[str, Any]], *, tick_s: float = 5.0) -> Dict[str, float]:
    """Config minutes/hours -> the seconds the shim's spec carries."""
    c = cfg or {}

    def num(key: str) -> float:
        try:
            v = float(c.get(key, CONFIG_DEFAULTS[key]))
            return v if v > 0 else float(CONFIG_DEFAULTS[key])
        except (TypeError, ValueError):
            return float(CONFIG_DEFAULTS[key])

    return {'grace_s': num('shim_orphan_grace_min') * 60,
            'exit_grace_s': num('shim_exit_grace_min') * 60,
            'orphan_max_s': num('shim_orphan_max_hours') * 3600,
            'tick_s': tick_s}


def shims_root(data_dir: Any) -> Path:
    """`<data>/agent_shims`, a sibling of data/projects (never inside it)."""
    return Path(data_dir).parent / 'agent_shims'


def session_dir(root: Any, session_id: str) -> Path:
    return Path(root) / session_id


def _log(msg: str) -> None:
    try:
        from mc.core import _log as core_log
        core_log(msg)
    except Exception:
        print(msg, flush=True)


# ─────────────────────────────── spawn recipe ────────────────────────────────
BRANCH_WIN_BREAKAWAY = 'win_breakaway'
BRANCH_WIN_REFUSED = 'win_breakaway_refused'
BRANCH_POSIX = 'posix_double_fork'
BRANCH_FAILED = 'spawn_failed'

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_TRAMP_WAIT_S = 20.0


@dataclass
class SpawnResult:
    ok: bool
    branch: str
    error: Optional[str] = None


def _tramp_argv(sdir: str) -> List[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--clayrune-shim', 'tramp', sdir]
    # A venv python.exe on Windows is a launcher stub with its own Job object;
    # the trampoline must run under the real interpreter (design section 2).
    exe = getattr(sys, '_base_executable', sys.executable) if sys.platform == 'win32' else sys.executable
    return [exe, str(Path(__file__).with_name('agent_shim_tramp.py')), sdir]


def spawn_detached(sdir: Any, *, env: Optional[Mapping[str, str]] = None,
                   log: Optional[Callable[[str], None]] = None,
                   popen: Callable[..., Any] = subprocess.Popen,
                   platform: Optional[str] = None) -> SpawnResult:
    """Start the shim for the session dir, detached from this process tree.

    Returns once the trampoline has exited (the shim is then orphaned and
    running); shim readiness is `wait_ready`. `ok=False` means NOTHING was left
    running and the caller must spawn the CLI the old way (non-adoptable)."""
    say = log or _log
    plat = platform or sys.platform
    sdir = str(sdir)
    argv = _tramp_argv(sdir)
    kw: Dict[str, Any] = dict(close_fds=True, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if env is not None:
        kw['env'] = dict(env)
    if plat == 'win32':
        kw['creationflags'] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
        branch = BRANCH_WIN_BREAKAWAY
    else:
        kw['start_new_session'] = True
        branch = BRANCH_POSIX
    try:
        p = popen(argv, **kw)
    except OSError as e:
        # WinError 5 (PermissionError): the launching Job forbids breakaway.
        refused = plat == 'win32' and isinstance(e, PermissionError)
        branch = BRANCH_WIN_REFUSED if refused else BRANCH_FAILED
        say(f'[agent-shim] spawn branch={branch} session={Path(sdir).name} error={e}')
        return SpawnResult(False, branch, f'{type(e).__name__}: {e}')
    try:
        rc = p.wait(timeout=_TRAMP_WAIT_S)
    except subprocess.TimeoutExpired:
        say(f'[agent-shim] spawn branch={branch} session={Path(sdir).name} trampoline did not exit')
        return SpawnResult(False, BRANCH_FAILED, 'trampoline did not exit')
    if rc != 0:
        err = (read_json(Path(sdir) / 'spawn_error.json') or {}).get('error') or f'trampoline rc={rc}'
        say(f'[agent-shim] spawn branch={BRANCH_FAILED} session={Path(sdir).name} error={err}')
        return SpawnResult(False, BRANCH_FAILED, str(err))
    say(f'[agent-shim] spawn branch={branch} session={Path(sdir).name}')
    return SpawnResult(True, branch)


# ─────────────────────────────── manifest I/O ────────────────────────────────
def read_json(path: Any) -> Optional[Dict[str, Any]]:
    """None if missing or unparseable (a torn read is retried once: the shim
    replaces atomically, but a Windows reader can still land mid-replace)."""
    for attempt in (0, 1):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if attempt == 0:
                time.sleep(0.02)
    return None


def build_spec(*, argv: List[str], cwd: Optional[str], mode: str, session_id: str,
               engine: str = '', env_names: Optional[List[str]] = None,
               owner_pid: Optional[int] = None,
               timers: Optional[Mapping[str, float]] = None,
               launch_id: Optional[str] = None) -> Dict[str, Any]:
    """The spec.json body. `env_names` are NAMES only; values are never
    written (they ride on the spawn call)."""
    extra: List[str] = []
    try:
        import psutil  # type: ignore
        extra.append(os.path.dirname(os.path.dirname(os.path.abspath(psutil.__file__))))
    except Exception:
        pass                                           # idle test degrades to the hard ceiling
    return {
        'schema': _shim.SCHEMA,
        'launch_id': launch_id or uuid.uuid4().hex,
        'session_id': session_id,
        'engine': engine,
        'mode': mode,
        'argv': list(argv),
        'cwd': cwd,
        'env_names': sorted(env_names or []),
        'owner_pid': owner_pid if owner_pid is not None else os.getpid(),
        'timers': dict(timers or {}),
        'extra_sys_path': extra,
    }


def write_spec(sdir: Any, spec: Dict[str, Any], prompt: Optional[bytes] = None) -> None:
    """Prepare the dir for a (re)launch: drop the previous run's shim.json /
    exit.json / spawn_error.json so a stale one is never mistaken for this
    run's. out.log is kept (append-only across turns)."""
    d = Path(sdir)
    d.mkdir(parents=True, exist_ok=True)
    for stale in ('shim.json', 'exit.json', 'spawn_error.json', 'prompt.txt'):
        try:
            (d / stale).unlink()
        except FileNotFoundError:
            pass
    if prompt is not None:
        with open(d / 'prompt.txt', 'wb') as f:
            f.write(prompt)
    write_json_atomic(d / 'spec.json', spec)


def wait_ready(sdir: Any, launch_id: str, timeout: float = 20.0,
               poll: float = 0.05) -> Optional[Dict[str, Any]]:
    """Wait for THIS launch's shim.json (matched by launch_id), or for an
    exit.json (the shim could not start the CLI). Returns the manifest, or None."""
    d = Path(sdir)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        m = read_json(d / 'shim.json')
        if m and m.get('launch_id') == launch_id:
            return m
        if (read_json(d / 'exit.json') or {}).get('launch_id') == launch_id:
            return None
        time.sleep(poll)
    return None


@dataclass
class ManifestStatus:
    manifest: Dict[str, Any]
    shim_alive: bool
    cli_alive: bool
    exit_info: Optional[Dict[str, Any]] = None

    @property
    def exited(self) -> bool:
        return self.exit_info is not None


def inspect(sdir: Any) -> Optional[ManifestStatus]:
    """Read a session dir and apply the PID-reuse guard: a pid counts as alive
    only if it is running AND its creation time matches the recorded one
    (`process_ledger._should_reap_entry` posture). None if there is no shim.json."""
    d = Path(sdir)
    m = read_json(d / 'shim.json')
    if not m or m.get('schema') != _shim.SCHEMA:
        return None
    return ManifestStatus(
        manifest=m,
        shim_alive=_shim.same_process(m.get('shim_pid'), m.get('shim_ct')),
        cli_alive=_shim.same_process(m.get('cli_pid'), m.get('cli_ct')),
        exit_info=read_json(d / 'exit.json'))


# ───────────────────────────────── client ────────────────────────────────────
class ShimError(Exception):
    pass


class ShimUnavailable(ShimError):
    """No reachable shim: dead, wrong identity, or a socketless (oneshot) one."""


class ShimRefused(ShimError):
    """A different LIVE owner holds the shim (two servers)."""


class ShimTokenRejected(ShimError):
    pass


class ShimStdinClosed(ShimError):
    pass


@dataclass
class Hello:
    last_seq: int
    cli_pid: int
    cli_alive: bool


class ShimClient:
    """One owner connection to an interactive shim. Thread-safe: calls are
    serialised. `send` is idempotent across a reconnect (sequence numbers)."""

    def __init__(self, sdir: Any, *, owner_pid: Optional[int] = None,
                 boot_id: Optional[str] = None, connect_timeout: float = 5.0,
                 io_timeout: float = 30.0):
        self.dir = Path(sdir)
        self.owner_pid = owner_pid if owner_pid is not None else os.getpid()
        self.boot_id = boot_id or uuid.uuid4().hex[:12]
        self._connect_timeout = connect_timeout
        self._io_timeout = io_timeout
        self._sock: Optional[socket.socket] = None
        self._rf: Any = None
        self._seq = 0
        self._lock = threading.RLock()

    # ── connection ──
    def connect(self) -> Hello:
        with self._lock:
            self._drop()
            st = inspect(self.dir)
            if st is None or not st.shim_alive:
                raise ShimUnavailable('shim not alive (missing manifest or pid/create-time mismatch)')
            m = st.manifest
            if not m.get('port') or not m.get('token'):
                raise ShimUnavailable('shim has no stdin channel (oneshot)')
            try:
                s = socket.create_connection(('127.0.0.1', int(m['port'])), timeout=self._connect_timeout)
            except OSError as e:
                raise ShimUnavailable(f'connect failed: {e}') from e
            s.settimeout(self._io_timeout)
            rf = s.makefile('rb')
            try:
                s.sendall(f'HELLO {m["token"]} {self.owner_pid} {self.boot_id}\n'.encode())
                reply = rf.readline(4096).decode('ascii', 'replace').split()
            except OSError as e:
                s.close()
                raise ShimUnavailable(f'handshake failed: {e}') from e
            if not reply:
                s.close()
                raise ShimUnavailable('shim closed during handshake')
            if reply[0] == 'OK' and len(reply) == 4:
                self._sock, self._rf = s, rf
                self._seq = int(reply[1])
                return Hello(int(reply[1]), int(reply[2]), reply[3] == '1')
            s.close()
            if reply[0] == 'REFUSED':
                raise ShimRefused(f'live owner pid {reply[1] if len(reply) > 1 else "?"} holds the shim')
            if reply[0] == 'ERR' and len(reply) > 1 and reply[1] == 'token':
                raise ShimTokenRejected('shim rejected the token')
            raise ShimError(f'unexpected handshake reply: {" ".join(reply)}')

    def _drop(self) -> None:
        for obj in (self._rf, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._sock = self._rf = None

    def close(self) -> None:
        with self._lock:
            self._drop()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def _readline(self) -> List[str]:
        line = self._rf.readline(4096).decode('ascii', 'replace').split()
        if not line:
            raise ConnectionError('shim closed the connection')
        return line

    # ── commands ──
    def send(self, data: bytes) -> int:
        """Write `data` to the CLI's stdin; returns its sequence number. If the
        connection drops mid-call the client reconnects and consults the shim's
        `last_seq`: a send the shim already applied is NOT repeated."""
        with self._lock:
            if self._sock is None:
                self.connect()
            seq = self._seq + 1
            for attempt in (0, 1):
                try:
                    assert self._sock is not None
                    self._sock.sendall(f'SEND {seq} {len(data)}\n'.encode() + data)
                    reply = self._readline()
                except (OSError, ConnectionError):
                    if attempt == 1:
                        raise ShimUnavailable('send failed after reconnect')
                    hello = self.connect()              # may raise ShimUnavailable/Refused
                    if hello.last_seq >= seq:
                        self._seq = seq                 # it landed; only the ACK was lost
                        return seq
                    continue
                if reply[0] == 'ACK':
                    self._seq = seq
                    return seq
                if reply[0] == 'ERR' and len(reply) > 1 and reply[1] == 'stdin_closed':
                    raise ShimStdinClosed('CLI stdin is closed')
                if reply[0] == 'ERR' and len(reply) > 1 and reply[1] == 'not_owner':
                    self._drop()
                    raise ShimRefused('this connection was displaced by another owner')
                if reply[0] == 'ERR' and len(reply) > 2 and reply[1] == 'seq_gap':
                    self._seq = int(reply[2]) - 1
                raise ShimError(f'send rejected: {" ".join(reply)}')
            raise ShimUnavailable('send failed')

    def close_stdin(self) -> None:
        with self._lock:
            if self._sock is None:
                self.connect()
            assert self._sock is not None
            self._sock.sendall(b'CLOSE_STDIN\n')
            reply = self._readline()
            if reply[0] != 'ACK':
                raise ShimError(f'close_stdin rejected: {" ".join(reply)}')

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if self._sock is None:
                self.connect()
            assert self._sock is not None
            self._sock.sendall(b'STATUS\n')
            line = self._rf.readline(65536).decode('utf-8', 'replace')
            if not line.startswith('OK '):
                raise ShimError(f'status rejected: {line.strip()}')
            return json.loads(line[3:])


# ─────────────────────────────── log tail ────────────────────────────────────
class LogTail:
    """Iterate complete lines of out.log from `offset`, like `for line in
    proc.stdout`. Polls (no inotify on Windows). Holds a trailing partial line
    back until its newline arrives, so a reader never parses half a JSON line.
    Ends when `finished()` is true AND the file is drained; any final
    unterminated bytes are then yielded as the last line. `offset` is the byte
    position just past the last line yielded: the checkpoint value.

    Decoding matches the existing Popen sites: UTF-8, errors='replace'. A
    multi-byte character split across reads is safe because lines are cut on
    b'\\n' (never inside a UTF-8 sequence) before decoding."""

    def __init__(self, path: Any, *, offset: int = 0,
                 finished: Callable[[], bool] = lambda: True, poll_s: float = 0.05):
        self.path = str(path)
        self.offset = int(offset)
        self._finished = finished
        self._poll = poll_s
        self._buf = b''
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        while True:
            nl = self._buf.find(b'\n')
            if nl >= 0:
                raw, self._buf = self._buf[:nl + 1], self._buf[nl + 1:]
                self.offset += len(raw)
                return raw.decode('utf-8', 'replace')
            if self._closed:
                raise StopIteration
            done = self._finished()                    # sample BEFORE the read: no lost tail
            chunk = self._read_more()
            if chunk:
                self._buf += chunk
                continue
            if done:
                if self._buf:
                    raw, self._buf = self._buf, b''
                    self.offset += len(raw)
                    return raw.decode('utf-8', 'replace')
                raise StopIteration
            time.sleep(self._poll)

    def _read_more(self) -> bytes:
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.offset + len(self._buf))
                return f.read(1 << 20)
        except FileNotFoundError:
            return b''
        except OSError:
            return b''


# ─────────────────────────── Popen-compatible facade ─────────────────────────
class _ClosedStdin:
    closed = True

    def write(self, data: Any) -> int:
        raise BrokenPipeError('CLI stdin is closed (oneshot shim)')

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class ShimStdin:
    """`proc.stdin` for an interactive shim: write/flush/close over the socket."""

    def __init__(self, client: ShimClient):
        self._client = client
        self.closed = False

    def write(self, data: Any) -> int:
        if self.closed:
            raise BrokenPipeError('CLI stdin is closed')
        raw = data.encode('utf-8') if isinstance(data, str) else bytes(data)
        try:
            self._client.send(raw)
        except ShimStdinClosed as e:
            self.closed = True
            raise BrokenPipeError(str(e)) from e
        except ShimError as e:
            raise BrokenPipeError(str(e)) from e
        return len(data)

    def flush(self) -> None:
        return None                                    # send() returns only after the shim ACKs

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._client.close_stdin()
        except ShimError:
            pass
        except (OSError, ConnectionError):
            pass


class ShimProc:
    """The pieces of `subprocess.Popen` the stream readers and stop paths use.
    `.pid` is the CLI's pid, so `_kill_pid(proc.pid, tree=True)` keeps meaning
    what it means today; the shim is not in the CLI's tree."""

    def __init__(self, sdir: Any, *, client: Optional[ShimClient] = None,
                 start_offset: int = 0, poll_s: float = 0.05):
        self.dir = Path(sdir)
        st = inspect(self.dir)
        if st is None:
            raise ShimUnavailable('no shim manifest')
        self.manifest = st.manifest
        self.pid: int = int(self.manifest['cli_pid'])
        self.shim_pid: int = int(self.manifest['shim_pid'])
        self._cli_ct = self.manifest.get('cli_ct')
        self._shim_ct = self.manifest.get('shim_ct')
        self._client = client
        self.returncode: Optional[int] = None
        self.exit_inferred = False
        self.stdout = LogTail(self.dir / 'out.log', offset=start_offset,
                              finished=self._finished, poll_s=poll_s)
        self.stdin: Any = ShimStdin(client) if client is not None else _ClosedStdin()

    def _cli_alive(self) -> bool:
        return _shim.same_process(self.pid, self._cli_ct)

    def _shim_alive(self) -> bool:
        return _shim.same_process(self.shim_pid, self._shim_ct)

    def _finished(self) -> bool:
        return self.poll() is not None

    def poll(self) -> Optional[int]:
        if self.returncode is not None:
            return self.returncode
        info = read_json(self.dir / 'exit.json')
        if info is not None and info.get('launch_id') in (None, self.manifest.get('launch_id')):
            rc = info.get('rc')
            self.returncode = int(rc) if rc is not None else -1
            return self.returncode
        # No exit.json. The CLI dying while the shim is alive is a few-ms race
        # (the shim is about to write it): keep waiting. Only when BOTH are
        # gone is the shim's own crash the cause; infer an unknown status.
        # "Gone" must be positively verified: a failed lookup ('unknown') is
        # still running, not an exit.
        if (_shim.process_state(self.pid, self._cli_ct) == 'gone'
                and _shim.process_state(self.shim_pid, self._shim_ct) == 'gone'):
            self.returncode = -1
            self.exit_inferred = True
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if end is not None and time.monotonic() >= end:
                raise subprocess.TimeoutExpired(['shim-cli'], timeout or 0)
            time.sleep(0.05)

    def kill(self) -> None:
        """Identity-checked tree kill of the CLI (never the shim)."""
        if self._cli_alive():
            _shim.kill_tree(self.pid)

    terminate = kill

    def close(self) -> None:
        self.stdout.close()
        if self._client is not None:
            self._client.close()


# ─────────────────────────────── launch helper ───────────────────────────────
@dataclass
class LaunchResult:
    ok: bool
    branch: str
    proc: Optional[ShimProc] = None
    error: Optional[str] = None
    spec: Dict[str, Any] = field(default_factory=dict)
    # True ONLY when ok=False yet the shim or CLI is still alive (a failed
    # teardown). The caller must NOT start a second CLI in that case.
    cli_running: bool = False


# A teardown that ends with a process not positively 'gone' (typically a failed
# liveness lookup) is retried in the background: the surviving shim's live owner
# stops orphan expiry, so nothing else would ever reap it. Overridable in tests.
_TEARDOWN_RETRY_INTERVAL_S = 0.5
_TEARDOWN_RETRY_DEADLINE_S = 30.0


def _shim_states(pairs: List[Any]) -> List[str]:
    return [_shim.process_state(pid, ct) for pid, ct in pairs]


def _teardown_retry(pairs: List[Any], interval: float, deadline: float,
                    log: Callable[[str], None]) -> None:
    """Bounded re-run of the teardown: each pass kills only what is positively
    'ours' (never on 'unknown'), stops once every process is 'gone', and logs the
    survivors if the deadline passes first."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        time.sleep(max(0.0, min(interval, end - time.monotonic())))
        sts = _shim_states(pairs)
        for (pid, _ct), st in zip(pairs, sts):
            if st == 'ours':
                _shim.kill_tree(int(pid))
        if all(st == 'gone' for st in _shim_states(pairs)):
            return
    log('[agent-shim] teardown retry deadline passed; still alive: ' +
        ', '.join(f'pid={pid} state={st}' for (pid, _ct), st in zip(pairs, _shim_states(pairs))
                  if st != 'gone'))


def _teardown_started(m: Mapping[str, Any], timeout: float = 5.0,
                      log: Optional[Callable[[str], None]] = None) -> bool:
    """Kill the CLI tree and then the shim a failed launch left behind, each
    identity-checked against the manifest. True only when EACH is positively
    verified gone; a process whose liveness cannot be determined counts as
    still running (the caller must then not start a second CLI), and a bounded
    background retry keeps trying to clean it up."""
    pairs = [(m.get('cli_pid'), m.get('cli_ct')), (m.get('shim_pid'), m.get('shim_ct'))]

    for (pid, ct), st in zip(pairs, _shim_states(pairs)):   # CLI first, then the shim
        if st == 'ours':
            _shim.kill_tree(int(pid))                  # type: ignore[arg-type]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if all(st == 'gone' for st in _shim_states(pairs)):
            return True
        time.sleep(0.05)
    if all(st == 'gone' for st in _shim_states(pairs)):
        return True
    threading.Thread(target=_teardown_retry,
                     args=(pairs, _TEARDOWN_RETRY_INTERVAL_S, _TEARDOWN_RETRY_DEADLINE_S, log or _log),
                     name='shim-teardown-retry', daemon=True).start()
    return False


def launch(root: Any, session_id: str, *, argv: List[str], cwd: Optional[str],
           mode: str, engine: str = '', env: Optional[Mapping[str, str]] = None,
           prompt: Optional[bytes] = None, cfg: Optional[Mapping[str, Any]] = None,
           tick_s: float = 5.0, ready_timeout: float = 20.0,
           owner_pid: Optional[int] = None,
           log: Optional[Callable[[str], None]] = None) -> LaunchResult:
    """Spec -> detached spawn -> wait for the manifest -> attach. `ok=False`
    means no CLI is running under a shim and the caller falls back to its
    ordinary spawn (the session stays non-adoptable, as today) -- unless
    `cli_running` is set, in which case a teardown failed and the caller must
    not fall back. A shim that started but could not be attached is torn down
    before `ok=False` is returned, so the fallback never doubles the CLI."""
    sdir = session_dir(root, session_id)
    env_map = dict(env) if env is not None else dict(os.environ)
    spec = build_spec(argv=argv, cwd=cwd, mode=mode, session_id=session_id, engine=engine,
                      env_names=list(env_map), owner_pid=owner_pid,
                      timers=timers_from_config(cfg, tick_s=tick_s))
    write_spec(sdir, spec, prompt)
    sp = spawn_detached(sdir, env=env_map, log=log)
    if not sp.ok:
        return LaunchResult(False, sp.branch, error=sp.error, spec=spec)
    m = wait_ready(sdir, spec['launch_id'], timeout=ready_timeout)
    if m is None:
        err = (read_json(Path(sdir) / 'exit.json') or {}).get('error') or 'shim did not become ready'
        return LaunchResult(False, sp.branch, error=str(err), spec=spec)
    client: Optional[ShimClient] = None
    if mode == _shim.MODE_INTERACTIVE:
        client = ShimClient(sdir, owner_pid=owner_pid)   # HELLO as the pid the spec names
        try:
            client.connect()
        except ShimError as e:
            client.close()
            gone = _teardown_started(m, log=log)
            (log or _log)(f'[agent-shim] attach failed session={session_id} error={e}; '
                          f'started shim torn down={gone}')
            return LaunchResult(False, sp.branch, spec=spec, cli_running=not gone,
                                error=f'connect: {e}' + ('' if gone else ' (teardown incomplete: CLI/shim still alive)'))
    return LaunchResult(True, sp.branch, proc=ShimProc(sdir, client=client), spec=spec)
