"""Integration identity excludes runtime state without weakening Preview inputs."""
import pytest

from mc.dev_harness_diff import FreezeError, freeze_candidate, preview
from mc.dev_harness_integration_inputs import freeze_integration, tracked_identity
from test_dev_harness_diff import repo, git, branch


def test_integration_tracks_head_index_and_working_bytes_only(repo):
    branch(repo)
    original = freeze_integration(repo, 'HEAD', 'incoming')
    with (repo/'runtime.sqlite3').open('wb') as stream:
        stream.truncate(17 * 1024 * 1024)
    runtime = repo/'runtime.json'
    runtime.write_text('first')
    assert freeze_integration(repo, 'HEAD', 'incoming') == original
    runtime.write_text('second')
    assert freeze_integration(repo, 'HEAD', 'incoming') == original
    # Ordinary/manual freezing and dirty Preview retain their full boundaries.
    with pytest.raises(FreezeError, match='dirty_integration_input'):
        freeze_candidate(repo, 'HEAD', 'incoming')
    with pytest.raises(FreezeError, match='file_size_limit'):
        preview(repo)


@pytest.mark.parametrize('change', ['working', 'index', 'head'])
def test_tracked_identity_cannot_hide_real_input_drift(repo, change):
    before = tracked_identity(repo)
    if change == 'head':
        git(repo, 'commit', '--allow-empty', '-qm', 'head only')
    else:
        (repo/'app.py').write_text('changed\n')
    if change in ('index', 'head'):
        git(repo, 'add', 'app.py')
    if change == 'index':
        (repo/'app.py').write_text('value = 1\n')
    assert tracked_identity(repo) != before
