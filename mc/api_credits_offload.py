"""Glue between `ClaudeRuntime.oneshot()` and the API-credits modules
(docs/API_CREDITS_BUILD_SPEC.md). Kept out of agent_runtime.py so the hook there
is two calls; the decisions live here.

Two entry points for the hook:

- `try_offload()`    before the subscription path. Returns an OneshotResult only
                     when mode is `on`, the gate allows it and the Messages API
                     call succeeded; otherwise None and the caller runs the
                     existing path unchanged. EVERY failure is None: a caller
                     must never see a failure the old path would not have had.
- `note_subscription_call()`  after a successful subscription call; in
                     `measure` mode records an ESTIMATED cost (chars/4).

Never raises.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from mc import api_credits_budget as budget
from mc import api_credits_transport as transport
from mc.core import _log

CONSUMER = 'api-credits-offload'
TEST_MODEL = 'claude-haiku-5-5'
_KEY_TTL_S = 60

# The CLI takes aliases; the API needs ids. A fixed map of the aliases the
# background callers pass (memory.py / distiller.py `_CLAUDE_MODEL_ALIASES`).
# It maps NAMES only: prices still come from the price table, so an alias whose
# target is unpriced still falls back.
MODEL_ALIASES = {'haiku': 'claude-haiku-5-5', 'sonnet': 'claude-sonnet-5-5',
                 'opus': 'claude-opus-5-5'}

_key_lock = threading.Lock()
_key_cache: Dict[str, Tuple[float, str]] = {}


def resolve_model(model: str, settings: Dict[str, Any]) -> str:
    m = (model or '').strip()
    if not m:
        return settings.get('default_model') or budget.DEFAULT_MODEL
    return MODEL_ALIASES.get(m.lower(), m)


def forget_key() -> None:
    with _key_lock:
        _key_cache.clear()


def _read_key(name: str) -> Tuple[Optional[str], str]:
    """(key, '') or (None, why). Goes through the vault's audited read; the value
    lives only in this return and a 60s in-process cache, never in a file or in
    os.environ."""
    from mc import secrets_store as vault
    try:
        if vault.is_locked():
            return None, 'vault is locked'
    except Exception:
        pass
    with _key_lock:
        hit = _key_cache.get(name)
        if hit and time.monotonic() - hit[0] < _KEY_TTL_S:
            return hit[1], ''
    try:
        value = vault.get_secret_value(name, consumer=CONSUMER, unattended=True)
    except Exception as e:
        return None, f'could not read vault secret: {type(e).__name__}'
    if not isinstance(value, str) or not value.strip():
        return None, 'vault secret is empty'
    with _key_lock:
        _key_cache[name] = (time.monotonic(), value)
    return value, ''


def _caller_label() -> str:
    """Module name of whoever called oneshot(): the first frame outside the
    runtime and the credits modules."""
    skip = ('mc.agent_runtime', 'mc.api_credits_offload')
    try:
        f = sys._getframe(1)
        while f is not None:
            name = str(f.f_globals.get('__name__', ''))
            if name not in skip:
                return name or 'unknown'
            f = f.f_back
    except Exception:
        pass
    return 'unknown'


def _estimate_tokens(text: str) -> int:
    return max(0, len(text) // 4)


def try_offload(*, user_text: str, model: str, timeout: float,
                post: Optional[Callable[..., Any]] = None):
    """See module docstring. `post` is the transport's injectable HTTP call."""
    try:
        s = budget.load_settings()
        if s['mode'] != 'on':
            return None
        resolved = resolve_model(model, s)
        ok, _why = budget.can_spend(resolved, s)
        if not ok:
            return None
        key, why = _read_key(s['secret_name'])
        if key is None:
            budget.note_error(why)
            return None
        res = transport.send(api_key=key, model=resolved, user_text=user_text,
                             timeout=timeout, post=post)
        cost: Optional[float] = None
        if res.usage and (res.ok or res.error_kind == transport.TRUNCATED):
            cost = budget.cost_usd(resolved, res.usage, s.get('prices'))
            budget.record(caller=_caller_label(), model=resolved, usage=res.usage,
                          cost=cost, estimated=False, settings=s)
        if res.ok:
            from mc.agent_runtime import OneshotResult
            return OneshotResult(text=res.text.strip(), usage=res.usage, cost_usd=cost)
        reason = _error_text(res)
        if res.error_kind in transport.HALTING_KINDS:
            forget_key()
            budget.halt(reason, res.error_kind, s)
        else:
            budget.note_error(reason)
        _log(f"[api-credits] offload fell back to subscription: {reason}", flush=True)
        return None
    except Exception as e:
        _log(f"[api-credits] offload failed, using subscription: {type(e).__name__}: {e}",
             flush=True)
        return None


def _error_text(res: 'transport.TransportResult') -> str:
    if res.error_kind == transport.CREDIT_LOW:
        return 'Credit balance too low'
    if res.error_kind == transport.SPEND_LIMIT:
        return 'API spend limit reached: ' + (res.error_message or 'usage limit')
    if res.error_kind == transport.AUTH:
        return 'API key rejected (401)'
    return res.error_message or res.error_kind


def note_subscription_call(*, user_text: str, output_text: str, model: str) -> None:
    """Measure mode: record what this call would have cost. No-op otherwise."""
    try:
        s = budget.load_settings()
        if s['mode'] != 'measure':
            return
        resolved = resolve_model(model, s)
        usage = {'input_tokens': _estimate_tokens(user_text),
                 'output_tokens': _estimate_tokens(output_text),
                 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}
        budget.record(caller=_caller_label(), model=resolved, usage=usage,
                      cost=budget.cost_usd(resolved, usage, s.get('prices')),
                      estimated=True, settings=s)
    except Exception as e:
        _log(f"[api-credits] measure record failed: {type(e).__name__}: {e}", flush=True)


def run_key_test(*, post: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """One human-clicked call: haiku, max_tokens 1. Returns {result, message} with
    result in ok | credit_too_low | spend_limit | invalid_key | error | no_key. The tiny spend
    is recorded; a pass clears a sticky stop."""
    s = budget.load_settings()
    if not s['secret_name']:
        return {'result': 'no_key', 'message': 'Pick or paste an API key first.'}
    key, why = _read_key(s['secret_name'])
    if key is None:
        return {'result': 'error', 'message': why}
    res = transport.send(api_key=key, model=TEST_MODEL, user_text='ping',
                         max_tokens=1, timeout=30, post=post)
    # stop_reason max_tokens is the expected outcome of max_tokens=1.
    passed = res.ok or res.error_kind == transport.TRUNCATED
    if res.usage and passed:
        budget.record(caller='key-test', model=TEST_MODEL, usage=res.usage,
                      cost=budget.cost_usd(TEST_MODEL, res.usage, s.get('prices')),
                      estimated=False, settings=s)
    if passed:
        budget.clear_halt()
        return {'result': 'ok', 'message': 'Key works.'}
    forget_key()
    if res.error_kind == transport.CREDIT_LOW:
        return {'result': 'credit_too_low',
                'message': 'The key is valid but its organization has no credit '
                           '("credit balance is too low"). Link your organization at '
                           'claude.ai Settings > Billing > API credits.'}
    if res.error_kind == transport.SPEND_LIMIT:
        return {'result': 'spend_limit',
                'message': 'The key is valid but its organization has reached the '
                           'spend limit set in the Anthropic Console (' +
                           (res.error_message or 'usage limit') + ').'}
    if res.error_kind == transport.AUTH:
        return {'result': 'invalid_key', 'message': 'Anthropic rejected this key (401/403).'}
    return {'result': 'error', 'message': res.error_message or res.error_kind}
