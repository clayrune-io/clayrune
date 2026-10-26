"""Live saved-app resolution and X token/app identity checks (MC-1113)."""
import hmac

from mc import secret_field_refs as refs, secrets_store as vault
from mc.core import _log

CHANGED = 'Your app changed; sign in again to reconnect this service.'


def bound(name):
    return any(s['name'] == name and s.get('has_field_refs') for s in vault.list_secrets())


def value(raw, **policy):
    return refs.resolve(raw, **policy) if isinstance(raw, dict) else raw


def check_id(expected, current):
    if not isinstance(current, str) or not hmac.compare_digest(str(expected).encode(), current.encode()):
        _log('[desk_oauth] saved app changed; sign-in required', flush=True)
        return False
    return True


def problem():
    for name in ('x.client-id', 'x.client-secret'):
        issue = refs.health(name)
        if issue:
            return issue
    return None
