"""MC-1150 P4 review blocker 2: completion work that survives a crash.

The reader takes a FORCED checkpoint past a turn's `result` line before the
turn's side effects (the agent-log row, the spawner / workflow wake). That keeps
a restart from running them twice, and it also meant a crash right after the
checkpoint lost them: the checkpoint said "finished, offset past the end", so
boot adoption declined the session, and nothing else knew the log row and the
wake had never happened.

This module is the record of what is owed:

    begin(session, kind)   stamp `_pending_completion` {id, kind, ...} on the
                           session, then force the checkpoint, so the stamp is
                           on disk BEFORE the first side effect.
    ack(session)           drop the stamp and checkpoint again, once the side
                           effects are done. Until then the record stays on disk.
    replay_pending()       boot: every checkpoint still carrying a stamp has its
                           completion run again on the restored session, then
                           the stamp is cleared. A run that fails or raises
                           leaves the stamp for the next boot.

Replay is at-least-once, so every step it re-runs is idempotent: the agent-log
row carries `completion_key` (= the stamp's id) and is not written twice; the
spawner wake is a durable outbox row keyed by the child's session + turn; the
workflow wake is a no-op for a step that already advanced. `agent_routes` owns
those; this file owns the record.

`kind` is 'exit' (the whole completion body, then the wake) or 'turn' (a Mode B
turn boundary: only the wake).
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from mc import agent_shim_adopt as _adopt
from mc.atomic_json import write_json_atomic

KEY = _adopt.PENDING_KEY
KINDS = ('exit', 'turn')


def _log(msg: str) -> None:
    _adopt._log(msg)


def pending_of(session: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    rec = session.get(KEY)
    return rec if isinstance(rec, dict) and rec.get('id') else None


def begin(session: Dict[str, Any], kind: str) -> Optional[str]:
    """Stamp the session and force the checkpoint that carries the stamp. Returns
    the stamp's id, or None when the session is not shim-run or the flag is off
    (nothing is stamped then, so those sessions behave exactly as before P4)."""
    if _adopt._shim_proc(session) is None or not _adopt.enabled_now():
        return None
    rec = {'id': uuid.uuid4().hex, 'kind': kind, 'status': session.get('status'),
           'turn': int(session.get('_delegation_turn', 1) or 1), 'at': time.time()}
    session[KEY] = rec
    if not _adopt.force_checkpoint(session):
        _log(f"[agent-shim-completion] pending record for {str(session.get('session_id'))[:12]} "
             f"is not on disk (checkpoint skipped or failed); an older checkpoint stands")
    return rec['id']


def ack(session: Dict[str, Any], *, owed: bool = False) -> bool:
    """The completion work is done: clear the stamp and checkpoint that fact.
    `owed` keeps the stamp when something (the durable wake) did not commit."""
    if pending_of(session) is None:
        return False
    if owed:
        _log(f"[agent-shim-completion] {str(session.get('session_id'))[:12]} completion not "
             f"acknowledged: a wake is still owed; the record stays for the next boot")
        return False
    session.pop(KEY, None)
    return _adopt.force_checkpoint(session)


# ─────────────────────────────────── replay ──────────────────────────────────
class ReplayReport:
    def __init__(self) -> None:
        self.replayed: List[str] = []
        self.retained: Dict[str, str] = {}

    def __repr__(self) -> str:                                   # pragma: no cover
        return f'ReplayReport(replayed={self.replayed}, retained={self.retained})'


Runner = Callable[[Dict[str, Any]], bool]


def default_runners() -> Dict[str, Runner]:
    from mc.blueprints import agent_routes as ar
    return {'exit': ar._replay_exit_completion, 'turn': ar._replay_turn_completion}


def replay_pending(root: Optional[Any] = None, *,
                   runners: Optional[Mapping[str, Runner]] = None) -> ReplayReport:
    """Boot phase: run the completion of every checkpoint that still carries a
    stamp, then clear it. Not gated on the flag: a stamp on disk is work owed
    whatever the flag says now. Never raises."""
    rep = ReplayReport()
    try:
        from mc import agent_spawn
        base = Path(root) if root is not None else agent_spawn.default_root()
        if not base.is_dir():
            return rep
        dirs = sorted(p for p in base.iterdir() if p.is_dir())
    except Exception as e:
        _log(f'[agent-shim-completion] replay scan failed: {e}')
        return rep
    for d in dirs:
        sid = d.name
        try:
            ck = _adopt.read_checkpoint(d)
            if not ck or ck.get('schema') != _adopt.SCHEMA:
                continue
            snap = ck.get('session')
            if not isinstance(snap, dict):
                continue
            rec = snap.get(KEY)
            if not isinstance(rec, dict) or not rec.get('id'):
                continue
            kind = rec.get('kind')
            if runners is None:
                runners = default_runners()
            run = runners.get(kind) if kind in KINDS else None
            if run is None:
                rep.retained[sid] = f'unknown completion kind {kind!r}'
                continue
            session = _adopt.restore_session(snap)
            session['_completion_replay'] = True
            if run(session) is not True:
                rep.retained[sid] = 'completion work still owed'
                _log(f'[agent-shim-completion] {sid[:12]} replay incomplete; kept for next boot')
                continue
            cleared = dict(ck)
            cleared['session'] = {k: v for k, v in snap.items() if k != KEY}
            cleared['written_epoch'] = time.time()
            write_json_atomic(str(_adopt.checkpoint_path(d)), cleared)
            rep.replayed.append(sid)
            _log(f'[agent-shim-completion] {sid[:12]} {kind} completion replayed and acknowledged')
        except Exception as e:
            rep.retained[sid] = f'{type(e).__name__}: {e}'
            _log(f'[agent-shim-completion] {sid[:12]} replay failed: {type(e).__name__}: {e}')
    return rep
