"""Desk-only engine disconnections. Vault entries and provider sign-ins stay intact.

Missing state preserves existing installs. Unreadable state fails closed; reconnecting
is an explicit human action, never a side effect of unlocking or editing the Vault.
"""
from __future__ import annotations

import json
import threading

from mc import secrets_store
from mc.atomic_json import write_json_atomic

_lock = threading.Lock()
_PROVIDERS = {('google_ai', 'api_key'): 'google', ('openai', 'api_key'): 'openai',
              ('higgsfield', 'api_key'): 'higgsfield', ('higgsfield', 'oauth'): 'higgsfield_mcp'}


def _path():
    return secrets_store.clayrune_home() / 'desk_engine_connections.json'


def _read() -> dict:
    try:
        value = json.loads(_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or any(v is not False for v in value.values()):
        raise ValueError('Desk engine connections could not be read')
    return value


def disconnected(engine_id: str) -> bool:
    with _lock:
        return _read().get(engine_id) is False


def refusal(engine_id: str) -> dict | None:
    from mc.core import _log
    try:
        if disconnected(engine_id):
            return {'state': 'disconnected', 'reason': 'This engine was removed from the Desk. Add the service again to reconnect it.'}
    except (OSError, ValueError) as e:
        _log(f'[desk-engines] connection state unreadable: {type(e).__name__}', flush=True)
        return {'state': 'connection_unreadable', 'reason': 'Desk connection state could not be read. See the server log.'}
    return None


def set_connected(engine_id: str, connected: bool) -> None:
    with _lock:
        value = _read()
        if connected:
            value.pop(engine_id, None)
        else:
            value[engine_id] = False
        target = _path()
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, value)


def reconnect_saved(service: str, method: str, undo) -> None:
    """A new passcode-approved Add service Save can reconnect its own engine."""
    engine_id = _PROVIDERS.get((service, method))
    if engine_id and disconnected(engine_id):
        set_connected(engine_id, True)
        undo.push(f'Desk connection {engine_id}', lambda: set_connected(engine_id, False))
