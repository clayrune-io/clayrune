"""Opt-in engine fallback — MC-961 (backlog de998c45), Ron's design 2026-09-26.

Standing position: allowance is the ONLY reason an installed, signed-in agent
may be refused (VENDOR_AGNOSTIC_PROGRAM.md §4, `mc/allowance_state.py`). This
module does not weaken that — it only lets the user OPT IN to a second vendor
absorbing that exact refusal instead of it ending the run, and it is loud
about doing so everywhere. Default is empty/off; nothing here ever fires
without a user-authored `engine_fallback_order`.

Trigger surface is intentionally narrow: the ONE choke point every dispatch
path shares (`_dispatch_agent_internal` in `mc/blueprints/agent_routes.py`)
already raises `ValueError` for allowance exhaustion
(`_allowance_refusal`/`mc.allowance_state`) and for a model MC has already
watched fail on quota (`_model_quota_blocked`) — both are "this vendor/model
cannot run right now", never a user typo or a config error. Hooking in
exactly there means chat, the scheduler, hivemind and every workflow agent
step (466c125's own comment: "one refusal covers all four surfaces") get the
fallback walk for free, with no per-surface copy to keep in sync.

Read-only with respect to `mc.allowance_state` — MC-966 is reading that
module concurrently for a usage bar; this file never calls its private
state or adds new public functions there, only the existing `is_exhausted`.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from mc import allowance_state as _allowance_state
from mc.core import _log

CONFIG_KEY = 'engine_fallback_order'

# Read by the frontend deep-link (Settings > Agent > Advanced > Engine
# fallback) and carried on both the chat error payload and the run-history /
# notification record, so every surface points at the same place.
SETTINGS_DEEP_LINK = 'agent.engine_fallback_order'


def get_order(config: Optional[dict]) -> List[Dict[str, str]]:
    """The configured fallback order as a clean list of {provider, model}
    dicts. `model` may be '' (use the fallback vendor's own default).
    Malformed entries (not a dict, no provider) are dropped rather than
    raising — a hand-edited config.json must not take dispatch down."""
    raw = (config or {}).get(CONFIG_KEY) or []
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        provider = (entry.get('provider') or '').strip().lower()
        if not provider:
            continue
        out.append({'provider': provider, 'model': (entry.get('model') or '').strip()})
    return out


def resolve_fallback(blocked_provider: str, config: Optional[dict],
                     is_available: Callable[[str], bool]) -> Optional[Dict[str, str]]:
    """First entry in the configured order that can actually run right now,
    or None (empty/unset order, or every entry unusable) — the caller keeps
    failing the run in that case, exactly as before this module existed.

    Skips: the blocked vendor itself, any vendor `allowance_state` already
    has recorded as exhausted, and any vendor `is_available` reports isn't
    installed/signed in (the caller passes a probe built from
    `mc.agent_runtime.get_runtime(...).health_check()` — this module never
    imports `agent_runtime` itself, to stay a leaf dependency of the
    blueprint rather than the other way around).
    """
    blocked_provider = (blocked_provider or '').strip().lower()
    for entry in get_order(config):
        provider = entry['provider']
        if not provider or provider == blocked_provider:
            continue
        if _allowance_state.is_exhausted(provider):
            continue
        try:
            if not is_available(provider):
                continue
        except Exception as e:
            _log(f"[engine_fallback] availability check for {provider} failed: {e}",
                 flush=True)
            continue
        return dict(entry)
    return None


def settings_pointer_text() -> str:
    """Appended to a blocked-run refusal when NO fallback resolved (empty
    list, or every configured entry unusable) — names the exact place a
    human can fix this, per MC-961 item 4. Kept as one line, reused by chat,
    schedule and workflow refusals alike so the wording never drifts."""
    return "set up a fallback: Settings › Agent › Advanced › Engine fallback"


class EngineFallbackBlocked(ValueError):
    """Raised instead of a bare `ValueError` wherever `blocked_payload` would
    otherwise be flattened to just its `error` string. Behaves exactly like
    that plain ValueError for every existing caller (`str(e)` is the same
    pointer message: scheduler/workflow `last_error` fields, the generic
    `except ValueError` in older routes) — only a caller that knows this
    subclass exists (the chat send/dispatch routes) reads `.payload` to
    render the actionable card MC-961 item 4 asks for instead of plain text.
    """
    def __init__(self, payload: Dict[str, object]):
        super().__init__(payload['error'])
        self.payload = payload


def blocked_payload(refusal_message: str) -> Dict[str, object]:
    """Structured shape for a blocked-run response/record when no fallback
    applied — a plain string refusal plus the fields a UI needs to render an
    actionable pointer instead of re-parsing prose. Chat's HTTP error body,
    the scheduler's run-history entry and the workflow run's failure record
    all carry this same shape (MC-961 item 4: "the same pointer")."""
    return {
        'error': f"{refusal_message.rstrip().rstrip('.')}. To keep working, {settings_pointer_text()}.",
        'allowance_blocked': True,
        'refusal_message': refusal_message,
        'settings_deep_link': SETTINGS_DEEP_LINK,
    }


def swap_record(*, from_provider: str, to_provider: str, reason: str,
                reset_display: str = '') -> Dict[str, str]:
    """The one shape a swap is recorded in everywhere: run history, the
    agent_log entry, the chat-visible line and the notification body. A
    single builder so all four surfaces say the same thing (MC-961 item 3:
    "no silent path may exist")."""
    return {
        'from': from_provider,
        'to': to_provider,
        'reason': reason,
        'vendor_reset': reset_display or '',
    }


def swap_chat_line(record: Dict[str, str]) -> str:
    reset = f" ({record['vendor_reset']})" if record.get('vendor_reset') else ''
    return (f"[Engine fallback: {record['from']} → {record['to']} "
            f"— {record['reason']}{reset}]")
