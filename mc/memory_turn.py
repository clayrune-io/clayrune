"""Per-turn memory delivery — MC-944, build-sequence step 5 of
`docs/MEMORY_DESIGN_V2_SPEC.md` (§9.6, Condition 45).

THE DEFECT THIS CLOSES (spec §2.1, break B5, measured 2026-09-06): the read
floor is rendered once, inside `_build_agent_context()`, at dispatch or
revival. A Mode-B process that stays alive never re-enters that function — a
live follow-up turn is written straight to `proc.stdin` with nothing but the
raw message (plus the existing per-turn brief-reply directive). A session
resumed on a task string of "Hi Dave" and continued for five days therefore
retrieves nothing new for the rest of its life: replayed live, 24 delivered
read-floor slots surfaced zero mentions of a 346 KB design this project had
already finished. This module is what makes retrieval bound to the TURN
instead of the session.

WHERE THIS IS CALLED FROM. The spec names four `agent_routes.py` sites where
Clayrune composes the JSON envelope written to `proc.stdin`
(`agent_routes.py:4021`, `:5615`, `:6460`, `:6563`). Two are dispatch/revival
(the read floor already ran, via `_build_agent_context`) — those call
`seed_delivered()` so the facts the system prompt already contains are not
re-sent as "new" two turns later. The other two are direct writes to an
ALREADY-LIVE process with no context rebuild at all — those call
`refresh_for_turn()`, which recomputes the notes/positions blocks against
THIS message and returns text to prepend.

WHAT THIS DOES vs WHAT IT DELIBERATELY DOES NOT DO (MC-944 scope):
  DOES      — recompute STANDING POSITIONS + RELEVANT MEMORY for the live
              message, via the SAME `mc.memory._memory_search` the dispatch
              floor already calls (unchanged: same ranking, same corpus, same
              lack of an origin filter — this module does not touch or
              weaken the unattended-origin gate, because `_memory_search` has
              never had one; see mc/distiller.py's gate, which governs
              Distiller/exploration artifacts, a different channel this
              module does not call).
  DOES      — track a per-CONVERSATION delivered-set (stored on the live
              session dict, so it lives exactly as long as the session does)
              so a note already sent once this conversation is not resent as
              a "new" fact. Positions are exempt — see `refresh_for_turn`.
  DOES      — fall back to exactly one cold FTS hit when the warm floor
              returns nothing at all (§9.4's miss condition, applied here per
              turn per Condition 42).
  DOES      — enforce a hard per-turn byte budget, logging when it truncates
              (P4: "a bound that bites silently gets worked around").
  DOES NOT  — the negation ledger, `supersedes`/head-substitution, minting,
              the index/journal split, or any embedding/semantic layer. Those
              are later build-sequence steps (§16 steps 6-10) and MC-944 is
              step 5 only.
  DOES NOT  — block or refuse anything. Report mode only (§16 step 9): every
              call logs what it delivered, what it suppressed, and the byte
              cost, so a later step can measure before any gate is flipped to
              enforcing. Never raises — the read floor is the only retrieval
              channel that actually runs, and a bug here must not cost a live
              turn its stdin write.

Leaf module, like mc/memory_delivery.py and mc/memory_fts.py beside it: it
imports mc.memory (also a leaf) but never mc.blueprints.* or server, so
mc/memory.py's "never imports server or any blueprint" invariant is not put
at risk by anything importing THIS module either.
"""
from __future__ import annotations

import os as _os

from mc.core import _log
from mc import memory as _mem

# Fallback if `memory_turn_budget_bytes` is absent from CONFIG (should not
# happen once server.py's defaults dict carries it — see Condition 9's
# lesson about keys that exist in code but not in the defaults dict).
DEFAULT_TURN_BUDGET_BYTES = 3200

# Stored on the live session dict (mc.state.agent_sessions[sid]), so its
# lifetime is exactly the conversation's — created empty on dispatch/revival,
# carried across every direct stdin write, gone when the session is gone.
# Never persisted to disk: a per-turn refresh is a report-mode convenience,
# not a record (P8 — nothing here is memory, it is a delivery cursor).
_DELIVERED_KEY = '_mem_turn_delivered'


def _cfg(key, default):
    try:
        from mc import state
        return state.CONFIG.get(key, default)
    except Exception:
        return default


def enabled() -> bool:
    """Master rollback lever — mirrors session_fts_enabled /
    exploration_readback_enabled. Per-turn read, no respawn needed."""
    try:
        return bool(_cfg('memory_turn_refresh_enabled', True))
    except Exception:
        return True


def turn_budget_bytes() -> int:
    try:
        return max(512, int(_cfg('memory_turn_budget_bytes', DEFAULT_TURN_BUDGET_BYTES)
                             or DEFAULT_TURN_BUDGET_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_TURN_BUDGET_BYTES


def _cold_probe_enabled() -> bool:
    try:
        return bool(_cfg('memory_turn_cold_probe_enabled', True))
    except Exception:
        return True


def _is_position_hit(h: dict) -> bool:
    try:
        return _mem._is_position_file(_os.path.basename(str(h.get('file', ''))))
    except Exception:
        return False


def _render_position_line(project, h: dict) -> str:
    """Mirrors agent_routes._render_position verbatim (that copy is the
    dispatch-time renderer; this module cannot import agent_routes — mc.memory
    and its siblings never import a blueprint — so the ~10-line format is
    duplicated rather than shared. Both read the same `_parse_position`
    record, so they cannot drift on content, only on layout.)"""
    try:
        fp = _mem._get_memory_path(project).parent / _os.path.basename(str(h.get('file')))
        rec = _mem._parse_position(fp.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        rec = {}
    if not rec:
        return f"[{h.get('file')}] {h.get('snippet', '')}"
    bits = [f"{(rec.get('verdict') or 'decided').upper()}: {rec.get('subject')}"]
    if rec.get('reason'):
        bits.append(f"because {rec['reason']}")
    if rec.get('expires_when'):
        bits.append(f"REOPEN IF {rec['expires_when']}")
    if rec.get('decided'):
        bits.append(f"({rec['decided']})")
    return " — ".join(bits) + f"  [{h.get('file')}]"


def _uid_of(h: dict):
    return h.get('uid') or h.get('file')


def seed_delivered(project, session, task, *, topk=None, expand=None) -> None:
    """Mark whatever the DISPATCH-time read floor already put in the system
    prompt as delivered for this conversation, with no injectable text.

    Call this once, right where dispatch or revival writes the FIRST stdin
    message (turn 1 already got the full read floor via
    `_build_agent_context`). Without it, `refresh_for_turn` on turn 2 would
    treat those same facts as brand new and resend them — the delivered-set
    would be seeing the conversation for the "first" time on every turn.

    Positions are NOT added to the delivered-set here or anywhere else in
    this module — see `refresh_for_turn`'s docstring for why they are exempt
    from suppression. Never raises.
    """
    if not enabled() or not isinstance(session, dict) or not (task or '').strip():
        return
    try:
        from mc import state
        _topk = int(topk if topk is not None else state.CONFIG.get('read_floor_topk', 6) or 6)
        _expand = int(expand if expand is not None else state.CONFIG.get('read_floor_link_expand', 2) or 0)
        hits = _mem._memory_search(project, task, _topk, expand=_expand, record=None)
        delivered = session.setdefault(_DELIVERED_KEY, set())
        added = 0
        for h in hits:
            if _is_position_hit(h):
                continue
            uid = _uid_of(h)
            if uid and uid not in delivered:
                delivered.add(uid)
                added += 1
        if added:
            _log(f"[mem-turn] {(project or {}).get('id')}: seeded {added} "
                 f"delivered uid(s) from the dispatch-time read floor")
    except Exception as e:
        _log(f"[mem-turn] seed failed for {(project or {}).get('id')}: {e}")


def _fit_lines(pos_lines, note_lines, budget):
    """Assemble the block within `budget` bytes, positions first.

    Adds a section's header only if at least one of its lines fits, and stops
    adding lines within an over-long section rather than truncating mid-line
    — the remaining (lower-ranked, since callers pre-sort by score) lines are
    simply dropped, which is what "hard budget" has to mean for a text block."""
    out: list[str] = []
    total = 0

    def _add(line):
        nonlocal total
        lb = len(line.encode('utf-8')) + 1
        if total + lb > budget:
            return False
        out.append(line)
        total += lb
        return True

    if pos_lines:
        header = ("--- STANDING POSITIONS (already decided — do NOT re-propose "
                   "these without first checking whether the stated reason still "
                   "holds; if it has expired, say so explicitly and reopen it) ---")
        if _add(header):
            for line in pos_lines:
                if not _add(line):
                    break
    if note_lines:
        header = ("--- RELEVANT MEMORY (re-surfaced for this message; recomputed "
                   "server-side for this turn, not just at session start) ---")
        if _add(header):
            for line in note_lines:
                if not _add(line):
                    break
    return "\n".join(out), total


def refresh_for_turn(project, session, message, *, topk=None, expand=None,
                      context='mem_turn_refresh') -> dict:
    """Recompute the STANDING POSITIONS + RELEVANT MEMORY blocks for ONE live
    turn on an already-running session, honouring the conversation's
    delivered-set.

    Positions are matched fresh every turn and are NEVER added to the
    delivered-set: a position is a standing "do not re-propose this" warning
    (mc/memory.py:5.1's Kill 3 — presence in context once was already shown
    NOT to stop a re-proposal on 2026-08-23), so suppressing it after one
    delivery would defeat the one thing it exists to do. This mirrors the
    spec's behaviour-rule/fact split (§ "Behaviour rules ... re-deliver every
    turn"): a position governs what the agent should NOT propose again, which
    is closer to a standing constraint than a one-time fact.

    Returns a dict — never raises:
      {'block': str,            ready to prepend to the outgoing message text;
                                 '' when there is nothing to add
       'delivered': [uid,...],  note/archive uids newly added to the
                                 delivered-set by THIS call
       'suppressed': [uid,...], uids that matched but were already delivered
                                 earlier this conversation
       'positions': [file,...], position files rendered this turn
       'cold_used': bool,       True when the warm floor came back with
                                 nothing and a cold FTS hit filled the slot
       'bytes': int}            size of `block`, always <= turn_budget_bytes()
    """
    empty = {'block': '', 'delivered': [], 'suppressed': [], 'positions': [],
             'cold_used': False, 'bytes': 0}
    if not enabled() or not isinstance(session, dict) or not (message or '').strip():
        return empty
    try:
        return _refresh_for_turn(project, session, message, topk=topk,
                                  expand=expand, context=context)
    except Exception as e:
        _log(f"[mem-turn] refresh failed for {(project or {}).get('id')}: {e}")
        return empty


def _refresh_for_turn(project, session, message, *, topk, expand, context):
    from mc import state
    pid = (project or {}).get('id')
    topk = int(topk if topk is not None else state.CONFIG.get('read_floor_topk', 6) or 6)
    expand = int(expand if expand is not None else state.CONFIG.get('read_floor_link_expand', 2) or 0)
    budget = turn_budget_bytes()

    delivered_set = session.setdefault(_DELIVERED_KEY, set())

    # Ask for more candidates than we'll keep so a suppressed hit can be
    # backfilled from the next-ranked one instead of the block silently
    # shrinking every time a topic recurs in a long conversation.
    raw = _mem._memory_search(project, message, max(topk * 2, topk + 4),
                               expand=expand, record=context)

    pos_hits = [h for h in raw if _is_position_hit(h)]
    note_hits = [h for h in raw if not _is_position_hit(h)]

    kept, suppressed = [], []
    for h in note_hits:
        uid = _uid_of(h)
        if uid in delivered_set:
            suppressed.append(uid)
            continue
        kept.append(h)
        if len(kept) >= topk:
            break

    # §9.4's miss condition, applied per turn (Condition 42): the warm floor
    # returned literally nothing — not merely "everything already delivered" —
    # so fall back to the one tier that reliably has an answer no curated note
    # shares vocabulary with (§2.1 B4). A non-empty note_hits list that got
    # fully suppressed is NOT a miss: the answer was already given once this
    # conversation, and a cold hit would just repeat it via a second channel.
    cold_used = False
    if not note_hits and not pos_hits and _cold_probe_enabled():
        try:
            from mc import memory_fts as _fts
            cold = _fts.cold_search(project, message, limit=1)
        except Exception as e:
            _log(f"[mem-turn] cold probe skipped for {pid}: {e}")
            cold = []
        if cold:
            kept = cold
            cold_used = True

    pos_lines = [f"  • {_render_position_line(project, h)}" for h in pos_hits]
    note_lines = []
    for h in kept:
        tag = ' [cold tier — a real past session excerpt, not a curated note]' \
            if h.get('tier') == 'cold' else ''
        via = f" (linked from {h['via']})" if h.get('via') else ''
        note_lines.append(f"  • [{h.get('file')}]{via}{tag} {h.get('snippet', '')}")

    block, cost = _fit_lines(pos_lines, note_lines, budget)
    if block:
        raw_bytes = sum(len(l.encode('utf-8')) + 1 for l in pos_lines + note_lines)
        if raw_bytes > cost:
            _log(f"[mem-turn] {pid}: per-turn block truncated {raw_bytes}B -> "
                 f"{cost}B (budget {budget}B)")

    delivered_uids = []
    for h in kept:
        uid = _uid_of(h)
        if uid and uid not in delivered_set:
            delivered_set.add(uid)
            delivered_uids.append(uid)

    _log(f"[mem-turn] {pid}: turn refresh — delivered={len(delivered_uids)} "
         f"suppressed={len(suppressed)} positions={len(pos_hits)} "
         f"cold_probe={cold_used} bytes={cost}/{budget}")

    return {'block': block, 'delivered': delivered_uids, 'suppressed': suppressed,
            'positions': [h.get('file') for h in pos_hits], 'cold_used': cold_used,
            'bytes': cost}
