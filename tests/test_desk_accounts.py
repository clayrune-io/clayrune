"""Desk v1 R1-W S5 - the workspace account store (plan M2-M5, §2.C).

Pinned:

  * `presences[*].accounts[]` is lifted into `store['accounts']` once: a second
    read changes nothing, an edit made to the workspace record is never
    overwritten by the stale presence copy, a bare-id entry (no platform) is
    skipped, and a lifted account is `manual` unless the presence said `direct`;
  * `publish` is DERIVED from the vault's metadata on every read, never stored:
    X needs a readable `x.oauth-token`; the LinkedIn Company Page is NOT ready
    while `LINKEDIN_ORG_POSTING_APPROVED` is off, whatever the vault holds; a blog
    (manual) is ready; a preview account is not;
  * no route takes or returns a credential: an account names a vault entry, and
    only the vault's metadata is ever read;
  * create/delete refuse an unattended caller (403), only X / LinkedIn / blog and the
    read-only pane sites (tests/test_desk_readonly_accounts.py) can be created, a duplicate identity is 409, delete is 409 while a live campaign
    or a live piece version is on the account and then drops the presence copies
    so nothing is lifted straight back in;
  * a read setting (M4 or the legacy presence route) lands on the workspace record
    AND the presence copy engagement reads, and an unattended caller cannot set one.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}, {'id': 'beta', 'name': 'Beta'}]
CID = 'camp-a'


class _Vault:
    """Stands in for `secrets_store`: metadata only, like the real listing."""

    def __init__(self):
        self.entries = {}
        self.unreadable = set()

    def add(self, name, allow_unattended=True, readable=True):
        self.entries[name] = {'name': name, 'allow_unattended': allow_unattended}
        if not readable:
            self.unreadable.add(name)

    def list_secrets(self, *a, **k):
        return list(self.entries.values())

    def is_readable(self, name):
        return name in self.entries and name not in self.unreadable


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = _Vault()
    monkeypatch.setattr(_accounts.secrets_store, 'list_secrets', vault.list_secrets)
    monkeypatch.setattr(_accounts.secrets_store, 'is_readable', vault.is_readable)
    caller = {'unattended': False}
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: caller['unattended'])
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client(), vault, caller


def _accts(pid):
    return (_desk.get_presence(pid) or {}).get('accounts') or []


def _by_id(client):
    return {a['id']: a for a in client.get('/api/desk/accounts').get_json()}


def _create(client, **body):
    return client.post('/api/desk/accounts', json=body)


# -- presence -> workspace migration -------------------------------------------

def test_lift_copies_presence_accounts_once(env):
    client, _, _ = env
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a', 'read_via': 'api', 'browser_profile': 'main'},
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'A', 'capability': 'direct'},
        'bare-id-only',
        {'channel_id': 'no-platform'}]})
    accounts = _by_id(client)
    assert sorted(accounts) == ['ch-li', 'ch-x']          # bare id and platformless skipped
    assert accounts['ch-x']['read_via'] == 'api' and accounts['ch-x']['browser_profile'] == 'main'
    assert accounts['ch-x']['capability'] == 'manual'      # never claims a direct route it was not given
    assert accounts['ch-li']['capability'] == 'direct'


def test_lift_is_idempotent_and_never_overwrites_an_edit(env):
    client, _, _ = env
    _desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'}]})
    first = client.get('/api/desk/accounts').get_json()
    assert client.get('/api/desk/accounts').get_json() == first    # two reads of one store agree
    r = client.patch('/api/desk/accounts/ch-x', json={'label': 'Ron on X', 'voice': 'plain'})
    assert r.status_code == 200
    # a later presence write still carries the stale copy; the lift must not touch the edit
    _desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'}]})
    after = _by_id(client)['ch-x']
    assert after['label'] == 'Ron on X' and after['voice'] == 'plain'
    with _desk._store_lock:
        store = _desk._read_store()
    assert list(store['accounts']) == ['ch-x']
    before = dict(store['accounts'])
    _desk._migrate_store(store)
    _desk._migrate_store(store)
    assert store['accounts'] == before
    assert store['presences']['alpha']['accounts']          # the presence copy stays


def test_lift_leaves_first_presence_record_for_a_shared_id(env):
    client, _, _ = env
    _desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@alpha'}]})
    _desk.upsert_presence('beta', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@beta'}]})
    assert _by_id(client)['ch-x']['identity'] == '@alpha'


# -- derived publish state -------------------------------------------------------

def test_x_publish_reads_the_vault_every_time(env):
    client, vault, _ = env
    assert _create(client, platform='x', identity='@ron', id='ch-x').status_code == 201
    pub = _by_id(client)['ch-x']['publish']
    assert pub['ready'] is False and 'no X API token' in pub['reason'] and pub['secret'] == 'x.oauth-token'
    assert _by_id(client)['ch-x']['connected'] is False
    vault.add('x.oauth-token', allow_unattended=False)
    ch = _by_id(client)['ch-x']
    assert ch['publish'] == {'ready': True, 'reason': None, 'secret': 'x.oauth-token', 'unattended_ok': False}
    assert ch['connected'] is True
    vault.unreadable.add('x.oauth-token')
    assert _by_id(client)['ch-x']['publish']['ready'] is False
    del vault.entries['x.oauth-token']
    assert _by_id(client)['ch-x']['publish']['ready'] is False


def test_publish_is_never_stored(env):
    client, vault, _ = env
    vault.add('x.oauth-token')
    _create(client, platform='x', identity='@ron', id='ch-x')
    with _desk._store_lock:
        rec = _desk._read_store()['accounts']['ch-x']
    assert 'publish' not in rec and 'connected' not in rec


def test_linkedin_page_is_not_connected_whatever_the_vault_holds(env, monkeypatch):
    client, vault, _ = env
    vault.add('linkedin.oauth-token')
    _create(client, platform='linkedin', identity='Clayrune', id='ch-li')
    li = _by_id(client)['ch-li']
    assert li['publish']['ready'] is False and li['connected'] is False
    assert li['publish']['reason'] == 'LinkedIn app review pending (w_organization_social)'
    # the one switch, flipped when LinkedIn approves the scope, makes it depend on the
    # organization id (account) and the token (vault)
    monkeypatch.setattr(_accounts, 'LINKEDIN_ORG_POSTING_APPROVED', True)
    no_org = _by_id(client)['ch-li']['publish']
    assert no_org['ready'] is False and 'organization id' in no_org['reason']
    assert client.patch('/api/desk/accounts/ch-li', json={'organization_id': '12345678'}).status_code == 200
    assert _by_id(client)['ch-li']['publish']['ready'] is True
    assert _by_id(client)['ch-li']['organization_id'] == '12345678'
    assert client.patch('/api/desk/accounts/ch-li', json={'organization_id': 'urn:li:organization:1'}).status_code == 400
    del vault.entries['linkedin.oauth-token']
    assert _by_id(client)['ch-li']['publish']['ready'] is False


def test_blog_is_manual_and_ready_preview_is_not(env):
    client, _, _ = env
    assert _create(client, platform='blog', identity='clayrune.io/blog', id='ch-blog').status_code == 201
    assert _by_id(client)['ch-blog']['publish'] == {'ready': True, 'reason': None, 'secret': None, 'unattended_ok': None}
    prev = _accounts.v1_account({'id': 'p', 'platform': 'x', 'identity': '@p', 'preview': True, 'capability': 'direct'}, {})
    assert prev['publish']['ready'] is False and prev['preview'] is True


# -- M3 create ---------------------------------------------------------------------

def test_create_refuses_other_platforms_and_bad_input(env):
    client, _, _ = env
    for plat in ('discord', 'reddit', 'drive', 'dropbox', None):
        r = _create(client, platform=plat, identity='x')
        assert r.status_code == 400, plat
    assert _create(client, platform='x', identity='  ').status_code == 400
    assert _create(client, platform='x', identity='a' * 81).status_code == 400
    assert _create(client, platform='x', identity='@a', capability='sideways').status_code == 400
    assert _create(client, platform='blog', identity='b', capability='direct').status_code == 400
    assert _create(client, platform='x', identity='@a', id='bad id!').status_code == 400
    assert client.get('/api/desk/accounts').get_json() == []


def test_create_defaults_and_duplicate(env):
    client, _, _ = env
    r = _create(client, platform='x', identity='@ron')
    assert r.status_code == 201
    acc = r.get_json()
    assert acc['id'].startswith('acct') and acc['capability'] == 'direct' and acc['label'].endswith('@ron')
    assert _create(client, platform='x', identity='@RON').status_code == 409      # same identity, same platform
    assert _create(client, platform='linkedin', identity='@ron').status_code == 201
    assert _create(client, platform='x', identity='@other', id=acc['id']).status_code == 409


def test_no_route_takes_or_returns_a_credential(env):
    client, vault, _ = env
    vault.add('x.oauth-token')
    r = _create(client, platform='x', identity='@ron', id='ch-x', token='hunter2', value='hunter2',
                secret='hunter2', password='hunter2')
    assert r.status_code == 201
    raw = r.get_data(as_text=True) + client.get('/api/desk/accounts').get_data(as_text=True)
    assert 'hunter2' not in raw
    with _desk._store_lock:
        stored = _desk._read_store()['accounts']['ch-x']
    assert 'hunter2' not in str(stored) and set(stored) == {
        'id', 'platform', 'identity', 'label', 'capability', 'voice', 'created_at', 'credentials'}
    assert stored['credentials'] == {'oauth_vault': 'oauth.x', 'oauth_profile': 'desk-x'}      # the first X account: legacy names
    assert set(_by_id(client)['ch-x']['publish']) == {'ready', 'reason', 'secret', 'unattended_ok'}
    # a secret is reachable only by NAME in `publish.secret`; no field carries a value
    assert _by_id(client)['ch-x']['publish']['secret'] == 'x.oauth-token'


def test_unattended_caller_cannot_create_or_delete(env):
    client, _, caller = env
    _create(client, platform='x', identity='@ron', id='ch-x')
    caller['unattended'] = True
    assert _create(client, platform='x', identity='@new').status_code == 403
    assert client.delete('/api/desk/accounts/ch-x').status_code == 403
    assert client.patch('/api/desk/accounts/ch-x', json={'read_via': 'api'}).status_code == 403
    assert client.patch('/api/desk/accounts/ch-x', json={'browser_profile': 'main'}).status_code == 403
    assert client.patch('/api/desk/accounts/ch-x', json={'label': 'Renamed'}).status_code == 200
    caller['unattended'] = False
    assert sorted(_by_id(client)) == ['ch-x'] and _by_id(client)['ch-x']['label'] == 'Renamed'


# -- M4 patch -------------------------------------------------------------------

def test_patch_label_voice_and_refusals(env):
    client, _, _ = env
    _create(client, platform='x', identity='@ron', id='ch-x')
    _create(client, platform='blog', identity='b', id='ch-b')
    r = client.patch('/api/desk/accounts/ch-x', json={'label': 'Ron', 'voice': 'plain, first person'})
    assert r.status_code == 200 and r.get_json()['label'] == 'Ron'
    assert client.patch('/api/desk/accounts/ch-x', json={'platform': 'linkedin'}).status_code == 400
    assert client.patch('/api/desk/accounts/ch-x', json={'capability': 'direct'}).status_code == 400
    assert client.patch('/api/desk/accounts/ch-x', json={'label': ''}).status_code == 400
    assert client.patch('/api/desk/accounts/ch-x', json={'read_via': 'carrier-pigeon'}).status_code == 400
    assert client.patch('/api/desk/accounts/ch-x', json={'browser_profile': 'Bad Name!'}).status_code == 400
    assert client.patch('/api/desk/accounts/ch-b', json={'read_via': 'api'}).status_code == 400   # blog has no read route
    assert client.patch('/api/desk/accounts/nope', json={'label': 'x'}).status_code == 404


def test_read_setting_reaches_the_presence_copy_engagement_reads(env):
    client, _, _ = env
    _desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'}]})
    r = client.patch('/api/desk/accounts/ch-x', json={'read_via': 'api', 'browser_profile': ' Main '})
    assert r.status_code == 200
    body = r.get_json()
    assert body['read_via'] == 'api' and body['browser_profile'] == 'main'
    copy = _accts('alpha')[0]
    assert copy['read_via'] == 'api' and copy['browser_profile'] == 'main'
    client.patch('/api/desk/accounts/ch-x', json={'browser_profile': ''})
    assert 'browser_profile' not in _by_id(client)['ch-x']
    assert 'browser_profile' not in _accts('alpha')[0]


def test_patch_project_id_files_a_presence_copy_for_an_unbound_account(env):
    client, _, _ = env
    _create(client, platform='x', identity='@ron', id='ch-x')
    assert _desk.get_presence('beta') is None                      # no project reads it yet
    client.patch('/api/desk/accounts/ch-x', json={'read_via': 'pane', 'project_id': 'beta'})
    copies = _accts('beta')
    assert [(c['channel_id'], c['platform'], c['read_via']) for c in copies] == [('ch-x', 'x', 'pane')]
    client.patch('/api/desk/accounts/ch-x', json={'read_via': 'api', 'project_id': 'beta'})   # no second copy
    assert len(_accts('beta')) == 1


def test_legacy_presence_read_route_writes_through_to_the_workspace(env):
    client, _, _ = env
    _desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'}]})
    r = client.patch('/api/desk/presence/alpha/accounts/ch-x/read', json={'read_via': 'api', 'platform': 'x'})
    assert r.status_code == 200
    assert _by_id(client)['ch-x']['read_via'] == 'api'
    # an account that exists nowhere yet is created by the legacy route in BOTH places
    r = client.patch('/api/desk/presence/alpha/accounts/ch-new/read', json={'read_via': 'pane', 'platform': 'x'})
    assert r.status_code == 200
    assert _by_id(client)['ch-new']['read_via'] == 'pane'


# -- M5 delete ------------------------------------------------------------------

def _campaign_using(client, account_id, state='draft'):
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': state, 'projectId': 'alpha', 'rules': {}, 'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'Launch', 'accounts': [account_id], 'cadence': {}, 'end': {}}})
    assert r.status_code == 201, r.get_json()


def test_delete_blocked_while_a_campaign_places_it(env):
    client, _, _ = env
    _create(client, platform='x', identity='@ron', id='ch-x')
    _campaign_using(client, 'ch-x')
    r = client.delete('/api/desk/accounts/ch-x')
    assert r.status_code == 409 and 'Launch' in r.get_json()['error']
    assert 'ch-x' in _by_id(client)
    client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'state': 'archived'})
    assert client.delete('/api/desk/accounts/ch-x').status_code == 200


def test_delete_blocked_while_a_piece_version_is_live_on_it(env):
    client, _, _ = env
    _create(client, platform='x', identity='@ron', id='ch-x')
    _create(client, platform='blog', identity='b', id='ch-b')
    _campaign_using(client, 'ch-b')
    pid = client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'post', 'title': 'P'}).get_json()['id']
    vid = client.post(f'/api/desk/pieces/{pid}/versions', json={'account_id': 'ch-x'}).get_json()['versions'][-1]['id']
    r = client.delete('/api/desk/accounts/ch-x')
    assert r.status_code == 409 and 'version' in r.get_json()['error']
    client.patch(f'/api/desk/pieces/{pid}/versions/{vid}', json={'state': 'archived'})
    assert client.delete('/api/desk/accounts/ch-x').status_code == 200


def test_delete_drops_presence_copies_so_nothing_is_lifted_back(env):
    client, _, _ = env
    _desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'},
                                                 {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'A'}]})
    assert sorted(_by_id(client)) == ['ch-li', 'ch-x']
    assert client.delete('/api/desk/accounts/ch-x').status_code == 200
    assert sorted(_by_id(client)) == ['ch-li']                     # not re-lifted on the next read
    assert [a['channel_id'] for a in _accts('alpha')] == ['ch-li']
    assert client.delete('/api/desk/accounts/ch-x').status_code == 404


# -- the read model and the piece store follow the account store -----------------

def test_workspace_and_pieces_use_the_account_store(env):
    client, vault, _ = env
    vault.add('x.oauth-token')
    _create(client, platform='x', identity='@ron', id='ch-x')
    ws = client.get('/api/desk/workspace').get_json()
    assert [a['id'] for a in ws['accounts']] == ['ch-x']
    assert ws['accounts'][0]['publish']['ready'] is True and ws['accounts'][0]['connected'] is True
    _campaign_using(client, 'ch-x')
    pid = client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'post', 'title': 'P'}).get_json()['id']
    ok = client.post(f'/api/desk/pieces/{pid}/versions', json={'account_id': 'ch-x'})
    assert ok.status_code == 201
    bad = client.post(f'/api/desk/pieces/{pid}/versions', json={'account_id': 'ch-ghost'})
    assert bad.status_code == 400
