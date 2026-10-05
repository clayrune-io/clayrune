"""What Connect by address may call "Verified" (docs/DESK_CONNECT_BY_URL_SPEC.md,
"Result": a stored key, an open profile or a token alone is never enough).

"Verified" needs a successful read-only probe by the service's provider
(`Provider.verify`), recorded with its method, capability, the vault entry it
checked and the time. Status is DERIVED each time it is asked:

    credential state from vault metadata  ->  verified only if a probe record exists
    AND the vault entry it checked still carries the created/updated stamps it had.

So a changed, replaced or deleted credential un-verifies itself with no code that
has to remember to; so does a token refresh (it rewrites the entry), which costs
one free re-check, never a false "Verified". Records are kept in memory, bounded,
and not persisted: after a restart everything reads "not verified" until checked
again, which is the safe direction.

The probe runs only when a human asks (`verify`); `status` never touches the
network, never decrypts anything.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from mc import secrets_store as _vault
from mc.core import _log, now_iso
from mc.desk_connect import providers
from mc.desk_connect.providers.base import Probe

LABELS = {
    'verified': 'Verified',
    'key_stored': 'Key stored, not verified',
    'signed_in': 'Signed in, not verified',
    'sign_in_required': 'Sign-in required',
    'key_unreadable': 'Stored key cannot be read',
    'check_failed': 'Check failed',
    'registered': 'Registered with agents, not verified',
    'setup_failed': 'Saved; setup failed',
    'not_connected': 'Not connected',
}
_REMEMBER = 200
_lock = threading.Lock()
_records: 'OrderedDict[tuple, dict]' = OrderedDict()


class VerifyError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


def _stamp(entry) -> tuple | None:
    if not entry:
        return None
    try:
        for s in _vault.list_secrets():
            if s['name'] == entry:
                return (s.get('created_at'), s.get('updated_at'))
    except _vault.SecretsError:
        return None
    return None


def _forget_all_for_tests() -> None:
    with _lock:
        _records.clear()


def _provider(service_id, method):
    prov = providers.for_service(service_id)
    if prov is None or not isinstance(method, str) or not prov.supports(method):
        raise VerifyError('that service and method cannot be checked from here', 404, 'unknown_method')
    return prov


def status(service_id, method, account_id=None) -> dict:
    """The derived state. Metadata only; no network."""
    prov = _provider(service_id, method)
    cs = prov.credential_state(method, account_id)
    state = {'not_connected': 'not_connected', 'needs_signin': 'sign_in_required',
             'key_unreadable': 'key_unreadable'}.get(cs['state'], cs['state'])
    out = {'state': state, 'entry': cs.get('entry')}
    if cs['state'] in ('key_stored', 'signed_in'):
        rec = _records.get((service_id, method, account_id or ''))
        if rec and rec['entry'] == cs.get('entry') and rec['stamp'] == _stamp(cs.get('entry')):
            out.update(state='verified', method=method, capability=rec['capability'], identity=rec['identity'],
                       at=rec['at'])
    out['label'] = LABELS.get(out['state'], out['state'])
    return out


def verify(service_id, method, account_id=None) -> dict:
    """Run the provider's one read-only check and return the new status plus the
    probe's own message. Never raises for a failed check: that is a result."""
    prov = _provider(service_id, method)
    cs = prov.credential_state(method, account_id)
    if cs['state'] not in ('key_stored', 'signed_in'):
        out = status(service_id, method, account_id)
        out['message'] = {'not_connected': 'Nothing is stored for this yet, so there is nothing to check.',
                          'needs_signin': 'Sign in again, then check.',
                          'key_unreadable': 'The stored key cannot be opened (the vault is locked, or its key '
                                            'changed). Unlock the vault, then check.'}.get(cs['state'], '')
        return out
    key = (service_id, method, account_id or '')
    stamp = _stamp(cs.get('entry'))        # taken BEFORE the probe: a change during it is not covered by it
    try:
        probe = prov.verify(method, account_id)
    except Exception as e:                 # a provider bug must read as "check failed", never as Verified
        _log(f'[desk_connect] verify {service_id}/{method} raised {type(e).__name__}', flush=True)
        probe = Probe(False, 'the check could not run; see the server log', kind='unavailable')
    with _lock:
        _records.pop(key, None)
        if probe.ok is True:
            _records[key] = {'entry': cs.get('entry'), 'stamp': stamp, 'at': now_iso(),
                             'identity': probe.identity or cs.get('entry') or '', 'capability': probe.capability}
            while len(_records) > _REMEMBER:
                _records.popitem(last=False)
    out = status(service_id, method, account_id)
    if probe.ok is False:
        out.update(state='check_failed', label=LABELS['check_failed'], failure=probe.kind or 'rejected')
    out['message'] = probe.message
    if probe.ok is None:
        out['verifiable'] = False
    return out
