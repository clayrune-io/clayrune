"""Plan-time negation interrupt — MC-944, build-sequence step 8 of
`docs/MEMORY_DESIGN_V2_SPEC.md` (§5.4, Conditions 15-16). REPORT MODE ONLY.

THE DEFECT THIS CLOSES (§5.1, Kill 1-3; the 2026-09-06 replay). The read-floor
reserve (`mc.memory`'s `_position_reserve`) matches a standing position against
the USER's task string, at dispatch. By the time an agent has fully formed a
plan — an `Agent` dispatch prompt, a spec written to `docs/**`, a plan file
under `~/.claude/plans/` — that plan is thousands of tokens downstream of the
task string the reserve gated on, on a floor that (for a live Mode-B session)
was built once, days ago. Presence in the ORIGINAL prompt already failed to
stop a re-proposal once (2026-08-23, Obsidian) — this is a SECOND, LATER firing
point, at the moment the re-proposal is written down rather than merely
conceived. `mc.memory_turn` (shipped `ba1e955`, MC-944 step 5) is the first,
EARLIER firing point — turn-scoped, resident-budget-bounded, purely additive to
the outgoing prompt. This module does not duplicate or replace that path; it
watches the agent's own OUTPUT instead of the delivery going IN.

REPORT MODE, PER CONDITION 15 — AND WHY THIS IS NOT A HOOK. §5.4 as drafted
describes a `PreToolUse` hook, mirroring `steward/fence.py`. This build step
deliberately does NOT do that: a hook sits on the execution path of every
matching tool call in the WHOLE repo (steward/fence.py is wired repo-wide in
`.claude/settings.json`, self-gating only by session type at runtime) — for a
~3,200-term content matcher with no df gate and an unmeasured false-positive
rate (Condition 15's own reasoning for why this ships in report mode at all),
adding that matcher to the execution path of every Bash/Write/Edit call is
exactly the wrong place to carry an unmeasured cost. Instead this is wired as
a passive OBSERVER at the same point `agent_routes.py` already scans live
`tool_use` stream events for plan-file detection (`_register_plan_file`) —
AFTER a tool call has already been dispatched, never before it, so a bug or
slowdown here cannot delay, deny, or alter a single tool call. `observe_tool_call`
never raises.

WHAT THIS DOES: for an `Agent` tool dispatch, a `Write`/`Edit` under a
project's `docs/**`, or a `Write`/`Edit` under `~/.claude/plans/**`, matches
the written text against every standing position (`mc.memory.list_positions`)
and appends one JSONL row per FIRE and per NEAR-MISS to
`data/negation_interrupt_log/<project_id>.jsonl` — sibling to `DATA_DIR`
(`data/projects/`), never inside it (CLAUDE.md's DATA_DIR-pollution rule).
That log is the whole deliverable of this step: §16 step 9 ("measure, then
decide") computes the interrupt's false-positive rate from it, and Conditions
15/48 are not decidable before that number exists.

WHAT THIS DELIBERATELY DOES NOT DO (out of scope for this build step):
  - Put anything in front of the agent. No stderr, no stdin injection, no
    `mc:question`. "Report" is the log; "advisory" (exit 0, stderr as a
    warning) and "block" are LATER stages Condition 15 gates on the false-
    positive rate this step exists to produce. Promoting past `report` is a
    config flip on `negation_interrupt_mode`, not a code change.
  - The Negation Ledger (§5.3) or the negation-obligation/waiver bookkeeping
    (§5.2). Those are separate parts of step 8 this build did not touch.
  - The `claim` field (§4.2's schema superset, step 2 — not shipped). Matching
    here runs against `subject`/`triggers`, the fields the live vault actually
    has; see `_phrases_for`.
  - A df gate. Condition 15 lists this as a KNOWN, DELIBERATE gap ("No df gate
    is specified for it... it scales the wrong way") — not a bug to silently
    fix; adding one here would understate the false-positive rate step 9 needs
    to measure honestly.

Leaf module, like `mc.memory_turn` beside it: imports `mc.memory` (also a
leaf) but never `mc.blueprints.*` or `server` — so `mc/memory.py`'s "never
imports server or any blueprint" invariant is not put at risk by anything
importing THIS module either. Duplicates `_is_plan_path`'s ~10-line check
rather than importing `agent_routes` for the same reason `mc.memory_turn`
duplicates `_render_position` (see that module's `_render_position_line`
docstring) — mc.memory's siblings never import a blueprint.
"""
from __future__ import annotations

import json as _json
from pathlib import Path as _Path

from mc.core import _log, now_iso as _now_iso
from mc import memory as _mem

# A plan-shaped tool call is rare (one Agent dispatch, one docs/plan write —
# not every tool call), so the cost of a full scan (list_positions() + a
# windowed conjunctive match over every position) is paid only on that rare
# path. `is_relevant()` is the CHEAP pre-filter run on every `tool_use` event
# (pure string ops, no I/O) that decides whether the expensive path runs at
# all.
_PLANS_DIR = _Path.home() / '.claude' / 'plans'

# How many tokens apart two phrase-tokens may be and still count as "the same
# claim" (Condition 16: "within a window"). Not spec-pinned; a tunable input
# to step 9's measurement, hence a config key rather than a bare constant.
_WINDOW_TOKENS_DEFAULT = 40

# Cap on interrupts logged as FIRES per single scan (Condition 16: "Cap at 2
# hits per turn ... so one write cannot produce a wall of interrupts").
_MAX_HITS_DEFAULT = 2

# A phrase with fewer than 2 significant tokens cannot be matched
# "conjunctively" at all — a single common word firing on presence alone is
# exactly the MC-898 shape the df gate exists to prevent elsewhere, and this
# path deliberately carries no df gate (Condition 15). Requiring >=2 tokens is
# the cheap, gate-free mitigation: a lone token never fires.
_MIN_PHRASE_TOKENS = 2

# Coverage floor (fraction of a phrase's tokens found within the window,
# around the SAME anchor occurrence) below which a partial match is not worth
# logging even as a near-miss — pure noise reduction on the log.
_NEAR_MISS_MIN_COVERAGE = 0.5


def _cfg(key, default):
    try:
        from mc import state
        return state.CONFIG.get(key, default)
    except Exception:
        return default


def mode() -> str:
    """`negation_interrupt_mode` — 'report' is the only stage this build step
    implements (Condition 15). Any other value (including the spec's later
    'advisory'/'block') is treated as not-yet-built and no-ops rather than
    guessing at unimplemented behaviour."""
    try:
        return str(_cfg('negation_interrupt_mode', 'report') or 'report').strip().lower()
    except Exception:
        return 'report'


def enabled() -> bool:
    return mode() == 'report'


def _max_hits() -> int:
    try:
        return max(1, int(_cfg('negation_interrupt_max_hits', _MAX_HITS_DEFAULT)
                           or _MAX_HITS_DEFAULT))
    except (TypeError, ValueError):
        return _MAX_HITS_DEFAULT


def _window_tokens() -> int:
    try:
        return max(5, int(_cfg('negation_interrupt_window_tokens', _WINDOW_TOKENS_DEFAULT)
                           or _WINDOW_TOKENS_DEFAULT))
    except (TypeError, ValueError):
        return _WINDOW_TOKENS_DEFAULT


# ── which tool calls are "a plan written down" ───────────────────────────────

def _norm(fp) -> str:
    return str(fp or '').replace('\\', '/').strip()


def _is_plan_path(fp) -> bool:
    """Mirrors `agent_routes._is_plan_path` (~/.claude/plans/*.md).
    Duplicated, not imported — see module docstring."""
    try:
        p = _Path(str(fp or ''))
        if p.suffix.lower() != '.md':
            return False
        p.resolve().relative_to(_PLANS_DIR.resolve())
        return True
    except Exception:
        return False


def _is_docs_path(fp) -> bool:
    """True for a `.md` write that LOOKS like it lands under a project's
    `docs/**` (spec §5.4 item 1). No project root is available at the cheap
    pre-filter stage, so this is a path-shape heuristic (a leading `docs/` or
    a `/docs/` path segment) rather than a resolved-and-verified containment
    check like `_is_plan_path` gets from the single global plans dir."""
    low = _norm(fp).lower()
    if not low.endswith('.md'):
        return False
    return low.startswith('docs/') or '/docs/' in low


def _text_and_source(tool_name, tool_input):
    """-> (text, source_label, path). ('', '', ...) when this tool call is not
    plan-shaped text this module cares about."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == 'Agent':
        prompt = str(ti.get('prompt') or '')
        desc = str(ti.get('description') or '')
        text = (desc + '\n' + prompt).strip()
        return (text, 'agent_dispatch', '') if text else ('', '', '')
    if tool_name not in ('Write', 'Edit'):
        return '', '', ''
    fp = str(ti.get('file_path') or '')
    if tool_name == 'Write':
        text = str(ti.get('content') or '')
    else:
        text = str(ti.get('new_string') or '')
    if not text.strip():
        return '', '', fp
    if _is_plan_path(fp):
        return text, ('plan_write' if tool_name == 'Write' else 'plan_edit'), fp
    if _is_docs_path(fp):
        return text, ('docs_write' if tool_name == 'Write' else 'docs_edit'), fp
    return '', '', fp


def is_relevant(tool_name, tool_input) -> bool:
    """Cheap pre-filter (no I/O) — run on EVERY `tool_use` event. True only
    for an Agent dispatch, or a Write/Edit landing under `docs/**` or
    `~/.claude/plans/**`."""
    text, source, _fp = _text_and_source(tool_name, tool_input)
    return bool(text) and bool(source)


# ── matching (Condition 16: phrase, conjunctive, windowed — never a bag) ────

def _phrases_for(rec) -> list:
    """Trigger PHRASES for a position: explicit `triggers:` split on comma
    (each item is one alternate phrasing a re-proposer might use), else the
    `subject` as a single phrase. `mc.memory._position_triggers` flattens the
    same source fields to a bag of tokens for the read-floor's OR-match —
    right for that gate, wrong for this one: Condition 16 requires the
    phrase's tokens to co-occur, and OR-ing across phrases first (splitting on
    comma) is what makes that requirement meaningful instead of demanding
    every trigger phrase share one window."""
    raw = str((rec or {}).get('triggers') or '').strip()
    if raw:
        phrases = [p.strip() for p in raw.split(',') if p.strip()]
    else:
        phrases = [str((rec or {}).get('subject') or '').strip()]
    return [p for p in phrases if p]


def _phrase_tokens(phrase) -> list:
    toks = {t for t in _mem._mem_tokens(phrase) if t not in _mem._POSITION_STOPWORDS}
    return sorted(toks)


def _token_positions(tokens) -> dict:
    pos: dict = {}
    for i, t in enumerate(tokens):
        pos.setdefault(t, []).append(i)
    return pos


def _phrase_match(phrase_tokens, tok_pos, window):
    """(fired, coverage). `fired` is the conjunctive-within-window test
    (every phrase token present within `window` tokens of one anchor
    occurrence). `coverage` is the best fraction found around any anchor —
    kept even on a miss so a near-fire is still loggable for step 9."""
    present = [t for t in phrase_tokens if t in tok_pos]
    if not present:
        return False, 0.0
    best_cov = 0.0
    anchor = min(present, key=lambda t: len(tok_pos[t]))
    for idx in tok_pos[anchor]:
        lo, hi = idx - window, idx + window
        hit = sum(1 for t in phrase_tokens
                  if t in tok_pos and any(lo <= p <= hi for p in tok_pos[t]))
        cov = hit / len(phrase_tokens)
        if cov > best_cov:
            best_cov = cov
        if hit == len(phrase_tokens):
            return True, 1.0
    return False, best_cov


def _match_positions(project, text):
    """-> (fires, near_misses). Each entry: {'rec', 'phrase', 'coverage'}.
    `fires` is capped at `_max_hits()`; `near_misses` is not (diagnostic
    only, never surfaced past the log)."""
    positions = _mem.list_positions(project)
    if not positions or not (text or '').strip():
        return [], []
    tokens = _mem._mem_tokens(text)
    if not tokens:
        return [], []
    tok_pos = _token_positions(tokens)
    window = _window_tokens()

    fires, near_misses = [], []
    for rec in positions:
        fire_phrase, best_cov, cov_phrase = None, 0.0, None
        for phrase in _phrases_for(rec):
            ptoks = _phrase_tokens(phrase)
            if len(ptoks) < _MIN_PHRASE_TOKENS:
                continue
            fired, cov = _phrase_match(ptoks, tok_pos, window)
            if fired:
                fire_phrase = phrase
                break
            if cov > best_cov:
                best_cov, cov_phrase = cov, phrase
        if fire_phrase:
            fires.append({'rec': rec, 'phrase': fire_phrase, 'coverage': 1.0})
        elif cov_phrase and best_cov >= _NEAR_MISS_MIN_COVERAGE:
            near_misses.append({'rec': rec, 'phrase': cov_phrase,
                                 'coverage': round(best_cov, 3)})

    fires.sort(key=lambda f: f['rec'].get('decided', ''), reverse=True)
    return fires[:_max_hits()], near_misses


# ── logging (the deliverable — step 9 reads this) ───────────────────────────

def _log_dir() -> _Path:
    """Sibling to `DATA_DIR` (`data/projects/`), never inside it — see the
    module docstring's DATA_DIR-pollution note. Resolved off `mc.memory.DATA_DIR`
    (late-bound by `memory.wire()`, same as every other memory-adjacent path)
    rather than re-deriving `MC_DATA_DIR` independently, so it always agrees
    with wherever the running process's data actually lives — tests included,
    by monkeypatching `mem.DATA_DIR` directly."""
    try:
        base = _mem.DATA_DIR.parent
    except Exception:
        base = _Path(__file__).resolve().parent.parent / 'data'
    return base / 'negation_interrupt_log'


def _log_path(project_id) -> _Path:
    safe = ''.join(c for c in str(project_id or 'unknown')
                    if c.isalnum() or c in ('-', '_')) or 'unknown'
    return _log_dir() / f'{safe}.jsonl'


def _write_log(project_id, row: dict) -> None:
    try:
        p = _log_path(project_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'a', encoding='utf-8') as f:
            f.write(_json.dumps(row, ensure_ascii=False) + '\n')
    except Exception as e:
        _log(f"[negation-interrupt] log write failed for {project_id}: {e}")


def _row(project_id, session_id, source, path, result, rec, phrase, coverage,
         text_len) -> dict:
    row = {
        'ts': _now_iso(),
        'project_id': project_id,
        'session_id': session_id or '',
        'source': source,
        'path': path,
        'result': result,                 # 'fire' | 'near_miss'
        'position_file': rec.get('file', ''),
        'subject': rec.get('subject', ''),
        'verdict': rec.get('verdict', ''),
        'matched_phrase': phrase,
        'coverage': coverage,
        'decided': rec.get('decided', ''),
        'text_len': text_len,
    }
    if result == 'fire':
        # The reason is the payload (brief, "Hard requirements") — a bare
        # "this was rejected" cannot be judged. expires_when is what lets the
        # reading agent decide whether the ruling still holds.
        row['reason'] = rec.get('reason', '')
        row['expires_when'] = rec.get('expires_when', '')
    return row


# ── entrypoint ────────────────────────────────────────────────────────────

def observe_tool_call(project, session_id, tool_name, tool_input) -> list:
    """Report-mode observer. NEVER raises, NEVER blocks — call this AFTER a
    tool call has already streamed back (see module docstring for why this is
    not a `PreToolUse` hook). Returns the fires logged (for tests); the log
    file is the real deliverable.

    `project` must already be the loaded project dict — this module does not
    load one itself, so a caller can use `is_relevant()` to skip that I/O on
    the (overwhelming majority of) tool calls this module does not care
    about."""
    if not enabled() or not isinstance(project, dict):
        return []
    try:
        text, source, path = _text_and_source(tool_name, tool_input)
        if not text or not source:
            return []
        fires, near_misses = _match_positions(project, text)
        pid = project.get('id', '')
        for m in fires:
            _write_log(pid, _row(pid, session_id, source, path, 'fire',
                                  m['rec'], m['phrase'], m['coverage'], len(text)))
        for m in near_misses:
            _write_log(pid, _row(pid, session_id, source, path, 'near_miss',
                                  m['rec'], m['phrase'], m['coverage'], len(text)))
        if fires:
            _log(f"[negation-interrupt] {pid}: {len(fires)} position(s) "
                 f"re-proposed in a {source} — see "
                 f"data/negation_interrupt_log/{pid}.jsonl")
        return fires
    except Exception as e:
        _log(f"[negation-interrupt] scan failed for "
             f"{(project or {}).get('id')}: {e}")
        return []
