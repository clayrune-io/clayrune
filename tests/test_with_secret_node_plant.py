"""tools/with-secret.py and tools/custom-mcp-gate.py are started by an engine as MCP servers in the
engine's own working folder. A node.exe planted in that folder must never run in place of the one on PATH,
whether the engine sets NoDefaultCurrentDirectoryInExePath for its children or not."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows current-directory search')


def _exe(name):
    found = shutil.which(name)
    assert found, f'{name}.exe needed for the planted-exe fixture'
    return found


def _out(exe):
    return subprocess.run([exe], capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def planted(tmp_path):
    work = tmp_path / 'engine-cwd'
    work.mkdir()
    shutil.copy(_exe('whoami'), work / 'node.exe')        # the plant prints the user name
    real = tmp_path / 'realbin'
    real.mkdir()
    shutil.copy(_exe('hostname'), real / 'node.exe')      # the "real" node prints the host name
    return work, real


def _env(path_value):
    # Windows upper-cases os.environ keys, so compare that way. Claude Code sets the variable for
    # its own children; drop it so the test models an engine that does not.
    drop = {'NODEFAULTCURRENTDIRECTORYINEXEPATH', 'CLAUDE_CODE_SESSION_ID'}
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    # The stand-in folders go first; the rest of PATH stays so Python itself can load.
    env['PATH'] = os.pathsep.join(p for p in (path_value, os.environ.get('PATH', '')) if p)
    env['PYTHONPATH'] = str(REPO)
    return env


@pytest.mark.parametrize('path_head', ['.', ''], ids=['dot-on-path', 'cwd-only'])
def test_with_secret_runs_the_path_node_not_the_planted_one(planted, tmp_path, path_head):
    work, real = planted
    head = os.pathsep.join(p for p in (path_head, str(real)) if p)
    env = _env(head)
    env['CLAYRUNE_HOME'] = str(tmp_path / '.clayrune')
    proc = subprocess.run([sys.executable, str(REPO / 'tools' / 'with-secret.py'), '--raw', '--', 'node'],
                          cwd=work, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert _out(str(real / 'node.exe')) in proc.stdout
    assert _out(str(work / 'node.exe')) not in proc.stdout


@pytest.mark.parametrize('script', ['with-secret.py', 'custom-mcp-gate.py'])
def test_gate_scripts_switch_off_current_directory_search(script):
    src = (REPO / 'tools' / script).read_text(encoding='utf-8')
    probe = ("import os, runpy, sys; sys.argv=['x','--help']\n"
             f"try: runpy.run_path({str(REPO / 'tools' / script)!r}, run_name='not_main')\n"
             "except SystemExit: pass\n"
             "print(os.environ.get('NoDefaultCurrentDirectoryInExePath'))")
    env = _env('')
    proc = subprocess.run([sys.executable, '-c', probe], env=env, capture_output=True, text=True, timeout=60)
    assert proc.stdout.strip().splitlines()[-1] == '1', (src[:200], proc.stderr)
