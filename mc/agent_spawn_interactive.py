"""Interactive (stdin-open) engine spawns through the supervisor shim (MC-1150 P3).

`mc.agent_spawn.spawn_engine_proc(..., interactive=True)` is the one entry point
for a CLI whose stdin stays open after launch: Claude Mode B (persistent
stream-json) and the Claude `-p` mid-turn roll, whose prompt is written to stdin
after the reader is draining. With the survive-restart flag OFF the factory
returns the same `subprocess.Popen` the call site built before; this module is
not reached. With it ON, `spawn_interactive` runs the CLI under an `interactive`
shim and returns a `ShimProc` whose `.stdin` writes over the shim's loopback
socket, so `_bounded_stdin_write` / `_bounded_stdin_close` / `_kill_proc_background`
work on it unchanged.

Three things the oneshot factory did not need:

  1. A session's directory is reused by every respawn, and a Mode B respawn
     kills the old CLI on another thread while the new spawn starts. So a prior
     shim that is still alive is WAITED for (`_await_prior`), bounded, instead of
     being treated as "run this one the old way" at the first sight of it.
  2. An interactive shim does not finish when its CLI exits: it waits for its
     owner to collect the exit (close the connection) or for `shim_exit_grace_min`.
     `start_collector` closes this server's connection once the CLI is done, so
     the shim leaves within a tick and the next respawn finds the directory free.
  3. A write to the CLI's stdin can legitimately block while the CLI runs its
     SessionStart hooks (`resume_target.STARTUP_GRACE_MAX_S`); the socket round
     trip is given that long plus the default, so the stdin-hang watchdog in
     `_bounded_stdin_write` (which kills the CLI, breaking the shim's pipe write)
     is what ends a hung write, as it is for a real pipe.

Fallback is the oneshot factory's: if no shim could be started nothing is left
running and the caller gets the plain Popen (a non-adoptable session, as today);
a launch that cannot confirm its teardown raises instead (`agent_uncertain_launch`).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from mc import agent_shim as _shim
from mc import agent_shim_client as _client
from mc import agent_uncertain_launch as _uncertain
from mc import resume_target as _resume_target

# How long a previous CLI/shim of this session may take to leave before this
# spawn gives up on reusing the directory. A respawn's kill runs concurrently
# (taskkill /T, then the shim's tick), so seconds, not milliseconds.
_PRIOR_WAIT_S = 15.0
# After the CLI has exited, how long to wait for the shim to report it before
# closing the connection anyway.
_COLLECT_STATUS_S = 5.0
_COLLECT_POLL_S = 0.5


def _log(msg: str) -> None:
    _client._log(msg)


def send_timeout_s() -> float:
    """One socket round trip may wait as long as a stdin write may legitimately
    block (the startup grace) plus ShimClient's own default."""
    return float(_resume_target.STARTUP_GRACE_MAX_S) + 30.0


def _collect_prior(sdir: Path) -> None:
    """A previous run's CLI has exited but its shim waits for an owner to
    collect (typically the server that started it is gone). Be that owner for
    one connect/close so the shim can finish. Best-effort."""
    c = _client.ShimClient(sdir, io_timeout=5.0)
    try:
        c.connect()
    except _client.ShimError as e:
        _log(f'[agent-spawn] session={sdir.name} collecting the previous shim failed: {e}')
    finally:
        c.close()


def _prior_states(st: '_client.ManifestStatus') -> 'tuple[str, str]':
    """(shim, cli) three-valued liveness of the previous run, from the same
    recorded (pid, create-time) identities `inspect` used. `inspect`'s booleans
    fold 'cannot tell' into False, which is not evidence that a process is gone."""
    m = st.manifest
    return (_shim.process_state(m.get('shim_pid'), m.get('shim_ct')),
            _shim.process_state(m.get('cli_pid'), m.get('cli_ct')))


def _await_prior(sdir: Path, session_id: str, engine: str, timeout: float = _PRIOR_WAIT_S) -> bool:
    """True only when the previous shim AND CLI of this directory are positively
    gone (`process_state == 'gone'`). 'unknown' (e.g. the create-time lookup was
    denied) is treated as alive: `write_spec` deletes shim.json/exit.json, and a
    live shim still writes its final manifest into them. Waits up to `timeout`
    for a kill that is in flight to land; collects a finished CLI's lingering
    shim."""
    end = time.monotonic() + timeout
    collected = False
    while True:
        st = _client.inspect(sdir)
        if st is None:
            return True
        shim_state, cli_state = _prior_states(st)
        if shim_state == 'gone' and cli_state == 'gone':
            return True
        if st.exited and shim_state != 'gone' and not collected:
            collected = True
            _collect_prior(sdir)
        if time.monotonic() >= end:
            _log(f'[agent-spawn] session={session_id} engine={engine}: previous '
                 f'{"CLI" if cli_state != "gone" else "shim"} still alive or unverifiable '
                 f'after {timeout:g}s (shim={shim_state} cli={cli_state}); '
                 'spawning without a shim')
            return False
        time.sleep(0.05)


def _collect_when_done(proc: Any) -> None:
    """Close this server's owner connection once the CLI has exited, so the
    shim's `_collected` flag is set and it finishes (see module docstring, 2).
    The shim only counts a release as a collection if it has already seen the
    CLI exit, so wait for its STATUS to say so (bounded)."""
    try:
        while proc.poll() is None:
            time.sleep(_COLLECT_POLL_S)
        client = getattr(proc, '_client', None)
        if client is None:
            return
        end = time.monotonic() + _COLLECT_STATUS_S
        while time.monotonic() < end:
            try:
                if not client.status().get('cli_alive', True):
                    break
            except (_client.ShimError, OSError, ValueError) as e:
                _log(f'[agent-spawn] session={proc.dir.name} status before collect failed: {e}')
                break
            time.sleep(0.05)
        client.close()
    except Exception as e:                              # a daemon thread must not die loudly
        _log(f'[agent-spawn] session={getattr(getattr(proc, "dir", None), "name", "?")} '
             f'collector failed: {e}')


def start_collector(proc: Any) -> None:
    threading.Thread(target=_collect_when_done, args=(proc,), daemon=True,
                     name='agent-shim-collector').start()


def spawn_interactive(cmd: Sequence[Any], *, session_id: str, engine: str, base: Path,
                      cwd: Any, env: Optional[Mapping[str, str]],
                      config: Mapping[str, Any], fallback: Any) -> Any:
    """Flag-ON half of `spawn_engine_proc(interactive=True)`. `fallback()` is
    the plain-Popen spawn the call site would have made without the flag."""
    sdir = _client.session_dir(base, session_id)
    if not _await_prior(sdir, session_id, engine):
        return fallback()
    try:
        start_offset = (sdir / 'out.log').stat().st_size   # out.log is append-only across respawns
    except OSError:
        start_offset = 0
    res = _client.launch(base, session_id, argv=[os.fspath(a) for a in cmd],
                         cwd=os.fspath(cwd) if cwd is not None else None,
                         mode=_shim.MODE_INTERACTIVE, engine=engine, env=env,
                         prompt=None, cfg=config, owner_pid=os.getpid(),
                         start_offset=start_offset, io_timeout=send_timeout_s())
    if res.ok and res.proc is not None:
        res.proc.args = list(cmd)
        start_collector(res.proc)
        return res.proc
    if res.cli_running:
        raise _uncertain.fail_launch(sdir, session_id, res.spec.get('launch_id'), res.error)
    _log(f'[agent-spawn] session={session_id} engine={engine}: shim launch failed '
         f'branch={res.branch} error={res.error}; spawning without a shim')
    return fallback()
