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
               stdin. `close()` then BLOCKS the delivery code until the CLI
               has really taken the prompt (the shim deletes prompt.txt only
               after the write is flushed): it returns on success and raises
               BrokenPipeError if the prompt was not delivered, exactly where
               a real pipe would. The delivery code's own bookkeeping (context
               fingerprint, steward ack) runs after `close()`, so it is
               truthful.

  Delivery code keeps all its own bookkeeping unchanged, and must reach the
  stream through `p.stdin`, never through the name the Popen result is
  assigned to. `defer_delivery=True` holds delivery back until the caller has
  installed the proc into its session (`finish_delivery(proc)`), for a
  delivery whose failure is reported into session state that does not exist
  until then.

Fallback
  If the shim cannot be launched (breakaway refused, trampoline failed, the
  shim reported it could not start the CLI) nothing is left running and the
  call falls back to the plain Popen path, so the session is simply
  non-adoptable, as today; the captured prompt is written to that Popen's
  stdin (the delivery code is not run a second time). A missing CLI binary
  raises the same FileNotFoundError it always did. The one case that does NOT
  fall back is a failed teardown (`cli_running`): a CLI may still be alive, and
  starting a second one would double the agent, so that raises instead -- after
  mc/agent_uncertain_launch.py has terminated the uncertain shim and CLI by
  identity-checked pid, or, if that cannot be confirmed, left a marker that
  makes every later spawn for the session raise until it can be.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from mc import agent_shim as _shim
from mc import agent_shim_client as _client
from mc import agent_uncertain_launch as _uncertain
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
    byte-for-byte as it would through the pipe. `close()` ends the payload
    (`done`) and then waits for `settle()`: only once the CLI has really
    received the bytes does it return, and a delivery that failed raises
    BrokenPipeError, so the caller's post-close bookkeeping never records a
    prompt the CLI did not get."""

    def __init__(self) -> None:
        self._sink = io.BytesIO()
        super().__init__(self._sink, encoding='utf-8', errors='replace', write_through=True)
        self.done = threading.Event()                  # close() reached: payload is final
        self._settled = threading.Event()
        self._ok = False
        self._why = ''

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
            self._settled.wait()
            if not self._ok:
                raise BrokenPipeError(self._why or 'prompt was not delivered to the CLI')

    def __del__(self) -> None:
        # IOBase.__del__ would call close(), which waits for settle(): never
        # block (or raise) in a finalizer.
        pass

    def settle(self, ok: bool, why: str = '') -> None:
        """The prompt reached the CLI (`ok`), or it did not. Idempotent: the
        first outcome stands."""
        if not self._settled.is_set():
            self._ok, self._why = ok, why
            self._settled.set()

    def payload(self) -> bytes:
        return self._sink.getvalue()


class _Holder:
    """The `p` handed to `deliver` when there is no Popen yet."""

    def __init__(self, stdin: Any):
        self.stdin = stdin


_CAPTURE_WAIT_S = 10.0
# How long a captured prompt may take to reach the CLI once the shim is up
# (the shim feeds it from a thread right after spawning the CLI).
_DELIVERY_CONFIRM_S = 10.0
# How long to wait for a previous turn's shim to leave before giving up on
# reusing the session directory (exit.json lands before the shim's last write).
_PRIOR_SHIM_GONE_S = 5.0


def _popen(cmd: Sequence[Any], *, cwd: Any, env: Optional[Mapping[str, str]],
           deliver: Optional[Callable[[Any], None]], pipe_stdin: Optional[bool] = None,
           creationflags: Optional[int] = None, startupinfo: Any = None,
           defer_delivery: bool = False) -> subprocess.Popen:
    """The pre-MC-1150 spawn, verbatim: every Mode A site passed exactly this.
    `creationflags`/`startupinfo` default to agent_runtime's; the Claude routes
    in agent_routes pass their own (server.py adds CREATE_NEW_PROCESS_GROUP to
    those), so each site keeps exactly the flags it had before the factory."""
    from mc.agent_runtime import _POPEN_FLAGS, _STARTUPINFO
    if pipe_stdin is None:
        pipe_stdin = deliver is not None
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if pipe_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        creationflags=_POPEN_FLAGS if creationflags is None else creationflags,
        startupinfo=_STARTUPINFO if startupinfo is None else startupinfo,
        **POPEN_NEW_SESSION,
    )
    if deliver is not None:
        if defer_delivery:
            _hold_delivery(proc, lambda: deliver(proc))
        else:
            deliver(proc)
    return proc


def _hold_delivery(proc: Any, start: Callable[[], None]) -> None:
    """Park the step that lets `proc`'s delivery proceed until `finish_delivery`."""
    proc._mc_finish_delivery = start


def finish_delivery(proc: Any) -> None:
    """Run the delivery step that `spawn_engine_proc(defer_delivery=True)` held
    back (once). A no-op for a proc with nothing pending, so a caller may call it
    unconditionally."""
    start = getattr(proc, '_mc_finish_delivery', None)
    if start is not None:
        proc._mc_finish_delivery = None
        start()


def _log(msg: str) -> None:
    _client._log(msg)


def _prompt_delivered(sdir: Path, launch_id: Any, timeout: float) -> 'tuple[bool, str]':
    """Did the shim hand prompt.txt to the CLI? The shim deletes it only after
    the write is flushed (agent_shim.Shim._feed_prompt), so its absence is the
    acknowledgment; a failed feed leaves it in place. A CLI that has exited with
    the file still there never took it."""
    path = sdir / 'prompt.txt'
    end = time.monotonic() + timeout
    while True:
        if not path.exists():
            return True, ''
        ex = _client.read_json(sdir / 'exit.json')
        if ex and ex.get('launch_id') == launch_id:
            if not path.exists():
                return True, ''
            return False, 'the CLI exited without taking the prompt'
        if time.monotonic() >= end:
            return False, f'the prompt was not delivered within {timeout:g}s'
        time.sleep(0.02)


def _prior_gone(prior: '_client.ManifestStatus', timeout: float) -> bool:
    """True only once the previous shim AND CLI are both positively gone
    (`process_state == 'gone'`). `inspect`'s booleans fold 'cannot tell' (the
    create-time lookup was denied) into False, which is not evidence of absence:
    `write_spec` deletes shim.json/exit.json, so reusing the directory beside an
    unverifiable process discards the only record of its pid/create-time. exit.json
    only says the CLI ended: its shim still writes a final manifest afterwards.
    Same rule as `agent_spawn_interactive._await_prior`."""
    m = prior.manifest
    end = time.monotonic() + timeout
    while True:
        if (_shim.process_state(m.get('shim_pid'), m.get('shim_ct')) == 'gone'
                and _shim.process_state(m.get('cli_pid'), m.get('cli_ct')) == 'gone'):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def _settle(cap: _CaptureStdin, proc: Any, defer: bool, ok: bool, why: str = '') -> None:
    """Tell the delivery code whether the CLI got the prompt. With `defer` the
    answer waits for the caller's `finish_delivery(proc)`."""
    if defer and proc is not None:
        _hold_delivery(proc, lambda: cap.settle(ok, why))
    else:
        cap.settle(ok, why)


def _capture_prompt(deliver: Callable[[Any], None], session_id: str, engine: str) -> _CaptureStdin:
    """Run `deliver` against a capture stream and wait until it has closed it
    (or `_CAPTURE_WAIT_S` passes), whichever thread ends up closing it. `deliver`
    normally just starts a writer thread, but a synchronous one blocks in
    `close()` until the outcome is settled, so it runs on its own thread. An
    exception it raises is re-raised here, as an inline call would."""
    cap = _CaptureStdin()
    err: list = []

    def run() -> None:
        try:
            deliver(_Holder(cap))
        except BaseException as e:
            err.append(e)
    runner = threading.Thread(target=run, daemon=True, name='agent-spawn-deliver')
    runner.start()
    end = time.monotonic() + _CAPTURE_WAIT_S
    # Wait for the CLOSE, not for `runner`: `deliver` normally returns as soon as it has
    # started a writer thread, so the runner dies long before the prompt is complete.
    # Only a raised exception ends the wait early.
    while not cap.done.is_set() and not err and time.monotonic() < end:
        cap.done.wait(0.02)
    if err:
        cap.settle(False, f'prompt delivery raised: {err[0]}')
        raise err[0]
    if not cap.done.is_set():
        _log(f'[agent-spawn] session={session_id} engine={engine}: prompt delivery did not '
             f'close stdin within {_CAPTURE_WAIT_S:.0f}s; sending what was written')
        cap.settle(False, 'prompt delivery did not finish')
    return cap


def spawn_engine_proc(cmd: Sequence[Any], *, session_id: Optional[str], engine: str,
                      cwd: Any = None, env: Optional[Mapping[str, str]] = None,
                      deliver: Optional[Callable[[Any], None]] = None,
                      cfg: Optional[Mapping[str, Any]] = None,
                      root: Optional[Any] = None,
                      creationflags: Optional[int] = None, startupinfo: Any = None,
                      defer_delivery: bool = False, interactive: bool = False) -> Any:
    """Spawn one engine turn. `deliver(p)` (optional) starts writing the prompt
    to `p.stdin` and closing it; its presence is what means "stdin is a pipe".
    `creationflags`/`startupinfo` are the flag-OFF Popen's (None = agent_runtime's).
    `interactive=True` is a CLI whose stdin stays open and is written by the
    caller after the spawn (Claude Mode B, the mid-turn roll): flag OFF it is the
    same Popen with `stdin=PIPE`; flag ON it runs under an interactive shim and
    `.stdin` goes over its socket (mc/agent_spawn_interactive.py). It excludes
    `deliver`. See the module docstring."""
    config = cfg if cfg is not None else _config()
    plain: dict = dict(cwd=cwd, env=env, creationflags=creationflags, startupinfo=startupinfo,
                       defer_delivery=defer_delivery)
    if interactive:
        if deliver is not None:
            raise ValueError('spawn_engine_proc: interactive and deliver are mutually exclusive')
        plain['pipe_stdin'] = True
    if not session_id:
        return _popen(cmd, deliver=deliver, **plain)

    base = Path(root) if root is not None else default_root()
    sdir = _client.session_dir(base, str(session_id))
    # Raises: no Popen fallback beside an uncertain CLI. Independent of the flag: turning the
    # feature off must not let a retry run beside a shim/CLI an earlier flag-ON launch left
    # unresolved. A session that never had one has no directory, so this is a single stat.
    _uncertain.refuse_if_pending(sdir, str(session_id))
    if not _client.enabled(config):
        return _popen(cmd, deliver=deliver, **plain)
    from mc import agent_shim_adopt as _adopt          # MC-1150 P4: flag ON only
    _adopt.ensure_checkpointer(config)
    if interactive:
        # Imported here so a default install (flag off) never loads it.
        from mc import agent_spawn_interactive as _interactive
        return _interactive.spawn_interactive(
            cmd, session_id=str(session_id), engine=engine, base=base, cwd=cwd, env=env,
            config=config, fallback=lambda: _popen(cmd, deliver=None, **plain))
    prior = _client.inspect(sdir)
    if prior is not None and not prior.exited and (prior.shim_alive or prior.cli_alive):
        # The previous turn of this session still runs under its shim; reusing
        # the directory would replace its manifest. Run this turn the old way.
        _log(f'[agent-spawn] session={session_id} engine={engine}: previous shim still alive; '
             'spawning without a shim')
        return _popen(cmd, deliver=deliver, **plain)
    if prior is not None and not _prior_gone(prior, _PRIOR_SHIM_GONE_S):
        # Either the previous CLI has exited but its shim has not (it still rewrites
        # shim.json after exit.json, which would overwrite this launch's manifest), or
        # a recorded process cannot be proven gone (identity lookup denied): reusing
        # the directory would delete its pid/create-time record. Leave shim.json/exit.json.
        _log(f'[agent-spawn] session={session_id} engine={engine}: previous shim/CLI still '
             'exiting or unverifiable; spawning without a shim')
        return _popen(cmd, deliver=deliver, **plain)

    cap = _capture_prompt(deliver, str(session_id), engine) if deliver is not None else None
    payload = cap.payload() if cap is not None else None
    try:
        start_offset = (sdir / 'out.log').stat().st_size   # out.log is append-only across turns
    except OSError:
        start_offset = 0

    try:
        res = _client.launch(base, str(session_id), argv=[os.fspath(a) for a in cmd],
                             cwd=os.fspath(cwd) if cwd is not None else None,
                             mode=_shim.MODE_ONESHOT, engine=engine,
                             env=env, prompt=payload, cfg=config, owner_pid=os.getpid(),
                             start_offset=start_offset)
    except BaseException as e:
        if cap is not None:
            cap.settle(False, f'shim launch raised: {e}')
        raise
    if res.ok and res.proc is not None:
        res.proc.args = list(cmd)
        if cap is not None:
            ok, why = _prompt_delivered(sdir, res.spec.get('launch_id'), _DELIVERY_CONFIRM_S)
            _settle(cap, res.proc, defer_delivery, ok, why)
        return res.proc
    if res.cli_running:
        if cap is not None:
            cap.settle(False, 'shim launch left the CLI in an unknown state')
        raise _uncertain.fail_launch(sdir, str(session_id), res.spec.get('launch_id'), res.error)
    _log(f'[agent-spawn] session={session_id} engine={engine}: shim launch failed '
         f'branch={res.branch} error={res.error}; spawning without a shim')
    if cap is None:
        return _popen(cmd, deliver=None, **plain)
    # The prompt is already captured: write it to the plain Popen's pipe and let
    # that write decide the delivery code's outcome (it is not run a second time).
    # The write runs on its own thread, as the pre-P2 delivery did: a CLI that fills
    # stdout before it reads a large prompt would block it, and this caller has not
    # started its reader yet. With `defer_delivery` the thread starts at finish_delivery.
    try:
        proc = _popen(cmd, deliver=None, pipe_stdin=True, **plain)
    except BaseException as e:
        cap.settle(False, f'spawn failed: {e}')
        raise
    pipe: Any = proc.stdin

    def feed() -> None:
        try:
            pipe.buffer.write(payload or b'')
            pipe.flush()
            pipe.close()
        except Exception as e:
            cap.settle(False, f'{type(e).__name__}: {e}')
        else:
            cap.settle(True)

    def start_feed() -> None:
        threading.Thread(target=feed, daemon=True, name='agent-spawn-fallback-feed').start()
    if defer_delivery:
        _hold_delivery(proc, start_feed)
    else:
        start_feed()
    return proc
