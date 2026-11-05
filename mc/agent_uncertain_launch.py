"""Uncertain shim launches: terminate what we cannot track (MC-1150 P2, blocker 4).

A shim launch is *uncertain* when `agent_shim_client.launch` returns
`cli_running=True`: the readiness deadline passed (or a teardown could not be
verified), so a shim and its CLI may be running that nothing here reads from
or has registered. Letting that CLI run, or starting a second one beside it,
doubles the agent. The rule (Dave, 2026-11-05) is that nothing runs that we do
not own:

  1. `resolve()` terminates the shim AND its CLI by (pid, create_time) from the
     launch's manifest, through P1's identity-checked kill (never by name),
     and waits until both are positively gone.
  2. If that cannot be confirmed (no manifest ever landed for this launch, or a
     process will not die), it leaves `uncertain.json` in the session dir.
  3. While that marker exists, `spawn_engine_proc` REFUSES the session (it
     raises; no plain-Popen fallback). Each later spawn re-runs `resolve()`,
     which clears the marker as soon as the launch is confirmed gone.

An uncertain launch is rare (a missed readiness deadline) and the feature is
dark, so the price of this is losing one possibly good run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from mc import agent_shim as _shim
from mc import agent_shim_client as _client

MARKER = 'uncertain.json'

# How long `resolve` waits for a late manifest to appear, right after a launch
# missed its readiness deadline (that wait has already burned the launch's own
# 20s). A refusal check on a later spawn does not wait at all.
RESOLVE_WAIT_S = 5.0
# Per-process kill-and-confirm window handed to P1's teardown.
KILL_CONFIRM_S = 5.0


def marker_path(sdir: Any) -> Path:
    return Path(sdir) / MARKER


def pending(sdir: Any) -> Optional[Dict[str, Any]]:
    """The marker, if the session has an unresolved uncertain launch."""
    return _client.read_json(marker_path(sdir))


def _write_marker(sdir: Path, session_id: str, launch_id: Any, why: str) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    _client.write_json_atomic(str(marker_path(sdir)), dict(
        schema=_shim.SCHEMA, session_id=session_id, launch_id=launch_id, why=why, ts=time.time()))


def _clear_marker(sdir: Path) -> None:
    try:
        marker_path(sdir).unlink()
    except FileNotFoundError:
        pass


def _attempt(sdir: Path, launch_id: Any, wait_s: float) -> 'tuple[bool, str]':
    """One pass: (confirmed gone, reason if not)."""
    end = time.monotonic() + max(0.0, wait_s)
    while True:
        m = _client.read_json(sdir / 'shim.json')
        if m and m.get('schema') == _shim.SCHEMA and m.get('launch_id') == launch_id:
            if _client._teardown_started(m, timeout=KILL_CONFIRM_S):
                return True, ''
            return False, 'the shim or its CLI could not be confirmed gone'
        ex = _client.read_json(sdir / 'exit.json')
        if ex and ex.get('launch_id') == launch_id:
            return True, ''                       # the shim reported it never started a CLI
        if time.monotonic() >= end:
            return False, 'no manifest for this launch, so its shim cannot be identified'
        time.sleep(0.05)


def resolve(sdir: Any, session_id: str, launch_id: Any, *, wait_s: Optional[float] = None) -> bool:
    """Terminate the uncertain launch `launch_id` of this session. True when
    both its shim and CLI are confirmed gone (marker cleared); False when not
    (marker left, spawn refuses). `launch_id=None` re-reads it from the marker."""
    sd = Path(sdir)
    if launch_id is None:
        launch_id = (pending(sd) or {}).get('launch_id')
    ok, why = _attempt(sd, launch_id, RESOLVE_WAIT_S if wait_s is None else wait_s)
    if ok:
        _clear_marker(sd)
        _client._log(f'[agent-spawn] session={session_id}: uncertain launch {launch_id} terminated')
        return True
    _write_marker(sd, session_id, launch_id, why)
    _client._log(f'[agent-spawn] session={session_id}: uncertain launch {launch_id} unresolved ({why}); '
                 'spawns for this session are refused until it is')
    return False


def refuse_if_pending(sdir: Any, session_id: str) -> None:
    """Called before any spawn for the session. Raises RuntimeError while an
    earlier uncertain launch is still unresolved; clears the marker if it has
    become confirmable."""
    marker = pending(sdir)
    if marker is None:
        return
    if resolve(sdir, session_id, marker.get('launch_id'), wait_s=0.0):
        return
    raise RuntimeError(f'an earlier agent shim launch for this session is unresolved '
                       f'({marker.get("why")}); not starting a second one. Once no shim or CLI '
                       f'for this session is running, delete {marker_path(sdir)} to release it')


def fail_launch(sdir: Any, session_id: str, launch_id: Any, error: Any) -> RuntimeError:
    """The exception for a launch that came back uncertain, after `resolve`."""
    if resolve(sdir, session_id, launch_id):
        return RuntimeError(f'agent shim launch was uncertain ({error}); its shim and CLI were '
                            'terminated, so this launch failed (nothing is running)')
    return RuntimeError(f'agent shim launch left a CLI possibly running ({error}); '
                        'not starting a second one')
