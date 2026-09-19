"""tests/conftest.py must stop any test from launching a real model CLI.

A leaked rollover respawn thread launched the real claude.exe (production
flags, handoff prompt on stdin) in 5 of 10 runs before the guard existed
(docs/_journal/rollover-not-firing.md). Precedent: af7e0a3.
"""
import subprocess
import sys

import pytest

from tests import conftest


@pytest.fixture()
def expect_blocked():
    """Lets a test provoke the guard without failing at the autouse teardown."""
    before = len(conftest._REAL_CLI_ATTEMPTS)
    yield
    del conftest._REAL_CLI_ATTEMPTS[before:]


@pytest.mark.parametrize('argv', [
    ['claude', '-p', 'hi'],
    [r'C:\Users\x\.local\bin\claude.exe', '--dangerously-skip-permissions'],
    ['/usr/local/bin/codex', 'exec'],
    ['gemini.cmd', '--yolo', '-p', 'hi'],
    ['qwen'],
])
def test_real_cli_argv_is_blocked_and_recorded(expect_blocked, argv):
    with pytest.raises(conftest.RealCliSpawnBlocked):
        subprocess.Popen(argv)
    assert len(conftest._REAL_CLI_ATTEMPTS) >= 1


def test_shell_string_and_run_are_blocked_too(expect_blocked):
    with pytest.raises(conftest.RealCliSpawnBlocked):
        subprocess.run('claude -p hi', shell=True)
    with pytest.raises(conftest.RealCliSpawnBlocked):
        subprocess.run(['claude', '--version', '-p', 'hi'])


def test_a_reference_captured_before_a_monkeypatch_is_still_guarded(expect_blocked):
    real = subprocess.Popen     # what test_midturn_rollover's _REAL_POPEN is
    with pytest.raises(conftest.RealCliSpawnBlocked):
        real(['claude'])


def test_a_bare_version_probe_is_not_a_model_launch():
    """Provider detection runs `<cli> --version`; that starts no session, so it
    is not blocked."""
    before = len(conftest._REAL_CLI_ATTEMPTS)
    assert conftest._is_version_probe(['claude', '--version'])
    assert not conftest._is_version_probe(['claude', '--version', '-p', 'hi'])
    assert not conftest._is_version_probe(['claude'])
    assert len(conftest._REAL_CLI_ATTEMPTS) == before


def test_an_unrelated_process_still_runs():
    out = subprocess.run([sys.executable, '-c', 'print("ok")'],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert out.stdout.strip() == 'ok'


def test_a_blocked_attempt_is_recorded_even_if_the_raise_is_swallowed():
    """The record is what fails a test whose thread swallowed the raise."""
    before = len(conftest._REAL_CLI_ATTEMPTS)
    try:
        subprocess.Popen(['claude'])
    except conftest.RealCliSpawnBlocked:
        pass
    assert len(conftest._REAL_CLI_ATTEMPTS) == before + 1
    del conftest._REAL_CLI_ATTEMPTS[before:]
