"""POSIX process-group kill with a self-kill guard (backlog 7cc8f7bc).

Agent CLI children used to be spawned WITHOUT ``start_new_session``, so on
Linux/macOS they shared the server's process group. ``os.killpg(os.getpgid(pid),
9)`` on such a child killed the whole group — the Clayrune server included — so
pressing Stop on an agent SIGKILLed the server (reproduced 2026-09-30 on Ubuntu
22.04: server pid 28110 pgid 28105, claude child pgid 28105).

Two layers, both required:

1. THE GUARD (this module): every ``killpg`` in ``mc/`` goes through
   ``safe_killpg`` / ``kill_tree``. A group equal to ``os.getpgrp()`` is NEVER
   signalled, and neither is a group the target does not lead (``pgid != pid`` —
   a group we did not create may hold siblings we do not own). When refused,
   ``kill_tree`` falls back to the target's own subtree, walked from a PPID map.
   It also refuses outright to touch this process or any of its ancestors.
2. THE ROOT FIX (spawn sites): ``POPEN_NEW_SESSION`` — spawn agent/CLI children
   with ``start_new_session=True`` so each leads its own group and the guard's
   fast path (one ``killpg``) reaches node/MCP grandchildren.

Leaf module: stdlib only (+ optional psutil). Importing it on Windows is safe;
the POSIX-only calls are resolved lazily and every helper is a no-op/False
there (Windows callers use ``taskkill /T``).
"""
import os
import signal
import subprocess
import sys
from typing import Dict, List, Optional, Set

from mc.core import _log

# Spread into a Popen(...) call. Windows: nothing (creationflags already handle
# it, no behaviour change). POSIX: the child leads its own session + group.
_IS_WIN = sys.platform == 'win32'
POPEN_NEW_SESSION: dict = {} if _IS_WIN else {'start_new_session': True}

_SIGKILL = getattr(signal, 'SIGKILL', 9)


def _ppid_map() -> Optional[Dict[int, int]]:
    """{pid: ppid} for every live process, or None when it cannot be read."""
    try:
        import psutil  # optional dependency
        return {p.pid: p.ppid() for p in psutil.process_iter(['ppid'])
                if p.info.get('ppid') is not None} or None
    except ImportError:
        pass
    except Exception as e:
        _log(f"[proc_kill] psutil process walk failed: {e}")
    try:
        out = subprocess.run(['ps', '-A', '-o', 'pid=,ppid='], capture_output=True,
                             text=True, encoding='utf-8', errors='replace', timeout=10).stdout
        m: Dict[int, int] = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2:
                m[int(parts[0])] = int(parts[1])
        return m or None
    except Exception as e:
        _log(f"[proc_kill] ps process walk failed: {e}")
        return None


def _ancestors_of(pid: int, ppids: Dict[int, int]) -> Set[int]:
    chain: Set[int] = set()
    cur = pid
    while cur in ppids and cur not in chain and cur > 1:
        chain.add(cur)
        cur = ppids[cur]
    return chain


def _descendants(root: int, ppids: Dict[int, int]) -> List[int]:
    kids: Dict[int, List[int]] = {}
    for pid, ppid in ppids.items():
        kids.setdefault(ppid, []).append(pid)
    out: List[int] = []
    stack = list(kids.get(root, []))
    seen = {root}
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def _group_is_ours_to_kill(pid: int, pgid: int) -> bool:
    """The bright line. False for the server's own group, and for any group the
    target does not lead (it may hold processes we never spawned)."""
    if pgid <= 1 or pgid == os.getpgrp():  # pyright: ignore[reportAttributeAccessIssue]
        return False
    return pgid == pid


def safe_killpg(pid: int, sig: int = _SIGKILL) -> bool:
    """``os.killpg`` on ``pid``'s group, refusing the server's own group.

    Returns True only if a group signal was actually sent. False means "nothing
    was signalled" (group refused, process gone, or not POSIX) — the caller
    decides what to do instead; this never raises.
    """
    if _IS_WIN or pid <= 1:
        return False
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    if not _group_is_ours_to_kill(pid, pgid):
        _log(f"[proc_kill] refused killpg: pid {pid} pgid {pgid} (server pgid "
             f"{os.getpgrp()}) — not a group this process may signal", flush=True)
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except OSError:
        return False


def kill_tree(pid: int, sig: int = _SIGKILL) -> bool:
    """Kill ``pid`` and everything under it. Never touches the server.

    Fast path: ``pid`` leads its own group (``start_new_session``) -> one
    guarded ``killpg``. Otherwise (legacy child sharing the server's group, or
    a group it does not lead) -> signal ``pid`` and its PPID-subtree only.
    Returns True if anything was signalled.
    """
    if _IS_WIN or pid <= 1:
        return False
    if safe_killpg(pid, sig):
        return True
    ppids = _ppid_map()
    if ppids is not None:
        me = os.getpid()
        if pid == me or pid in _ancestors_of(me, ppids):
            _log(f"[proc_kill] refused to kill pid {pid}: it is this server or "
                 f"one of its ancestors", flush=True)
            return False
        victims = [pid] + _descendants(pid, ppids)
    elif pid == os.getpid():
        return False
    else:
        victims = [pid]
    sent = False
    for v in victims:
        try:
            os.kill(v, sig)
            sent = True
        except OSError:
            pass
    return sent
