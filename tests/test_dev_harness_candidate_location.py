"""Native candidates use the registered lifecycle, even inside Hivemind."""
from pathlib import Path

import pytest

from mc import agent_worktree as worktrees, hivemind_integration as hive
from test_dev_harness_diff import repo, git
from test_dev_harness_native_plan import inputs
from test_dev_harness_integration import executable
from test_dev_harness_landing import configured


@pytest.mark.parametrize('create_failure', [False, True])
def test_hivemind_native_candidate_is_not_nested(configured, monkeypatch, create_failure):
    fixture, _ = configured
    store, project, *_ = fixture
    root = Path(project['project_path'])
    baseline = git(root, 'rev-parse', 'HEAD')
    ok, path = worktrees.create(project, 'hm_location')
    assert ok
    (Path(path) / 'new.py').write_text('work\n')
    git(Path(path), 'add', 'new.py')
    git(Path(path), 'commit', '-qm', 'work')
    original_create = worktrees.create
    candidates = []

    def create(candidate_project, session, *args, **kwargs):
        if kwargs.get('isolated'):
            # This invariant catches the path bug even on POSIX or in a
            # checkout whose pytest temp sequence is still short enough.
            assert candidate_project['project_path'] == project['project_path']
            candidates.append(worktrees.worktree_path(candidate_project, session))
            if create_failure:
                return False, 'synthetic Git creation failure'
        return original_create(candidate_project, session, *args, **kwargs)

    monkeypatch.setattr(worktrees, 'create', create)
    smokes = []
    monkeypatch.setattr(hive, '_run_smoke', lambda *a: (smokes.append(a) or (True, '')))
    result = hive.integrate(project, 'candidate-location', [{
        'id': 'one', 'status': 'completed', 'worktree_session_id': 'hm_location',
        'worktree_branch': worktrees.branch_name('hm_location'), 'smokes': ['boot.mjs'],
    }], baseline)
    assert len(candidates) == 1
    assert all(p.parent == root / '.clayrune/agents' and not p.exists() for p in candidates)
    if create_failure:
        assert result['status'] == 'stopped', result
        assert result['failed']['kind'] == 'standards'
        assert not result['merged'] and not smokes
        record = store._read_json(store.project_dir('one') / 'integration_runs' /
                                  (result['failed']['run_id'] + '.json'))
        receipt = store._read_json(store.project_dir('one') / 'runs' /
                                   (record['receipt_ids'][0] + '.json'))
        assert receipt['code'] == 'worktree_create_failed' and receipt['results'] == []
    else:
        assert result['status'] == 'completed', result
        assert len(result['merged']) == 1 and len(smokes) == 1
    assert git(root, 'rev-parse', 'HEAD') == baseline
    assert Path(path).exists()
