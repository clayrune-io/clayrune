"""Plain-words reason for a report that was parked without a recorded handoff.

Inbox rows parked before MC-1106 began recording handoff boundaries (or parked
before any handoff was attempted) can never be receipt-checked: the check has
no boundary to read, so the stored `last_error` is the only account of what
happened. That text is internal ("parent stdin write acknowledgment is
unknown"); this maps the known closed set to a sentence a person can read, and
falls back to a generic one. Pure function, no I/O, never raises.
"""
from __future__ import annotations

# Prefix match, first hit wins. Prefixes are the `last_error` texts raised in
# agent_routes._process_inbox / delegation_wake.parent_status_block_message.
_REASONS = (
    ('parent status stopped cannot accept', 'this chat was stopped when the agent finished.'),
    ('parent status error cannot accept', 'this chat had ended in an error when the agent finished.'),
    ('parent status ', 'this chat could not accept a report when the agent finished.'),
    ('parent identity/privacy state changed',
     'this chat was switched or reset while the report was being handed over.'),
    ('parent has no durable identity record',
     'this chat had no saved record to hand the report to.'),
    ('parent durable outcome is ambiguous',
     'this chat was not in a settled state, so it is not known whether the report arrived.'),
    ('parent stdin write acknowledgment is unknown',
     'the hand-over was sent but never confirmed, so it is not known whether it arrived.'),
    ('inbox claim expired', 'the hand-over timed out before it started.'),
    ('parent revival outcome unknown', 'this chat was not running and could not be safely restarted.'),
)
_FALLBACK = 'it was sent before report tracking existed, so it cannot be checked.'


def unchecked_reason(last_error: str) -> str:
    text = str(last_error or '')
    for prefix, sentence in _REASONS:
        if text.startswith(prefix):
            return sentence
    return _FALLBACK
