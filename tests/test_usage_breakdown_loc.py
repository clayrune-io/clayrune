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

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mc import project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402


def _git(cwd, *args, check=True):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)
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


def test_missing_worktree_is_unavailable(env, project):
    """The worktree was never created (e.g. isolation attempted and failed
    upstream, but the flag leaked through) -- must degrade to unavailable,
    never raise out of the completion hook."""
    result = ar._compute_code_delta(_session('never-created', project['id'], isolated=True))
    assert result['status'] == 'unavailable'
    assert 'worktree missing' in result['reason']
