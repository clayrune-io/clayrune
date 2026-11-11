"""Bounded read-only status metadata for the agent-mail panel (MC-1166 unit 7e-1).

Everything here is a count, a boolean, an integer timestamp or a closed code.
Never a body, mailbox code, secret, signed credential, flow id, free-text
``last_error`` or remote exception text. Each helper degrades to a safe default
instead of raising: a status read must not become a way to wedge the kill switch
(``Enrollment.status`` must still answer when old evidence is corrupt).

The owner mailbox is returned only as a masked hint (``owner_hint``): the 7b
read status deliberately carried no owner mailbox, and the full address stays
behind the passcode-gated ``review`` call.
"""
from __future__ import annotations

import re
from typing import Any

from mc.core import _log

_CODE = re.compile(r'[a-z0-9_]{1,48}')
_PROOF_STATES = frozenset({'armed', 'proved', 'invalidated', 'consumed'})
_ACTIVATION_STATES = frozenset({'pending', 'acked'})
_STEP_STATES = frozenset({'ok', 'pending', 'held', 'skipped', 'failed'})
MAX_REASONS = 8
MAX_STEPS = 16


def owner_hint(owner: Any) -> str:
    """'owner@example.test' -> 'o***@example.test'. Empty for anything else."""
    if not isinstance(owner, str) or owner.count('@') != 1:
        return ''
    local, domain = owner.split('@')
    if not local or not domain or len(owner) > 254:
        return ''
    return f'{local[0]}***@{domain}'


def _has(db: Any, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def proof_view(db: Any, install: str, generation: int) -> dict[str, Any]:
    """Local proof/activation row state for the current install+generation."""
    out: dict[str, Any] = {'state': 'none', 'expires_ms': 0, 'activation': 'none'}
    try:
        if _has(db, 'mail_delivery_proof'):
            row = db.execute('SELECT state,expires_ms FROM mail_delivery_proof WHERE install=? AND generation=?',
                             (install, generation)).fetchone()
            if row is not None and row['state'] in _PROOF_STATES:
                out['state'], out['expires_ms'] = row['state'], _int(row['expires_ms'])
        if _has(db, 'mail_delivery_activation'):
            row = db.execute('SELECT state FROM mail_delivery_activation WHERE install=? AND generation=? '
                             'ORDER BY epoch DESC LIMIT 1', (install, generation)).fetchone()
            if row is not None and row['state'] in _ACTIVATION_STATES:
                out['activation'] = row['state']
    except Exception as exc:
        _log(f'[agent-mail-status] proof view failed: {type(exc).__name__}', flush=True)
    return out


def mail_counts(db: Any) -> dict[str, Any]:
    """Held / uncertain work, by count and closed reason code."""
    out: dict[str, Any] = {'blocked': 0, 'uncertain': 0, 'retryable': 0, 'outbound_uncertain': 0,
                           'hold_reasons': {}}
    try:
        if _has(db, 'events'):
            for row in db.execute("SELECT state,COUNT(*) AS n FROM events "
                                  "WHERE state IN ('blocked','uncertain','retryable') GROUP BY state"):
                out[row['state']] = _int(row['n'])
            reasons: dict[str, int] = {}
            for row in db.execute("SELECT reason,COUNT(*) AS n FROM events WHERE state='blocked' "
                                  "GROUP BY reason ORDER BY n DESC LIMIT ?", (MAX_REASONS,)):
                code = row['reason']
                if isinstance(code, str) and _CODE.fullmatch(code):
                    reasons[code] = _int(row['n'])
            out['hold_reasons'] = reasons
        if _has(db, 'mail_outbound_intents'):
            out['outbound_uncertain'] = _int(db.execute(
                "SELECT COUNT(*) FROM mail_outbound_intents WHERE state='uncertain'").fetchone()[0])
    except Exception as exc:
        _log(f'[agent-mail-status] counts failed: {type(exc).__name__}', flush=True)
    return out


def prerequisite_view(prerequisites: Any) -> dict[str, bool]:
    """Booleans only; an unreadable probe reads as not ready."""
    return {key: getattr(prerequisites, key, False) is True
            for key in ('tunnel', 'claude_extraction', 'machine_custody')}


def recovery_snapshot(supervisor: Any) -> dict[str, Any]:
    """Allow-listed projection of ``Supervisor.status()`` (counts and closed codes).

    ``None`` (no supervisor installed in this process) is reported as
    ``not_running`` so the panel can say recovery is not being driven.
    """
    if supervisor is None:
        return {'state': 'not_running'}
    try:
        raw = supervisor.status()
    except Exception as exc:
        _log(f'[agent-mail-status] supervisor status failed: {type(exc).__name__}', flush=True)
        return {'state': 'unknown'}
    from mc.agent_mail_recovery import REASONS, STEPS
    steps = []
    for step in raw.get('steps', [])[:MAX_STEPS]:
        if not isinstance(step, dict) or (step.get('name') not in STEPS and step.get('name') != 'supervisor'):
            continue
        reason = step.get('reason')
        steps.append({'name': step['name'],
                      'state': step.get('state') if step.get('state') in _STEP_STATES else 'failed',
                      'reason': reason if isinstance(reason, str) and reason in REASONS else 'step_failed',
                      'count': _int(step.get('count'))})
    reason = raw.get('reason')
    holds = {k: _int(v) for k, v in (raw.get('holds') or {}).items()
             if isinstance(k, str) and _CODE.fullmatch(k)}
    state = raw.get('state')
    return {'state': state if isinstance(state, str) and _CODE.fullmatch(state) else 'unknown',
            'enabled': raw.get('enabled') is True, 'thread_alive': raw.get('thread_alive') is True,
            'last_attempt': raw.get('last_attempt') if isinstance(raw.get('last_attempt'), (int, float)) else None,
            'last_success': raw.get('last_success') if isinstance(raw.get('last_success'), (int, float)) else None,
            'consecutive_failures': _int(raw.get('consecutive_failures')),
            'reason': reason if isinstance(reason, str) and reason in REASONS else '',
            'steps': steps, 'holds': holds}
