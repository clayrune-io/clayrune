"""The Save of a known-host method (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 2): the
adapter's local writes, undone newest-first if a later one fails, then the part that
cannot be undone.

    apply   inside commit's lock: the provider's local writes (vault entries, a Desk
            account), each pushing its undo. A failure unwinds them all and raises
            ProviderError; nothing is left behind except what an undo could not
            remove, and that is named in the message.
    follow  outside the lock, after the commit is durable: the sign-in to start. It
            is NOT a transaction (spec, "Explicit partial-success boundary"): if it
            fails the committed credential and account stay, reported as
            `setup.state == 'failed'`, never Verified.

Neither returns a credential value. `commit.py` turns ProviderError into its own
CommitError (this module must not import it: commit imports this one).
"""
from __future__ import annotations

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import providers, verification
from mc.desk_connect.providers.base import Applied, ProviderError
from mc.desk_connect.undo import UndoStack


def apply(clean: dict) -> tuple[Applied, dict]:
    """Make the provider's local writes. Returns `(applied, result)`; `result` is
    safe to remember and to return (names and states only)."""
    prov = providers.for_service(clean['service'])
    if prov is None:
        raise ProviderError('that service has no connector yet', 400, 'method_not_available')
    method = clean['method']
    if _vault.is_locked() and _writes_secrets(prov, method, clean['fields']):
        raise ProviderError('the vault is locked: unlock it in Secrets, then save again', 409, 'vault_locked')
    undo = UndoStack()
    try:
        applied = prov.apply(method, clean['fields'], undo)
    except ProviderError as e:
        left = undo.unwind()
        raise ProviderError(_with_left(str(e), left), e.status, e.code) from e
    except Exception as e:
        _log(f'[desk_connect] {clean["service"]}/{method} failed unexpectedly ({type(e).__name__}); undoing', flush=True)
        left = undo.unwind()
        raise ProviderError(_with_left('could not save; see the server log', left), 500, 'record_failed') from e
    undo.commit()
    try:
        status = verification.status(clean['service'], method, applied.account_id)
    except Exception as e:                  # the save has landed: a status that cannot be read must not undo that
        _log(f'[desk_connect] {clean["service"]}/{method} saved; its status could not be read ({type(e).__name__})', flush=True)
        status = {'state': 'unknown', 'label': 'Saved; its status could not be read', 'entry': None}
    result = {'service': {'id': clean['service'], 'label': clean['label']}, 'method': method,
              'credential': None, 'stored': list(applied.extra.get('stored') or []),
              'account': applied.extra.get('account'),
              'status': status,
              'account_id': applied.account_id}
    return applied, result


def follow(clean: dict, applied: Applied) -> dict:
    """What happens after the durable commit: the sign-in. Never raises."""
    prov = providers.for_service(clean['service'])
    try:
        return prov.after_commit(clean['method'], clean['fields'], applied) if prov else {}
    except Exception as e:
        _log(f'[desk_connect] {clean["service"]} sign-in start raised {type(e).__name__}', flush=True)
        return {'setup': {'state': 'failed', 'message': 'the sign-in could not be started; see the server log'}}


def _writes_secrets(prov, method: str, fields: dict) -> bool:
    return any(f.get('kind') == 'secret' and fields.get(f['key']) for f in prov.fields(method, None))


def _with_left(msg: str, left: list) -> str:
    if left:
        msg += f'; this could not be removed and needs removing by hand in Secrets or Desk: {", ".join(left)}'
    return msg
