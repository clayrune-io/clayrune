"""AP60: a Claude transcript miss must not repeat the whole-store search per
agent worktree; the runtime lookup for the project path already covers them."""
from pathlib import Path

from mc import resume_cwd


class _CountingRuntime:
    def __init__(self, found=None):
        self.calls = []
        self.found = found

    def transcript_path(self, cwd, native_id):
        self.calls.append(cwd)
        return self.found


def _project_with_worktrees(tmp_path, n=3):
    agents = tmp_path / '.clayrune' / 'agents'
    for i in range(n):
        (agents / f'wt{i}').mkdir(parents=True)
    return str(tmp_path)


def test_claude_miss_makes_one_runtime_lookup_not_one_per_worktree(tmp_path):
    project = _project_with_worktrees(tmp_path)
    rt = _CountingRuntime()
    assert resume_cwd.transcript_cwd(rt, project, 'sid', 'claude') is None
    assert rt.calls == [str(Path(project))]


def test_qwen_still_tries_each_worktree_directory(tmp_path):
    project = _project_with_worktrees(tmp_path)
    rt = _CountingRuntime()
    assert resume_cwd.transcript_cwd(rt, project, 'sid', 'qwen') is None
    assert len(rt.calls) == 4
