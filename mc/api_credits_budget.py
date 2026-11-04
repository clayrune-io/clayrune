"""Settings, ledger and gate for the API-credits offload (docs/API_CREDITS_BUILD_SPEC.md).

What this module owns: the user's mode/budget/stop-point settings
(`data/api_credits.json`), the per-cycle spend ledger
(`data/api_credits_ledger.json`), the price table, and the single question
"may this call be offloaded?" (`can_spend`). It never talks to the network and
never touches the API key; `mc/api_credits_transport.py` and
`mc/api_credits_offload.py` do that.

Both files live in `data/`, NOT `data/projects/` (CLAUDE.md DATA_DIR pollution
rule), and are gitignored: they carry one operator's budget.

Anthropic only enforces the Console workspace spend limit. This ledger is
Clayrune's own count (tokens x price), so every dollar figure derived from the
price table is an ESTIMATE and the UI says so.
"""
from __future__ import annotations

import calendar
import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log

MODES = ('off', 'measure', 'on')
DEFAULT_MODEL = 'claude-haiku-5-5'
ENTRIES_PER_CYCLE_CAP = 5000

# ── price table ──────────────────────────────────────────────────────────────
# USD per million tokens. Source: docs/API_CREDITS_BUILD_SPEC.md "Appendix:
# price table", copied 2026-10-30 from the bundled claude-api skill
# (shared/models.md, model-migration.md, prompt-caching.md, build 2.1.312),
# then checked 2026-10-30 against platform.claude.com/docs/en/about-claude/
# pricing: Sonnet 5.5 cache read corrected to $0.10 (0.05x input), Opus 5
# added ($0.50 cache read is now stated).
# Only explicitly stated figures are listed. A model or field absent here is
# "price unknown" and that call falls back to the subscription path; a price is
# never guessed. Cache write is the 5-minute-TTL rate (1.25x input); phase 1
# writes no other TTL. Haiku 5.5 has two rate cards chosen by the request's
# total prompt tokens (input + cache read + cache write), so a model maps to a
# list of tiers ordered by `up_to_prompt_tokens` (None = no upper bound).
# `data/api_credits.json` > `prices` overrides this per model, same shape,
# because the skill says to check the live pricing page before budgeting.
_HAIKU_LIMIT = 100_000
PRICE_TABLE: Dict[str, List[Dict[str, Any]]] = {
    'claude-fable-5-1': [{'up_to_prompt_tokens': None, 'input': 10.00, 'output': 50.00,
                          'cache_read': 0.25, 'cache_write': 12.50}],
    'claude-fable-5': [{'up_to_prompt_tokens': None, 'input': 10.00, 'output': 50.00,
                        'cache_read': 1.00, 'cache_write': 12.50}],
    'claude-opus-5-5': [{'up_to_prompt_tokens': None, 'input': 4.00, 'output': 20.00,
                         'cache_read': 0.20, 'cache_write': 5.00}],
    'claude-sonnet-5-5': [{'up_to_prompt_tokens': None, 'input': 2.00, 'output': 10.00,
                           'cache_read': 0.10, 'cache_write': 2.50}],
    'claude-sonnet-5': [{'up_to_prompt_tokens': None, 'input': 2.00, 'output': 10.00,
                         'cache_read': 0.20, 'cache_write': 2.50}],
    'claude-haiku-5-5': [
        {'up_to_prompt_tokens': _HAIKU_LIMIT, 'input': 0.10, 'output': 0.50,
         'cache_read': 0.01, 'cache_write': 0.125},
        {'up_to_prompt_tokens': None, 'input': 0.50, 'output': 2.50,
         'cache_read': 0.05, 'cache_write': 0.625},
    ],
    'claude-opus-5': [{'up_to_prompt_tokens': None, 'input': 5.00, 'output': 25.00,
                       'cache_read': 0.50, 'cache_write': 6.25}],
}
_RATE_FIELDS = ('input', 'output', 'cache_read', 'cache_write')

# ── paths / wiring ───────────────────────────────────────────────────────────
_lock = threading.RLock()
_data_dir: Path = Path(__file__).resolve().parent.parent / 'data'
_notifier: Optional[Callable[[str, str], None]] = None
_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
_cap_logged: set = set()


def wire(data_dir) -> None:
    """Point at the server's data dir (frozen apps keep it outside the checkout)."""
    global _data_dir
    _data_dir = Path(data_dir)


def set_notifier(fn: Optional[Callable[[str, str], None]]) -> None:
    """Install the notification sender `fn(title, body)`; None = built-in push."""
    global _notifier
    _notifier = fn


def settings_path() -> Path:
    return _data_dir / 'api_credits.json'


def ledger_path() -> Path:
    return _data_dir / 'api_credits_ledger.json'


def _now() -> datetime:
    return _now_fn()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')


# ── settings ─────────────────────────────────────────────────────────────────

def default_settings() -> Dict[str, Any]:
    return {'mode': 'off', 'secret_name': '', 'plan_credit_usd': None,
            'budget_usd': None, 'stop_pct': 90, 'reset_day': 1,
            'default_model': DEFAULT_MODEL, 'prices': {}}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(read_text_with_retry(path))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log(f"[api-credits] could not read {path.name}: {e}", flush=True)
        return {}


def _num(v: Any, lo: float, hi: float) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if lo <= f <= hi else None


def clean_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a stored/submitted dict to the settings shape. Bad fields fall back
    to their default; use `validate_settings` to REJECT instead."""
    s = default_settings()
    if raw.get('mode') in MODES:
        s['mode'] = raw['mode']
    if isinstance(raw.get('secret_name'), str):
        s['secret_name'] = raw['secret_name'].strip()
    for key, lo, hi in (('plan_credit_usd', 0, 100_000), ('budget_usd', 0, 100_000)):
        s[key] = _num(raw.get(key), lo, hi)
    pct = _num(raw.get('stop_pct'), 1, 100)
    s['stop_pct'] = pct if pct is not None else 90
    day = _num(raw.get('reset_day'), 1, 31)
    s['reset_day'] = int(day) if day is not None else 1
    if isinstance(raw.get('default_model'), str) and raw['default_model'].strip():
        s['default_model'] = raw['default_model'].strip()
    s['prices'] = _clean_prices(raw.get('prices'))
    return s


def validate_settings(raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """Strict check for the PUT route: (settings, '') or (None, reason)."""
    if not isinstance(raw, dict):
        return None, 'body must be an object'
    if 'mode' in raw and raw['mode'] not in MODES:
        return None, f"mode must be one of {', '.join(MODES)}"
    for key, lo, hi in (('plan_credit_usd', 0, 100_000), ('budget_usd', 0, 100_000),
                        ('stop_pct', 1, 100), ('reset_day', 1, 31)):
        if raw.get(key) is not None and _num(raw[key], lo, hi) is None:
            return None, f'{key} must be a number between {lo} and {hi}'
    if raw.get('reset_day') is not None and float(raw['reset_day']) != int(raw['reset_day']):
        return None, 'reset_day must be a whole day of the month'
    if 'secret_name' in raw and not isinstance(raw['secret_name'], str):
        return None, 'secret_name must be a string'
    if raw.get('prices') and (not isinstance(raw['prices'], dict)
                              or len(_clean_prices(raw['prices'])) != len(raw['prices'])):
        return None, 'prices has an entry that is not {input, output, cache_read, cache_write} numbers'
    return clean_settings(raw), ''


def _clean_tiers(value: Any) -> Optional[List[Dict[str, Any]]]:
    tiers = value if isinstance(value, list) else [value]
    out: List[Dict[str, Any]] = []
    for t in tiers:
        if not isinstance(t, dict):
            return None
        rec: Dict[str, Any] = {}
        for f in _RATE_FIELDS:
            n = _num(t.get(f), 0, 10_000)
            if n is None:
                return None
            rec[f] = n
        limit = t.get('up_to_prompt_tokens')
        bound = _num(limit, 1, 10**9)
        rec['up_to_prompt_tokens'] = int(bound) if bound is not None else None
        out.append(rec)
    if not out:
        return None
    out.sort(key=lambda r: (r['up_to_prompt_tokens'] is None, r['up_to_prompt_tokens'] or 0))
    return out


def _clean_prices(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(raw, dict):
        for model, tiers in raw.items():
            cleaned = _clean_tiers(tiers)
            if isinstance(model, str) and model and cleaned:
                out[model] = cleaned
    return out


def load_settings() -> Dict[str, Any]:
    with _lock:
        return clean_settings(_read_json(settings_path()))


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        s = clean_settings(settings)
        _data_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(settings_path(), s, indent=2)
        return s


# ── price lookup ─────────────────────────────────────────────────────────────

def price_for(model: str, prompt_tokens: int,
              overrides: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, float]]:
    """The rate card for `model` at this prompt size, or None = price unknown."""
    ov: Dict[str, Any] = overrides if overrides is not None else (load_settings().get('prices') or {})
    tiers = ov.get(model) or PRICE_TABLE.get(model)
    if not tiers:
        return None
    for t in tiers:
        lim = t.get('up_to_prompt_tokens')
        if lim is None or prompt_tokens <= lim:
            return {f: float(t[f]) for f in _RATE_FIELDS}
    return None


def prices_overridden(model: str, settings: Optional[Dict[str, Any]] = None) -> bool:
    s = settings if settings is not None else load_settings()
    return model in (s.get('prices') or {})


def cost_usd(model: str, usage: Dict[str, int],
             overrides: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Dollar cost of one call, or None when the model's price is unknown."""
    inp = int(usage.get('input_tokens', 0))
    out = int(usage.get('output_tokens', 0))
    cr = int(usage.get('cache_read_input_tokens', 0))
    cw = int(usage.get('cache_creation_input_tokens', 0))
    rate = price_for(model, inp + cr + cw, overrides)
    if rate is None:
        return None
    return (inp * rate['input'] + out * rate['output']
            + cr * rate['cache_read'] + cw * rate['cache_write']) / 1_000_000


def is_priced(model: str, settings: Optional[Dict[str, Any]] = None) -> bool:
    ov = (settings if settings is not None else load_settings()).get('prices') or {}
    return price_for(model, 0, ov) is not None


# ── cycle math ───────────────────────────────────────────────────────────────

def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def cycle_bounds(now: datetime, reset_day: int) -> Tuple[date, date]:
    """(start, next_reset) of the billing cycle containing `now` (UTC dates).

    A reset day past the end of a short month lands on that month's last day
    (day 31 in February -> Feb 28/29)."""
    reset_day = max(1, min(31, int(reset_day)))
    today = now.astimezone(timezone.utc).date()
    this = _clamped(today.year, today.month, reset_day)
    py, pm = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    ny, nm = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
    if today >= this:
        return this, _clamped(ny, nm, reset_day)
    return _clamped(py, pm, reset_day), this


# ── ledger ───────────────────────────────────────────────────────────────────

def _empty_cycle() -> Dict[str, Any]:
    return {'entries': [], 'spent_usd': 0.0, 'estimated_usd': 0.0, 'calls': 0,
            'estimated_calls': 0}


def _load_ledger() -> Dict[str, Any]:
    led = _read_json(ledger_path())
    if not isinstance(led.get('cycles'), dict):
        led['cycles'] = {}
    return led


def _save_ledger(led: Dict[str, Any]) -> None:
    _data_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ledger_path(), led, indent=1)


def _prune(led: Dict[str, Any], start: date, reset_day: int) -> None:
    """Keep only the current and the previous cycle."""
    prev_start, _ = cycle_bounds(datetime.combine(start - timedelta(days=1),
                                                  datetime.min.time(), timezone.utc), reset_day)
    keep = {start.isoformat(), prev_start.isoformat()}
    for k in list(led['cycles']):
        if k not in keep:
            del led['cycles'][k]


def _cycle(led: Dict[str, Any], start: date) -> Dict[str, Any]:
    cyc = led['cycles'].setdefault(start.isoformat(), _empty_cycle())
    for k, v in _empty_cycle().items():
        cyc.setdefault(k, v)
    return cyc


def record(*, caller: str, model: str, usage: Dict[str, int], cost: Optional[float],
           estimated: bool, settings: Optional[Dict[str, Any]] = None) -> None:
    """Append one call to the current cycle. `estimated` marks token counts that
    came from chars/4 (measure mode) rather than the API's `usage`; those add to
    `estimated_usd` and never to `spent_usd`, so measuring cannot trip the gate."""
    s = settings or load_settings()
    now = _now()
    start, _end = cycle_bounds(now, s['reset_day'])
    entry = {'ts': _iso(now), 'caller': caller, 'model': model,
             'input_tokens': int(usage.get('input_tokens', 0)),
             'output_tokens': int(usage.get('output_tokens', 0)),
             'cache_read_input_tokens': int(usage.get('cache_read_input_tokens', 0)),
             'cache_creation_input_tokens': int(usage.get('cache_creation_input_tokens', 0)),
             'cost_usd': cost, 'estimated': bool(estimated)}
    notice = ''
    with _lock:
        led = _load_ledger()
        cyc = _cycle(led, start)
        cyc['entries'].append(entry)
        if len(cyc['entries']) > ENTRIES_PER_CYCLE_CAP:
            del cyc['entries'][:len(cyc['entries']) - ENTRIES_PER_CYCLE_CAP]
            key = start.isoformat()
            if key not in _cap_logged:
                _cap_logged.add(key)
                _log(f"[api-credits] ledger entries capped at {ENTRIES_PER_CYCLE_CAP} for "
                     f"cycle {key}; oldest dropped, totals are unaffected", flush=True)
        if estimated:
            cyc['estimated_calls'] += 1
            cyc['estimated_usd'] = round(cyc['estimated_usd'] + (cost or 0.0), 8)
        else:
            cyc['calls'] += 1
            cyc['spent_usd'] = round(cyc['spent_usd'] + (cost or 0.0), 8)
        _prune(led, start, s['reset_day'])
        if not estimated:
            notice = _crossing_notice(led, s, start, cyc)
        _save_ledger(led)
    if notice:
        _notify('API credits: stop point reached', notice)


def _crossing_notice(led: Dict[str, Any], s: Dict[str, Any], start: date,
                     cyc: Dict[str, Any]) -> str:
    """If this cycle just reached its stop point, claim the one notice for it.
    Caller holds the lock and saves the ledger."""
    stop = _stop_usd(s)
    if stop is None or cyc['spent_usd'] < stop or led.get('notified_cycle') == start.isoformat():
        return ''
    led['notified_cycle'] = start.isoformat()
    return (f"Clayrune has used ${cyc['spent_usd']:.2f} of its ${s['budget_usd']:.2f} API-credit "
            f"budget (stop at {s['stop_pct']:g}%). Background calls are back on your "
            f"subscription until the cycle resets.")


def _stop_usd(s: Dict[str, Any]) -> Optional[float]:
    if s.get('budget_usd') is None or s['budget_usd'] <= 0:
        return None
    return s['budget_usd'] * s['stop_pct'] / 100.0


def spent_this_cycle(settings: Optional[Dict[str, Any]] = None) -> float:
    s = settings or load_settings()
    start, _ = cycle_bounds(_now(), s['reset_day'])
    with _lock:
        cyc = _load_ledger()['cycles'].get(start.isoformat()) or {}
    return float(cyc.get('spent_usd', 0.0))


# ── halt state (credit-low / bad key) ────────────────────────────────────────

def halt(reason: str, kind: str, settings: Optional[Dict[str, Any]] = None) -> None:
    """Flip to stopped after a credit-low, spend-limit or 401. Sticky until the cycle changes,
    the user saves settings, or a key test succeeds. One notice per cycle."""
    s = settings or load_settings()
    start, _ = cycle_bounds(_now(), s['reset_day'])
    notice = ''
    with _lock:
        led = _load_ledger()
        led['halt'] = {'reason': reason, 'kind': kind, 'ts': _iso(_now()),
                       'cycle': start.isoformat()}
        led['last_error'] = reason
        if led.get('notified_cycle') != start.isoformat():
            led['notified_cycle'] = start.isoformat()
            notice = f'Background calls are back on your subscription. {reason}'
        _save_ledger(led)
    if notice:
        _notify('API credits stopped', notice)


def clear_halt() -> None:
    with _lock:
        led = _load_ledger()
        if led.pop('halt', None) is not None:
            _save_ledger(led)


def note_error(message: str) -> None:
    """Remember the last non-halting failure for the status card."""
    with _lock:
        led = _load_ledger()
        led['last_error'] = message
        led['last_error_ts'] = _iso(_now())
        _save_ledger(led)


def _active_halt(led: Dict[str, Any], start: date) -> Optional[Dict[str, Any]]:
    h = led.get('halt')
    if isinstance(h, dict) and h.get('cycle') == start.isoformat():
        return h
    return None


def _notify(title: str, body: str) -> None:
    """Existing push/notification path, never email. Best-effort."""
    try:
        if _notifier is not None:
            _notifier(title, body)
            return
        from mc.blueprints import push_mobile as _push
        _push._notify_push(title, body, kind='agent')
    except Exception as e:
        _log(f"[api-credits] notice failed: {e}", flush=True)


# ── the gate ─────────────────────────────────────────────────────────────────

def can_spend(model: str, settings: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """May a call to `model` be offloaded right now? (ok, reason-if-not)."""
    s = settings or load_settings()
    if s['mode'] != 'on':
        return False, 'mode_not_on'
    if not s['secret_name']:
        return False, 'no_key_selected'
    if s['budget_usd'] is None or s['budget_usd'] <= 0:
        return False, 'no_budget'
    if not is_priced(model, s):
        return False, 'price_unknown'
    start, _ = cycle_bounds(_now(), s['reset_day'])
    with _lock:
        led = _load_ledger()
    if _active_halt(led, start):
        return False, 'halted'
    cyc = led['cycles'].get(start.isoformat()) or {}
    stop = _stop_usd(s)
    if stop is not None and float(cyc.get('spent_usd', 0.0)) >= stop:
        return False, 'stop_point_reached'
    return True, ''


def status() -> Dict[str, Any]:
    """Everything the card and the usage popup show. No secret values: only the
    secret's NAME is ever in settings."""
    s = load_settings()
    now = _now()
    start, end = cycle_bounds(now, s['reset_day'])
    with _lock:
        led = _load_ledger()
    cyc = led['cycles'].get(start.isoformat()) or _empty_cycle()
    spent = float(cyc.get('spent_usd', 0.0))
    stop = _stop_usd(s)
    h = _active_halt(led, start)
    if s['mode'] == 'off':
        state, reason = 'off', ''
    elif s['mode'] == 'measure':
        state, reason = 'measuring', 'estimated, not billed'
    elif h:
        state, reason = 'stopped', h['reason']
    elif stop is not None and spent >= stop:
        state, reason = 'stopped', 'stop point reached'
    elif not s['secret_name'] or not s['budget_usd']:
        state, reason = 'needs_setup', 'pick an API key and set a budget'
    else:
        state, reason = 'active', ''
    return {
        'settings': s,
        'state': state, 'reason': reason,
        'cycle': {'start': start.isoformat(), 'resets_on': end.isoformat(),
                  'spent_usd': round(spent, 6),
                  'estimated_usd': round(float(cyc.get('estimated_usd', 0.0)), 6),
                  'calls': int(cyc.get('calls', 0)),
                  'estimated_calls': int(cyc.get('estimated_calls', 0)),
                  'budget_usd': s['budget_usd'],
                  'stop_usd': None if stop is None else round(stop, 6),
                  'stop_pct': s['stop_pct']},
        'last_error': led.get('last_error', ''),
        'cost_basis': 'estimate: tokens x price table'
                      + (' (with your price overrides)' if s['prices'] else ''),
        'known_models': sorted(set(PRICE_TABLE) | set(s['prices'])),
    }
