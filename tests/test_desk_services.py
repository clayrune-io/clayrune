"""Desk "Something else" services (Connections' Add service, Ron 2026-10-03).

Pinned:

  * a service is a name + optional link + optional vault credential NAME, stored in
    its own `store['services']` section: it never shows up as a workspace account,
    so nothing that places or publishes can pick it up;
  * every read says what it is: `kind: saved_for_agents`, `publish: False`;
  * `credential.in_vault` is derived from the vault's metadata on every read (a
    name with nothing behind it is False, no credential at all is None), and a
    value-shaped credential (spaces, capitals, a key) is refused, not stored;
  * the link must be http(s); a name is required and unique (case-insensitive);
  * save / change / remove refuse an unattended caller (403), reading does not;
  * a duplicate id or name is 409, an unknown id is 404, and the section survives
    the Desk store's own read/write round trips (a campaign write keeps it).
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_services as _services  # noqa: E402
from mc.blueprints import desk_services_routes as routes  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = {'names': set()}
    monkeypatch.setattr(_services.secrets_store, 'list_secrets',
                        lambda *a, **k: [{'name': n} for n in sorted(vault['names'])])
    caller = {'unattended': False}
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: caller['unattended'])
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    return app.test_client(), vault, caller


def _post(client, **body):
    return client.post('/api/desk/services', json=body)


def test_saved_service_is_honest_about_what_it_is(env):
    client, vault, _ = env
    r = _post(client, name='Plausible', link='https://plausible.io/mysite', credential='plausible.api')
    assert r.status_code == 201
    svc = r.get_json()
    assert svc['name'] == 'Plausible' and svc['link'] == 'https://plausible.io/mysite'
    assert svc['kind'] == 'saved_for_agents' and svc['publish'] is False
    assert svc['credential'] == {'name': 'plausible.api', 'in_vault': False}
    assert client.get('/api/desk/services').get_json() == [svc]
    vault['names'].add('plausible.api')                     # derived on read, never stored
    assert client.get('/api/desk/services').get_json()[0]['credential']['in_vault'] is True


def test_link_and_credential_are_optional(env):
    client, _, _ = env
    svc = _post(client, name='Notes').get_json()
    assert svc['link'] == '' and svc['credential'] == {'name': '', 'in_vault': None}


def test_it_is_not_a_workspace_account(env):
    client, _, _ = env
    _post(client, name='Notes')
    store = _desk._read_store()
    assert store['accounts'] == {} and len(store['services']) == 1
    from mc import desk_accounts
    assert desk_accounts.list_accounts() == []
    _desk._write_store(store)                                # another module's write keeps the section
    assert len(_desk._read_store()['services']) == 1


@pytest.mark.parametrize('body', [
    {},                                                      # no name
    {'name': '   '},
    {'name': 'x' * 81},
    {'name': 'A', 'link': 'javascript:alert(1)'},
    {'name': 'A', 'link': 'ftp://host/file'},
    {'name': 'A', 'link': 'https://'},
    {'name': 'A', 'credential': 'sk-live ABC123 secret value'},   # a value, not a name
    {'name': 'A', 'credential': 'Has-Capitals'},
    {'name': 'A', 'id': 'has space'},
])
def test_bad_input_is_refused_and_nothing_is_stored(env, body):
    client, _, _ = env
    assert _post(client, **body).status_code == 400
    assert client.get('/api/desk/services').get_json() == []


def test_duplicates_and_unknown_ids(env):
    client, _, _ = env
    assert _post(client, name='Plausible', id='svc-a').status_code == 201
    assert _post(client, name='plausible').status_code == 409          # name, any case
    assert _post(client, name='Other', id='svc-a').status_code == 409  # id
    assert client.patch('/api/desk/services/nope', json={'name': 'Z'}).status_code == 404
    assert client.delete('/api/desk/services/nope').status_code == 404


def test_change_and_remove(env):
    client, _, _ = env
    sid = _post(client, name='Plausible').get_json()['id']
    other = _post(client, name='Other').get_json()['id']
    r = client.patch(f'/api/desk/services/{sid}', json={'link': 'https://example.com', 'credential': 'plausible.api'})
    assert r.status_code == 200 and r.get_json()['link'] == 'https://example.com'
    assert client.patch(f'/api/desk/services/{sid}', json={'name': 'other'}).status_code == 409
    assert client.patch(f'/api/desk/services/{sid}', json={'kind': 'account'}).status_code == 400
    assert client.patch(f'/api/desk/services/{sid}', json={'link': ''}).get_json()['link'] == ''
    assert client.delete(f'/api/desk/services/{sid}').status_code == 200
    assert [s['id'] for s in client.get('/api/desk/services').get_json()] == [other]


def test_only_a_human_saves_changes_or_removes(env):
    client, _, caller = env
    sid = _post(client, name='Plausible').get_json()['id']
    caller['unattended'] = True
    assert _post(client, name='Sneaky').status_code == 403
    assert client.patch(f'/api/desk/services/{sid}', json={'name': 'X'}).status_code == 403
    assert client.delete(f'/api/desk/services/{sid}').status_code == 403
    assert client.get('/api/desk/services').status_code == 200       # an agent may read
    assert [s['name'] for s in client.get('/api/desk/services').get_json()] == ['Plausible']
