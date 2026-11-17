"""Delivery refusals that clear on their own (MC-1186 / MB6).

A reply refused for one of these reasons goes back to the store's retryable
state under its existing backoff instead of becoming a permanent hold that also
freezes the chat's later replies. Every other hold reason is unchanged, and
releasing a held reply stays passcode-gated: this only decides retry-or-hold.

``rollover_required`` is deliberately NOT here. Mail delivery may not roll a chat
over, so the chat has to be opened once by a person; the hold text says so.
"""
from __future__ import annotations

# provider_quota_held / provider_recovery_held: raised by agent_mail_target.validate.
# spawner_busy: the spawner fallback (MC-1177) found the dispatching chat mid-turn.
RETRYABLE_HOLDS = frozenset({'provider_quota_held', 'provider_recovery_held', 'spawner_busy'})


def is_retryable_hold(reason: object) -> bool:
    return isinstance(reason, str) and reason in RETRYABLE_HOLDS
