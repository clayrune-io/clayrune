"""MC-1179 AP43: the vendor context mirror must not make a worktree read dirty.

`sync_vendor_context_file` writes AGENTS.md / GEMINI.md / QWEN.md into the
worktree an isolated Codex, Gemini or Qwen session runs in. A project that does
not ignore those names then saw an untracked (or modified) file in `git status`
and the worktree neither landed nor was cleaned up. Throwaway repos only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mc import project_sync, vendor_context_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f'git {" ".join(args)}: {r.stderr.strip()}'
    return r.stdout.strip()


@pytest.fixture(scope='module', autouse=True)
def _register():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '', Path(tempfile.gettempdir()))
    w.register(lambda p, m: None, lambda p: None, lambda m: None)


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='wt_mirror_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (d / 'CLAUDE.md').write_text('# Clayrune — Claude Code project notes\n\nrules v1\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'mirrorproj', 'project_path': str(repo)}


def _worktree(project, sid):
    _, path = w.create(project, sid)
    return Path(path)


def _sync_mirror(wt, name='AGENTS.md'):
    vendor_context_sync.sync_vendor_context_file(str(wt), name)


def test_an_untracked_mirror_does_not_make_the_worktree_dirty(project):
    wt = _worktree(project, 'mir1')
    _sync_mirror(wt)
    assert (wt / 'AGENTS.md').is_file()
    assert project_sync._dirty(str(wt))            # plain status sees it: the bug
    assert not w.has_uncommitted(project, 'mir1')
    assert not w.dirty_outside_runtime(project, wt)
    assert not w.has_unmerged_work(project, 'mir1')


@pytest.mark.parametrize('name', ['AGENTS.md', 'GEMINI.md', 'QWEN.md'])
def test_every_vendor_mirror_name_is_covered(project, name):
    wt = _worktree(project, 'mir2' + name[:2].lower())
    _sync_mirror(wt, name)
    assert not w.has_uncommitted(project, 'mir2' + name[:2].lower())


def test_a_worktree_with_a_mirror_still_lands(project, repo):
    wt = _worktree(project, 'mir3')
    (wt / 'feature.py').write_text('y = 2\n', encoding='utf-8')
    _git(wt, 'add', 'feature.py')
    _git(wt, 'commit', '-q', '-m', 'feature')
    _sync_mirror(wt)
    status, detail = w.merge_back(project, 'mir3')
    assert status != 'dirty', detail
    assert (repo / 'feature.py').is_file()


def test_real_work_next_to_the_mirror_still_counts(project):
    wt = _worktree(project, 'mir4')
    _sync_mirror(wt)
    (wt / 'notes.md').write_text('forgot to commit\n', encoding='utf-8')
    assert w.has_uncommitted(project, 'mir4')
    assert w.dirty_outside_runtime(project, wt)


def test_an_untracked_file_without_the_marker_is_not_the_mirror(project):
    """An agent's or a person's own AGENTS.md has no generated-from marker."""
    wt = _worktree(project, 'mir5')
    (wt / 'AGENTS.md').write_text('hand written by the agent\n', encoding='utf-8')
    assert w.has_uncommitted(project, 'mir5')
    assert w.dirty_outside_runtime(project, wt)


def _tracked_mirror_repo(repo):
    """A project that commits its generated mirror, then moves CLAUDE.md on."""
    _sync_mirror(repo)
    _git(repo, 'add', 'AGENTS.md')
    _git(repo, 'commit', '-q', '-m', 'track the mirror')
    (repo / 'CLAUDE.md').write_text('# Clayrune — Claude Code project notes\n\nrules v2\n',
                                    encoding='utf-8')
    _git(repo, 'add', 'CLAUDE.md')
    _git(repo, 'commit', '-q', '-m', 'claude rules moved on')


def test_a_regenerated_tracked_mirror_is_ignored(project, repo):
    _tracked_mirror_repo(repo)
    wt = _worktree(project, 'mir6')
    _sync_mirror(wt)                               # rewrites the tracked file
    assert 'AGENTS.md' in _git(wt, 'status', '--porcelain')    # git reports it modified
    assert not w.has_uncommitted(project, 'mir6')
    assert not w.dirty_outside_runtime(project, wt)


def test_an_edited_tracked_mirror_still_counts(project, repo):
    _tracked_mirror_repo(repo)
    wt = _worktree(project, 'mir7')
    _sync_mirror(wt)
    with open(wt / 'AGENTS.md', 'a', encoding='utf-8') as f:
        f.write('an agent added this line\n')
    assert w.has_uncommitted(project, 'mir7')
    assert w.dirty_outside_runtime(project, wt)


def test_a_staged_mirror_still_counts(project):
    wt = _worktree(project, 'mir8')
    _sync_mirror(wt)
    _git(wt, 'add', 'AGENTS.md')
    assert w.has_uncommitted(project, 'mir8')
