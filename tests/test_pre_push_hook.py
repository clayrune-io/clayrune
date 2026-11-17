"""tools/git-hooks/pre-push against a throwaway repo (MC-1179 pass 12 second
wave, TL24): a private file ADDED by a commit being pushed blocks the push
even when a later commit deleted it, so the tip tree is clean."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'tools' / 'git-hooks' / 'pre-push'

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')

ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
       'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
       'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}


def git(cwd, *args, check=True):
    r = subprocess.run(['git', *args], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=60)
    if check and r.returncode != 0:
        raise AssertionError(f'git {args} failed: {r.stderr}')
    return r


@pytest.fixture()
def repos(tmp_path):
    remote = tmp_path / 'remote.git'
    work = tmp_path / 'work'
    git(tmp_path, 'init', '--bare', '-q', str(remote))
    git(tmp_path, 'init', '-q', '-b', 'main', str(work))
    git(work, 'config', 'commit.gpgsign', 'false')
    git(work, 'remote', 'add', 'origin', str(remote))
    hook = work / '.git' / 'hooks' / 'pre-push'
    # The working copy may carry CRLF (autocrlf); sh needs LF.
    hook.write_bytes(HOOK.read_bytes().replace(b'\r\n', b'\n'))
    hook.chmod(0o755)
    commit(work, 'README.md', 'hello', 'base')
    git(work, 'push', '-q', 'origin', 'main')
    return work, remote


def commit(work, rel, text, msg):
    p = work / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')
    git(work, 'add', '-f', rel)
    git(work, 'commit', '-q', '-m', msg)


def remove(work, rel, msg):
    git(work, 'rm', '-q', rel)
    git(work, 'commit', '-q', '-m', msg)


def push(work, *refspec):
    return git(work, 'push', 'origin', *refspec, check=False)


def test_clean_push_is_allowed(repos):
    work, _ = repos
    commit(work, 'src/a.py', 'x', 'code')
    assert push(work, 'main').returncode == 0


def test_private_file_in_tip_tree_is_blocked(repos):
    work, _ = repos
    commit(work, 'data/SHARED_RULES.md', 'private', 'oops')
    r = push(work, 'main')
    assert r.returncode != 0 and 'data/SHARED_RULES.md' in r.stderr


def test_private_file_added_then_deleted_is_blocked_on_a_new_branch(repos):
    work, remote = repos
    git(work, 'checkout', '-q', '-b', 'feature')
    commit(work, 'docs/poc/pricing.md', 'private', 'add pricing')
    remove(work, 'docs/poc/pricing.md', 'drop pricing')
    assert 'poc' not in git(work, 'ls-tree', '-r', '--name-only', 'HEAD').stdout   # tip is clean
    r = push(work, 'feature')
    assert r.returncode != 0 and 'docs/poc/pricing.md' in r.stderr
    assert git(remote, 'branch', '--list', 'feature').stdout.strip() == ''


def test_private_file_added_then_deleted_is_blocked_on_an_existing_branch(repos):
    work, _ = repos
    commit(work, 'tools/signing.env', 'secret', 'add env')
    remove(work, 'tools/signing.env', 'drop env')
    r = push(work, 'main')
    assert r.returncode != 0 and 'tools/signing.env' in r.stderr


def test_history_already_on_the_remote_is_not_blocked_again(repos):
    work, remote = repos
    # The remote already holds the bad commits (pushed before the hook existed).
    commit(work, 'data/SHARED_RULES.md', 'private', 'old mistake')
    remove(work, 'data/SHARED_RULES.md', 'old cleanup')
    (work / '.git' / 'hooks' / 'pre-push').rename(work / '.git' / 'hooks' / 'pre-push.off')
    assert push(work, 'main').returncode == 0
    (work / '.git' / 'hooks' / 'pre-push.off').rename(work / '.git' / 'hooks' / 'pre-push')
    commit(work, 'src/b.py', 'x', 'more code')
    assert push(work, 'main').returncode == 0


def test_branch_deletion_is_not_scanned(repos):
    work, _ = repos
    git(work, 'checkout', '-q', '-b', 'tmp')
    commit(work, 'src/c.py', 'x', 'c')
    assert push(work, 'tmp').returncode == 0
    assert push(work, ':tmp').returncode == 0
