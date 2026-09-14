"""Mid-task memory push — observer, REPORT MODE ONLY (MC-944, Ron's idea
2026-09-14, `~/.claude/plans/memory-architecture-resolve.md` "Mid-task memory
push"). Generalises the plan-time negation interrupt (`mc/negation_interrupt.py`,
`a49d269`, build-sequence step 8) from STANDING POSITIONS to the whole memory
corpus, and from the agent's WRITTEN OUTPUT (a plan) to its TOOL STREAM (what
it is doing right now, not what it has decided to do).

THE DEFECT THIS CLOSES. `mc.memory_turn`'s per-turn refresh (step 5, `ba1e955`)
re-runs the read floor on every live message — but only against the USER's
message text. Knowledge relevant to what the AGENT is doing mid-turn — an error
string in a tool result, a file path it opens, an HTTP 404 — never arrives,
because nothing reads the tool stream for it. Real case, 2026-09-14: a
workflow-cancel route 404'd because the running server predated the commit
that added it; a note about that exact class of issue existed in the vault and
would have saved Bram a full diagnosis, but nothing surfaced it — the user's
message that turn was about something else entirely.

WHAT THIS DOES: for a tool call's INPUT (a file path a Read/Edit/Write opens,
a Grep pattern) or its RESULT (Bash/PowerShell output, an error line, an
exception name, an HTTP status), extracts a short query and matches it against
the SAME BM25 ranker `mc.memory._memory_search` already runs for the read
floor and the per-turn refresh (same corpus, same ranking — this module adds
no second index). A hit above a HIGH score threshold is a "would-send"
candidate; below it, a "near-miss". Every decision — fire or near-miss — is
appended to `data/memory_push_log/<project_id>.jsonl`, sibling to `DATA_DIR`
(`data/projects/`), never inside it (CLAUDE.md's DATA_DIR-pollution rule).
That log IS the deliverable of this build step: nothing here reaches the
agent yet (`memory_push_mode` defaults to `'report'`; only `'report'` is
implemented, matching Condition 15's precedent in `negation_interrupt.py`) —
a human reviews the log with `tools/memory-eval/push_review.py` to grade
precision before anything is wired to actually nudge a live agent.

WHY A SCORE THRESHOLD INSTEAD OF THE CONJUNCTIVE PHRASE MATCH
`negation_interrupt.py` uses. That module matches WRITTEN TEXT (a plan, a
dispatch prompt) against a small, curated set of standing POSITIONS — a
conjunctive within-window match on explicit trigger phrases is precise there
because positions are few and their triggers are hand-authored to be
distinguishing. This module matches arbitrary TOOL OUTPUT — a Bash stderr
line, a file basename — against the WHOLE corpus (topic notes, positions,
archive), where no per-unit trigger list exists for most units. The read
floor already solved "rank the whole corpus against a short query" with BM25;
reusing it is what keeps this module leaf-simple and its ranking identical to
every other retrieval path this project has. The cost is that BM25 alone is a
noisier signal than a curated trigger match — measured against the live
mission_control vault (2026-09-14): a query of unrelated filler tokens still
scored 11.8 on a real topic file via incidental title-word overlap, while a
genuinely on-topic query ("DATA_DIR pollution stray file 500 restart
blocker") scored 16.8 on the note that actually discusses it. The default
`memory_push_min_score` (15.0) sits above that measured noise ceiling, not at
a round number — see `_MIN_SCORE_DEFAULT`. This is exactly the kind of
threshold Condition 15's report-mode-first discipline exists to let a human
re-tune once the log has real volume, not something to treat as settled.

WHY ARCHIVE NEEDS A HIGHER BAR. The archive is ~30x the topic corpus in unit
count (`_memory_search`'s own docstring) and each unit is a single stripped
line with far less context than a topic note — the same reason
`_archive_quota` exists for the read floor. A push channel that fires on
archive as readily as on a topic note would mostly surface stray one-liners;
`_ARCHIVE_SCORE_MULTIPLIER` demands a proportionally stronger match before an
archive line is worth interrupting a tool stream for. The curated `managed`
class (MEMORY.md's own index entries) is excluded outright — it is already
auto-loaded into every prompt, so "surfacing" it via this channel would just
be pointing the agent at text it already has.

WHAT THIS DELIBERATELY DOES NOT DO (out of scope for this build step):
  - Deliver anything to the agent. No stderr, no stdin injection, no
    PostToolUse hook. Whether/how to do that is `~/.claude/plans/
    memory-architecture-resolve.md` step 7's RESEARCH question (verify
    `hookSpecificOutput.additionalContext` support), tracked separately and
    NOT wired by this module regardless of its answer.
  - Wire any provider besides Claude Mode B. The two call sites are the same
    two `agent_routes.py` stream readers `negation_interrupt` already uses
    (`_read_agent_stream`, `_read_agent_stream_b`) — the only place this
    project currently parses a live Claude tool_use/tool_result stream at
    all. Other providers' delivery path is the other half of step 7's
    research question, not a code change here.
  - A df gate, an embeddings layer, or anything beyond the existing BM25
    ranker. `_memory_search` is reused unmodified.

Leaf module, like `mc.memory_turn` and `mc.negation_interrupt` beside it:
imports `mc.memory` (also a leaf) but never `mc.blueprints.*` or `server`.
"""
from __future__ import annotations

import json as _json
import os as _os
import re as _re
from pathlib import Path as _Path

from mc.core import _log, now_iso as _now_iso
from mc import memory as _mem

# ── extraction (pure string ops, no I/O — the cheap pre-filter) ────────────

# A tool RESULT is only worth a search if it carries a concrete failure
# signal. Deliberately narrow (not "any word that sounds bad") — the point is
# to skip the overwhelming majority of successful tool output without paying
# for a BM25 scan on it, not to classify sentiment.
_ERROR_LINE_RE = _re.compile(
    r'(?i)(\berror\b|\bexception\b|\btraceback\b|\bfailed\b|\bfailure\b|'
    r'\bnot\s+found\b|\bdenied\b|\brefused\b|\btimed?\s*out\b|\bpanic\b|'
    r'\b[45]\d{2}\b|'
    # CamelCase exception class names (KeyError, TypeError,
    # ConnectionRefusedError, ValueError, ...): "Error"/"Exception" glued
    # onto a preceding word never has a \b of its own between the two — the
    # bare \berror\b/\bexception\b alternatives above never match "KeyError".
    r'\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b)'
)

# A tool result can be a multi-MB dump (a huge Bash stdout, a giant grep).
# Real failure signals cluster at the START (a usage/traceback header) or the
# END (the last raised exception, the final HTTP response) of output that
# size — so scanning a bounded head+tail window instead of the whole string
# keeps this synchronous and cheap regardless of how large the tool result
# was, without needing to move the scan off-thread.
_SCAN_HEAD_CHARS = 4000
_SCAN_TAIL_CHARS = 2000

_MAX_QUERY_CHARS = 300

# Tool inputs worth turning into a query: the file/pattern a tool is about to
# touch. Not every tool — Bash/TodoWrite/etc. inputs are not "a path", and
# their OUTPUT is what this module wants instead (see extract_result_query).
_PATH_INPUT_TOOLS = ('Read', 'Edit', 'Write')


def extract_input_query(tool_name, tool_input) -> str:
    """-> a short query built from a tool call's INPUT, or '' when this tool
    call's input carries no path/pattern signal worth searching on. Pure
    string ops — safe to call before any project load."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in _PATH_INPUT_TOOLS:
        fp = str(ti.get('file_path') or '').strip()
        return _os.path.basename(fp) if fp else ''
    if tool_name == 'Grep':
        pat = str(ti.get('pattern') or '').strip()
        fp = str(ti.get('path') or '').strip()
        parts = [p for p in (pat, _os.path.basename(fp) if fp else '') if p]
        return ' '.join(parts)[:_MAX_QUERY_CHARS]
    return ''


def extract_result_query(result_text) -> str:
    """-> a short query built from a tool call's RESULT text (error/exception
    lines, HTTP status lines), or '' when the result carries no failure
    signal (success-only output, empty, or a huge dump with no match in the
    scanned window). Pure string ops — safe to call before any project load.
    """
    text = str(result_text or '')
    if not text.strip():
        return ''
    if len(text) > _SCAN_HEAD_CHARS + _SCAN_TAIL_CHARS:
        window = text[:_SCAN_HEAD_CHARS] + '\n' + text[-_SCAN_TAIL_CHARS:]
    else:
        window = text
    hits = [ln.strip() for ln in window.splitlines()
            if ln.strip() and _ERROR_LINE_RE.search(ln)]
    if not hits:
        return ''
    return ' '.join(hits)[:_MAX_QUERY_CHARS]


# ── config ───────────────────────────────────────────────────────────────

# Measured against the live mission_control vault, 2026-09-14 (see module
# docstring): unrelated filler tokens scored up to 11.8 on a real topic file
# via incidental title-word overlap; a genuinely on-topic query scored 16.8
# on the note that actually discusses it. 15.0 sits above the measured noise
# ceiling. Not spec-pinned — a tunable input to grading the report-mode log,
# hence a config key rather than a bare constant.
_MIN_SCORE_DEFAULT = 15.0

# Cap on NEW (non-duplicate) would-send fires logged per single observe()
# call — mirrors negation_interrupt's `_max_hits` (Condition 16: "so one
# write cannot produce a wall of interrupts"), applied here to one tool
# call's worth of output instead of one plan write.
_MAX_PER_TURN_DEFAULT = 2

# Archive lines are short, numerous (~30:1 vs topic notes — _memory_search's
# own docstring), and easy to match on an incidental shared word. Demand a
# proportionally stronger score before pushing one out of a tool stream.
_ARCHIVE_SCORE_MULTIPLIER = 1.5

# How many BM25 candidates to pull per observe() call. Bounds both the
# would-send cap and the near-miss log volume per call without a separate
# knob — the search itself never returns more than this many rows.
_SEARCH_TOPK = 6

_DELIVERED_KEY = '_mem_push_sent'


def _cfg(key, default):
    try:
        from mc import state
        return state.CONFIG.get(key, default)
    except Exception:
        return default


def mode() -> str:
    """`memory_push_mode` — 'report' is the only stage this build step
    implements. Any other value (including the spec's later 'advisory') is
    treated as not-yet-built and no-ops rather than guessing at unimplemented
    behaviour (mirrors `negation_interrupt.mode`)."""
    try:
        return str(_cfg('memory_push_mode', 'report') or 'report').strip().lower()
    except Exception:
        return 'report'


def enabled() -> bool:
    return mode() == 'report'


def _min_score() -> float:
    try:
        return float(_cfg('memory_push_min_score', _MIN_SCORE_DEFAULT) or _MIN_SCORE_DEFAULT)
    except (TypeError, ValueError):
        return _MIN_SCORE_DEFAULT


def _max_per_turn() -> int:
    try:
        return max(1, int(_cfg('memory_push_max_per_turn', _MAX_PER_TURN_DEFAULT)
                           or _MAX_PER_TURN_DEFAULT))
    except (TypeError, ValueError):
        return _MAX_PER_TURN_DEFAULT


# ── classification (duplicates the filename heuristics `memory_turn.py` /
#    `negation_interrupt.py` already use — `_memory_search` strips `cls` from
#    its returned hits before they leave the function, so a caller has to
#    re-derive class from the filename, not import an internal field) ───────

def _hit_class(file_name) -> str:
    fn = str(file_name or '')
    base = _os.path.basename(fn)
    if _mem._is_position_file(base):
        return 'position'
    if fn.endswith('MEMORY_ARCHIVE.md') or '#archive' in fn:
        return 'archive'
    if '#managed' in fn:
        return 'managed'
    return 'topic'


def _passes(cls, score, min_score) -> bool:
    if cls == 'managed':
        return False
    bar = min_score * _ARCHIVE_SCORE_MULTIPLIER if cls == 'archive' else min_score
    return score >= bar


# ── logging (the deliverable — push_review.py reads this) ──────────────────

def _log_dir() -> _Path:
    """Sibling to `DATA_DIR` (`data/projects/`), never inside it — see the
    module docstring's DATA_DIR-pollution note. Resolved off `mc.memory.DATA_DIR`
    (late-bound by `memory.wire()`) so it always agrees with wherever the
    running process's data actually lives, tests included (monkeypatch
    `mem.DATA_DIR` directly, same as `test_negation_interrupt.py`'s fixture)."""
    try:
        base = _mem.DATA_DIR.parent
    except Exception:
        base = _Path(__file__).resolve().parent.parent / 'data'
    return base / 'memory_push_log'


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
        _log(f"[memory-push] log write failed for {project_id}: {e}")


def _row(project_id, session_id, provider, tool_name, source, query, result,
         note_file, cls, score, rank, threshold, already_delivered) -> dict:
    return {
        'ts': _now_iso(),
        'project_id': project_id,
        'session_id': session_id or '',
        'provider': provider or '',
        'tool': tool_name or '',
        'source': source,                  # 'tool_input' | 'tool_result'
        'query': query,
        'result': result,                  # 'would_send' | 'near_miss'
        'note': note_file,
        'note_class': cls,                 # 'topic' | 'position' | 'archive'
        'score': score,
        'rank': rank,
        'threshold': threshold,
        # True when mc.memory_turn's per-turn refresh already sent this same
        # note for real this conversation — a would-send flagged True here is
        # redundant with a channel that already ships (Condition-15-style
        # report-mode grading needs this to separate "genuinely new
        # knowledge" from "the real delivery path already covered it").
        'already_delivered_by_per_turn': bool(already_delivered),
    }


# ── entrypoint ───────────────────────────────────────────────────────────

def observe(project, session, tool_name, source, query, provider=None) -> list:
    """Report-mode observer. NEVER raises, NEVER blocks — call this AFTER a
    tool call (or its result) has already streamed back. Returns the file
    names logged as new would-send fires (for tests); the log file is the
    real deliverable.

    `project` must already be the loaded project dict — this module does not
    load one itself, so a caller can use `extract_input_query`/
    `extract_result_query` to skip that I/O on the (overwhelming majority of)
    tool calls that produce no query at all."""
    if not enabled() or not isinstance(project, dict) or not isinstance(session, dict):
        return []
    if not (query or '').strip():
        return []
    try:
        return _observe(project, session, tool_name, source, query, provider)
    except Exception as e:
        _log(f"[memory-push] observe failed for {(project or {}).get('id')}: {e}")
        return []


def _observe(project, session, tool_name, source, query, provider) -> list:
    pid = project.get('id', '')
    sid = session.get('session_id') or session.get('id') or ''
    prov = provider or session.get('provider') or 'claude'
    min_score = _min_score()

    hits = _mem._memory_search(project, query, topk=_SEARCH_TOPK, expand=0, record=None)
    if not hits:
        return []

    sent_set = session.setdefault(_DELIVERED_KEY, set())
    turn_delivered = session.get('_mem_turn_delivered') or set()

    fires, dropped_dupes = [], 0
    for rank, h in enumerate(hits, start=1):
        fname = h.get('file', '')
        cls = _hit_class(fname)
        if cls == 'managed':
            continue
        score = h.get('score', 0.0)
        already = fname in turn_delivered
        if _passes(cls, score, min_score):
            if fname in sent_set:
                # Dedupe: never log the same note twice this session via this
                # channel. Not a near-miss (it already fired once) and not
                # worth a fresh log row every time the same file keeps coming
                # up in a long tool loop.
                dropped_dupes += 1
                continue
            fires.append((fname, _row(pid, sid, prov, tool_name, source, query,
                                       'would_send', fname, cls, score, rank,
                                       min_score, already)))
        else:
            _write_log(pid, _row(pid, sid, prov, tool_name, source, query,
                                  'near_miss', fname, cls, score, rank,
                                  min_score, already))

    fires = fires[:_max_per_turn()]
    new_files = []
    for fname, row in fires:
        sent_set.add(fname)
        _write_log(pid, row)
        new_files.append(fname)

    if new_files:
        _log(f"[memory-push] {pid}: {len(new_files)} note(s) surfaced for a "
             f"{source} on {tool_name} — see data/memory_push_log/{pid}.jsonl")
    return new_files
