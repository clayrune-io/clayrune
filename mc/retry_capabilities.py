"""Audited enforcement capabilities, also displayed verbatim in Settings.

UNSUPPORTED describes this integration, not proof that a vendor has no knob.
An incomplete native audit is explicitly marked unverified.
"""
from __future__ import annotations

from typing import Any, Dict, List

_ROWS = (
    ('claude', 'NATIVE', 'All three durations; requires Claude Code 2.1.305+.'),
    ('qwen', 'PARTIAL', 'Observed capacity-wait cap only. Usage classification and retry cadence unsupported.'),
    ('codex', 'UNSUPPORTED', 'Audited TOML: retry counts and stream idle timeout are not wait budgets.'),
    ('gemini', 'UNSUPPORTED', 'Audited settings: attempt count and fetch-error retry are not wait budgets.'),
    ('opencode', 'UNSUPPORTED', 'Request/header/chunk timeouts are not wait budgets; retry signal unverified.'),
    ('goose', 'UNSUPPORTED', 'Native capability unverified; no wait signal verified in this integration.'),
    ('aider', 'UNSUPPORTED', 'API-call timeout is not a wait budget; retry signal unverified.'),
    ('kiro', 'UNSUPPORTED', 'Response/idle timeouts are not wait budgets; retry signal unverified.'),
)


def settings_rows() -> List[Dict[str, Any]]:
    return [{'engine': engine, 'status': status, 'detail': detail}
            for engine, status, detail in _ROWS]
