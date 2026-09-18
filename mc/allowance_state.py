"""Per-vendor allowance state — VENDOR_AGNOSTIC_PROGRAM.md §4.

Ron's bar: the ONLY reason an installed, signed-in agent may be refused is
vendor allowance, and it must be shown, never silently rerouted. This module
is the single place that:

  1. Parses each adapter's real exhaustion signal into one normalized shape
     (`detect(provider, msg)` — never invented text, only what the vendor's
     own CLI actually sends; see each `detect_from_*` docstring for the
     evidence it is built from).
  2. Holds the resulting state per vendor, server-side, OUTSIDE `DATA_DIR`
     (`data/projects/` — CLAUDE.md's `load_projects()` rule: anything else
     written there becomes a malformed "project" and 500s both restart
     endpoints). Mirrors `mc/blueprints/system_routes.py`'s
     `SYSTEM_STATUS_PATH` sibling-file pattern exactly.
  3. Answers the two questions callers actually need: `is_exhausted(vendor)`
     for a dispatch-time refusal, and `refusal_message(vendor)` for the
     user-facing text naming the vendor, the limit and the reset time.

A terminal failure is never content (Fenn #4): callers that observe an
ALLOWANCE_EXHAUSTED event record it here and raise/refuse — they never let
the quota text ride through as if it were the model's answer.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from mc.atomic_json import write_json_atomic
from mc.core import _log

# Wired by server.py at startup, same pattern as
# system_routes.SYSTEM_STATUS_PATH — a sibling of data/system_status.json,
# never a member of data/projects/.
STATE_PATH: Optional[Path] = None

_lock = threading.Lock()
_STATE: Dict[str, dict] = {}

# Reused from agent_routes.py's own `_QUOTA_ERROR_HINTS` heuristic (kept as a
# separate copy — that one lives in a module allowance_state must not import,
# to avoid a blueprint importing back into this seam). Applied only to
# providers whose CLI gives no structured exhaustion field (Gemini, Qwen);
# Claude and Codex are detected from real structured fields instead.
_TEXT_HINTS = ('quota', 'rate limit', 'resource_exhausted', '429',
               'too many requests', 'usage limit')


def wire(state_path) -> None:
    global STATE_PATH
    STATE_PATH = Path(state_path)
    _load()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> None:
    global _STATE
    try:
        if STATE_PATH and STATE_PATH.is_file():
            data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            _STATE = data if isinstance(data, dict) else {}
        else:
            _STATE = {}
    except Exception as e:
        _log(f"[allowance] state file unreadable, starting empty: {e}")
        _STATE = {}


def _save() -> None:
    try:
        if not STATE_PATH:
            return
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(STATE_PATH, _STATE, indent=2)
    except Exception as e:
        _log(f"[allowance] failed to persist state: {e}")


# ─────────────────────────────────────────────────────────────────────────
# Recording / querying — the two things a dispatch call site needs.
# ─────────────────────────────────────────────────────────────────────────


def record_exhaustion(vendor: str, *, limit_kind: str = '',
                       resets_at: Optional[str] = None,
                       resets_at_display: str = '',
                       raw_ref: str = '', verified: bool = True) -> None:
    """Record vendor as exhausted. `resets_at` is an ISO8601 string when the
    vendor gave a machine-parseable time (Claude's epoch `resetsAt`); None
    when only human text exists (Codex's "Sep 24th, 2026 7:58 AM") or no
    reset time was given at all (Gemini/Qwen's plain error text).
    `resets_at_display` is what a human should read; it is always populated,
    falling back to "reset time unknown" at read time if empty.
    """
    vendor = (vendor or '').strip().lower()
    if not vendor:
        return
    with _lock:
        _STATE[vendor] = {
            'limit_kind': limit_kind or 'unknown',
            'resets_at': resets_at,
            'resets_at_display': resets_at_display,
            'raw_ref': (raw_ref or '')[:2000],
            'verified': bool(verified),
            'recorded_at': _now_iso(),
        }
        _save()


def clear_exhaustion(vendor: str) -> None:
    """Called on the next successful run for a vendor (VENDOR_AGNOSTIC_PROGRAM
    §4: "cleared at resets_at or on the next successful run")."""
    vendor = (vendor or '').strip().lower()
    with _lock:
        if vendor in _STATE:
            del _STATE[vendor]
            _save()


def _is_expired(entry: dict) -> bool:
    resets_at = entry.get('resets_at')
    if not resets_at:
        return False  # unknown reset time never auto-clears; needs a success
    try:
        dt = datetime.fromisoformat(str(resets_at).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            # Best-effort for a reset time parsed from vendor prose with no
            # timezone (Codex): compare naive-to-naive. Worst case this clears
            # a little early or late — the same tolerance the codebase already
            # accepts elsewhere (agent_routes.py's _QUOTA_BLOCK_WINDOW_HOURS
            # slack for exactly this kind of uncertainty).
            return datetime.now() >= dt
        return datetime.now(timezone.utc) >= dt
    except Exception:
        return False


def get(vendor: str) -> Optional[dict]:
    """The vendor's current exhaustion record, or None if not exhausted (or
    its reset time has passed — auto-cleared here on read)."""
    vendor = (vendor or '').strip().lower()
    with _lock:
        entry = _STATE.get(vendor)
        if not entry:
            return None
        if _is_expired(entry):
            del _STATE[vendor]
            _save()
            return None
        return dict(entry)


def is_exhausted(vendor: str) -> bool:
    return get(vendor) is not None


def all_states() -> Dict[str, dict]:
    """Every currently-exhausted vendor's record, expiry-checked. For the
    Floor/bench/chooser to paint every figure in one pass without one
    `get()` call per card."""
    with _lock:
        vendors = list(_STATE.keys())
    out = {}
    for v in vendors:
        entry = get(v)
        if entry:
            out[v] = entry
    return out


def format_resets_at(entry: dict) -> str:
    display = entry.get('resets_at_display')
    if display:
        return display
    resets_at = entry.get('resets_at')
    if not resets_at:
        return 'reset time unknown'
    try:
        dt = datetime.fromisoformat(str(resets_at).replace('Z', '+00:00'))
        return _format_dt(dt)
    except Exception:
        return str(resets_at)


def _format_dt(dt: datetime) -> str:
    # Portable "Sep 24, 2026 7:58 AM" — %-d/%-I are glibc-only and %#d/%#I
    # are Windows-only; building it by hand works on both.
    hour12 = ((dt.hour - 1) % 12) + 1
    ampm = 'AM' if dt.hour < 12 else 'PM'
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} {hour12}:{dt.minute:02d} {ampm}"


def refusal_message(vendor: str) -> str:
    """'' if the vendor is usable; otherwise the user-facing refusal, naming
    the vendor, the limit and the reset time (VENDOR_AGNOSTIC_PROGRAM §4
    item 3). Never suggests a fallback — there is none."""
    entry = get(vendor)
    if not entry:
        return ''
    limit = entry.get('limit_kind') or 'usage limit'
    return (f"{vendor} is out of allowance ({limit}), "
            f"resets {format_resets_at(entry)} — no fallback to another vendor")


def display_text(vendor: str) -> str:
    """Short form for a Floor/bench/chooser badge: 'Out of allowance, resets
    <time>'."""
    entry = get(vendor)
    if not entry:
        return ''
    return f"Out of allowance, resets {format_resets_at(entry)}"


# ─────────────────────────────────────────────────────────────────────────
# Detection — one real-evidence parser per vendor, dispatched by `detect()`.
# ─────────────────────────────────────────────────────────────────────────

_CODEX_RESET_RE = re.compile(
    r'try again at ([A-Za-z]{3,9}\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4}'
    r'(?:,? \d{1,2}:\d{2} ?[AaPp][Mm])?)')
_ORDINAL_RE = re.compile(r'(\d+)(st|nd|rd|th)\b')


def _parse_codex_reset_text(message: str) -> Optional[datetime]:
    m = _CODEX_RESET_RE.search(message or '')
    if not m:
        return None
    text = _ORDINAL_RE.sub(r'\1', m.group(1)).replace(',', '')
    for fmt in ('%b %d %Y %I:%M %p', '%B %d %Y %I:%M %p',
                '%b %d %Y', '%B %d %Y'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def detect_from_claude_rate_limit_event(msg: dict) -> Optional[dict]:
    """Claude's `rate_limit_event` (mc/agent_runtime.py `parse_event`,
    mc/blueprints/system_routes.py `_capture_system_init`). Field SHAPE is
    real, live-captured on this box (`data/system_status.json`,
    2026-09-18T06:26 — `rate_limit_info: {status: "allowed", resetsAt:
    1789714800 (unix epoch seconds, NOT the ISO string the old test fixture
    in tests/test_claude_runtime.py assumed), rateLimitType: "five_hour",
    overageStatus: "rejected", ...}`). No EXHAUSTED sample was captured live
    on this box (Ron's 2026-09-17 outage left no rate_limit_event in
    ~/.claude/projects/*/*.jsonl — that transcript format only persists
    conversation turns, not the stream-json control channel MC reads over
    stdout). `status == 'rejected'` as the exhausted value is Anthropic's
    documented enum for this field, not itself a live-captured exhausted
    sample — `verified=False` reflects that gap honestly.
    """
    if msg.get('type') != 'rate_limit_event':
        return None
    info = msg.get('rate_limit_info') or {}
    status = info.get('status')
    if status not in ('rejected', 'exceeded'):
        return None
    resets_at = None
    raw_resets = info.get('resetsAt')
    if raw_resets is not None:
        try:
            resets_at = datetime.fromtimestamp(
                float(raw_resets), tz=timezone.utc).isoformat()
        except Exception:
            resets_at = None
    return {
        'limit_kind': info.get('rateLimitType') or 'unknown',
        'resets_at': resets_at,
        'resets_at_display': '',
        'raw_ref': json.dumps(info)[:2000],
        'verified': False,
    }


_CODEX_USAGE_LIMIT_TEXT = "You've hit your usage limit"


def detect_from_codex_message(msg: dict) -> Optional[dict]:
    """Codex's `usage_limit_exceeded`. Real, live-captured (2026-09-18,
    ~/.codex/sessions/2026/09/17/rollout-2026-09-17T22-19-23-01a0b2f4-*.jsonl,
    ordinal 82):

      {"type":"event_msg","payload":{"type":"task_complete", ...,
       "error":{"message":"You've hit your usage limit. Visit
       https://chatgpt.com/codex/settings/usage to purchase more credits or
       try again at Sep 24th, 2026 7:58 AM.",
       "codex_error_info":"usage_limit_exceeded"}}}

    That is the rollout file's own event schema (`event_msg`/`task_complete`)
    — a different wire shape from `codex exec --json`'s translated
    `item.completed`/`turn.completed` protocol that CodexRuntime.parse_event
    otherwise handles. Both shapes are checked here since it is unconfirmed
    (without spending Codex allowance to find out) whether a session-level
    failure like this one is translated the same way `exec --json` translates
    turn/item events, or passed through in Codex's native shape — this
    function accepts either without guessing which.
    """
    error = None
    if msg.get('type') == 'event_msg':
        payload = msg.get('payload') or {}
        error = payload.get('error')
    if error is None and msg.get('type') in ('error', 'turn.failed'):
        error = msg.get('error')
        # `codex exec --json` stream shape, live-captured 2026-09-18 from a real
        # launch while out of allowance (Dave, config-parse check):
        #   {"type":"error","message":"You've hit your usage limit. ... try again at Sep 24th, 2026 7:58 AM."}
        #   {"type":"turn.failed","error":{"message":"You've hit your usage limit. ..."}}
        # Neither carries codex_error_info, and `error` carries the message at
        # the top level, so the rollout-only check below missed the live path.
        if error is None and isinstance(msg.get('message'), str):
            error = {'message': msg['message']}
    if not isinstance(error, dict):
        return None
    if error.get('codex_error_info') != 'usage_limit_exceeded':
        if _CODEX_USAGE_LIMIT_TEXT not in (error.get('message') or ''):
            return None
    message = error.get('message') or ''
    reset_dt = _parse_codex_reset_text(message)
    reset_match = _CODEX_RESET_RE.search(message)
    return {
        'limit_kind': 'usage_limit',
        'resets_at': reset_dt.isoformat() if reset_dt else None,
        'resets_at_display': reset_match.group(1) if reset_match else '',
        'raw_ref': message[:2000],
        'verified': True,
    }


def _generic_text_exhaustion(text: str) -> Optional[dict]:
    """Gemini/Qwen: neither CLI's error envelope carries a structured
    exhaustion field or reset time (checked: GeminiRuntime.parse_event's
    `result`+`status=='error'` branch and QwenRuntime.parse_event's
    `result`+`is_error` branch both surface only a free-text `message`).
    No real captured exhaustion sample exists on this box for either vendor
    (Ron has not hit a Gemini/Qwen limit that left a log here) — this is
    built from the documented error text shape only
    (`_TEXT_HINTS`, already used by agent_routes.py's own quota-log scraper)
    and is explicitly `verified=False`. Reset time is always unknown: neither
    vendor's plain-text error names one.
    """
    t = (text or '').lower()
    if not any(h in t for h in _TEXT_HINTS):
        return None
    return {
        'limit_kind': 'unknown',
        'resets_at': None,
        'resets_at_display': '',
        'raw_ref': (text or '')[:2000],
        'verified': False,
    }


def detect(provider: str, msg: dict) -> Optional[dict]:
    """Dispatch to the right vendor parser. `msg` is the parsed JSON dict
    from that vendor's own stream — never a re-derived or invented shape."""
    provider = (provider or '').lower()
    if provider == 'claude':
        return detect_from_claude_rate_limit_event(msg)
    if provider == 'codex':
        return detect_from_codex_message(msg)
    if provider == 'gemini':
        if msg.get('type') == 'result' and msg.get('status') == 'error':
            err = msg.get('error') or {}
            text = err.get('message') or msg.get('message') or ''
            return _generic_text_exhaustion(text)
        return None
    if provider == 'qwen':
        if msg.get('type') == 'result' and msg.get('is_error'):
            err = msg.get('error') or {}
            text = err.get('message') or msg.get('result') or ''
            return _generic_text_exhaustion(text)
        return None
    return None


def observe(provider: str, msg: dict) -> Optional[dict]:
    """Convenience: detect() + record_exhaustion() in one call, for the
    stream/oneshot reader call sites. Returns the recorded entry, or None if
    `msg` showed no exhaustion."""
    hit = detect(provider, msg)
    if hit:
        record_exhaustion(provider, **hit)
    return hit
