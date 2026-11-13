"""Signup final enable: one bounded attempt per explicit local human action.

No timer, thread, network at import, automatic consent, or management-generation
rebind. Signup owns this flow's checked store, sealed identity and trusted human
gate. Status/resume only project durable intent; retry requires enable() again.
Persist an operation and body digest, never the plaintext flow secret. The
Worker replays that exact operation as an inert receipt, even after revocation;
local consent, deadline, generation and fences must therefore be rechecked.
"""
from __future__ import annotations

import secrets
import sqlite3
from typing import TYPE_CHECKING, Any

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_service_manifest import ManifestRefused
from mc.agent_mail_signing import IngressRefused
from mc.agent_mail_signup import H, I, MAX_INT, SKEW, _digest, _json, _need
from mc.agent_mail_store import MailStoreCorrupt, MailStoreError
from mc.core import _log

if TYPE_CHECKING:
    from mc.agent_mail_signup import Signup

PATH = '/v1/pull/activate'
MAX_ATTEMPTS = 3
FIELDS = frozenset({'activation_op', 'activation_digest', 'activation_attempts',
                    'activation_authorized_ms', 'activation_active_since_ms'})


def validate_metadata(meta: dict[str, Any]) -> bool:
    """Flat version-1 extension, all-or-nothing; old signup metadata is valid."""
    if not FIELDS.intersection(meta):
        return False
    _need(FIELDS <= meta.keys() and isinstance(meta['activation_op'], str)
          and I.fullmatch(meta['activation_op']) and isinstance(meta['activation_digest'], str)
          and H.fullmatch(meta['activation_digest']), 'schema_corrupt')
    _need(all(type(meta[k]) is int for k in ('activation_attempts', 'activation_authorized_ms',
                                            'activation_active_since_ms'))
          and 1 <= meta['activation_attempts'] <= MAX_ATTEMPTS
          and 0 < meta['activation_authorized_ms'] <= MAX_INT
          and 0 <= meta['activation_active_since_ms'] <= MAX_INT, 'schema_corrupt')
    return True


def project(row: dict[str, Any], view: dict[str, Any], reason: str) -> dict[str, Any]:
    """Pure projection. Never opens a transport or changes consent."""
    if 'activation_op' not in row['meta']:
        failure = reason or row['reason']
        return {**view, 'state': view['state'] if row['state'] in {'expired', 'cancelled'} else 'held',
                'reason': failure} if failure else view
    enabled = (row['account_state'] == 'active' and row['local_enabled'] == 1
               and row['state'] == 'completed' and row['meta']['activation_active_since_ms'] > 0)
    reason = reason or row['reason']
    return {**view, 'state': 'held' if reason else 'active' if enabled else row['state'],
            'reason': reason, 'enabled': enabled and not reason,
            'activation_attempts': row['meta']['activation_attempts'],
            'activation_pending': not enabled and row['state'] == 'enable_pending'}


def validate_registration(db: sqlite3.Connection, row: dict[str, Any]) -> None:
    """A completed pull receipt cannot override a later base-store revoke."""
    if row['account_state'] == 'active':
        base = db.execute('SELECT generation,enabled FROM registrations WHERE registration_id=?',
                          (row['install_id'],)).fetchone()
        _need(base is not None and base['generation'] == 1 and base['enabled'] == 1, 'stale_generation')


class Activation:
    def __init__(self, signup: Signup):
        self.signup = signup

    def _body(self, row: dict[str, Any], op: str) -> bytes:
        s = self.signup
        ref = s._ref(row)
        reason = s.seal.status(ref)
        _need(reason == 'ready', reason)
        _need(s.seal.public(ref.flow, ref.install, 1, ref.key_id) == row['meta']['public_key'],
              'operation_conflict')
        return _json({'op_id': op, 'flow_id': ref.flow,
                      'flow_secret': s.seal.flow_secret(ref.flow, ref.install, 1, ref.key_id).hex(),
                      'consent_version': row['consent_version']})

    def _registration(self, db: sqlite3.Connection, row: dict[str, Any]) -> None:
        r = db.execute('SELECT generation,enabled FROM registrations WHERE registration_id=?',
                       (row['install_id'],)).fetchone()
        _need(r is not None and r['generation'] == 1 and r['enabled'] == 0, 'stale_generation')

    def _current(self, db: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
        current = self.signup._current(db, row, _json(row['meta']))
        _need(all(current[k] == row[k] for k in (
            'account_state', 'local_enabled', 'address', 'candidate_key_id', 'seal_flow_id',
            'service_origin', 'service_key_id', 'service_public_key', 'manifest_sequence', 'manifest_digest')),
            'stale_generation')
        return current

    def enable(self) -> dict[str, Any]:
        """Exactly one exchange; even retries require a fresh trusted human gate."""
        s = self.signup
        claimed: dict[str, Any] | None = None
        observed: dict[str, Any] | None = None
        try:
            IdentitySeal._authorize(s.authorize)
            with s.store._read() as db:
                observed = s._row(db)
            _need(observed is not None, 'invalid_flow')
            assert observed is not None
            if 'activation_op' in observed['meta']:
                claimed = observed  # Persist preflight failures only against this exact pending intent.
            pin = s._service(observed)[0]  # Manifest-floor CAS must be outside tx.
            if observed['account_state'] == 'active':
                return s.status()  # Local committed replay, no new operation.
            _need(s._now() < observed['expires_ms'], 'flow_expired')
            _need(observed['account_state'] == 'verified' and observed['state'] in
                  {'verified', 'completed', 'enable_pending'}, 'verification_pending')
            op = observed['meta'].get('activation_op') or secrets.token_hex(16)
            body = self._body(observed, op)
            with s.store._tx() as db:
                row = self._current(db, observed)
                _need(s._now() < row['expires_ms'], 'flow_expired')
                meta = row['meta']
                if 'activation_op' not in meta:
                    # Consent is separate from signup's opt-in. Do not enable
                    # either execution surface until signed active evidence.
                    epoch = row['consent_epoch'] + 1
                    _need(epoch <= MAX_INT, 'invalid_flow')
                    meta.update(activation_op=op, activation_digest=_digest(body), activation_attempts=0,
                                activation_authorized_ms=s._now(), activation_active_since_ms=0)
                    db.execute('UPDATE mail_pull_accounts SET consent_epoch=?,updated_ms=? WHERE install_id=?',
                               (epoch, s._now(), row['install_id']))
                    db.execute('UPDATE mail_pull_flows SET consent_epoch=? WHERE flow_id=?', (epoch, row['flow_id']))
                    if db.execute('SELECT 1 FROM registrations WHERE registration_id=?',
                                  (row['install_id'],)).fetchone() is None:
                        db.execute('INSERT INTO registrations VALUES(?,1,0,?)',
                                   (row['install_id'], s._now() / 1000))
                self._registration(db, row)
                _need(meta['activation_digest'] == _digest(body), 'operation_conflict')
                _need(meta['activation_attempts'] < MAX_ATTEMPTS, 'request_limit')
                meta['activation_attempts'] += 1  # Claim BEFORE signing/I/O, including lost responses.
                db.execute("UPDATE mail_pull_flows SET state='enable_pending',reason='',result_json=? WHERE flow_id=?",
                           (_json(meta).decode('ascii'), row['flow_id']))
                claimed = s._row(db)
                assert claimed is not None
            row = claimed
            nonce, stamp = secrets.token_hex(16), str(s._now())
            fields = {'version': '2', 'direction': 'install-to-pull', 'key-id': row['candidate_key_id'],
                      'install-id': row['install_id'], 'generation': '1', 'owner-generation': '1',
                      'timestamp': stamp, 'nonce': nonce, 'digest': _digest(body)}
            transcript = _json(['clayrune-agent-mail-pull-http', '1', fields['direction'], pin.origin,
                                'POST', PATH, fields['key-id'], row['install_id'], '1', '1', stamp, nonce, fields['digest']])
            headers = {'x-mail-' + k: v for k, v in fields.items()}
            headers.update({'content-type': 'application/json', 'cache-control': 'no-store',
                            'x-content-type-options': 'nosniff',
                            'x-mail-signature': s.seal.sign_pull(s._ref(row), transcript)})
            # Recheck the claimed consent/fence/deadline immediately before I/O.
            s._service(row)
            with s.store._read() as db:
                self._current(db, row)
                self._registration(db, row)
                _need(s._now() < row['expires_ms'], 'flow_expired')
            try:
                response = s.transport('POST', PATH, headers, body)
            except Exception:
                # Exception messages can contain the flow secret or raw request.
                raise EnrollmentRefused('connection_lost') from None
            from mc.agent_mail_service_client import verify_pull_response
            value = verify_pull_response(response, candidate=pin, install=row['install_id'], generation=1,
                                         owner_generation=1, path=PATH, nonce=nonce, request_id=op, clock_ms=s._now)
            _need(response[0] == 200, value['code'])
            signed_time = int({k.lower(): v for k, v in response[1].items()}['x-mail-timestamp'])
            _need(value.keys() == {'v', 'code', 'request_id', 'install_id', 'generation',
                                   'owner_generation', 'state', 'active_since_ms'}
                  and value['install_id'] == row['install_id'] and type(value['generation']) is int
                  and type(value['owner_generation']) is int and value['generation'] == value['owner_generation'] == 1
                  and value['state'] == 'active' and type(value['active_since_ms']) is int
                  and max(1, row['meta']['activation_authorized_ms'] - SKEW) <= value['active_since_ms'] <= signed_time
                  and value['active_since_ms'] < row['expires_ms'])
            s._service(row)
            seal_reason = s.seal.status(s._ref(row))
            _need(seal_reason == 'ready', seal_reason)
            with s.store._tx() as db:
                current = self._current(db, row)
                _need(s._now() < current['expires_ms'], 'flow_expired')
                self._registration(db, row)
                meta = current['meta']
                meta['activation_active_since_ms'] = value['active_since_ms']
                db.execute('UPDATE registrations SET enabled=1,updated_at=? WHERE registration_id=? AND generation=1 AND enabled=0',
                           (s._now() / 1000, row['install_id']))
                db.execute("UPDATE mail_pull_accounts SET state='active',local_enabled=1,reason='',updated_ms=? WHERE install_id=?",
                           (s._now(), row['install_id']))
                db.execute("UPDATE mail_pull_flows SET state='completed',reason='',result_json=? WHERE flow_id=?",
                           (_json(meta).decode('ascii'), row['flow_id']))
                raw = response[2]
                db.execute('INSERT OR REPLACE INTO mail_pull_status VALUES(?,?,1,1,?,?)',
                           (row['install_id'], s._now(), raw.decode('utf-8'), _digest(raw)))
            return s.status()
        except EnrollmentRefused as exc:
            from mc.agent_mail_service_client import REASONS
            reason = str(exc)
            reason = 'vault_locked' if reason == 'vault_locked_or_unconfigured' else reason
            if reason not in REASONS and reason not in {'invalid_flow', 'identity_material_invalid',
                                                       'identity_material_unavailable', 'vault_key_invalid'}:
                reason = 'service_unavailable'
        except ManifestRefused:
            reason = 'service_key_changed'
        except IngressRefused:
            reason = 'response_unverified'
        except MailStoreCorrupt:
            reason = 'store_corrupt'
        except (MailStoreError, OSError, sqlite3.Error):
            reason = 'storage_unavailable'
        except Exception:
            reason = 'service_unavailable'
        _log(f'[agent-mail] activation {reason}', flush=True)
        try:
            with s.store._tx() as db:
                current = s._row(db)
                snapshots = [expected for expected in (claimed, observed) if expected is not None]
                if (current and current['account_state'] != 'active' and
                        (not snapshots or any(current['flow_id'] == expected['flow_id']
                         and _json(current['meta']) == _json(expected['meta'])
                         and current['consent_epoch'] == expected['consent_epoch'] for expected in snapshots))):
                    db.execute('UPDATE mail_pull_flows SET reason=? WHERE flow_id=?',
                               (reason, current['flow_id']))
        except Exception:
            _log('[agent-mail] activation failure_status storage_unavailable', flush=True)
        try:
            view = s.status()
        except Exception:
            _log('[agent-mail] activation status storage_unavailable', flush=True)
            view = s._view(None, 'storage_unavailable')
        return {**view, 'state': 'held', 'reason': reason, 'enabled': False}
