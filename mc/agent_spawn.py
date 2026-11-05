"""One factory for the Mode A engine spawn sites (MC-1150 P2).

`spawn_engine_proc` replaces the `subprocess.Popen(...)` at every non-Mode-B
engine spawn: the per-turn engines (Gemini, Qwen, Codex, OpenCode, Goose, Aider,
Kiro) and the Claude `-p` per-turn paths. It returns either

  flag OFF   the same `subprocess.Popen` the call site built before, with the
             same keyword arguments (this module is NOT on the path of a
             default install), or
  flag ON    a `ShimProc` (mc/agent_shim_client.py): the CLI runs under a
             detached supervisor shim, writes its output to
             `<data>/agent_shims/<session_id>/out.log`, and so outlives a
             server restart. `.stdout` is a tail of that file.

`agent_survive_restart_enabled` is read from `state.CONFIG` on every call.

Prompt delivery (`deliver`)
  Most engines read the turn's prompt from stdin, written by a daemon thread
  the call site starts right after the Popen. A oneshot shim cannot take that
  late write (it has no socket), so the call site hands its "start delivery"
  step here as `deliver(p)`, where `p` is anything with a `.stdin`:

    flag OFF   `deliver(proc)` runs right after the Popen, exactly where the
               call site used to run it.
    flag ON    `deliver(holder)` runs before the shim is launched; `holder.stdin`
               is a capture stream that behaves like Popen's text-mode stdin
               (UTF-8, errors='replace', "\\n" translated to os.linesep). When
               the delivery code closes it, the captured bytes become the
               shim's prompt.txt; the shim feeds them to the CLI and closes
               stdin.

  Delivery code therefore keeps all its own bookkeeping (context fingerprint,
  steward ack) unchanged, and must reach the stream through `p.stdin`, never
  through the name the Popen result is assigned to.

Fallback
  If the shim cannot be launched (breakaway refused, trampoline failed, the
  shim reported it could not start the CLI) nothing is left running and the
  call falls back to the plain Popen path, so the session is simply
  non-adoptable, as today; a missing CLI binary raises the same
  FileNotFoundError it always did. The one case that does NOT fall back is a
  failed teardown (`cli_running`): a CLI may still be alive, and starting a
  second one would double the agent, so that raises instead.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from mc import agent_shim as _shim
from mc import agent_shim_client as _client
from mc.proc_kill import POPEN_NEW_SESSION

_root_override: Optional[Path] = None


def set_root(root: Optional[Any]) -> None:
    """Point the factory at a shims root (tests; the server uses the default)."""
    global _root_override
    _root_override = Path(root) if root is not None else None


def default_root() -> Path:
    """`<data root>/data/agent_shims`, the sibling of data/projects.

    Mirrors server._resolve_dirs(): MC_DATA_DIR wins, else the repo root (dev)
    or %APPDATA%/MissionControl (frozen)."""
    if _root_override is not None:
        return _root_override
    env = os.environ.get('MC_DATA_DIR')
    if env:
        base = Path(env)
    elif getattr(sys, 'frozen', False):
        base = Path(os.environ.get('APPDATA', str(Path.home()))) / 'MissionControl'
    else:
        base = Path(__file__).resolve().parent.parent
    return base / 'data' / 'agent_shims'


def _config() -> Mapping[str, Any]:
    from mc import state
    return state.CONFIG


class _CaptureStdin(io.TextIOWrapper):
    """Stands in for `Popen(text=True, encoding='utf-8', errors='replace').stdin`.
    Same TextIOWrapper settings subprocess uses, so a prompt reaches the CLI
    byte-for-byte as it would through the pipe. `close()` keeps the bytes and
    signals `done`."""

    def __init__(self) -> None:
        self._sink = io.BytesIO()
        super().__init__(self._sink, encoding='utf-8', errors='replace', write_through=True)
        self.done = threading.Event()

    @property
    def closed(self) -> bool:                          # type: ignore[override]
        return self.done.is_set()

    def write(self, s: str) -> int:                    # type: ignore[override]
        if self.done.is_set():
            raise ValueError('I/O operation on closed file.')
        return super().write(s)

    def close(self) -> None:
        if not self.done.is_set():
            try:
                super().flush()
            finally:
                self.done.set()

    def payload(self) -> bytes:
        return self._sink.getvalue()


class _Holder:
    """The `p` handed to `deliver` when there is no Popen yet."""

    def __init__(self, stdin: Any):
        self.stdin = stdin


_CAPTURE_WAIT_S = 10.0


def _popen(cmd: Sequence[Any], *, cwd: Any, env: Optional[Mapping[str, str]],
           deliver: Optional[Callable[[Any], None]]) -> subprocess.Popen:
    """The pre-MC-1150 spawn, verbatim: every Mode A site passed exactly this."""
    from mc.agent_runtime import _POPEN_FLAGS, _STARTUPINFO
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if deliver is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        creationflags=_POPEN_FLAGS,
        startupinfo=_STARTUPINFO, **POPEN_NEW_SESSION,
    )
    if deliver is not None:
        deliver(proc)
    return proc


def _log(msg: str) -> None:
    _client._log(msg)


def spawn_engine_proc(cmd: Sequence[Any], *, session_id: Optional[str], engine: str,
                      cwd: Any = None, env: Optional[Mapping[str, str]] = None,
                      deliver: Optional[Callable[[Any], None]] = None,
                      cfg: Optional[Mapping[str, Any]] = None,
                      root: Optional[Any] = None) -> Any:
    """Spawn one engine turn. `deliver(p)` (optional) starts writing the prompt
    to `p.stdin` and closing it; its presence is what means "stdin is a pipe".
    Without it stdin is DEVNULL. See the module docstring."""
    config = cfg if cfg is not None else _config()
    if not (session_id and _client.enabled(config)):
        return _popen(cmd, cwd=cwd, env=env, deliver=deliver)

    base = Path(root) if root is not None else default_root()
    sdir = _client.session_dir(base, str(session_id))
    prior = _client.inspect(sdir)
    if prior is not None and not prior.exited and (prior.shim_alive or prior.cli_alive):
        # The previous turn of this session still runs under its shim; reusing
        # the directory would replace its manifest. Run this turn the old way.
        _log(f'[agent-spawn] session={session_id} engine={engine}: previous shim still alive; '
             'spawning without a shim')
        return _popen(cmd, cwd=cwd, env=env, deliver=deliver)

    payload: Optional[bytes] = None
    if deliver is not None:
        cap = _CaptureStdin()
        deliver(_Holder(cap))
        if not cap.done.wait(_CAPTURE_WAIT_S):
            _log(f'[agent-spawn] session={session_id} engine={engine}: prompt delivery did not '
                 f'close stdin within {_CAPTURE_WAIT_S:.0f}s; sending what was written')
        payload = cap.payload()

    try:
        start_offset = (sdir / 'out.log').stat().st_size   # out.log is append-only across turns
    except OSError:
        start_offset = 0

    res = _client.launch(base, str(session_id), argv=[os.fspath(a) for a in cmd],
                         cwd=os.fspath(cwd) if cwd is not None else None,
                         mode=_shim.MODE_ONESHOT, engine=engine,
                         env=env, prompt=payload, cfg=config, owner_pid=os.getpid(),
                         start_offset=start_offset)
    if res.ok and res.proc is not None:
        res.proc.args = list(cmd)
        return res.proc
    if res.cli_running:
        raise RuntimeError(f'agent shim launch left a CLI possibly running ({res.error}); '
                           'not starting a second one')
    _log(f'[agent-spawn] session={session_id} engine={engine}: shim launch failed '
         f'branch={res.branch} error={res.error}; spawning without a shim')
    return _popen(cmd, cwd=cwd, env=env, deliver=deliver)
