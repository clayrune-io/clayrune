"""mc/unattended.py — the shared "is this caller unattended?" helper.

docs/_review/2026-09-10_security.md F1/F5: this logic used to live only in
backup_routes.py, and three other routes that implement a human-only
decision (secrets writes, distiller promote, config PUT) had no gate at all.
Lifted here so there is exactly one definition — the 50-note-cap duplication
bug is the reason that matters, not hypothetically.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc.state import agent_sessions  # noqa: E402
from mc.unattended import is_unattended_caller  # noqa: E402


def setup_function(_fn):
    agent_sessions.clear()


def teardown_function(_fn):
    agent_sessions.clear()


def test_no_running_session_is_attended():
    """The common case: a human clicking a button in the SPA isn't a Claude
    CLI session and has nothing to find in agent_sessions."""
    assert is_unattended_caller() is False


def test_running_manual_session_is_attended():
    agent_sessions['s1'] = {'status': 'running', 'trigger_type': 'manual',
                            'project_id': 'p'}
    assert is_unattended_caller() is False


def test_running_non_manual_session_is_unattended():
    agent_sessions['s1'] = {'status': 'running', 'trigger_type': 'schedule',
                            'project_id': 'p'}
    assert is_unattended_caller() is True


def test_missing_trigger_type_fails_closed():
    agent_sessions['s1'] = {'status': 'running', 'trigger_type': None,
                            'project_id': 'p'}
    assert is_unattended_caller() is True


def test_idle_session_does_not_count():
    agent_sessions['s1'] = {'status': 'idle', 'trigger_type': 'schedule',
                            'project_id': 'p'}
    assert is_unattended_caller() is False


def test_project_scope_isolates_unrelated_project():
    agent_sessions['s1'] = {'status': 'running', 'trigger_type': 'schedule',
                            'project_id': 'other'}
    assert is_unattended_caller(project_id='p') is False


def test_project_scope_none_counts_every_project():
    agent_sessions['s1'] = {'status': 'running', 'trigger_type': 'schedule',
                            'project_id': 'other'}
    assert is_unattended_caller(project_id=None) is True


def test_exactly_one_definition_in_the_repo():
    """A second copy of this function is exactly the failure mode the docs
    for this module warn about (backup_routes.py grew its own first, and the
    50-note-cap bug was two copies that silently drifted). Grep the tracked
    source tree for the def and fail if it appears anywhere but here."""
    import subprocess
    out = subprocess.run(
        ['git', 'grep', '-n', '--untracked',
         r'^def is_unattended_caller\|^    def is_unattended_caller'],
        cwd=REPO, capture_output=True, text=True)
    hits = [line for line in out.stdout.splitlines() if line.strip()]
    assert len(hits) == 1, hits
    assert hits[0].startswith('mc/unattended.py:'), hits
