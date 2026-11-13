"""Read-only pull status adapter (MC-1166 unit 25), plus legacy panel helpers.

Except the dashboard's explicit identity fields, outputs are counts, booleans,
timestamps or closed codes.
Never a body, mailbox code, secret, signed credential, flow id, free-text
``last_error`` or remote exception text. Each helper degrades to a safe default
instead of raising: a status read must not become a way to wedge the kill switch
(``Enrollment.status`` must still answer when old evidence is corrupt).

``pull_status`` implements the local projection in pull contract section 10;
its owner/address fields are for the authenticated dashboard, never agent
context. Remote queue values are observations, not authority or live counters.
It never composes a transport, starts a worker, bootstraps or advances a flow.
The GET cutover belongs to a separate route-wiring unit; this adapter is unwired.

Legacy prerequisite/proof helpers remain solely for existing Enrollment callers
until their separately scoped retirement. Their tunnel/proof readiness fields
are NEVER used in the pull projection. Legacy owner_hint remains masked.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from itertools import islice
from typing import Any

from mc.core import _log

_CODE = re.compile(r'[a-z0-9_]{1,48}')
_PROOF_STATES = frozenset({'armed', 'proved', 'invalidated', 'consumed'})
_ACTIVATION_STATES = frozenset({'pending', 'acked'})
_STEP_STATES = frozenset({'ok', 'pending', 'held', 'skipped', 'failed'})
MAX_REASONS = 8
MAX_STEPS = 16
MAX_FAILURES = 20
MAX_NUMBER = 2**53 - 1


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
    """Legacy Enrollment compatibility only; never a pull prerequisite."""
    return {key: getattr(prerequisites, key, False) is True
            for key in ('tunnel', 'claude_extraction', 'machine_custody')}


def _number(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_NUMBER else None


def _seconds(value: Any) -> int | float | None:
    return value if type(value) in (int, float) and 0 <= value <= MAX_NUMBER and math.isfinite(value) else None


def _reason(value: Any, fallback: str = '') -> str:
    # Use the producer's finite vocabulary, never arbitrary regex-shaped text.
    from mc.agent_mail_supervisor import _CODES
    from mc.agent_mail_signup import LOCAL_REASONS
    return value if isinstance(value, str) and value in (_CODES | LOCAL_REASONS | {'ready'}) else fallback


def _failure(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get('source') not in (
            'clock', 'recovery', 'thread', 'client', 'shutdown', 'client_status', 'ownership_status'):
        return None
    return {'source': value['source'], 'reason': _reason(value.get('reason'), 'step_failed'),
            'at': _seconds(value.get('at'))}


def _client(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {'state': 'unknown', 'running': None}
    states = {'not_configured', 'connecting', 'scanning', 'connected', 'reconnecting',
              'held', 'stopping', 'stopped'}
    phase = value.get('state')
    return {'state': phase if isinstance(phase, str) and phase in states else 'unknown',
            'running': value.get('running') if type(value.get('running')) is bool else None,
            'reason': _reason(value.get('reason')), 'last_notice': _reason(value.get('last_notice')),
            **{k: _number(value.get(k)) for k in ('epoch', 'last_activity_ms', 'failures', 'next_attempt_ms')}}


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
    if not isinstance(raw, dict):
        return {'state': 'unknown'}
    from mc.agent_mail_recovery import STEPS
    steps = []
    supplied_steps = raw.get('steps')
    for step in (supplied_steps if isinstance(supplied_steps, list) else [])[:MAX_STEPS]:
        if not isinstance(step, dict) or (step.get('name') not in STEPS and step.get('name') != 'supervisor'):
            continue
        reason = step.get('reason')
        phase = step.get('state')
        item = {'name': step['name'],
                'state': phase if isinstance(phase, str) and phase in _STEP_STATES else 'failed',
                'reason': _reason(reason, 'step_failed'),
                'count': (_number(step.get('count')) or 0) if 'count' in step else None}
        details = step.get('details')
        if isinstance(details, dict):
            keys = (('pending_count', 'held_count', 'oldest_age_ms', 'oldest_pending_age_ms',
                     'oldest_held_age_ms') if step['name'] == 'acks' else
                    ('pending_count',) if step['name'] == 'revocations' else ())
            if keys:
                item['details'] = {k: _number(details.get(k)) for k in keys}
        steps.append(item)
    reason = raw.get('reason')
    supplied_holds = raw.get('holds')
    holds = {k: _number(v) or 0 for k, v in islice(
                 (supplied_holds if isinstance(supplied_holds, dict) else {}).items(), MAX_REASONS)
             if isinstance(k, str) and k and _reason(k)}
    state = raw.get('state')
    out = {'state': state if isinstance(state, str) and state in {
                'off', 'starting', 'stopped', 'dead', 'ok', 'degraded', 'stopping'} else 'unknown',
            'enabled': raw.get('enabled') if type(raw.get('enabled')) is bool else None,
            'thread_alive': raw.get('thread_alive') if type(raw.get('thread_alive')) is bool else None,
            'last_attempt': _seconds(raw.get('last_attempt')), 'last_success': _seconds(raw.get('last_success')),
            'consecutive_failures': _number(raw.get('consecutive_failures')),
            'reason': _reason(reason),
            'steps': steps, 'holds': holds}
    # Preserve live-worker evidence and timestamps that the original 7e view lost.
    out.update({'last_tick': _seconds(raw.get('last_tick')),
                'tick_stale': raw.get('tick_stale') if type(raw.get('tick_stale')) is bool else None,
                'client': _client(raw.get('client')), 'last_failure': _failure(raw.get('last_failure')),
                'failure_count': _number(raw.get('failure_count'))})
    failures = raw.get('recent_failures')
    out['recent_failures'] = [f for v in (failures if isinstance(failures, list) else [])[-MAX_FAILURES:]
                              if (f := _failure(v)) is not None]
    event = raw.get('last_client_event')
    out['last_client_event'] = ({**_client(event), 'observed_at': _seconds(event.get('observed_at'))}
                                if isinstance(event, dict) else None)
    return out


def signup_snapshot(signup: Any) -> dict[str, Any]:
    """Optional unit19 read-only status; never call resume/enable/retry here."""
    if signup is None:
        return {'state': 'unavailable'}
    try:
        value = signup.status()
        if not isinstance(value, dict):
            return {'state': 'unknown'}
        phase = value.get('state')
        states = {'not_enrolled', 'preparing', 'start_pending', 'pending', 'verified',
                  'enable_pending', 'completed', 'expired', 'cancelled', 'held', 'active'}
        notice = value.get('verification_state')
        return {'state': phase if isinstance(phase, str) and phase in states else 'unknown',
                'reason': _reason(value.get('reason')), 'expires_ms': _number(value.get('expires_ms')),
                'verification_state': notice if isinstance(notice, str) and notice in {
                    'queued', 'accepted', 'uncertain', 'failed'} else None}
    except Exception as exc:
        _log(f'[agent-mail-status] signup status failed: {type(exc).__name__}', flush=True)
        return {'state': 'unknown'}


def _cached_status(row: Any, account: Any, now: int) -> dict[str, Any]:
    """Only the current verified-response cache; never fetch or accept new trust.

    Signup/activation responses share this table but have no remote queue data.
    Scope and digest are rechecked against corruption; signature verification is
    the writer's responsibility. observed_ms remains visible, never made fresh.
    """
    if row is None:
        return {}
    from mc.agent_mail_service_client import ServiceClient
    try:
        text = row['status_json']
        if (not isinstance(text, str) or len(text) > 32768 or
                _number(row['observed_ms']) is None or row['observed_ms'] > now or
                (row['generation'], row['owner_generation']) !=
                (account['generation'], account['owner_generation']) or
                hashlib.sha256(text.encode('utf-8')).hexdigest() != row['response_digest']):
            return {}
        value = json.loads(text)
        if not isinstance(value, dict) or 'queue_count' not in value:
            return {}
        ServiceClient._status_shape(value, account)
        if (value['owner'] != account['owner_address'] or value['address'] != account['address']
                or type(value['v']) is not int or value['v'] != 1 or value['code'] != 'ok'):
            return {}
        return {**{k: value[k] for k in ('queue_count', 'queue_bytes', 'invalid_message_id_holds',
                                       'oldest_received_ms', 'oldest_expires_ms', 'last_notice_state')},
                'observed_ms': row['observed_ms']}
    except Exception as exc:
        _log(f'[agent-mail-status] cached status unavailable: {type(exc).__name__}', flush=True)
        return {}


def _mailbox(value: Any) -> str:
    from mc.agent_mail_signup import _mailbox as canonical
    try:
        return value if canonical(value) == value else ''
    except (ValueError, TypeError, AttributeError):
        return ''


def pull_status(factory: Any, *, supervisor: Any = None, signup: Any = None) -> dict[str, Any]:
    """Unwired section10 dashboard view. All I/O is local and read-only.

    factory must be the checked MailFactory (or its explicit fake). It supplies
    require_pull_store, seal and clock, never bootstrap/default global lookup.
    signup is an optional existing Signup adapter; only its passive status is
    sampled. Remote queue values stay null without a current-scope status cache;
    local_queue is separately labelled, never substituted for the service queue.
    """
    recovery, signup_view = recovery_snapshot(supervisor), signup_snapshot(signup)
    out: dict[str, Any] = {
        'v': 1, 'state': 'unknown', 'reason': 'status_failed', 'enabled': None,
        'install_id': None, 'generation': None, 'owner_generation': None,
        'owner': '', 'owner_verified': None, 'address': '', 'connection': 'unknown',
        'vault': {'state': 'unknown'}, 'queue_count': None, 'queue_bytes': None,
        'invalid_message_id_holds': None, 'oldest_received_ms': None, 'oldest_expires_ms': None,
        'queue_age_ms': None, 'observed_ms': None, 'notice_state': None,
        'revocation_pending': None, 'revocations_pending': None, 'local_queue': None,
        'flow': {'state': 'unknown'}, 'signup': signup_view, 'recovery': recovery}
    if factory is None:
        return out
    try:
        store = factory.require_pull_store()
        clock = _seconds(factory.clock())
        if clock is None or clock * 1000 > MAX_NUMBER:
            return out
        now = int(clock * 1000)
        out['now_ms'] = now
        with store._read() as db:
            db.execute('BEGIN')
            pending_fences = db.execute("SELECT COUNT(*) FROM mail_pull_fences WHERE state='pending'").fetchone()[0]
            out.update(revocation_pending=bool(pending_fences), revocations_pending=pending_fences)
            accounts = db.execute('SELECT * FROM mail_pull_accounts LIMIT 2').fetchall()
            if not accounts:
                out.update(state='not_enrolled', reason='not_enrolled', enabled=False, owner_verified=False)
                return out
            if len(accounts) != 1:
                out.update(state='held', reason='schema_corrupt', connection='held')
                return out
            account = dict(accounts[0])
            install, generation = account['install_id'], account['generation']
            current = db.execute('SELECT * FROM mail_pull_generations WHERE install_id=? AND generation=?',
                                 (install, generation)).fetchone()
            if (current is None or any(current[k] != account[k] for k in
                    ('owner_generation', 'key_id', 'seal_flow_id'))):
                out.update(state='held', reason='schema_corrupt', connection='held')
                return out
            flow = db.execute('''SELECT state,expires_ms,reason FROM mail_pull_flows
                WHERE install_id=? AND expected_generation=? AND expected_owner_generation=?
                ORDER BY human_authorized_ms DESC,flow_id LIMIT 1''',
                (install, generation, account['owner_generation'])).fetchone()
            fences = db.execute("SELECT COUNT(*) FROM mail_pull_fences WHERE install_id=? AND state='pending'",
                                (install,)).fetchone()[0]
            fenced = db.execute('''SELECT 1 FROM mail_pull_fences WHERE install_id=? AND generation=?
                AND (mode='revoke' OR flow_id=?) LIMIT 1''',
                (install, generation, account['seal_flow_id'])).fetchone() is not None
            conn = db.execute('SELECT * FROM mail_pull_connections WHERE install_id=?', (install,)).fetchone()
            reg = db.execute('SELECT enabled,generation FROM registrations WHERE registration_id=?',
                             (install,)).fetchone()
            cached = _cached_status(db.execute('SELECT * FROM mail_pull_status WHERE install_id=?',
                                              (install,)).fetchone(), account, now)
            local = db.execute('''SELECT COUNT(*) AS n,COALESCE(SUM(body_len),0) AS bytes,
                MIN(received_at) AS oldest,MIN(expires_at) AS expires FROM events
                WHERE registration_id=? AND state NOT IN ('turn_completed','rejected','expired','revoked')''',
                (install,)).fetchone()
        # No transaction spans seal reads or calls to other status producers.
        from mc.agent_mail_enrollment_keys import SealReference
        try:
            vault = _reason(factory.seal().status(SealReference(account['seal_flow_id'], install,
                                                              generation, account['key_id'])), 'status_failed')
            # 'ready' is a seal observation, never a service/transport claim.
        except Exception as exc:
            _log(f'[agent-mail-status] vault status failed: {type(exc).__name__}', flush=True)
            vault = 'status_failed'
        phase = account['state']
        verified = True if phase in {'verified', 'active'} else False if phase in {'pending', 'deleted'} else None
        if (phase != 'deleted' and not _mailbox(account['owner_address']) or
                account['address'] and not _mailbox(account['address'])):
            out.update(state='held', reason='schema_corrupt', connection='held')
            return out
        out.update(state=phase, reason=_reason(account['reason']), enabled=False, install_id=install,
                   generation=generation, owner_generation=account['owner_generation'], owner_verified=verified,
                   owner=account['owner_address'] if verified else '',
                   address=account['address'] if phase != 'deleted' else '',
                   revocation_pending=bool(fences), revocations_pending=fences, vault={'state': vault})
        out.update({k: cached[k] for k in ('queue_count', 'queue_bytes', 'invalid_message_id_holds',
                    'oldest_received_ms', 'oldest_expires_ms', 'observed_ms') if k in cached})
        out['notice_state'] = cached.get('last_notice_state')
        if out['oldest_received_ms'] is not None:
            out['queue_age_ms'] = max(0, now - out['oldest_received_ms'])
        oldest = _seconds(local['oldest'])
        expires = _seconds(local['expires'])
        out['local_queue'] = {'count': local['n'], 'bytes': local['bytes'],
                              'oldest_age_ms': max(0, now - int(oldest * 1000)) if oldest is not None else None,
                              'oldest_expires_ms': int(expires * 1000) if expires is not None else None}
        if flow is not None:
            expired = flow['expires_ms'] <= now and flow['state'] not in {'completed', 'cancelled', 'expired'}
            out['flow'] = {'state': 'expired' if expired else flow['state'], 'expires_ms': flow['expires_ms'],
                           'reason': 'flow_expired' if expired else _reason(flow['reason'])}
            if not expired and flow['state'] == 'enable_pending':
                out.update(state='enable_pending', reason='activation_pending')
        eligible = (phase == 'active' and account['local_enabled'] == 1 and current['state'] == 'current'
                    and reg is not None and reg['enabled'] == 1 and reg['generation'] == generation and not fenced
                    and (flow is None or flow['state'] == 'completed'))
        if fenced or current['state'] != 'current':
            out.update(state='held', reason='fenced' if fenced or current['state'] == 'fenced'
                       else 'registration_inactive', connection='held')
        elif vault != 'ready':
            locked = vault in {'vault_locked', 'vault_locked_or_unconfigured'}
            out.update(state='held', reason=vault, connection='vault_locked' if locked else 'held')
        elif not eligible:
            out['connection'] = 'offline'
            if phase == 'active' and out['state'] != 'enable_pending':
                out['reason'] = 'local_disabled'
        else:
            out['enabled'] = True
            live = recovery.get('client', {})
            if (getattr(supervisor, 'factory', None) is factory
                    and recovery.get('enabled') is True and recovery.get('thread_alive') is True
                    and recovery.get('tick_stale') is False and live.get('running') is True
                    and live.get('state') == 'connected' and conn is not None
                    and (conn['generation'], conn['owner_generation']) == (generation, account['owner_generation'])
                    and live.get('epoch') == conn['epoch'] and conn['epoch'] > 0
                    and conn['scan_complete'] == 1 and conn['reauth_ms'] is not None and conn['reauth_ms'] > now
                    and not conn['reason']):
                out['connection'] = 'connected'
            elif (getattr(supervisor, 'factory', None) is factory
                  and recovery.get('state') not in {'not_running', 'unknown'}):
                out['connection'] = 'offline'
        return out
    except Exception as exc:
        _log(f'[agent-mail-status] pull status failed: {type(exc).__name__}', flush=True)
        reason = _reason(getattr(exc, 'reason', None) or (str(exc) if isinstance(exc, ValueError) else None),
                         'store_unavailable')
        out.update(state='not_enrolled' if reason == 'not_enrolled' else 'held', reason=reason)
        return out
