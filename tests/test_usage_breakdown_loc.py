"""MC-998 Phase 3: LOC attribution (docs/USAGE_BREAKDOWN_SPEC.md "Trigger and
code attribution"). Pins `agent_routes._compute_code_delta` against a REAL
throwaway git repo + worktree, mirroring `tests/test_agent_worktree.py`'s
fixture pattern — the bug class here (miscounted or double-counted lines) can
only be caught against real `git diff --numstat` output, not a mock.

Acceptance check 3 (spec): "A shared dirty worktree yields LOC unavailable,
while an isolated branch with one source-code insertion and one deletion
reports added 1, deleted 1."
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if sys.platform == 'win32':
    # This module is the only one that spawns real `git` subprocesses
    # (through both the test's own helper and mc.agent_worktree/project_sync
    # in production). In a headless/no-console run the process's Win32
    # standard-input handle can be invalid, which makes every child spawn
    # fail with WinError 6 before git ever runs — unrelated to the code
    # under test. Fenn's MC-998 review hit and diagnosed the same thing,
    # calling it "a harness limitation, not evidence the branch's LOC
    # runtime cannot run on Windows," and worked around it the same way:
    # give the process a real, inheritable stdin handle. `os.dup2` alone
    # only fixes the C-runtime fd table, not the Win32 STD_INPUT_HANDLE
    # that subprocess actually duplicates, so set that directly.
    try:
        import ctypes
        devnull_handle = ctypes.windll.kernel32.CreateFileW(
            'NUL', 0x80000000, 1, None, 3, 0, None)  # GENERIC_READ, OPEN_EXISTING
        if devnull_handle and devnull_handle != -1:
            ctypes.windll.kernel32.SetStdHandle(-10, devnull_handle)  # STD_INPUT_HANDLE
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    except OSError:
        pass

from mc import project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402


def _git(cwd, *args, check=True):
    # stdin=DEVNULL: this harness's own stdin handle is sometimes invalid
    # when running headless on Windows, which makes subprocess fail
    # inheriting it (WinError 6) before git even runs — unrelated to the
    # code under test (Fenn's MC-998 review hit and confirmed the same).
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                        stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture(scope='module', autouse=True)
def _register():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    w.register(lambda p, m: None, lambda p: None, lambda m: None)


@pytest.fixture
def repo():
    """Throwaway git repo on branch 'master' with one shared source file and
    one excluded (docs) file — never the real Clayrune working tree."""
    d = Path(tempfile.mkdtemp(prefix='loc_test_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text(
        'def one():\n    return "orig one"\n\n\ndef two():\n    return "orig two"\n',
        encoding='utf-8')
    (d / 'README.md').write_text('# notes\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'testproj', 'project_path': str(repo)}


@pytest.fixture
def env(project, monkeypatch):
    monkeypatch.setattr(ar, 'load_project',
                        lambda pid: project if pid == project['id'] else None)
    return project


def _session(sid, project_id, isolated):
    s = {'session_id': sid, 'project_id': project_id}
    if isolated:
        s['_worktree_isolated'] = True
    return s


# ── not isolated: always unavailable ────────────────────────────────────────

def test_shared_tree_session_is_unavailable(env, project):
    result = ar._compute_code_delta(_session('s1', project['id'], isolated=False))
    assert result['status'] == 'unavailable'
    assert 'shared' in result['reason'].lower()


def test_unknown_project_is_unavailable(env):
    result = ar._compute_code_delta(_session('s2', 'no-such-project', isolated=True))
    assert result['status'] == 'unavailable'
    assert 'project not found' in result['reason']


# ── isolated worktree: real numstat ─────────────────────────────────────────

def test_one_insertion_and_one_deletion_reports_added_1_deleted_1(env, project, repo):
    sid = 'mb1'
    ok, path = w.create(project, sid)
    assert ok, path
    (Path(path) / 'app.py').write_text(
        'def one():\n    return "orig one"\n\n\ndef two():\n    return "NEW two"\n'
        'def three():\n    return "added"\n',
        encoding='utf-8')
    _git(path, 'add', '.')
    _git(path, 'commit', '-q', '-m', 'edit')

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 3   # "NEW two" line + two new "def three" lines
    assert result['deleted'] == 1  # the replaced "orig two" line
    assert result['branch'] == w.branch_name(sid)
    assert result['base_commit']
    assert result['head_commits']  # at least one commit SHA recorded


def test_uncommitted_edit_is_still_counted(env, project):
    """The diff is taken against the working tree, not just HEAD — an agent
    session that ends without a final commit must not read as `Unavailable`
    or silently drop its uncommitted lines."""
    sid = 'mb2'
    ok, path = w.create(project, sid)
    assert ok, path
    (Path(path) / 'app.py').write_text(
        'def one():\n    return "orig one"\n\n\ndef two():\n    return "orig two"\n'
        'def three():\n    return "uncommitted"\n',
        encoding='utf-8')
    # deliberately NOT committed

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 2
    assert result['deleted'] == 0


def test_docs_only_change_reports_zero_but_status_ok(env, project):
    """README.md is excluded by extension (spec's LOC table); a doc-only
    session must not read as `Unavailable` -- it genuinely has zero
    attributable source lines, which is a real ok 0, not missing data."""
    sid = 'mb3'
    ok, path = w.create(project, sid)
    assert ok, path
    (Path(path) / 'README.md').write_text('# notes\n\nmore notes\n', encoding='utf-8')
    _git(path, 'add', '.')
    _git(path, 'commit', '-q', '-m', 'docs')

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 0
    assert result['deleted'] == 0


def test_no_changes_reports_zero(env, project):
    sid = 'mb4'
    ok, path = w.create(project, sid)
    assert ok, path

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 0
    assert result['deleted'] == 0
    assert result['head_commits'] == ''


# ── review findings (baseline durability, untracked files, vendor paths) ────

def test_baseline_survives_base_branch_landing_the_sessions_own_commits(env, project, repo):
    """MC-998 review finding #5: merge-base(HEAD, base_ref) recomputed at
    completion time collapses to HEAD once base_ref (master) has been
    advanced to include the session's own commits — exactly what happens
    when an agent follows the project's own "land your work" instruction
    before the chat ends. The frozen baseline captured at worktree creation
    must still report the real diff afterward."""
    sid = 'mb5'
    ok, path = w.create(project, sid)
    assert ok, path
    (Path(path) / 'app.py').write_text(
        'def one():\n    return "orig one"\n\n\ndef two():\n    return "orig two"\n'
        'def three():\n    return "added"\n',
        encoding='utf-8')
    _git(path, 'add', '.')
    _git(path, 'commit', '-q', '-m', 'edit')

    # Simulate landing: fast-forward master to the session branch's tip, as
    # `_worktree_merge_back_on_end` would.
    head = _git(path, 'rev-parse', 'HEAD')
    _git(repo, 'merge', '--ff-only', head)
    assert _git(repo, 'rev-parse', 'HEAD') == head

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 2  # unchanged from before master advanced
    assert result['deleted'] == 0
    assert result['head_commits']


def test_untracked_new_file_is_counted(env, project):
    """A new file the agent created but never `git add`ed is invisible to
    `git diff` entirely — must not silently report zero (MC-998 review
    finding #5)."""
    sid = 'mb6'
    ok, path = w.create(project, sid)
    assert ok, path
    (Path(path) / 'new_module.py').write_text(
        'def helper():\n    return 1\n\n\ndef other():\n    return 2\n',
        encoding='utf-8')
    # deliberately left untracked

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 6  # every newline-terminated line in the file
    assert result['deleted'] == 0


def test_vendor_path_is_excluded_even_though_extension_matches(env, project):
    """Staged lines under a vendor directory must not count as session
    source, even though `.js` is an attributed extension (MC-998 review
    finding #5)."""
    sid = 'mb7'
    ok, path = w.create(project, sid)
    assert ok, path
    vendor_dir = Path(path) / 'vendor'
    vendor_dir.mkdir()
    (vendor_dir / 'dependency.js').write_text(
        'line one;\nline two;\nline three;\n', encoding='utf-8')
    _git(path, 'add', '.')
    _git(path, 'commit', '-q', '-m', 'vendor drop')

    result = ar._compute_code_delta(_session(sid, project['id'], isolated=True))
    assert result['status'] == 'ok', result
    assert result['added'] == 0
    assert result['deleted'] == 0


def test_missing_worktree_is_unavailable(env, project):
    """The worktree was never created (e.g. isolation attempted and failed
    upstream, but the flag leaked through) -- must degrade to unavailable,
    never raise out of the completion hook."""
    result = ar._compute_code_delta(_session('never-created', project['id'], isolated=True))
    assert result['status'] == 'unavailable'
    assert 'worktree missing' in result['reason']
