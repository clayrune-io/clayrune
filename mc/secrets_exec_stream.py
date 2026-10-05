"""Server-parented streaming exec — the engine behind ``POST /api/secrets/exec-stream``
(MC-1047).

``tools/with-secret.py --raw`` exists for children that need live stdin and stdout
(an MCP stdio server above all). On a passphrase-locked vault the CLI process can
never read the vault itself: the unwrapped key lives only in the server's memory
after a human unlock (see ``mc/secrets_store.py``'s passphrase-lock section). The
one-shot ``/api/secrets/exec`` route cannot serve it (it buffers the whole output
and returns once). This module lets the SERVER spawn the child with the resolved
environment and relay stdin/stdout/stderr to the CLI, so the credential still
travels only from the vault into the child's environment and never back over HTTP.

What is held to the same bar as ``/api/secrets/exec``: the gates (loopback, no
Cloudflare header, per-boot token) are the route's; scope, ``allow_unattended`` and
the audit log are applied here by the same ``vault`` calls the exec route makes.

What is new, and why:

* **Output is redacted as a stream.** A dispensed value that straddles two reads is
  still replaced (`StreamRedactor`): the tail of a chunk that could be the start of a
  value is held back until the next read or end of stream. A child that deliberately
  prints a transformed value (base64, split in two writes with a pause) is not caught;
  neither is it by the one-shot route. This is an accident guard, not a sandbox.
* **The child lives and dies with its relay.** One attached output stream per
  session; when it drops (the CLI exited, was killed, or the machine's loopback
  broke) the child is killed, and a session nobody attaches to within
  ``ATTACH_GRACE_S`` is killed too. Idle heartbeats make a dead client visible
  without waiting for the child to speak. The CLI ends the session when its own
  stdin closes or its own process exits.
* **No orphans on a server restart.** On Windows the child joins a job object that
  kills its whole tree when the server's handle closes (the server exiting, however it
  exits). Elsewhere the child's stdin pipe breaks when the server dies, and the
  Process Manager's PID ledger lets the next boot reap whatever is left.
* **Registered with the Process Manager** (``tracked_processes`` + the PID ledger), so
  the user sees it and can kill it, and a restart can reap it. Its command preview is
  the command AS THE CALLER TYPED IT, before ``{{secret:…}}`` placeholders resolve; so
  are the program name in the audit record and the server log. The RESOLVED command line
  is never logged, audited or echoed in an error.
* **The child's environment is the caller's.** The client sends its own ``os.environ`` and
  that is the base (then ``unset``, then the injected secrets). The server's own
  environment, which carries every provider key it has hydrated, never reaches a child.
* **Bounded lifetime.** A session is ended after ``exec_stream_max_age_hours`` (default 24,
  0 disables), and a reader that stops taking output while the child is writing is cut off
  after ``STALL_WRITE_S``. Teardown kills the child BEFORE it touches stdin, so a stuck
  stdin write can never keep a child alive or hang ``shutdown_all``.
"""
from __future__ import annotations

import atexit
import ctypes
import json
import os
import queue
import secrets as _pysecrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass

from mc import process_ledger
from mc import secrets_exec_stream_wire as wire
from mc import secrets_store as vault
from mc.blueprints.secrets_routes import _kill_process_tree, _parse_pairs, _win_job_object_for
from mc.core import _log, now_iso
from mc.state import process_tracker_lock, tracked_processes

CONSUMER = 'server-exec-stream'

MAX_SESSIONS = 16
HEARTBEAT_S = 5.0        # idle gap after which a heartbeat frame probes the client
ATTACH_GRACE_S = 20.0    # a started session must be attached to within this
STALL_WRITE_S = 60.0     # a frame handed to the server that is not taken within this = stalled
WATCHDOG_TICK_S = 1.0
DEFAULT_MAX_AGE_HOURS = 24.0
STDIN_CLOSE_WAIT_S = 2.0 # how long teardown waits for a stdin writer before leaving it
EXIT_DRAIN_S = 2.0      # after the child exits, how long its pipes may still drain
READ_CHUNK = 65536
QUEUE_FRAMES = 64        # bounded: a slow reader backs the child up, not the server
MAX_STDIN_POST = 1 << 20

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def _max_age_s() -> float:
    """Maximum session age in seconds, from ``exec_stream_max_age_hours`` in config.json
    (live read, default 24; 0 disables — the convention of every other minutes/hours knob).
    A junk value falls back to the default rather than disabling the cap."""
    try:
        from mc import state
        hours = float(state.CONFIG.get('exec_stream_max_age_hours', DEFAULT_MAX_AGE_HOURS) or 0)
    except (ImportError, AttributeError, TypeError, ValueError):
        hours = DEFAULT_MAX_AGE_HOURS
    return max(0.0, hours) * 3600.0


class TooManySessions(Exception):
    pass


class StdinClosed(Exception):
    pass


@dataclass
class Launch:
    """A request after every secret has been resolved. Holds plaintext (``env``,
    ``command`` after placeholder resolution, ``stdin_value``): never log or return it."""
    command: list[str]
    env: dict
    stdin_value: str | None
    cwd: str | None
    project_id: str | None
    unattended: bool
    preview: str
    program: str     # argv[0] AS TYPED (placeholders unresolved): the only name that is logged


def resolve_launch(data: dict) -> Launch:
    """Resolve a request body into a `Launch`, applying the vault's policy exactly as
    ``/api/secrets/exec`` does (scope, ``allow_unattended``, unattended detection that
    fails closed on an omitted session id, the audit trail). Raises ``ValueError`` for a
    malformed body, ``vault.VaultLocked`` for a locked vault, any other
    ``vault.SecretsError`` for a refusal. ``data['environ']`` (required) is the
    CALLER's environment, the child's base; ``data['unset']`` names variables to drop from it
    BEFORE secrets are injected, so a secret injected under a dropped name still arrives
    (``tools/with-secret.py --unset``). A locked vault is refused even when the request
    references no secret: the route spawns arbitrary commands, which a human-held lock
    should gate."""
    command = data.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(a, str) for a in command):
        raise ValueError('command must be a non-empty list of strings')
    unset = data.get('unset') or []
    if not isinstance(unset, list) or not all(isinstance(n, str) and n for n in unset):
        raise ValueError('unset must be a list of variable names')
    environ = data.get('environ')
    if (not isinstance(environ, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in environ.items())
            or any(not k or '=' in k[1:] or '\0' in k or '\0' in v for k, v in environ.items())):
        raise ValueError('environ must be an object mapping variable names to string values: '
                         "the child's environment is the caller's, never the server's")
    if vault.lock_state() == 'locked':
        raise vault.VaultLocked('the vault is locked')

    project_id = data.get('project_id') or None
    unattended, _reason = vault.detect_effective_unattended(
        bool(data.get('unattended', False)), data.get('claude_session_id') or None)
    env_pairs = _parse_pairs(data.get('env'), 'env')
    user_pairs = _parse_pairs(data.get('user'), 'user')
    totp_pairs = _parse_pairs(data.get('totp'), 'totp')
    stdin_name = data.get('stdin') or None

    nt = os.name == 'nt'
    drop = {n.upper() for n in unset} if nt else set(unset)
    env = {k: v for k, v in environ.items() if (k.upper() if nt else k) not in drop}
    env.update(vault.env_for(env_pairs, consumer=CONSUMER, project_id=project_id,
                             unattended=unattended))
    for var, sec in user_pairs:
        env[var] = vault.get_username(sec, project_id=project_id)
    for var, sec in totp_pairs:
        code, remaining = vault.generate_totp_code(
            sec, consumer=CONSUMER, project_id=project_id, unattended=unattended)
        if remaining < 5:                       # see tools/with-secret.py
            time.sleep(remaining + 1)
            code, remaining = vault.generate_totp_code(
                sec, consumer=CONSUMER, project_id=project_id, unattended=unattended)
        env[var] = code
    preview = vault.redact(' '.join(command))[:80]
    program = vault.redact(command[0] if '{{' in command[0] else os.path.basename(command[0]))
    resolved = [vault.resolve_placeholders(a, consumer=CONSUMER, project_id=project_id,
                                           unattended=unattended)[0] for a in command]
    stdin_value = (vault.get_secret_value(stdin_name, consumer=CONSUMER,
                                          project_id=project_id, unattended=unattended)
                   if stdin_name else None)
    return Launch(resolved, env, stdin_value, data.get('cwd') or None, project_id,
                  unattended, preview, program)


class StreamRedactor:
    """Replace every dispensed secret value in a byte stream, including one split
    across reads. After each chunk the longest suffix that could still grow into a
    value is held back (not emitted) until the next chunk or `finish()`. A chunk whose
    ending cannot begin a value is emitted whole, so a line-delimited protocol (MCP
    stdio, every message ending in a newline) loses no latency."""

    def __init__(self) -> None:
        self._buf = b''

    @staticmethod
    def _forms(value: str, name: str) -> list[tuple[bytes, bytes]]:
        """The value as it appears in a byte stream: raw, and inside a JSON string
        (an MCP reply escapes quotes, backslashes, control characters and, by default,
        non-ASCII, so a token holding one never matches its raw form). Encodings a child
        chooses on purpose (base64, hex, UTF-16) are not recognised; see docs/SECRETS.md."""
        marker = f'[redacted:{name}]'.encode('utf-8')
        forms = [(value.encode('utf-8'), marker)]
        for ascii_only in (True, False):
            escaped = json.dumps(value, ensure_ascii=ascii_only)[1:-1].encode('utf-8')
            if escaped and all(escaped != f for f, _m in forms):
                forms.append((escaped, marker))
        return forms

    def feed(self, data: bytes) -> bytes:
        values = [form for v, n in vault.dispensed_values() for form in self._forms(v, n)]
        values.sort(key=lambda f: len(f[0]), reverse=True)
        buf = self._buf + data
        if not values:
            self._buf = b''
            return buf
        for value, marker in values:
            if value in buf:
                buf = buf.replace(value, marker)
        hold = 0
        for value, _marker in values:
            for k in range(min(len(value) - 1, len(buf)), hold, -1):
                if buf.endswith(value[:k]):
                    hold = k
                    break
        self._buf = buf[len(buf) - hold:] if hold else b''
        return buf[:len(buf) - hold]

    def finish(self) -> bytes:
        out, self._buf = self._buf, b''
        return out


def _signed32(code: int) -> int:
    """Windows reports e.g. 0xC000013A as an unsigned int; the wire carries int32."""
    return code - (1 << 32) if code >= (1 << 31) else code


def _set_kill_on_close(job) -> bool:
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


class StreamSession:
    """One child, its three pipes, and the bounded queue of frames headed for the
    one client attached to read them. Build with `start`, never directly."""

    def __init__(self, launch: Launch, proc: subprocess.Popen, job) -> None:
        self.id = _pysecrets.token_urlsafe(24)
        self.proc = proc
        self.job = job
        self.project_id = launch.project_id
        self.unattended = launch.unattended
        self.program = launch.program
        self.started = time.monotonic()
        self._q: queue.Queue = queue.Queue(QUEUE_FRAMES)
        self._lock = threading.Lock()
        self._attached = False
        self._torn_down = False
        self._closing = threading.Event()
        self._stdin_lock = threading.Lock()
        self._stdin_closed = False
        self._exit_code: int | None = None
        self._pumps: list[threading.Thread] = []
        self._timer = threading.Timer(ATTACH_GRACE_S, self._attach_deadline)
        self._timer.daemon = True
        self._yielded_at: float | None = None   # set while a frame is with the server's writer
        self.client_socket = None               # the response's socket, to cut a stalled reader loose

    # -- output side -------------------------------------------------------

    def _put(self, frame: bytes) -> None:
        while not self._closing.is_set():
            try:
                self._q.put(frame, timeout=0.5)
                return
            except queue.Full:
                continue

    def _pump(self, stream, channel: int) -> None:
        redactor = StreamRedactor()
        try:
            while True:
                chunk = stream.read(READ_CHUNK)
                if not chunk:
                    break
                out = redactor.feed(chunk)
                if out:
                    self._put(wire.pack(channel, out))
        except (OSError, ValueError):
            pass                                  # the pipe closed under us: that is the end
        finally:
            tail = redactor.finish()
            if tail:
                self._put(wire.pack(channel, tail))
            try:
                stream.close()
            except OSError:
                pass

    def _watch_exit(self) -> None:
        code = self.proc.wait()
        for t in self._pumps:
            t.join(EXIT_DRAIN_S)
        # A grandchild that kept a pipe open outlives its parent: end the tree so
        # the pipes close and the last output is flushed before the exit frame.
        if any(t.is_alive() for t in self._pumps):
            _kill_process_tree(self.proc, self.job)
            for t in self._pumps:
                t.join(0.5)
        self._exit_code = _signed32(code)
        self._put(wire.pack_exit(self._exit_code))

    def attach(self) -> bool:
        """Claim the one output reader. False if another reader already has it."""
        with self._lock:
            if self._attached or self._torn_down:
                return False
            self._attached = True
        self._timer.cancel()
        return True

    def _handover(self, frame: bytes):
        """Yield ``frame`` to the server's writer, timing how long it holds it: a client
        that does not read blocks that write, and the watchdog reads this stamp."""
        self._yielded_at = time.monotonic()
        yield frame
        self._yielded_at = None

    def first_frame(self):
        """The session-id frame the response opens with."""
        yield from self._handover(wire.pack(wire.CH_SESSION, self.id.encode('ascii')))

    def frames(self):
        """Frames for the attached reader, ending after the exit frame. When the
        generator is closed early (the client's connection broke) the child is
        killed: a relay that is gone is the signal that the agent session dropped it."""
        delivered_exit = False
        try:
            while True:
                try:
                    frame = self._q.get(timeout=HEARTBEAT_S)
                except queue.Empty:
                    if self._closing.is_set():      # ended by the server (age, stall, /end)
                        return
                    frame = wire.pack(wire.CH_HEARTBEAT)
                yield from self._handover(frame)
                if frame[0] == wire.CH_EXIT:
                    delivered_exit = True
                    return
        finally:
            self.teardown('exit' if delivered_exit else 'client_gone')

    def _attach_deadline(self) -> None:
        with self._lock:
            attached = self._attached
        if not attached:
            self.teardown('never_attached')

    def _watchdog(self) -> None:
        """End the session at its maximum age, or when the attached reader has stopped
        taking frames for ``STALL_WRITE_S`` (werkzeug has no send timeout, and a reader
        that never reads would otherwise pin one of the ``MAX_SESSIONS`` slots forever)."""
        max_age = _max_age_s()
        while not self._closing.wait(WATCHDOG_TICK_S):
            now = time.monotonic()
            if max_age and now - self.started >= max_age:
                try:
                    self._q.put_nowait(wire.pack(
                        wire.CH_ERROR, b'the session reached its maximum age and was ended'))
                except queue.Full:
                    pass
                self.teardown('max_age')
                return
            handed = self._yielded_at
            if handed is not None and now - handed >= STALL_WRITE_S:
                self.teardown('write_stalled')
                return

    # -- input side --------------------------------------------------------

    def write_stdin(self, data: bytes) -> None:
        with self._stdin_lock:
            if self._stdin_closed or self.proc.stdin is None:
                raise StdinClosed()
            view = memoryview(data)
            try:
                while view:
                    written = self.proc.stdin.write(view)
                    if not written:
                        raise OSError('stdin pipe accepted no bytes')
                    view = view[written:]
            except (OSError, ValueError) as e:
                self._close_stdin_locked()
                raise StdinClosed() from e

    def close_stdin(self, wait: float | None = None) -> None:
        """Close the child's stdin. ``wait`` bounds how long to wait for a writer that is
        holding the lock (blocked on a full pipe); on timeout the close is left to that
        writer, which fails as soon as the child is gone."""
        if not self._stdin_lock.acquire(timeout=-1 if wait is None else wait):
            return
        try:
            self._close_stdin_locked()
        finally:
            self._stdin_lock.release()

    def _close_stdin_locked(self) -> None:
        self._stdin_closed = True
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass

    # -- end of life -------------------------------------------------------

    def teardown(self, reason: str) -> None:
        """Idempotent. Kills the child tree if it is still running, forgets the
        session, leaves the Process Manager and the PID ledger, and audits the end."""
        with self._lock:
            if self._torn_down:
                return
            self._torn_down = True
        self._closing.set()
        self._timer.cancel()
        # Kill FIRST. A stdin write blocked on a full pipe holds `_stdin_lock` until the
        # child reads or dies; closing stdin before the kill waited on that lock and so
        # never reached the kill (Wren P1-1). Once the child is dead the write fails and
        # releases the lock, so the close that follows is bounded, never required.
        if self.proc.poll() is None:
            _kill_process_tree(self.proc, self.job)
        self.close_stdin(wait=STDIN_CLOSE_WAIT_S)
        if reason in ('max_age', 'write_stalled') and self.client_socket is not None:
            try:                                  # release the server thread stuck sending
                self.client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.job:
            try:
                ctypes.windll.kernel32.CloseHandle(self.job)
            except Exception as e:
                _log(f"[secrets] exec-stream job handle close failed: {e}")
        with _sessions_lock:
            _sessions.pop(self.id, None)
        with process_tracker_lock:
            tracked_processes.pop(self.proc.pid, None)
        process_ledger._persist_pid_ledger()
        code = self.proc.poll()
        vault.audit_event('exec_stream_end', consumer=CONSUMER, project=self.project_id,
                          pid=self.proc.pid, reason=reason,
                          exit_code=None if code is None else _signed32(code),
                          seconds=round(time.monotonic() - self.started, 1))
        _log(f"[secrets] exec-stream {self.id[:8]} ended ({reason}) pid={self.proc.pid}")


_sessions: dict[str, StreamSession] = {}
_sessions_lock = threading.Lock()


def get(session_id: str) -> StreamSession | None:
    with _sessions_lock:
        return _sessions.get(session_id)


def start(launch: Launch) -> StreamSession:
    """Spawn the child and begin relaying. Raises `TooManySessions` at the cap,
    ``OSError`` if the program cannot be started. The child's stdin carries
    ``launch.stdin_value`` and is then closed (as ``with-secret.py --stdin`` does
    in-process); otherwise it stays open for the client's POSTs."""
    kwargs: dict = dict(env=launch.env, cwd=launch.cwd, bufsize=0,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if os.name == 'nt':
        # Own process group so a tree kill reaches it; no console window, because
        # the server may have no console and a node child would otherwise open one.
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True
    with _sessions_lock:
        if len(_sessions) >= MAX_SESSIONS:
            raise TooManySessions()
        proc = subprocess.Popen(launch.command, **kwargs)
        job = None
        if os.name == 'nt':
            job = _win_job_object_for(proc.pid)
            if job and not _set_kill_on_close(job):
                _log('[secrets] exec-stream: kill-on-close not set; a server exit may leave the child')
        session = StreamSession(launch, proc, job)
        _sessions[session.id] = session

    with process_tracker_lock:
        image, created = process_ledger._proc_identity(proc.pid)
        tracked_processes[proc.pid] = {
            'pid': proc.pid, 'name': 'with-secret child (server-parented)',
            'type': 'external', 'session_id': '',
            'project_id': launch.project_id or '',
            'project_name': launch.project_id or '',
            'command_preview': launch.preview, 'started_at': now_iso(),
            'os_image': image, 'create_time': created, 'proc': proc,
        }
    process_ledger._persist_pid_ledger()
    vault.audit_event('exec_stream_start', consumer=CONSUMER, project=launch.project_id,
                      unattended=launch.unattended, pid=proc.pid, program=session.program)
    _log(f"[secrets] exec-stream {session.id[:8]} started pid={proc.pid} program={session.program}")

    for stream, channel in ((proc.stdout, wire.CH_STDOUT), (proc.stderr, wire.CH_STDERR)):
        t = threading.Thread(target=session._pump, args=(stream, channel), daemon=True)
        session._pumps.append(t)
        t.start()
    threading.Thread(target=session._watch_exit, daemon=True).start()
    session._timer.start()
    threading.Thread(target=session._watchdog, daemon=True).start()
    # Last, and with the deadlines already running: a child that never reads its stdin
    # would block this write, and the attach deadline then kills it and releases us.
    if launch.stdin_value is not None:
        try:
            session.write_stdin(launch.stdin_value.encode('utf-8'))
        except StdinClosed:
            pass
        session.close_stdin()
    return session


def shutdown_all() -> None:
    """Orderly exit: end every session. (A hard exit is covered by the job object
    on Windows and by the next boot's ledger reaper.)"""
    with _sessions_lock:
        live = list(_sessions.values())
    for s in live:
        s.teardown('server_exit')


atexit.register(shutdown_all)
