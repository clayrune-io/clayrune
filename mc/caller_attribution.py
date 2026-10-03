"""Caller attribution for loopback requests (backlog 40260b57).

POST /api/project/<id>/agent/dispatch used to decide attended ('manual') vs
unattended ('dispatch') from fields the CALLER writes: body `source`, body
`client`, the `Origin` header. An agent running `curl` against the loopback
API could therefore pose as the UI and get a child with trigger_type 'manual'
-- the one value every fence/vault/config gate reads as "a human is watching" --
and the child came up unfenced (Fenn, round 2: `{"source":"ui"}`,
`-H 'Origin: ...'` and `{"client":"mobile"}` all did it).

This module answers the question from the OS instead of the request: map the
loopback TCP peer port to the owning PID, walk that PID's parent chain, and
see whether it passes through a process Clayrune itself spawned for an agent
session (or one of its background jobs). If it does, the caller IS that
session, and nothing the body or headers claim can make the dispatch
attended. Body/Origin stay as hints for callers that are NOT attributed
(the real UI, a human's own curl).

Never raises. A lookup that cannot run reports `unavailable`; the caller
decides what that means (see `resolve_dispatch`).

Known residual (documented, not closed): an agent that launches a fully
detached process -- parent chain broken before it reaches the session
process -- is not attributed and can still pose as the UI. Outside the
fence's threat model (mistakes / injected text, not a deliberate two-step
adversary). Long-term close is passkeys (backlog e1bd5f1c).

psutil is the only dependency, imported lazily: without it every lookup is
`unavailable`, which degrades to today's behaviour, never to a crash.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from mc.core import _log

ATTRIBUTED = 'attributed'
UNATTRIBUTED = 'unattributed'
UNAVAILABLE = 'unavailable'

# Chain walks are bounded: a real tool chain is a handful of hops
# (claude -> shell -> curl); a runaway loop on a corrupt process table must
# not hang the request.
_MAX_CHAIN = 64


@dataclass(frozen=True)
class Attribution:
    status: str                 # ATTRIBUTED | UNATTRIBUTED | UNAVAILABLE
    session_id: str = ''        # the managed session the caller descends from
    pid: Optional[int] = None   # the managed process that matched
    detail: str = ''            # why, for the log


def _load_psutil():
    """Lazy import so a box without psutil degrades instead of failing at
    import time. Split out so tests can hand in a fake."""
    try:
        import psutil
        return psutil
    except Exception:
        return None


def _is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address((addr or '').strip().split('%', 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, 'ipv4_mapped', None)
    return bool((mapped or ip).is_loopback)


# Creation-time slack when matching a tracked PID to the process we spawned
# (same tolerance the startup reaper uses, mc/process_ledger.py).
_CREATE_TIME_SLACK_S = 2.0


def _tracked_pid_is_ours(pid: int, entry: Dict[str, Any], psutil) -> bool:
    """Is `pid` still the process Clayrune registered, not a stranger that
    inherited the number after it exited?

    A live Popen handle settles it: poll() is None only while that very child
    runs, so the PID cannot belong to anyone else. An exited handle is a no.
    Without a handle the stored creation time must match the live process's
    (an entry with neither, or a process psutil cannot read, is not ours --
    a reused browser/proxy PID must never inherit a dead session's fence)."""
    proc = (entry or {}).get('proc')
    if proc is not None:
        try:
            return proc.poll() is None
        except Exception:
            pass                          # unusable handle: fall to identity
    recorded = (entry or {}).get('create_time')
    if not isinstance(recorded, (int, float)) or psutil is None:
        return False
    try:
        live = float(psutil.Process(pid).create_time())
    except Exception:
        return False
    return abs(live - float(recorded)) <= _CREATE_TIME_SLACK_S


def managed_roots(agent_sessions: Dict[str, Any],
                  tracked_processes: Dict[int, Dict[str, Any]],
                  psutil_mod=None) -> Dict[int, str]:
    """{pid: session_id} for every live process Clayrune spawned on behalf of
    an agent session: the session's own CLI process, plus the processes the
    process tracker holds against a session id (background jobs run as a
    server-owned Popen, so they are NOT children of the session's CLI)."""
    roots: Dict[int, str] = {}
    for sid, sess in list(agent_sessions.items()):
        proc = (sess or {}).get('proc')
        pid = getattr(proc, 'pid', None)
        if proc is None or not isinstance(pid, int) or pid <= 0:
            continue
        try:
            if proc.poll() is not None:       # exited: its PID may be reused
                continue
        except Exception:
            pass
        roots[pid] = str((sess or {}).get('session_id') or sid)
    psutil = None
    for pid, entry in list(tracked_processes.items()):
        sid = (entry or {}).get('session_id')
        if not (sid and isinstance(pid, int) and pid > 0) or pid in roots:
            continue
        if (entry or {}).get('proc') is None and psutil is None:
            psutil = psutil_mod or _load_psutil()
        if _tracked_pid_is_ours(pid, entry, psutil):
            roots[pid] = str(sid)
    return roots


def _conn_matches(conn, peer_port: int, server_port: Optional[int]) -> bool:
    laddr, raddr = getattr(conn, 'laddr', None), getattr(conn, 'raddr', None)
    if not laddr or not raddr:
        return False
    if getattr(laddr, 'port', None) != peer_port:
        return False
    if server_port is not None and getattr(raddr, 'port', None) != server_port:
        return False
    return True


def _owner_pid_system_wide(psutil, peer_port: int,
                           server_port: Optional[int]) -> Optional[int]:
    """PID that owns the client end of the peer connection, from the
    system-wide socket table. None when the table does not name it. Raises
    whatever psutil raises (macOS needs root for this call)."""
    for conn in psutil.net_connections(kind='tcp'):
        if _conn_matches(conn, peer_port, server_port) and getattr(conn, 'pid', None):
            return int(conn.pid)
    return None


def _proc_connections(proc):
    fn = getattr(proc, 'net_connections', None) or getattr(proc, 'connections')
    return fn(kind='tcp')


def _search_managed_trees(psutil, roots: Dict[int, str], peer_port: int,
                          server_port: Optional[int]) -> Tuple[Optional[Tuple[int, int]], bool]:
    """Fallback that needs no privilege: look only at the sockets of each
    managed root and its descendants. Returns ((root_pid, owner_pid) | None,
    incomplete). `incomplete` is True when a process could not be inspected,
    so "not found" cannot be read as "not there"."""
    incomplete = False
    for root_pid in roots:
        try:
            root = psutil.Process(root_pid)
            tree = [root] + list(root.children(recursive=True))
        except Exception:
            incomplete = True
            continue
        for proc in tree:
            try:
                conns = _proc_connections(proc)
            except Exception:
                incomplete = True
                continue
            for conn in conns:
                if _conn_matches(conn, peer_port, server_port):
                    return (root_pid, int(proc.pid)), incomplete
    return None, incomplete


def attribute_caller(peer_addr: str, peer_port: Any, server_port: Any,
                     roots: Dict[int, str], *, own_pid: Optional[int] = None,
                     psutil_mod=None) -> Attribution:
    """Decide whether the loopback caller descends from a managed session.
    Never raises."""
    try:
        return _attribute(peer_addr, peer_port, server_port, roots,
                          os.getpid() if own_pid is None else own_pid, psutil_mod)
    except Exception as e:
        return Attribution(UNAVAILABLE, detail=f'lookup raised: {e!r}')


def _attribute(peer_addr, peer_port, server_port, roots, own_pid, psutil_mod):
    if not _is_loopback(peer_addr):
        # A LAN/tunnel peer is not a process on this box; nothing to attribute.
        return Attribution(UNATTRIBUTED, detail='peer is not loopback')
    if not roots:
        return Attribution(UNATTRIBUTED, detail='no managed session processes')
    try:
        peer_port = int(peer_port)
    except (TypeError, ValueError):
        return Attribution(UNAVAILABLE, detail='no peer port on the request')
    try:
        server_port = int(server_port) if server_port else None
    except (TypeError, ValueError):
        server_port = None
    psutil = psutil_mod or _load_psutil()
    if psutil is None:
        return Attribution(UNAVAILABLE, detail='psutil not installed')

    owner: Optional[int] = None
    try:
        owner = _owner_pid_system_wide(psutil, peer_port, server_port)
    except Exception:
        owner = None                     # e.g. macOS without root: use the fallback
    if owner is None:
        hit, incomplete = _search_managed_trees(psutil, roots, peer_port, server_port)
        if hit is None:
            if incomplete:
                return Attribution(UNAVAILABLE, detail='could not inspect every managed process')
            return Attribution(UNATTRIBUTED, detail='peer socket is not in any managed process tree')
        # The socket was found under a managed root's tree, but that tree can
        # contain the server itself (server started from an agent's shell), so
        # the owner still goes through the same walk -- and the same stop at
        # the server PID -- as a system-wide hit.
        owner = hit[1]

    try:
        proc = psutil.Process(owner)
        chain = [owner] + [int(p.pid) for p in proc.parents()][:_MAX_CHAIN]
    except Exception as e:
        return Attribution(UNAVAILABLE, detail=f'process {owner} not inspectable: {e!r}')
    for pid in chain:
        if pid in roots:
            return Attribution(ATTRIBUTED, roots[pid], pid,
                               f'caller pid {owner} descends from session pid {pid}')
        if pid == own_pid:
            # Reached this server without passing through a session: the caller
            # is the server itself or something it launched for the human
            # (desktop webview, browser pane). Even if the server was itself
            # started from an agent's shell, that must not turn the UI into a
            # dispatched caller.
            return Attribution(UNATTRIBUTED, detail=f'caller pid {owner} descends from the server')
    return Attribution(UNATTRIBUTED, detail=f'caller pid {owner} has no managed ancestor')


def resolve_dispatch(request, trigger_type: str, source: str,
                     agent_sessions: Dict[str, Any],
                     tracked_processes: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    """(trigger_type, source) for POST .../agent/dispatch after attribution.

    `trigger_type`/`source` arrive as the route derived them from the body and
    headers (hints). Rules:
      attributed   -> ('dispatch', 'agent'): the caller is a managed session;
                      no body field or Origin can upgrade it.
      unattributed -> the hints stand (the real UI, a human's own curl).
      unavailable  -> no Origin: ('dispatch', 'agent'), fail closed; with an
                      Origin: the hints stand, as before this change.
    Logs whenever it overrides a hint or cannot attribute. Never raises."""
    env = getattr(request, 'environ', None) or {}
    try:
        att = attribute_caller(getattr(request, 'remote_addr', '') or '',
                               env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
                               managed_roots(agent_sessions, tracked_processes))
    except Exception as e:
        att = Attribution(UNAVAILABLE, detail=f'attribution failed: {e!r}')
    has_origin = bool(request.headers.get('Origin'))
    if att.status == ATTRIBUTED:
        if trigger_type != 'dispatch' or source != 'agent':
            _log(f"[dispatch] caller attributed to session {att.session_id[:12]} "
                 f"({att.detail}); body/Origin hints source={source!r} "
                 f"origin={has_origin} ignored -> trigger_type 'dispatch'")
        return 'dispatch', 'agent'
    if att.status == UNAVAILABLE:
        _log(f"[dispatch] caller attribution unavailable ({att.detail}); "
             f"origin={has_origin} -> "
             f"{'hints stand' if has_origin else 'trigger_type dispatch (fail closed)'}")
        if not has_origin:
            return 'dispatch', 'agent'
    return trigger_type, source


def warn_if_degraded() -> bool:
    """Startup check: True when attribution can run. Without psutil every
    dispatch lookup is `unavailable`, so a request carrying an Origin header
    is trusted as the UI again -- the exact spoof this module closes -- and the
    only sign is a per-request log line. Say so once, loudly, at boot. Never
    raises."""
    if _load_psutil() is not None:
        return True
    _log("[dispatch] WARNING: psutil is not installed -- dispatch caller attribution is "
         "DEGRADED: an agent can pose as the UI with an Origin header and get an unfenced "
         "child. Fix: python -m pip install -r requirements.txt, then restart.", flush=True)
    return False
