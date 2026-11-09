"""MC-1167 probes: transcript readability does not establish resume cwd.

Use the real ClaudeRuntime lookup and a private transcript store. No provider
is launched. The two worktree cases expose the baseline defect; the policy
for a missing worktree must be selected before these become passing regressions.
"""
import json

import pytest

from mc import agent_runtime
from mc.blueprints import agent_routes as ar


CSID = '2033f331-a034-4efe-a95c-d1fd89fabf0a'
OWNER = 'ea7620565ce1'


@pytest.fixture
def transcript_store(tmp_path, monkeypatch):
    home = tmp_path / 'claude-projects'
    home.mkdir()
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', home)
    # Avoid the neutral transform directory, which lives outside this fixture.
    monkeypatch.setattr(agent_runtime.ClaudeRuntime, '_transform_dirs',
                        classmethod(lambda cls: []))
    rt = agent_runtime.ClaudeRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda provider: rt)
    project = tmp_path / 'project'
    project.mkdir()
    return project, home, rt


def _write_transcript(home, rt, cwd):
    path = home / rt._encoded_dir_candidates(str(cwd))[-1] / f'{CSID}.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'type': 'user', 'cwd': str(cwd),
                                'sessionId': CSID}) + '\n', encoding='utf-8')
    return path


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason='MC-1167 reproduced; missing-tree policy awaiting Dave')
@pytest.mark.parametrize('tree_exists', [True, False], ids=['live-tree', 'deleted-tree'])
def test_worktree_transcript_must_not_resolve_resume_to_main(
        transcript_store, tree_exists):
    project, home, rt = transcript_store
    tree = project / '.clayrune' / 'agents' / OWNER
    tree.mkdir(parents=True)
    transcript = _write_transcript(home, rt, tree)
    if not tree_exists:
        tree.rmdir()
    assert tree.is_dir() is tree_exists
    # This broad lookup is intentional: cleaned-up chats must remain readable.
    assert rt.transcript_path(str(project), CSID) == transcript
    assert rt.transcript_path(str(tree), CSID) == transcript

    actual = ar._resume_cwd_for(str(project), CSID)
    print(f'{tree_exists=}: resume cwd={actual}; original cwd={tree}')
    assert actual != str(project), 'worktree transcript silently selected main'
    if tree_exists:
        assert actual == str(tree)


def test_main_transcript_keeps_main_cwd(transcript_store):
    project, home, rt = transcript_store
    _write_transcript(home, rt, project)
    assert ar._resume_cwd_for(str(project), CSID) == str(project)


def test_absent_transcript_has_no_resume_cwd(transcript_store):
    project, _, _ = transcript_store
    assert ar._resume_cwd_for(str(project), CSID) is None
