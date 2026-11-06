"""MC-1148 P8: passive, default-off fallback for unwrapped vendor tool calls.

No command execution, rewriting, output parsing or launcher/job correlation.
Readers enqueue small observations; only the bounded worker touches storage.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import queue
import re
import shlex
import threading
import time
import uuid
from typing import Any, Callable, Iterator

from mc import state
from mc.core import _log
from mc.test_run_store import SCHEMA, TestRunStore

FLAG = 'test_run_progress_enabled'
MAX_PENDING = 256
MAX_IDENTITIES = 4096
MAX_COMMAND = 32768
_observer: DetectionObserver | None = None


@dataclass(frozen=True)
class ToolObservation:
    call_id: str
    name: str = ''
    command: Any = None
    completed: bool = False
    exit_code: int | None = None
    output: bool = False
    error: bool = False


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _code(record: dict[str, Any]) -> int | None:
    # Never read a command input, console string, is_error or status as an exit.
    value = record.get('exit_code', _mapping(record.get('metadata')).get('exit_code'))
    return value if type(value) is int else None


def _call(record: dict[str, Any], *, completed: bool = False) -> ToolObservation:
    args = record.get('input', record.get('arguments', record.get('parameters')))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    args = _mapping(args)
    return ToolObservation(
        record.get('call_id') or record.get('tool_id') or record.get('id') or '',
        record.get('name') or record.get('tool_name') or '',
        args.get('command', args.get('cmd')), completed,
        _code(record) if completed else None,
        bool(record.get('output') or record.get('content')) if completed else False,
        record.get('is_error') is True)


def observations(provider: str, raw: Any) -> Iterator[ToolObservation]:
    """Decode transport envelopes before normalized event/UI suppression."""
    if isinstance(raw, str):
        if provider == 'codex':
            raw = raw.removeprefix('Reading prompt from stdin...')
        try:
            raw = json.loads(raw)
        except ValueError:
            return
    msg = _mapping(raw)
    kind = msg.get('type')
    if provider == 'gemini' and kind in {'tool_use', 'tool_result'}:
        if kind == 'tool_use':
            yield _call(msg)
        else:
            yield ToolObservation(msg.get('tool_id') or '', completed=True,
                                  exit_code=_code(msg), output=bool(msg.get('output')),
                                  error=msg.get('status') == 'error')
        return
    if provider == 'codex' and kind in {'item.started', 'item.updated', 'item.completed'}:
        item = _mapping(msg.get('item'))
        item_kind = item.get('type')
        if item_kind == 'command_execution':
            yield ToolObservation(item.get('id') or '', 'shell', item.get('command'),
                                  kind == 'item.completed', _code(item),
                                  bool(item.get('aggregated_output')))
            return
        if item_kind in {'function_call', 'tool_call'}:
            # Completion of a call declaration is not completion of the tool.
            yield _call(item)
            return
        if item_kind in {'function_call_output', 'tool_result'}:
            yield ToolObservation(item.get('call_id') or item.get('tool_use_id') or
                                  item.get('id') or '', completed=True,
                                  exit_code=_code(item), output=bool(item.get('output')))
            return
        content = item.get('content') if item_kind == 'message' else None
    elif provider in {'claude', 'qwen'} and kind in {'assistant', 'user'}:
        content = _mapping(msg.get('message')).get('content')
    else:
        return
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'tool_use':
            yield _call(block)
        elif block.get('type') == 'tool_result':
            yield ToolObservation(block.get('tool_use_id') or '', completed=True,
                                  exit_code=_code(block), output=bool(block.get('content')),
                                  error=block.get('is_error') is True)


def candidate(name: str, command: Any) -> bool:
    """Conservative direct invocation recognition, never shell evaluation."""
    if not isinstance(name, str) or name.lower() not in {'bash', 'powershell', 'shell', 'run_shell_command',
                            'exec_command', 'shell_command', 'run_terminal_cmd'}:
        return False
    if isinstance(command, str):
        if len(command) > MAX_COMMAND or any(c in command for c in '\n\r;&|<>`'):
            return False
        # Reject substitutions, grouping and opaque shell execution.
        if '$(' in command or re.search(r'[()]', command):
            return False
        try:
            argv = [part.strip('"\'') for part in shlex.split(command, posix=False)]
        except ValueError:
            return False
    elif (isinstance(command, list) and command and
          all(isinstance(arg, str) for arg in command)):
        argv = command
    else:
        return False
    if not argv or sum(map(len, argv)) > MAX_COMMAND:
        return False
    normalized = [arg.replace('\\', '/').lower() for arg in argv]
    if any(arg.endswith('/tools/test-run.py') or arg == 'tools/test-run.py'
           for arg in normalized):
        return False
    exe = normalized[0].rsplit('/', 1)[-1].removesuffix('.exe')
    if exe in {'pytest', 'py.test'}:
        return True
    if re.fullmatch(r'python(?:\d+(?:\.\d+)*)?|py', exe):
        return len(argv) > 2 and argv[1:3] == ['-m', 'pytest']
    if exe == 'node' and len(argv) > 1:
        return argv[1] == '--test' or (
            normalized[1].endswith(('.js', '.mjs', '.cjs')) and
            bool(re.search(r'(?:^|[/_.-])(?:test[s]?|smoke)(?:[/_.-]|$)', normalized[1])))
    if exe in {'npm', 'npm.cmd', 'pnpm', 'pnpm.cmd', 'yarn', 'yarn.cmd'}:
        tail = argv[2:] if len(argv) > 1 and argv[1] == 'run' else argv[1:]
        return bool(tail and (tail[0] == 'test' or tail[0].startswith('test:')))
    if exe in {'npx', 'npx.cmd'}:
        return len(argv) > 2 and argv[1:3] == ['playwright', 'test']
    return False


@dataclass
class _Run:
    run_id: str
    seq: int = 0
    terminal: bool = False
    output_at: float | None = None
    marked: bool = False


class DetectionObserver:
    """Bounded nonblocking reader seam; no raw commands/output are retained."""

    def __init__(self, data_root: Path, load_project: Callable[[str], Any]):
        self.data_root = data_root
        self._store: TestRunStore | None = None
        self.load_project = load_project
        self.pending: queue.Queue[tuple[dict[str, Any], str, str, ToolObservation, float]] = queue.Queue(MAX_PENDING)
        self.runs: dict[tuple[str, str, str, str], _Run] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def store(self) -> TestRunStore:
        if self._store is None:
            self._store = TestRunStore(self.data_root)
        return self._store

    def observe(self, session: dict[str, Any], provider: str, raw: Any, proc: Any) -> None:
        if state.CONFIG.get(FLAG) is not True or provider not in {'claude', 'codex', 'gemini', 'qwen'}:
            return
        if session.get('incognito') or session.get('_codex_unattended_sandbox'):
            return
        if proc is None:
            return
        for item in observations(provider, raw):
            if not isinstance(item.call_id, str) or not item.call_id or len(item.call_id) > 512:
                continue
            # Commands are discarded on the reader thread after classification.
            if not item.completed and not candidate(item.name, item.command):
                continue
            eligible = candidate(item.name, item.command)
            item = ToolObservation(item.call_id, 'candidate' if eligible else '',
                                   completed=item.completed, exit_code=item.exit_code,
                                   output=item.output, error=item.error)
            with self._lock:
                if self._stop.is_set():
                    return
                identity = getattr(proc, '_test_run_detection_identity', None)
                if identity is None:
                    identity = uuid.uuid4().hex
                    setattr(proc, '_test_run_detection_identity', identity)
                if self._thread is None:
                    self._thread = threading.Thread(target=self._work, daemon=True,
                                                    name='test-run-detection')
                    self._thread.start()
                try:
                    self.pending.put_nowait((session, identity, provider, item, time.time()))
                except queue.Full:
                    _log('[test-run-detection] observation queue full; progress incomplete', flush=True)

    def _event(self, pid: str, run: _Run, kind: str, payload: dict[str, Any], at: float) -> None:
        run.seq += 1
        self.store.apply(pid, run.run_id, dict(schema=SCHEMA, run_id=run.run_id,
                         producer_id='tool_observer', producer_seq=run.seq,
                         observed_at=at, type=kind, payload=payload))

    def _apply(self, session: dict[str, Any], launch: str, provider: str,
               item: ToolObservation, at: float) -> None:
        pid, sid = session.get('project_id'), session.get('session_id')
        if (state.CONFIG.get(FLAG) is not True or not pid or not sid or pid == '_incognito'
                or state.agent_sessions.get(sid) is not session
                or session.get('incognito') or session.get('_codex_unattended_sandbox')):
            return
        project = self.load_project(pid)
        if not project or project.get('_is_incognito_project'):
            return
        identity = hashlib.sha256(f'{launch}:{item.call_id}'.encode()).hexdigest()
        key = (pid, sid, provider, identity)
        run = self.runs.get(key)
        if run is None:
            if item.name != 'candidate':
                return
            # Never evict identities then accidentally recreate a completed call.
            if len(self.runs) >= MAX_IDENTITIES:
                _log('[test-run-detection] identity capacity reached; progress incomplete', flush=True)
                return
            snap = self.store.create(pid, observed_at=at, owner_session_id=sid,
                                     source='generic-fallback', runner='unknown', unit='script',
                                     target_id=f'{provider}:{identity}', display_label='Test command')
            run = _Run(snap['run_id'])
            self.runs[key] = run
        if run.terminal:
            return
        if not run.marked:
            # A failed startup event must not leave a later green/complete run.
            self._event(pid, run, 'diagnostic', {'code': 'generic_fallback', 'capture': 'partial'}, at)
            run.marked = True
            self._event(pid, run, 'run_started', {}, at)
        if item.output and (run.output_at is None or item.completed or at - run.output_at >= 0.5):
            self._event(pid, run, 'heartbeat', {'output': True}, at)
            run.output_at = at
        if item.completed:
            if item.error:
                self._event(pid, run, 'diagnostic', {'code': 'tool_error_reported'}, at)
            if item.exit_code is None:
                self._event(pid, run, 'diagnostic', {'code': 'exit_code_unavailable'}, at)
            self.store.finish(pid, run.run_id, observed_at=at, exit_code=item.exit_code,
                              state='lost' if item.exit_code is None else
                              'passed' if item.exit_code == 0 else 'failed')
            run.terminal = True

    def _work(self) -> None:
        while not self._stop.is_set() or not self.pending.empty():
            try:
                args = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue  # Empty control queue, not a failed operation.
            try:
                self._apply(*args)
            except Exception as exc:
                _log(f'[test-run-detection] delivery failed: {type(exc).__name__}', flush=True)
            finally:
                self.pending.task_done()

    def close(self) -> None:
        with self._lock:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                _log('[test-run-detection] worker drain incomplete', flush=True)


def wire(data_root: Path, load_project: Callable[[str], Any]) -> None:
    global _observer
    if _observer is not None:
        _observer.close()
    _observer = DetectionObserver(data_root, load_project)


def observe(session: dict[str, Any], provider: str, raw: Any, proc: Any = None) -> None:
    """All observer failures are contained; arbitrary exception text is withheld."""
    try:
        if state.CONFIG.get(FLAG) is True and _observer is not None:
            _observer.observe(session, provider, raw, proc if proc is not None else session.get('proc'))
    except Exception as exc:
        _log(f'[test-run-detection] observation failed: {type(exc).__name__}', flush=True)


def compose_raw(previous: Callable[..., Any] | None, provider: str) -> Callable[..., Any] | None:
    """Preserve the full-fidelity callback and its existing failure semantics."""
    if state.CONFIG.get(FLAG) is not True or provider not in {'claude', 'codex', 'gemini', 'qwen'}:
        return previous
    def raw(line: str, sequence: int, session_id: str, session: dict[str, Any]) -> None:
        observe(session, provider, line)
        if previous is not None:
            previous(line, sequence, session_id, session)
    return raw
