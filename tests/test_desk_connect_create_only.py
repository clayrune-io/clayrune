"""DCW-C6: a typed-credential Save is a create, guarded where the vault holds its lock.

The names check before the write is a courtesy message. If another Save makes the same
name between that check and the write, the entry it made must survive: not replaced by
this Save, and not deleted by this Save's undo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_desk_connect import _commit_req, _services, env  # noqa: E402,F401
from tests.test_desk_connect_providers import _key_draft, _post, _x_draft, net  # noqa: E402,F401

OTHER = 'MADE-BY-THE-OTHER-SAVE'


@pytest.fixture()
def raced(monkeypatch):
    """The other Save lands after this one's names check: the first write of `name` finds it there."""
    from mc import secrets_store
    real = secrets_store.set_secret
    done = set()

    def arm(name):
        def racing(n, *a, **k):
            if n == name and n not in done:
                done.add(n)
                real(n, OTHER, scope='global', entry_type='api_key')
            return real(n, *a, **k)
        monkeypatch.setattr(secrets_store, 'set_secret', racing)
    return arm


def _value(name):
    from mc import secrets_store
    return secrets_store.get_secret_value(name, consumer='test')


def test_pasted_key_does_not_replace_an_entry_made_meanwhile(env, net, raced):
    client, _, _ = env
    raced('gemini-api')
    r = _post(client, _key_draft())
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    assert _value('gemini-api') == OTHER


def test_x_app_key_does_not_replace_an_entry_made_meanwhile(env, net, raced):
    from mc import desk_accounts
    client, _, _ = env
    raced('x.client-id')
    r = _post(client, _x_draft())
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    assert _value('x.client-id') == OTHER
    assert desk_accounts.list_accounts() == [] and net['starts'] == []


def test_reference_save_does_not_replace_an_entry_made_meanwhile(env, raced):
    client, _, _ = env
    raced('plausible.api-key')
    r = _commit_req(client)
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    assert _value('plausible.api-key') == OTHER
    assert _services() == []
