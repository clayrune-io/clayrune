"""A node.exe planted in an approved repository must never run in place of the real Node (Windows
looks in the current directory before PATH for a bare name)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from mc.desk_connect import github_activation as activation, custom_npm_gate, custom_npm_node_paths

NT = os.name == 'nt'


def _real_node_dir(tmp_path):
    """A PATH folder holding a stand-in 'node' that is not the planted one."""
    real = tmp_path / 'realbin'
    real.mkdir()
    if NT:
        (real / 'node.cmd').write_text('@echo real\r\n')
    else:
        (real / 'node').write_text('#!/bin/sh\necho real\n')
        (real / 'node').chmod(0o755)
    return real


def _plant(repo):
    repo.mkdir()
    if NT:
        who = shutil.which('whoami')
        assert who, 'whoami.exe needed for the planted-exe fixture'
        shutil.copy(who, repo / 'node.exe')
    else:
        (repo / 'node').write_text('#!/bin/sh\necho planted > planted.marker\n')
        (repo / 'node').chmod(0o755)
    (repo / 'node.cmd').write_text('@echo planted > planted.marker\r\n')


def test_planted_node_is_not_resolved(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    _plant(repo)
    real = _real_node_dir(tmp_path)
    monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)
    monkeypatch.setenv('PATH', os.pathsep.join(['.', str(repo), '', str(real)]))
    monkeypatch.chdir(repo)
    got = Path(activation.resolve_command('node', str(repo)))
    assert got.parent == real.resolve() or got.parent == real
    assert repo.resolve() not in got.resolve().parents
    assert not (repo / 'planted.marker').exists()


def test_unresolvable_node_fails_clearly_and_never_runs_plant(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    _plant(repo)
    monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    monkeypatch.chdir(repo)
    with pytest.raises(RuntimeError, match='could not find "node"'):
        activation.resolve_command('node', str(repo))
    assert not (repo / 'planted.marker').exists()


def test_paths_with_a_directory_are_left_alone():
    assert activation.resolve_command(os.path.join('x', 'node'), 'd') == os.path.join('x', 'node')


@pytest.mark.skipif(not NT, reason='current-directory lookup is the Windows behaviour')
def test_resolved_command_runs_real_not_planted_exe(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    _plant(repo)
    real = _real_node_dir(tmp_path)
    monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)
    monkeypatch.setenv('PATH', str(real))
    monkeypatch.chdir(repo)
    cmd = activation.resolve_command('node', str(repo))
    done = subprocess.run([cmd], cwd=repo, capture_output=True, text=True, shell=False) if not cmd.lower().endswith('.cmd') \
        else subprocess.run(['cmd', '/c', cmd], cwd=repo, capture_output=True, text=True)
    assert done.stdout.strip() == 'real'
    assert not (repo / 'planted.marker').exists()


def test_start_launches_absolute_node_with_cwd_and_no_chdir(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    _plant(repo)
    real = _real_node_dir(tmp_path)
    monkeypatch.setenv('PATH', os.pathsep.join([str(repo), str(real)]))
    monkeypatch.chdir(repo)
    here = os.getcwd()
    op = {'command': 'node', 'args': ['server.js'], 'directory': str(repo), 'credentials': [], 'strip_env': []}
    rec = {'fingerprint': 'f', 'state': 'registered', 'operation': op}
    monkeypatch.setattr(activation.store, 'get', lambda *a: rec)
    monkeypatch.setattr(activation.operation, 'fingerprint', lambda o: 'f')
    monkeypatch.setattr(activation, 'problem', lambda o: '')
    probed = []
    monkeypatch.setattr(custom_npm_gate, 'ancestor_problem', lambda *a: None)
    monkeypatch.setattr(custom_npm_node_paths, 'problem', lambda o, rest: probed.append(rest) or None)
    seen = {}
    monkeypatch.setattr(activation, '_launch', lambda command, argv, env, cwd=None: seen.update(command=command, argv=argv, cwd=cwd))
    activation.start('project', 'p', 'n', 'f')
    assert Path(seen['command']).is_absolute() and Path(seen['command']).parent != repo
    assert seen['argv'][0] == seen['command'] and seen['cwd'] == str(repo)
    assert probed == [['--', seen['command']]]
    assert os.getcwd() == here


@pytest.mark.skipif(not NT, reason='Windows only')
def test_preflight_sets_current_dir_guard_on_windows_and_user_value_wins(monkeypatch):
    from mc import preflight
    monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)
    preflight.harden_windows_search()
    assert os.environ['NoDefaultCurrentDirectoryInExePath'] == '1'
    monkeypatch.setenv('NoDefaultCurrentDirectoryInExePath', '0')
    preflight.harden_windows_search()
    assert os.environ['NoDefaultCurrentDirectoryInExePath'] == '0'


def test_preflight_leaves_env_alone_off_windows(monkeypatch):
    from mc import preflight
    monkeypatch.setattr(preflight.os, 'name', 'posix')
    monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)
    preflight.harden_windows_search()
    assert 'NoDefaultCurrentDirectoryInExePath' not in os.environ


def test_gate_process_started_inside_repo_runs_real_node_and_probes_it(tmp_path):
    """The start check launches the gate with cwd = the repository, so the gate's OWN current directory is
    the repo (Windows searches that for a bare name). Drive start() in such a process, variable unset."""
    repo = tmp_path / 'repo'
    _plant(repo)
    real = _real_node_dir(tmp_path)
    (tmp_path / 'driver.py').write_text(
        "import sys, os\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from mc.desk_connect import github_activation as a, custom_npm_gate as g, custom_npm_node_paths as n\n"
        f"op={{'command':'node','args':[],'directory':{str(repo)!r},'credentials':[],'strip_env':[]}}\n"
        "a.store.get=lambda *x:{'fingerprint':'f','state':'registered','operation':op}\n"
        "a.operation.fingerprint=lambda o:'f'\n"
        "a.problem=lambda o:''\n"
        "g.ancestor_problem=lambda *x:None\n"
        "def probe(node, env):\n"
        "    print('PROBE '+node, flush=True); return []\n"
        "n.run_probe=probe\n"
        "a.start('project','p','n','f')\n")
    env = {k: v for k, v in os.environ.items() if k.lower() != 'nodefaultcurrentdirectoryinexepath'}
    env['PATH'] = os.pathsep.join([str(real), os.path.dirname(sys.executable)])
    from mc.desk_connect import github_start_check as check
    result = check.handshake([sys.executable, '-I', str(tmp_path / 'driver.py')], str(repo), deadline_s=15)
    done = subprocess.run([sys.executable, '-I', str(tmp_path / 'driver.py')], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=30)
    assert not (repo / 'planted.marker').exists()
    lines = done.stdout.splitlines()
    probe = [l for l in lines if l.startswith('PROBE ')][0][6:]
    assert Path(probe).is_absolute() and repo.resolve() not in Path(probe).resolve().parents
    assert 'real' in lines and 'whoami' not in done.stdout.lower()
    assert isinstance(result, dict)
