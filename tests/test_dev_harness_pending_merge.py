import pytest
from mc.dev_harness_pending_merge import freeze_pending, revalidate
from mc.dev_harness_diff import FreezeError, freeze_candidate
from test_dev_harness_diff import repo, git


def pending(repo):
    git(repo, 'checkout', '-qb', 'incoming')
    (repo/'new.py').write_text('incoming\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'incoming')
    git(repo, 'checkout', '-q', 'master')
    (repo/'base.py').write_text('base\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    git(repo, 'merge', '--no-commit', '--no-ff', 'incoming')


def test_real_pending_index_is_not_head(repo):
    assert freeze_pending(repo) is None
    pending(repo)
    frozen = freeze_pending(repo)
    assert frozen['T'] != git(repo, 'rev-parse', 'HEAD^{tree}')
    assert frozen['T'] == frozen['auto_tree']
    revalidate(repo, frozen)
    with pytest.raises(FreezeError, match='dirty_integration_input'):
        freeze_candidate(repo, 'HEAD', 'incoming')
    (repo/'new.py').write_text('changed\n')
    with pytest.raises(FreezeError, match='pending_worktree_differs'):
        revalidate(repo, frozen)


def test_manual_resolution_and_octopus_refused(repo):
    pending(repo)
    (repo/'new.py').write_text('manual\n')
    git(repo, 'add', 'new.py')
    with pytest.raises(FreezeError, match='pending_resolution_unsupported'):
        freeze_pending(repo)
    mh = repo / git(repo, 'rev-parse', '--git-path', 'MERGE_HEAD')
    mh.write_text(git(repo, 'rev-parse', 'incoming')+'\n'+git(repo, 'rev-parse', 'master')+'\n')
    with pytest.raises(FreezeError, match='ambiguous_pending_parents'):
        freeze_pending(repo)


def test_pending_identity_ignores_large_changing_untracked_runtime(repo):
    pending(repo)
    with (repo/'runtime.sqlite3').open('wb') as stream:
        stream.truncate(17 * 1024 * 1024)
    runtime = repo/'runtime.json'
    runtime.write_text('before')
    frozen = freeze_pending(repo)
    runtime.write_text('after')
    revalidate(repo, frozen)
    assert 'runtime.sqlite3' not in frozen['working_manifest']['files']
    (repo/'app.py').write_text('tracked drift\n')
    with pytest.raises(FreezeError, match='pending_worktree_differs'):
        revalidate(repo, frozen)
