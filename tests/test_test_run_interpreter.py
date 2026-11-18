"""Launcher interpreter-dependency warning (backlog dbbd02c0 / 2982701a).

The launcher must say so when the child python lacks the project's deps, and
must not change the child's argv, interpreter or exit code while doing it.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from test_test_run_launcher import api, invoke, family  # noqa: F401  (api is a fixture)

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
interp = importlib.import_module('test_run_interpreter')


@pytest.fixture(scope='module')
def bare_python(tmp_path_factory):
    """A real interpreter with NO psutil and NO pytest: a dependency-free venv."""
    root = tmp_path_factory.mktemp('barevenv')
    venv.create(root, with_pip=False, system_site_packages=False)
    exe = root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    probe = subprocess.run([str(exe), '-c', 'import importlib.util as u;'
                            'print(u.find_spec("psutil") is None, u.find_spec("pytest") is None)'],
                           capture_output=True, text=True, timeout=60)
    assert probe.stdout.strip() == 'True True', probe  # the fixture's own premise
    return str(exe)


def test_is_python_argv_recognises_only_direct_python():
    for argv in (['python'], ['python3'], ['python3.12'], ['python.exe'], ['C:/x/Scripts/python.EXE', '-m', 'pytest']):
        assert interp.is_python_argv(argv), argv
    for argv in (['pytest'], ['py', '-3'], ['node', 'a.mjs'], ['pythonista'], [], 'python -m pytest'):
        assert not interp.is_python_argv(argv), argv


def test_check_reports_each_missing_module_and_names_the_interpreter(bare_python, tmp_path):
    found = interp.check([bare_python, '-m', 'pytest'], str(tmp_path), os.environ.copy())
    assert [code for code, _ in found] == ['interpreter_missing_psutil', 'interpreter_missing_pytest']
    for _, message in found:
        assert 'barevenv' in message and sys.executable in message  # child exe + launcher exe


def test_check_is_silent_when_deps_present_or_not_python(tmp_path):
    env = os.environ.copy()
    assert interp.check([sys.executable, '-c', 'pass'], str(tmp_path), env) == []
    assert interp.check(['node', 'x.mjs'], str(tmp_path), env) == []


def test_check_never_raises_and_does_not_double_report_a_missing_executable(tmp_path):
    assert interp.check([str(tmp_path / 'no' / 'python')], str(tmp_path), os.environ.copy()) == []
    # A bad cwd must come back as a list (the real start reports it), never an exception.
    assert isinstance(interp.check([sys.executable], str(tmp_path / 'not-a-dir'), os.environ.copy()), list)


def test_probe_failure_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(interp, 'PROBE_TIMEOUT_SECONDS', 0.001)
    found = interp.check([sys.executable], str(tmp_path), os.environ.copy())
    assert [code for code, _ in found] == ['interpreter_probe_failed']


def test_launcher_warns_in_stderr_and_report_but_keeps_exit_code_and_output(api, bare_python):
    done = invoke(api, '--', bare_python, '-c', 'print("ran"); raise SystemExit(7)')
    assert done.returncode == 7, done.stderr.decode()  # exit code untouched
    assert done.stdout.strip() == b'ran'               # interpreter was NOT swapped: the bare one ran
    err = done.stderr.decode()
    assert 'interpreter_missing_psutil' in err and 'interpreter_missing_pytest' in err, err
    _, children = family(api, 1)
    codes = [d['code'] for d in children[0]['diagnostics']]
    assert 'interpreter_missing_psutil' in codes and 'interpreter_missing_pytest' in codes, codes


def test_launcher_stays_silent_when_the_child_interpreter_has_the_deps(api):
    done = invoke(api, '--', sys.executable, '-c', 'raise SystemExit(0)')
    assert done.returncode == 0 and done.stderr == b'', done.stderr.decode()
