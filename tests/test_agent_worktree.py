"""Safety tests for per-agent worktree isolation (b264200a).

Every test runs against a THROWAWAY git repo in a temp dir — never the Clayrune
working tree. The bug under test destroys work, so it must never be reproduced
against real code.

The suite is organised around the properties that must hold before this can be
enabled on the live product:

  A. Isolation      — concurrent agents cannot clobber each other
  B. Runtime parity — an isolated agent can still run/read what it needs
  C. No work loss   — nothing deletes or strands an agent's output
  D. Containment    — a single-agent project is completely unaffected
  E. Fail-safe      — any failure degrades to today's behavior
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mc import project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402


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
    """Throwaway git repo on branch 'master' with one shared source file."""
    d = Path(tempfile.mkdtemp(prefix='wt_test_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text(
        'def one():\n    return "orig one"\n\n\ndef two():\n    return "orig two"\n',
        encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\nsecret.txt\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'testproj', 'project_path': str(repo)}


def _turn(tree: Path, func: str, body: str, think: float = 0.0):
    """One agent turn: read whole file → think → write whole file back.
    Mirrors what an Edit/Write tool does, which is why the loser vanishes."""
    f = Path(tree) / 'app.py'
    content = f.read_text(encoding='utf-8')
    time.sleep(think)
    f.write_text(content.replace(f'return "orig {func}"', f'return "{body}"'),
                 encoding='utf-8')


# ── A. Isolation ────────────────────────────────────────────────────────────

def test_shared_tree_loses_work_baseline(repo):
    """The bug this feature exists to fix. Documents today's behavior: with a
    shared tree, one agent's edit is silently destroyed."""
    t1 = threading.Thread(target=_turn, args=(repo, 'one', 'A', 0.25))
    t2 = threading.Thread(target=_turn, args=(repo, 'two', 'B', 0.10))
    t1.start(); t2.start(); t1.join(); t2.join()
    final = (repo / 'app.py').read_text(encoding='utf-8')
    survived = ('A' in final) + ('B' in final)
    assert survived < 2, 'expected the shared-tree race to lose an edit'


def test_worktrees_preserve_both_agents(project):
    """The fix: concurrent agents in separate worktrees both keep their work."""
    okA, pA = w.create(project, 'agentA')
    okB, pB = w.create(project, 'agentB')
    assert okA and okB, (pA, pB)
    t1 = threading.Thread(target=_turn, args=(pA, 'one', 'AWORK', 0.25))
    t2 = threading.Thread(target=_turn, args=(pB, 'two', 'BWORK', 0.10))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert 'AWORK' in (Path(pA) / 'app.py').read_text(encoding='utf-8')
    assert 'BWORK' in (Path(pB) / 'app.py').read_text(encoding='utf-8')
    # Neither can see the other — that IS the isolation.
    assert 'BWORK' not in (Path(pA) / 'app.py').read_text(encoding='utf-8')


def test_merge_combines_disjoint_edits(project):
    """Different regions of one file merge cleanly — both survive."""
    _, pA = w.create(project, 'a1')
    _, pB = w.create(project, 'b1')
    _turn(pA, 'one', 'AWORK'); _git(pA, 'add', '.'); _git(pA, 'commit', '-q', '-m', 'a')
    _turn(pB, 'two', 'BWORK'); _git(pB, 'add', '.'); _git(pB, 'commit', '-q', '-m', 'b')
    status, _ = w.sync_into(project, 'b1', w.branch_name('a1'))
    assert status == 'clean'
    merged = (Path(pB) / 'app.py').read_text(encoding='utf-8')
    assert 'AWORK' in merged and 'BWORK' in merged


# ── B. Runtime parity ───────────────────────────────────────────────────────

def test_gitignored_runtime_dirs_are_linked_in(project, repo):
    """A worktree gets only TRACKED files. Without linking, an agent lands in a
    tree with no venv/node_modules/data — it can't run tests or read the
    backlog. Regression guard for that."""
    (repo / '.venv').mkdir()
    (repo / '.venv' / 'marker.txt').write_text('real', encoding='utf-8')
    (repo / 'node_modules').mkdir()
    (repo / 'node_modules' / 'pkg.txt').write_text('real', encoding='utf-8')
    ok, path = w.create(project, 'rt1')
    assert ok, path
    assert (Path(path) / '.venv' / 'marker.txt').read_text(encoding='utf-8') == 'real'
    assert (Path(path) / 'node_modules' / 'pkg.txt').exists()


def test_placeholder_dir_is_replaced_not_skipped(project, repo):
    """data/projects arrives from git as a REAL dir holding .gitkeep. A naive
    'already exists' check skips the junction and leaves an empty dir that
    merely LOOKS present. This is the bug that shipped in the first draft."""
    (repo / 'data').mkdir()
    (repo / 'data' / 'projects').mkdir()
    (repo / 'data' / 'projects' / '.gitkeep').write_text('', encoding='utf-8')
    _git(repo, 'add', '-f', 'data/projects/.gitkeep')
    _git(repo, 'commit', '-q', '-m', 'placeholder')
    # Real content lives only in the main tree (gitignored in real life).
    (repo / 'data' / 'projects' / 'real.json').write_text('{}', encoding='utf-8')
    ok, path = w.create(project, 'ph1')
    assert ok, path
    assert (Path(path) / 'data' / 'projects' / 'real.json').exists(), \
        'placeholder dir was not replaced by the junction'


def test_real_content_is_never_clobbered(project, repo):
    """The replace-placeholder logic must refuse when the dir has real content,
    so we can never destroy a checked-in directory."""
    (repo / 'node_modules').mkdir()
    (repo / 'node_modules' / 'real.txt').write_text('main', encoding='utf-8')
    ok, path = w.create(project, 'nc1')
    assert ok
    wt_nm = Path(path) / 'node_modules'
    # Whatever happened, the MAIN tree's content is intact.
    assert (repo / 'node_modules' / 'real.txt').read_text(encoding='utf-8') == 'main'
    assert wt_nm.exists()


def test_mcp_config_is_copied_and_retargeted(project, repo):
    """.mcp.json is gitignored (never reaches a worktree) AND pins absolute
    paths. Un-copied → agent loses all MCP servers. Copied but un-rewritten →
    agent edits the worktree while MCP reads/writes the MAIN tree."""
    (repo / '.mcp.json').write_text(
        '{"mcpServers":{"filesystem":{"args":["' + str(repo).replace('\\', '/') + '"]}}}',
        encoding='utf-8')
    ok, path = w.create(project, 'mcp1')
    assert ok, path
    cfg = (Path(path) / '.mcp.json').read_text(encoding='utf-8')
    assert 'mcp1' in cfg, 'MCP config was not retargeted to the worktree'
    # The main tree's own file is untouched.
    assert 'mcp1' not in (repo / '.mcp.json').read_text(encoding='utf-8')


# ── C. No work loss ─────────────────────────────────────────────────────────

def test_teardown_never_follows_link_into_real_dirs(project, repo):
    """THE dangerous case: a recursive delete that follows a junction would
    destroy the real .venv / data/projects."""
    (repo / '.venv').mkdir()
    for i in range(5):
        (repo / '.venv' / f'f{i}.txt').write_text('real', encoding='utf-8')
    ok, _ = w.create(project, 'del1')
    assert ok
    w.remove(project, 'del1', delete_branch=True, force=True)
    assert len(list((repo / '.venv').iterdir())) == 5, 'real .venv was damaged'


def test_remove_refuses_to_destroy_uncommitted_work(project):
    """An automatic cleanup must never delete an agent's in-progress work."""
    _, path = w.create(project, 'unc1')
    (Path(path) / 'app.py').write_text('agent work in progress', encoding='utf-8')
    ok, msg = w.remove(project, 'unc1')
    assert not ok and 'refused' in msg
    assert Path(path).exists()
    w.remove(project, 'unc1', delete_branch=True, force=True)  # cleanup


def test_remove_refuses_to_destroy_unmerged_commits(project):
    """Committed-but-unmerged work must also survive cleanup."""
    _, path = w.create(project, 'unm1')
    _turn(path, 'one', 'PRECIOUS')
    _git(path, 'add', '.'); _git(path, 'commit', '-q', '-m', 'precious')
    ok, msg = w.remove(project, 'unm1')
    assert not ok and 'refused' in msg
    w.remove(project, 'unm1', delete_branch=True, force=True)


def test_merge_back_returns_work_to_base_branch(project, repo):
    """Isolation without merge-back is just a fancier way to lose work."""
    _, path = w.create(project, 'mb1')
    _turn(path, 'one', 'SHIPPED')
    _git(path, 'add', '.'); _git(path, 'commit', '-q', '-m', 'work')
    status, detail = w.merge_back(project, 'mb1')
    assert status == 'clean', detail
    assert 'SHIPPED' in (repo / 'app.py').read_text(encoding='utf-8')


def test_gc_preserves_unmerged_orphans(project):
    """A crash-orphaned worktree holding work is PRESERVED, not reaped."""
    _, path = w.create(project, 'orph1')
    (Path(path) / 'app.py').write_text('unsaved agent work', encoding='utf-8')
    out = w.gc_stale(project, live_session_ids=())
    assert 'orph1' in out['preserved']
    assert Path(path).exists()
    w.remove(project, 'orph1', delete_branch=True, force=True)


def test_gc_never_touches_a_live_session(project):
    """Reaping a running agent's tree would destroy live work."""
    _, path = w.create(project, 'live1')
    out = w.gc_stale(project, live_session_ids=('live1',))
    assert out['removed'] == 0
    assert Path(path).exists()
    w.remove(project, 'live1', delete_branch=True, force=True)


def test_conflict_aborts_and_restores_tree(project):
    """Same-line collision: merge aborts, the agent's tree is left EXACTLY as
    it was, and the tree is not left in a conflicted state."""
    _, pA = w.create(project, 'cf_a')
    _, pB = w.create(project, 'cf_b')
    _turn(pA, 'one', 'AVERSION'); _git(pA, 'add', '.'); _git(pA, 'commit', '-q', '-m', 'a')
    _turn(pB, 'one', 'BVERSION'); _git(pB, 'add', '.'); _git(pB, 'commit', '-q', '-m', 'b')
    status, _ = w.sync_into(project, 'cf_b', w.branch_name('cf_a'))
    assert status == 'conflict'
    body = (Path(pB) / 'app.py').read_text(encoding='utf-8')
    assert 'BVERSION' in body and '<<<<<<<' not in body, 'tree left conflicted'
    assert not project_sync._dirty(str(pB))


def test_merge_back_conflict_preserves_branch(project, repo):
    """On a conflicting merge-back the branch survives for manual resolution
    and the main tree is left clean."""
    _git(repo, 'checkout', '-q', '-b', 'tmp')
    _git(repo, 'checkout', '-q', 'master')
    _, path = w.create(project, 'mbc1')
    _turn(path, 'one', 'AGENTVER'); _git(path, 'add', '.'); _git(path, 'commit', '-q', '-m', 'agent')
    # Main tree changes the same line first.
    _turn(repo, 'one', 'MAINVER'); _git(repo, 'add', '.'); _git(repo, 'commit', '-q', '-m', 'main')
    status, _ = w.merge_back(project, 'mbc1')
    assert status == 'conflict'
    assert not project_sync._dirty(str(repo)), 'main tree left dirty after conflict'
    branches = _git(repo, 'branch', '--list', w.branch_name('mbc1'))
    assert branches.strip(), 'agent branch was destroyed on conflict'


def test_sync_skips_dirty_worktree(project):
    """Never merge into a tree an agent is mid-edit in — that is the clobber."""
    _, pA = w.create(project, 'sk_a')
    _, pB = w.create(project, 'sk_b')
    _turn(pA, 'one', 'AWORK'); _git(pA, 'add', '.'); _git(pA, 'commit', '-q', '-m', 'a')
    (Path(pB) / 'app.py').write_text('mid-edit', encoding='utf-8')
    status, detail = w.sync_into(project, 'sk_b', w.branch_name('sk_a'))
    assert status == 'skipped' and 'dirty' in detail
    assert (Path(pB) / 'app.py').read_text(encoding='utf-8') == 'mid-edit'


def test_merge_back_skips_dirty_main_tree(project, repo):
    """Never merge into the USER's tree while they have uncommitted edits."""
    _, path = w.create(project, 'dm1')
    _turn(path, 'one', 'WORK'); _git(path, 'add', '.'); _git(path, 'commit', '-q', '-m', 'w')
    (repo / 'app.py').write_text('user is editing this', encoding='utf-8')
    status, _ = w.merge_back(project, 'dm1')
    assert status == 'skipped'
    assert (repo / 'app.py').read_text(encoding='utf-8') == 'user is editing this'


# ── D/E. Containment + fail-safe ────────────────────────────────────────────

def test_non_git_dir_degrades_gracefully():
    """A non-git project must fall back, never raise."""
    d = Path(tempfile.mkdtemp(prefix='wt_nogit_'))
    try:
        ok, msg = w.create({'id': 'x', 'project_path': str(d)}, 'sX')
        assert not ok and 'git' in msg.lower()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_session_id_is_rejected(project):
    """Path-traversal guard — a session id must never escape the worktree root."""
    for bad in ('../evil', 'a/b', 'x' * 200, ''):
        ok, _ = w.create(project, bad)
        assert not ok, f'accepted unsafe session id {bad!r}'


def test_create_is_idempotent(project):
    """Re-creating an existing worktree returns the same path, no error."""
    ok1, p1 = w.create(project, 'idem1')
    ok2, p2 = w.create(project, 'idem1')
    assert ok1 and ok2 and p1 == p2
    w.remove(project, 'idem1', delete_branch=True, force=True)


def test_remove_of_missing_worktree_is_noop(project):
    ok, _ = w.remove(project, 'never_existed')
    assert ok
