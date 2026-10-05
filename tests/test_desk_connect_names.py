"""Desk connect-by-URL, slice 2 / M1: the Service step takes a NAME or an address
(Ron 2026-10-03, "add service only from url or name"; Dave's ruling).

Pinned:

  * a name is the registry's label or alias, compared case-insensitively on letters
    and digits only; "Higgsfield", "linkedin" and "X" give the same answer as the
    service's own address;
  * a bare domain ("higgsfield.ai") is an address, https:// assumed, and still goes
    through every url_check refusal;
  * a name the registry does not know raises, asks for the web address, and makes NO
    lookup of any kind (no socket, no DNS);
  * suggestions are registry data only and never match a host by substring;
  * the registry refuses a service without a canonical address on its own host and
    two services claiming one name.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def client():
    from mc.blueprints import desk_connect_routes as routes
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    return app.test_client()


def _inspect(client, text, key='input'):
    return client.post('/api/desk/connect/inspect', json={key: text})


@pytest.mark.parametrize('text, sid, host', [
    ('Higgsfield', 'higgsfield', 'higgsfield.ai'), ('higgsfield', 'higgsfield', 'higgsfield.ai'),
    ('  HIGGSFIELD  ', 'higgsfield', 'higgsfield.ai'), ('Higgsfield AI', 'higgsfield', 'higgsfield.ai'),
    ('linkedin', 'linkedin', 'www.linkedin.com'), ('LinkedIn', 'linkedin', 'www.linkedin.com'),
    ('X', 'x', 'x.com'), ('x', 'x', 'x.com'), ('Twitter', 'x', 'x.com'),
    ('google ai-studio', 'google_ai', 'aistudio.google.com'), ('OpenAI', 'openai', 'platform.openai.com'),
])
def test_a_name_resolves_like_its_address(client, text, sid, host):
    r = _inspect(client, text)
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j['input_kind'] == 'name' and j['service']['id'] == sid and j['host'] == host
    by_address = _inspect(client, j['url']).get_json()
    assert by_address['input_kind'] == 'url'
    keys = ('url', 'host', 'service', 'options')
    assert {k: by_address[k] for k in keys} == {k: j[k] for k in keys}


def test_the_old_url_key_still_works(client):
    r = _inspect(client, 'https://x.com/clayrune', key='url')
    assert r.status_code == 200 and r.get_json()['service']['id'] == 'x'


@pytest.mark.parametrize('text, host, sid', [
    ('higgsfield.ai', 'higgsfield.ai', 'higgsfield'), ('Higgsfield.AI', 'higgsfield.ai', 'higgsfield'),
    ('www.linkedin.com/company/x', 'www.linkedin.com', 'linkedin'), ('plausible.io', 'plausible.io', None),
])
def test_a_bare_domain_is_an_address(client, text, host, sid):
    j = _inspect(client, text).get_json()
    assert j['input_kind'] == 'url' and j['host'] == host
    assert (j['service'] or {}).get('id') == sid
    assert j['url'].startswith('https://')


@pytest.mark.parametrize('text', ['localhost', '127.0.0.1', '10.0.0.5/x', 'printer.local', 'clayrune.io',
                                  'plausible.io/x?token=1'])
def test_a_bare_domain_meets_the_same_refusals(client, text):
    r = _inspect(client, text)
    assert r.status_code == 400, text
    assert r.get_json()['error']


def test_an_unknown_name_asks_for_the_address_and_looks_nothing_up(client, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError('a name must never cause a network lookup')
    monkeypatch.setattr(socket, 'getaddrinfo', boom)
    monkeypatch.setattr(socket, 'create_connection', boom)
    r = _inspect(client, 'Plausible')
    j = r.get_json()
    assert r.status_code == 400 and j['code'] == 'unknown_name'
    assert 'Plausible' in j['error'] and 'web address' in j['hint'] and j['suggestions'] == []


def test_an_unknown_name_offers_the_nearest_registry_names(client):
    j = _inspect(client, 'Google').get_json()
    assert j['code'] == 'unknown_name'
    assert {s['id'] for s in j['suggestions']} >= {'google_ai', 'google_drive', 'google_photos'}


@pytest.mark.parametrize('bad', [None, 5, [], {}, '', '   ', 'x' * 500])
def test_a_missing_or_silly_input_is_a_plain_400(client, bad):
    r = _inspect(client, bad)
    assert r.status_code == 400 and r.get_json()['error']


def test_classify_name_vs_address():
    from mc.desk_connect import resolve
    assert resolve.classify('X') == 'name' and resolve.classify('higgsfield.ai') == 'url'
    assert resolve.classify('https://x.com') == 'url' and resolve.classify('Plausible') == 'name'


def test_suggestions_as_you_type(client):
    def ids(q):
        r = client.get('/api/desk/connect/suggest', query_string={'q': q})
        return [s['id'] for s in r.get_json()['suggestions']]
    assert ids('') == [] and ids('   ') == []
    assert ids('hig') == ['higgsfield']
    assert ids('lin') == ['linkedin']
    assert ids('x') == ['x']                               # exact first; no substring match inside other names
    assert set(ids('google')) == {'google_ai', 'google_drive', 'google_photos'}
    assert ids('studio') == ['google_ai']                  # a word that starts with it
    assert ids('ube') == []                                # not a word start
    assert ids('x.com') == []                              # hosts are never matched by suggest
    assert len(ids('g')) <= 6
    s = client.get('/api/desk/connect/suggest', query_string={'q': 'hig'}).get_json()['suggestions'][0]
    assert s == {'id': 'higgsfield', 'label': 'Higgsfield', 'host': 'higgsfield.ai', 'url': 'https://higgsfield.ai'}


def test_registry_names_and_urls_are_validated(tmp_path):
    from mc.desk_connect import registry
    base = registry.v1_projection()     # the shipped profiles in the version 1 file shape, which still loads

    def load_with(mutate):
        d = json.loads(json.dumps(base))
        mutate(d)
        p = tmp_path / 'r.json'
        p.write_text(json.dumps(d), encoding='utf-8')
        return registry.load(p)
    for mutate in (
        lambda d: d['services'][1]['aliases'].append('x'),                       # a name belongs to one service
        lambda d: d['services'][1]['aliases'].append('X!'),                      # ...after normalising
        lambda d: d['services'][0].pop('url'),                                   # a service must say where it lives
        lambda d: d['services'][0].update(url='http://x.com'),                   # https only
        lambda d: d['services'][0].update(url='https://evil.example'),           # on one of its own hosts
        lambda d: d['services'][0].update(url='https://x.com/some/path'),        # no path
        lambda d: d['services'][0].update(aliases=['!!!']),                      # a name needs a letter or digit
        lambda d: d['services'][0].update(aliases='X'),
        lambda d: d['services'][0]['aliases'].append('x.example'),               # a dot would read as an address
    ):
        with pytest.raises(registry.RegistryError):
            load_with(mutate)


def test_normalize():
    from mc.desk_connect.registry import normalize
    assert normalize('  Google  AI-Studio ') == 'google ai studio'
    assert normalize('ＬｉｎｋｅｄＩｎ') == 'linkedin'
    assert normalize(None) == '' and normalize(5) == ''
