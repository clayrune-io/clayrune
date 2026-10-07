"""Desk connect-by-URL, slice 1 (docs/DESK_CONNECT_BY_URL_SPEC.md, Dave's
2026-10-03 decisions): address validation, the registry, and the one Save.

Pinned:

  * an address is https, <= 300 characters, with no userinfo, query, fragment,
    odd port, localhost/private/reserved host or Clayrune origin, and is matched
    to the registry by exact host, never by substring;
  * the registry says what Clayrune supports today; MCP is information only and an
    unknown host is "Not recognised yet" with the Save for agents fallback;
  * commit refuses an unattended caller before anything else (403, nothing
    written, passcode not even asked), validates the draft BEFORE the passcode, and
    asks `_require_human_passcode` exactly once per request;
  * the vault entry is written first, then the Desk record; if the record write
    fails the vault entry is deleted in the same request (compensating rollback);
  * a repeated request_id with the same draft writes nothing twice, a changed draft
    is 409, and a FAILED commit can be retried with the same id;
  * a wrong passcode writes nothing; the secret value appears in no response, no
    log line and not in the Desk store.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'
PASSCODE = 'dash-passcode-1'
RID = 'req-0001-abcdef'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import desk as _desk
    from mc import secrets_store
    from mc.blueprints import desk_connect_routes as routes
    from mc.blueprints import local_auth
    from mc.desk_connect import commit as _commit
    from mc.state import agent_sessions
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    agent_sessions.clear()
    _commit._forget_all_for_tests()
    local_auth._local_auth_set_passcode(PASSCODE)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    calls = {'n': 0}
    real = routes._require_human_passcode

    def counting(data):
        calls['n'] += 1
        return real(data)
    monkeypatch.setattr(routes, '_require_human_passcode', counting)
    yield app.test_client(), calls, tmp_path
    agent_sessions.clear()
    _commit._forget_all_for_tests()


def _draft(**over):
    d = {'url': 'https://plausible.io/mysite', 'method': 'save_for_agents', 'name': 'Plausible',
         'credential': {'name': 'plausible.api-key', 'entry_type': 'api_key', 'value': SECRET,
                        'description': 'stats', 'allow_unattended': True}}
    d.update(over)
    return d


def _commit_req(client, draft=None, rid=RID, passcode=PASSCODE, **extra):
    body = {'request_id': rid, 'draft': _draft() if draft is None else draft, 'passcode': passcode}
    body.update(extra)
    return client.post('/api/desk/connect/commit', json=body)


def _vault_names():
    from mc import secrets_store
    return sorted(s['name'] for s in secrets_store.list_secrets())


def _services():
    from mc import desk_services
    return desk_services.list_services()


# ── the address ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url', [
    'http://plausible.io/x',                 # not https
    'ftp://plausible.io/x',
    'https://user:pw@plausible.io/x',        # userinfo
    'https://plausible.io/x?token=1',        # query
    'https://plausible.io/x#frag',           # fragment
    'https://plausible.io:8443/x',           # odd port
    'https://localhost/x', 'https://app.localhost/x', 'https://printer.local/x',
    'https://intranet.internal/x', 'https://127.0.0.1/x', 'https://10.0.0.5/x',
    'https://192.168.1.1/x', 'https://169.254.169.254/latest', 'https://[::1]/x',
    'https://127.1/x', 'https://0x7f.1/x', 'https://2130706433/x',
    'https://clayrune.io/x', 'https://app.clayrune.com/x',
    'https://plausible.io/' + 'a' * 300,     # too long
    'https://plausible io/x', '', '   ',
])
def test_refused_addresses(env, url):
    client, _, _ = env
    r = client.post('/api/desk/connect/inspect', json={'url': url})
    assert r.status_code == 400, url
    assert r.get_json()['error'] and 'hint' in r.get_json()


def test_the_clayrune_server_itself_is_refused(env):
    client, _, _ = env
    r = client.post('/api/desk/connect/inspect', json={'url': 'https://localhost/x'},
                    base_url='https://my-box.example')
    assert r.status_code == 400
    r = client.post('/api/desk/connect/inspect', json={'url': 'https://my-box.example/desk'},
                    base_url='https://my-box.example')
    assert r.status_code == 400 and 'Clayrune' in r.get_json()['error']


def test_normalised_host_and_path(env):
    client, _, _ = env
    r = client.post('/api/desk/connect/inspect', json={'url': '  https://X.com/ClayRune/  '}).get_json()
    assert r['host'] == 'x.com' and r['url'] == 'https://x.com/ClayRune/'
    assert client.post('/api/desk/connect/inspect', json={'url': 'https://x.com/'}).get_json()['url'] == 'https://x.com'


# ── the registry / Method step ───────────────────────────────────────────────

def _rows(client, url):
    r = client.post('/api/desk/connect/inspect', json={'url': url}).get_json()
    return r, {(o['method'], o['support']): o for o in r['options']}


def test_known_hosts_say_what_is_supported(env):
    client, _, _ = env
    r, rows = _rows(client, 'https://twitter.com/clayrune')
    assert r['service'] == {'id': 'x', 'label': 'X'}
    assert rows[('oauth', 'available')]['open'] == 'account:x'
    r, rows = _rows(client, 'https://www.higgsfield.ai/')
    assert {o['open'] for o in r['options'] if o.get('open')} == {'engine:higgsfield_mcp', 'engine:higgsfield'}
    r, rows = _rows(client, 'https://www.linkedin.com/company/clayrune')
    assert rows[('oauth', 'restricted')] and 'open' not in rows[('oauth', 'restricted')]
    for url in ('https://www.youtube.com/@x', 'https://drive.google.com/drive', 'https://photos.google.com/'):
        r, rows = _rows(client, url)
        assert r['service'] and [o for o in r['options'] if o['support'] == 'info_only' and o['method'] == 'oauth'], url


def test_mcp_is_information_only_everywhere(env):
    client, _, _ = env
    for url in ('https://x.com/a', 'https://plausible.io/a'):
        _, rows = _rows(client, url)
        mcp = rows[('mcp', 'info_only')]
        assert mcp['selectable'] is False


def test_unknown_host_is_not_recognised_and_offers_save_for_agents(env):
    client, _, _ = env
    r, rows = _rows(client, 'https://plausible.io/mysite')
    assert r['service'] is None
    save = rows[('save_for_agents', 'available')]
    assert save['selectable'] is True
    assert [o['method'] for o in r['options'] if o['selectable']] == ['save_for_agents']


@pytest.mark.parametrize('url', ['https://notx.com/a', 'https://x.com.evil.example/a', 'https://evil.example/x.com',
                                 'https://sub.x.com/a', 'https://xx.com/a'])
def test_host_match_is_exact_not_a_substring(env, url):
    client, _, _ = env
    assert client.post('/api/desk/connect/inspect', json={'url': url}).get_json()['service'] is None


def test_registry_rejects_bad_data(tmp_path):
    from mc.desk_connect import registry
    base = registry.v1_projection()     # the shipped profiles in the version 1 file shape, which still loads

    def load_with(mutate):
        d = json.loads(json.dumps(base))
        mutate(d)
        p = tmp_path / 'r.json'
        p.write_text(json.dumps(d), encoding='utf-8')
        return registry.load(p)
    registry.load()                                              # the shipped file is valid
    with pytest.raises(registry.RegistryError):                  # an available option must open a flow
        load_with(lambda d: d['services'][0]['options'][0].pop('open'))
    with pytest.raises(registry.RegistryError):                  # info_only cannot open one
        load_with(lambda d: d['services'][5]['options'][0].update(open='account:x'))
    with pytest.raises(registry.RegistryError):                  # a host belongs to one service
        load_with(lambda d: d['services'][1]['hosts'].append('x.com'))
    with pytest.raises(registry.RegistryError):                  # unknown field
        load_with(lambda d: d['services'][0]['options'][0].update(run='curl evil'))
    with pytest.raises(registry.RegistryError):
        load_with(lambda d: d.update(version=2))


# ── commit: who may, in what order ───────────────────────────────────────────

def test_unattended_caller_is_refused_before_anything(env):
    from mc.state import agent_sessions
    client, calls, _ = env
    agent_sessions['scheduled-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}
    r = _commit_req(client)
    assert r.status_code == 403
    assert calls['n'] == 0                     # the passcode was not even consulted
    assert _vault_names() == [] and _services() == []


def test_passcode_is_checked_exactly_once_per_commit(env):
    client, calls, _ = env
    assert _commit_req(client).status_code == 201
    assert calls['n'] == 1
    assert _commit_req(client).status_code == 200      # duplicate: still one check for this request
    assert calls['n'] == 2


def test_bad_draft_is_refused_before_the_passcode(env):
    client, calls, _ = env
    r = _commit_req(client, draft=_draft(url='http://plausible.io/x'))
    assert r.status_code == 400 and r.get_json()['code'] == 'bad_url'
    assert calls['n'] == 0
    assert _vault_names() == [] and _services() == []


def test_wrong_passcode_writes_nothing_and_a_retry_works(env):
    client, calls, _ = env
    r = _commit_req(client, passcode='not-it')
    assert r.status_code == 403 and r.get_json()['error'] == 'bad_passcode'
    assert _vault_names() == [] and _services() == []
    r = _commit_req(client)                            # same request_id, right passcode: the draft survived
    assert r.status_code == 201
    assert _vault_names() == ['plausible.api-key'] and len(_services()) == 1


def test_missing_passcode_is_refused(env):
    client, _, _ = env
    r = client.post('/api/desk/connect/commit', json={'request_id': RID, 'draft': _draft()})
    assert r.status_code == 403
    assert _vault_names() == [] and _services() == []


# ── commit: what it writes ───────────────────────────────────────────────────

def test_saves_vault_entry_then_record_and_links_them(env):
    from mc import secrets_store
    client, _, _ = env
    r = _commit_req(client)
    body = r.get_json()
    assert r.status_code == 201 and body['ok'] is True and body['duplicate'] is False
    svc = body['service']
    assert svc['name'] == 'Plausible' and svc['link'] == 'https://plausible.io/mysite'
    assert svc['kind'] == 'saved_for_agents' and svc['publish'] is False
    assert svc['credential'] == {'name': 'plausible.api-key', 'in_vault': True}
    assert body['credential'] == {'name': 'plausible.api-key', 'stored': True}
    meta = [s for s in secrets_store.list_secrets() if s['name'] == 'plausible.api-key'][0]
    assert meta['entry_type'] == 'api_key' and meta['scope'] == 'global' and meta['allow_unattended'] is True
    assert secrets_store.get_secret_value('plausible.api-key', consumer='test') == SECRET


def test_a_save_with_no_credential_writes_only_the_record(env):
    client, _, _ = env
    r = _commit_req(client, draft=_draft(credential=None))
    assert r.status_code == 201 and r.get_json()['credential'] is None
    assert _vault_names() == [] and _services()[0]['credential'] == {'name': '', 'in_vault': None}


def test_secret_value_is_in_no_response_no_log_and_not_the_desk_store(env, capsys):
    client, _, tmp = env
    for r in (_commit_req(client), _commit_req(client)):                       # created, then duplicate
        assert SECRET not in r.get_data(as_text=True)
    bad = _commit_req(client, rid='req-0002-abcdef', draft=_draft(name='Other'))   # secret_exists refusal
    assert bad.status_code == 409 and SECRET not in bad.get_data(as_text=True)
    out = capsys.readouterr()
    assert SECRET not in out.out and SECRET not in out.err
    assert SECRET not in (tmp / 'desk.json').read_text(encoding='utf-8')


def test_existing_secret_name_is_never_replaced(env):
    from mc import secrets_store
    secrets_store_value = 'ORIGINAL-VALUE'
    client, _, _ = env
    secrets_store.set_secret('plausible.api-key', secrets_store_value, entry_type='api_key')
    r = _commit_req(client)
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    assert secrets_store.get_secret_value('plausible.api-key', consumer='test') == secrets_store_value
    assert _services() == []


def test_duplicate_service_name_is_refused_without_a_vault_write(env):
    client, _, _ = env
    assert _commit_req(client, draft=_draft(credential=None)).status_code == 201
    r = _commit_req(client, rid='req-0002-abcdef')
    assert r.status_code == 409 and r.get_json()['code'] == 'service_exists'
    assert _vault_names() == []


@pytest.mark.parametrize('cred', [
    {'name': 'Bad Name', 'entry_type': 'api_key', 'value': SECRET},
    {'name': 'oauth.x', 'entry_type': 'api_key', 'value': SECRET},
    {'name': 'a.b', 'entry_type': 'nonsense', 'value': SECRET},
    {'name': 'a.b', 'entry_type': 'api_key', 'value': ''},
    {'name': 'a.b', 'entry_type': 'api_key_pair', 'value': SECRET},          # key pair needs a Key ID
    {'name': 'a.b', 'entry_type': 'api_key', 'value': SECRET, 'scope': 'p'}, # unknown field
    {'name': 'a.b', 'entry_type': 'api_key', 'value': SECRET, 'allow_unattended': 'yes'},
])
def test_bad_credentials_are_refused(env, cred):
    client, calls, _ = env
    r = _commit_req(client, draft=_draft(credential=cred))
    assert r.status_code == 400 and calls['n'] == 0
    assert SECRET not in r.get_data(as_text=True)
    assert _vault_names() == [] and _services() == []


def test_only_save_for_agents_can_be_saved(env):
    client, _, _ = env
    r = _commit_req(client, draft=_draft(method='mcp'))
    assert r.status_code == 400 and r.get_json()['code'] == 'method_not_available'
    assert _commit_req(client, draft=_draft(extra='x')).status_code == 400


def test_locked_vault_refuses_a_credential_save(env, monkeypatch):
    from mc import secrets_store
    client, _, _ = env
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: True)
    r = _commit_req(client)
    assert r.status_code == 409 and r.get_json()['code'] == 'vault_locked'
    assert _services() == []
    assert _commit_req(client, rid='req-0002-abcdef', draft=_draft(credential=None)).status_code == 201   # no secret, no vault needed


# ── compensation ─────────────────────────────────────────────────────────────

def test_record_failure_deletes_the_vault_entry_it_just_wrote(env, monkeypatch):
    from mc import desk as _desk
    client, _, _ = env

    real_write = _desk._write_store

    def boom(store):
        raise OSError('disk full')
    monkeypatch.setattr(_desk, '_write_store', boom)
    r = _commit_req(client)
    assert r.status_code == 500 and r.get_json()['code'] == 'record_failed'
    assert _vault_names() == [], 'the vault entry must be rolled back'
    assert 'orphan' not in r.get_data(as_text=True) and SECRET not in r.get_data(as_text=True)
    monkeypatch.setattr(_desk, '_write_store', real_write)   # the failure was not remembered: the same id retries
    r = _commit_req(client)
    assert r.status_code == 201 and _vault_names() == ['plausible.api-key']


def test_record_refusal_also_rolls_back(env, monkeypatch):
    from mc import desk_services
    client, _, _ = env
    real = desk_services.create_service

    def refuse(*a, **k):
        raise desk_services.ServiceError('at most 200 services can be saved', 409)
    monkeypatch.setattr(desk_services, 'create_service', refuse)
    r = _commit_req(client)
    assert r.status_code == 409 and r.get_json()['code'] == 'service_refused'
    assert _vault_names() == []
    monkeypatch.setattr(desk_services, 'create_service', real)


def test_failed_compensation_says_which_entry_to_remove(env, monkeypatch):
    from mc import desk_services, secrets_store
    client, _, _ = env
    monkeypatch.setattr(desk_services, 'create_service', lambda *a, **k: (_ for _ in ()).throw(OSError('x')))
    monkeypatch.setattr(secrets_store, 'delete_secret', lambda name: (_ for _ in ()).throw(OSError('locked')))
    r = _commit_req(client)
    body = r.get_json()
    assert r.status_code == 500
    assert 'plausible.api-key' in body['error'] and 'Vault' in body['error']
    assert SECRET not in r.get_data(as_text=True)


def test_vault_refusal_writes_no_record(env, monkeypatch):
    from mc import secrets_store
    client, _, _ = env

    def refuse(*a, **k):
        raise secrets_store.SecretsError('vault is read-only')
    monkeypatch.setattr(secrets_store, 'set_secret', refuse)
    r = _commit_req(client)
    assert r.status_code == 400 and r.get_json()['code'] == 'vault_refused'
    assert _services() == []


# ── duplicate request_id ─────────────────────────────────────────────────────

def test_duplicate_request_id_does_not_write_twice(env):
    from mc import secrets_store
    client, _, _ = env
    first = _commit_req(client)
    assert first.status_code == 201
    stamp = [s for s in secrets_store.list_secrets() if s['name'] == 'plausible.api-key'][0]['updated_at']
    again = _commit_req(client)
    assert again.status_code == 200 and again.get_json()['duplicate'] is True
    assert again.get_json()['service'] == first.get_json()['service']
    assert len(_services()) == 1 and _vault_names() == ['plausible.api-key']
    assert [s for s in secrets_store.list_secrets() if s['name'] == 'plausible.api-key'][0]['updated_at'] == stamp


def test_same_request_id_with_a_changed_payload_is_409(env):
    client, _, _ = env
    assert _commit_req(client).status_code == 201
    changed = _draft()
    changed['credential']['value'] = SECRET + '-changed'
    r = _commit_req(client, draft=changed)
    assert r.status_code == 409 and r.get_json()['code'] == 'request_id_reused'
    assert _commit_req(client, draft=_draft(name='Renamed')).status_code == 409
    assert len(_services()) == 1


@pytest.mark.parametrize('rid', ['', 'short', 'has space in it', 'x' * 81, None, 5])
def test_request_id_shape(env, rid):
    client, calls, _ = env
    assert _commit_req(client, rid=rid).status_code == 400 and calls['n'] == 0
