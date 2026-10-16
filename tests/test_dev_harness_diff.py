"""Frozen Git/dirty-input contracts on disposable repositories only."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mc import dev_harness_diff as dh


def git(repo, *args):
    result = subprocess.run(['git', *args], cwd=repo, capture_output=True,
                            stdin=subprocess.DEVNULL, check=True)
    return result.stdout.decode('utf-8').strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'master')
    git(tmp_path, 'config', 'user.name', 'Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@localhost')
    git(tmp_path, 'config', 'core.autocrlf', 'false')
    (tmp_path / '.gitignore').write_text('.clayrune/\n__pycache__/\n*.out\n', encoding='utf-8')
    (tmp_path / 'app.py').write_text('value = 1\n', encoding='utf-8')
    git(tmp_path, 'add', '.gitignore', 'app.py')
    git(tmp_path, 'commit', '-qm', 'baseline')
    return tmp_path


def branch(repo):
    git(repo, 'checkout', '-qb', 'incoming')
    (repo / 'new.py').write_text('value = 2\n', encoding='utf-8')
    git(repo, 'add', 'new.py')
    git(repo, 'commit', '-qm', 'incoming')
    git(repo, 'checkout', '-q', 'master')


def test_preview_covers_index_unstaged_untracked_deleted_and_ignored(repo):
    git(repo, 'mv', 'app.py', 'renamed file.py')
    (repo / 'renamed file.py').write_text('value = 2\n', encoding='utf-8')
    (repo / 'untracked file.py').write_text('extra = True\n', encoding='utf-8')
    (repo / 'ignored.out').write_text('ignored', encoding='utf-8')
    first = dh.preview(repo)
    assert first['authorizes_merge'] is False
    files = first['manifest']['files']
    assert {'renamed file.py', 'untracked file.py'} <= files.keys()
    assert 'ignored.out' not in files
    assert first['manifest']['index']['renamed file.py']['oid']
    (repo / 'renamed file.py').unlink()
    second = dh.preview(repo)
    assert second['manifest']['files']['renamed file.py']['kind'] == 'deleted'
    assert second['manifest']['digest'] != first['manifest']['digest']


def test_rename_paths_and_modes_are_preserved(repo):
    git(repo, 'mv', 'app.py', 'new name.py')
    git(repo, 'commit', '-qm', 'rename')
    result = dh.changes(repo, 'HEAD~1', 'HEAD')
    assert result[0]['status'] == 'R100'
    assert result[0]['old_path'] == 'app.py'
    assert result[0]['new_path'] == 'new name.py'
    assert result[0]['old_mode'] == result[0]['new_mode'] == '100644'


@pytest.mark.skipif(os.name == 'nt', reason='Windows rejects newline filenames')
def test_nul_safe_newline_paths(repo):
    (repo / 'line\nbreak.py').write_text('True\n')
    assert 'line\nbreak.py' in dh.preview(repo)['manifest']['files']


def test_candidate_uses_current_target_and_separate_attribution(repo):
    branch(repo)
    (repo / 'target.py').write_text('target = True\n', encoding='utf-8')
    git(repo, 'add', 'target.py')
    git(repo, 'commit', '-qm', 'advance target')
    frozen = dh.freeze_candidate(repo, 'master', 'incoming')
    assert frozen['B'] == git(repo, 'rev-parse', 'master')
    assert frozen['H'] == git(repo, 'rev-parse', 'incoming')
    assert frozen['M'] == git(repo, 'merge-base', 'master', 'incoming')
    assert [v['new_path'] for v in frozen['integration_changes']] == ['new.py']
    project = {'id': 'fixture', 'project_path': str(repo)}
    before = git(repo, 'status', '--porcelain')
    with dh.candidate_worktree(project, frozen) as candidate:
        assert (candidate / 'target.py').is_file()
        assert (candidate / 'new.py').is_file()
        assert git(candidate, 'rev-parse', 'HEAD^{tree}') == frozen['T']
        assert not (candidate / 'config.json').exists()
    assert not candidate.exists()
    assert git(repo, 'status', '--porcelain') == before
    assert git(repo, 'branch', '--list', 'clayrune/agent/dh_*') == ''


def test_conflict_preserves_checkout_and_refs(repo):
    git(repo, 'checkout', '-qb', 'incoming')
    (repo / 'app.py').write_text('incoming = True\n')
    git(repo, 'commit', '-qam', 'incoming')
    git(repo, 'checkout', '-q', 'master')
    (repo / 'app.py').write_text('target = True\n')
    git(repo, 'commit', '-qam', 'target')
    before = git(repo, 'show-ref')
    with pytest.raises(dh.FreezeError):
        dh.freeze_candidate(repo, 'master', 'incoming')
    assert git(repo, 'show-ref') == before
    assert (repo / 'app.py').read_text() == 'target = True\n'


@pytest.mark.parametrize('dirty', ['staged', 'unstaged', 'untracked'])
def test_dirty_integration_refused(repo, dirty):
    branch(repo)
    name = 'newer.py' if dirty == 'untracked' else 'app.py'
    (repo / name).write_text('changed = True\n')
    if dirty == 'staged':
        git(repo, 'add', name)
    with pytest.raises(dh.FreezeError, match='dirty_integration_input'):
        dh.freeze_candidate(repo, 'master', 'incoming')


def test_dirty_incoming_worktree_is_not_substituted_with_commit(repo):
    branch(repo)
    other = repo / '.clayrune' / 'other'
    git(repo, 'worktree', 'add', str(other), 'incoming')
    try:
        (other / 'new.py').write_text('uncommitted = True\n')
        with pytest.raises(dh.FreezeError, match='dirty_incoming_checkout'):
            dh.freeze_candidate(repo, 'master', 'incoming')
    finally:
        git(repo, 'worktree', 'remove', '--force', str(other))


def test_binary_explicit_and_resource_limits_fail(repo, monkeypatch):
    (repo / 'binary.bin').write_bytes(b'\0\xff')
    assert dh.preview(repo)['manifest']['files']['binary.bin']['kind'] == 'binary'
    monkeypatch.setattr(dh, 'MAX_FILES', 1)
    with pytest.raises(dh.FreezeError, match='file_count_limit'):
        dh.preview(repo)


@pytest.mark.parametrize('limit,code', [('MAX_FILE_BYTES', 'file_size_limit'), ('MAX_TOTAL_BYTES', 'total_size_limit')])
def test_byte_limits(repo, monkeypatch, limit, code):
    monkeypatch.setattr(dh, limit, 1)
    with pytest.raises(dh.FreezeError, match=code):
        dh.manifest(repo)


def test_escape_symlink_is_error(repo, tmp_path):
    link = repo / 'escape.py'
    try:
        link.symlink_to(repo.parent / 'outside.py')
    except OSError:
        pytest.skip('OS does not allow symlink creation')
    with pytest.raises(dh.FreezeError, match='escaping_path'):
        dh.preview(repo)


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'C:/absolute', 'sub\\escape'])
def test_path_containment(repo, path):
    with pytest.raises(dh.FreezeError):
        dh.safe_path(repo, path)


def test_unborn_file_preview(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'new.py').write_text('new = True\n')
    result = dh.preview(tmp_path)
    assert result['head'] is None
    assert result['manifest']['files']['new.py']['kind'] == 'text'


def test_non_git_preview(tmp_path):
    (tmp_path / 'plain.py').write_text('value = True\n')
    result = dh.preview(tmp_path)
    assert result['head'] is None and result['authorizes_merge'] is False
    assert result['manifest']['files']['plain.py']['kind'] == 'text'


def test_dirty_preview_copies_exact_bytes_and_preserves_user_tree(repo):
    git(repo, 'mv', 'app.py', 'renamed.py')
    (repo / 'renamed.py').write_text('dirty = True\n')
    (repo / 'new.py').write_text('untracked = True\n')
    frozen = dh.preview(repo)
    project = {'id': 'fixture', 'project_path': str(repo)}
    with dh.preview_worktree(project, frozen) as root:
        assert not (root / 'app.py').exists()
        assert (root / 'renamed.py').read_text() == 'dirty = True\n'
        assert (root / 'new.py').read_text() == 'untracked = True\n'
    assert dh.preview(repo) == frozen


def test_changing_preview_fails(repo, monkeypatch):
    real = dh.manifest
    count = 0
    def changing(root):
        nonlocal count
        count += 1
        value = real(root)
        if count == 1:
            (root / 'app.py').write_text('mutated = True\n')
        return value
    monkeypatch.setattr(dh, 'manifest', changing)
    with pytest.raises(dh.FreezeError, match='preview_changed'):
        dh.preview(repo)
