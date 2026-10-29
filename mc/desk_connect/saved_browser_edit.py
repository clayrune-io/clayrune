"""Update one inert saved browser-site reference by ID, never create an account."""
from __future__ import annotations

import copy
import threading
from collections import OrderedDict

from mc import desk
from mc.desk_connect import browser_connections as connections, signin_method
from mc.desk_connect.browser_setup import SetupError

_lock = threading.Lock()
_done: OrderedDict[tuple[str, str], tuple[str, dict]] = OrderedDict()


def _target(store: dict, identifier: str, clean: dict) -> dict:
    rec = store.get('services', {}).get(identifier)
    if not isinstance(rec, dict) or not isinstance(rec.get('browser_connection'), dict):
        raise SetupError('saved browser site not found', 404, 'not_found')
    if rec.get('account_id') or connections.pane_account.platform_of(clean):
        raise SetupError('edit this connection through its account', 409, 'account_connection')
    if rec['link'] != clean['url'] or rec['browser_connection'].get('service') != clean['service']:
        raise SetupError('the saved site changed; close Edit and open it again', 409, 'source_changed')
    if any(s['id'] != identifier and s.get('name', '').casefold() == clean['name'].casefold()
           for s in store['services'].values()):
        raise SetupError('a service with this name is already saved', 409, 'name_exists')
    return rec


def validate(identifier: str, clean: dict) -> None:
    with desk._store_lock:
        _target(desk._read_store(), identifier, clean)


def update(identifier: str, request_id: str, clean: dict) -> dict:
    connections.clean_request_id(request_id)
    fp = connections._fingerprint(clean)
    key = (identifier, request_id)
    with _lock, desk._store_lock:
        store = desk._read_store()
        rec = _target(store, identifier, clean)
        if key in _done:
            seen, result = _done[key]
            if seen != fp:
                raise SetupError('this request was already used for different details', 409, 'request_changed')
            return copy.deepcopy(result)
        held = rec['browser_connection']
        unchanged = (rec['name'] == clean['name'] and held['browser_profile'] == clean['browser_profile']
                     and signin_method.stored(held) == clean['signin_method'])
        rec['name'] = clean['name']
        held.update(browser_profile=clean['browser_profile'], signin_method=clean['signin_method'])
        if not unchanged:
            desk._write_store(store)
        result = {'ok': True, **connections.view(rec), 'unchanged': unchanged}
        _done[key] = (fp, copy.deepcopy(result))
        while len(_done) > 200:
            _done.popitem(last=False)
        return result
