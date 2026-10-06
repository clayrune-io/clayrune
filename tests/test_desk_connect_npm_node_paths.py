"""Desk user-chosen MCP server: the launch gate refuses when a folder in Node's own global search list exists
(`custom_npm_node_paths`; docs/DESK_SERVICE_PROFILES_SPEC.md, U2b audit follow-up).

The Node-backed cases start the real `node` (skipped where there is none) with the real `subprocess.Popen`
put back; every other test in the desk suite keeps `Popen` poisoned and the probe replaced by a clean box.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mc.desk_connect import custom_npm_gate as gate
from mc.desk_connect import custom_npm_node_paths as node_paths
from tests.test_desk_connect_custom import env  # noqa: F401
from tests.test_desk_connect_npm_closure import _fresh_caches, card_of  # noqa: F401
from tests.test_desk_connect_npm_launch_gate import ARGV, _approved_closure, _self_contained, _start

REAL_POPEN = subprocess.Popen
REAL_PROBE = node_paths.run_probe
NODE = shutil.which('node')
needs_node = pytest.mark.skipif(NODE is None, reason='no node on this machine')
HEAD = ARGV[:7]                                                 # --scope S --project P --name N --
REST = ['with-secret', '--unset', 'NODE_OPTIONS', '--unset', 'NODE_PATH', '--', NODE or 'node', 'x']
LINE = [*HEAD, *REST]


@pytest.fixture()
def real_node(env, monkeypatch, tmp_path):                      # noqa: F811
    monkeypatch.setattr(subprocess, 'Popen', REAL_POPEN)
    monkeypatch.setattr(node_paths, 'run_probe', REAL_PROBE)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv('NODE_PATH', raising=False)
    monkeypatch.delenv('NODE_OPTIONS', raising=False)
    return home


def _own_dirs_exist(home):
    """True when Node's list on this machine already holds an existing folder (the planted ones aside)."""
    return any(Path(p).exists() for p in REAL_PROBE(NODE, dict(os.environ)))


def _stub(monkeypatch, result):
    seen = {}

    def probe(node, env):
        seen.update(node=node, env=dict(env))
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(node_paths, 'run_probe', probe)
    return seen


# ── 1. a planted folder in Node's list stops the launch ─────────────────────

@needs_node
@pytest.mark.parametrize('folder', ['.node_modules', '.node_libraries'])
def test_a_planted_home_folder_node_searches_stops_a_closure_launch(env, real_node, folder):     # noqa: F811
    _approved_closure(env)
    if _own_dirs_exist(real_node):
        pytest.skip('this machine already has a folder in its Node search list')
    assert _start(LINE)[0] == 0                                  # clean: starts
    (real_node / folder).mkdir()
    rc, started = _start(LINE)
    assert rc == gate.EXIT_REFUSED and started == []


@needs_node
def test_a_planted_home_folder_node_searches_stops_a_self_contained_launch(env, real_node):      # noqa: F811
    _self_contained(env)
    if _own_dirs_exist(real_node):
        pytest.skip('this machine already has a folder in its Node search list')
    assert _start(LINE)[0] == 0
    (real_node / '.node_modules').mkdir()
    rc, started = _start(LINE)
    assert rc == gate.EXIT_REFUSED and started == []


@needs_node
def test_the_refusal_names_the_folder(env, real_node, capsys):                                   # noqa: F811
    _self_contained(env)
    (real_node / '.node_modules').mkdir()
    assert _start(LINE)[0] == gate.EXIT_REFUSED
    assert '.node_modules' in capsys.readouterr().err


@needs_node
def test_a_node_path_entry_refuses_when_it_would_reach_the_server(env, real_node, monkeypatch, tmp_path):   # noqa: F811
    _, op = _self_contained(env)
    planted = tmp_path / 'planted'
    planted.mkdir()
    monkeypatch.setenv('NODE_PATH', str(planted))
    keeps = dict(op, strip_env=[])                               # a record that did NOT strip NODE_PATH
    found = node_paths.problem(keeps, REST)
    assert found is not None and found['code'] == 'dependency_changed' and str(planted) in found['paths']
    assert 'planted' in found['message']


@needs_node
def test_a_node_path_entry_the_launch_line_strips_does_not_stop_it(env, real_node, monkeypatch, tmp_path):   # noqa: F811
    """The line unsets NODE_PATH, so it never reaches the server and is not in the probe's environment."""
    _, op = _self_contained(env)
    if _own_dirs_exist(real_node):
        pytest.skip('this machine already has a folder in its Node search list')
    planted = tmp_path / 'planted'
    planted.mkdir()
    monkeypatch.setenv('NODE_PATH', str(planted))
    assert 'NODE_PATH' in op['strip_env']
    assert node_paths.problem(op, REST) is None
    assert _start(LINE)[0] == 0


# ── 2. the probe is run as the server will be, and a probe that cannot answer refuses ──

def test_the_probe_gets_the_servers_environment_and_the_node_of_the_line(env, monkeypatch):      # noqa: F811
    _self_contained(env)
    monkeypatch.setenv('NODE_OPTIONS', '--require ./evil.js')
    monkeypatch.setenv('NODE_PATH', '/elsewhere')
    monkeypatch.setenv('KEEP_ME', 'yes')
    seen = _stub(monkeypatch, [])
    line = [*HEAD, 'py', 'ws', '--unset', 'NODE_OPTIONS', '--unset', 'NODE_PATH', '--', '/the/node', 'x']
    assert _start(line)[0] == 0
    assert seen['node'] == '/the/node'
    assert 'NODE_OPTIONS' not in seen['env'] and 'NODE_PATH' not in seen['env'] and seen['env']['KEEP_ME'] == 'yes'


@pytest.mark.parametrize('failure', [node_paths.ProbeError('Node did not answer within 2 seconds'),
                                     OSError('boom'), ValueError('boom')])
def test_a_probe_that_fails_refuses_and_starts_nothing(env, monkeypatch, failure):               # noqa: F811
    _approved_closure(env)
    _stub(monkeypatch, failure)
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


def test_a_probe_that_times_out_refuses(env, monkeypatch):                                       # noqa: F811
    _self_contained(env)
    monkeypatch.setattr(node_paths, 'run_probe', REAL_PROBE)

    def slow(*a, **k):
        assert k['timeout'] == 2.0
        raise subprocess.TimeoutExpired(a[0], k['timeout'])
    monkeypatch.setattr(subprocess, 'run', slow)
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


@pytest.mark.parametrize('out', [b'', b'not json', b'{"a": 1}', b'[1, 2]', b'[""]', b'[' + b'"x",' * 70 + b'"x"]'])
def test_an_answer_that_is_not_a_list_of_folders_refuses(env, monkeypatch, out):                 # noqa: F811
    _self_contained(env)
    monkeypatch.setattr(node_paths, 'run_probe', REAL_PROBE)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0, out, b''))
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


def test_a_probe_that_exits_non_zero_refuses(env, monkeypatch):                                  # noqa: F811
    _self_contained(env)
    monkeypatch.setattr(node_paths, 'run_probe', REAL_PROBE)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b'[]', b'err'))
    assert _start()[0] == gate.EXIT_REFUSED


def test_a_node_that_is_not_there_refuses(env, monkeypatch, tmp_path):                           # noqa: F811
    _self_contained(env)
    monkeypatch.setattr(node_paths, 'run_probe', REAL_PROBE)

    def gone(*a, **k):
        raise FileNotFoundError('no node')
    monkeypatch.setattr(subprocess, 'run', gone)
    rc, started = _start([*HEAD, 'w', '--', str(tmp_path / 'nope'), 'x'])
    assert rc == gate.EXIT_REFUSED and started == []


# ── 3. a clean box starts ───────────────────────────────────────────────────

def test_a_clean_box_starts_a_closure_package(env, monkeypatch, tmp_path):                       # noqa: F811
    _approved_closure(env)
    _stub(monkeypatch, [str(tmp_path / 'absent' / '.node_modules'), str(tmp_path / 'absent' / 'lib' / 'node')])
    rc, started = _start()
    assert rc == 0 and len(started) == 1


def test_a_listed_folder_that_exists_refuses_for_a_self_contained_package(env, monkeypatch, tmp_path):   # noqa: F811
    _self_contained(env)
    here = tmp_path / 'lib' / 'node'
    here.mkdir(parents=True)
    _stub(monkeypatch, [str(here)])
    rc, started = _start()
    assert rc == gate.EXIT_REFUSED and started == []


def test_the_card_names_the_node_search_folders(env):                                             # noqa: F811
    assert '~/.node_modules' in card_of(env)['install_note']
