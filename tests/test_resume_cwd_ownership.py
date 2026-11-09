"""MC-1167/1165 integration: real transcript provenance keeps owner lifecycle."""
import json
from pathlib import Path

import pytest

from mc import agent_runtime
from test_cold_review_wiring import project, routes  # noqa: F401
from test_merge_review_gate import repo  # noqa: F401
from test_mc1165_review4_probes import revive_setup, _no_leaked_revived_sessions  # noqa: F401


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('removed', [False, True])
def test_owner_transcript_keeps_recreation_review_and_merge(
        routes, project, monkeypatch, tmp_path, streaming, removed):
    sid, tree, spawned, _ = revive_setup(
        routes, project, monkeypatch, streaming, removed=removed)
    home = tmp_path / 'claude-projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', home)
    monkeypatch.setattr(agent_runtime.ClaudeRuntime, '_transform_dirs',
                        classmethod(lambda cls: []))
    runtime = agent_runtime.ClaudeRuntime()
    transcript = home / runtime._encoded_dir_candidates(tree)[-1] / 'prior-native-id.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'user', 'cwd': tree}) + '\n', encoding='utf-8')
    monkeypatch.setattr(routes._agent_runtime, 'get_runtime', lambda *a: runtime)
    assert runtime.transcript_path(project['project_path'], 'prior-native-id') == transcript
    assert Path(tree).is_dir() is not removed

    session = routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert spawned == [tree]
    assert session['_worktree_isolated'] is True
    assert session['_worktree_borrowed'] is False
    assert (Path(tree) / '.git').is_file()

    calls = []
    mismatch = routes._isolated_launch.held_tree_mismatch

    def check(session, p):
        calls.append('identity')
        assert mismatch(session, p) == ''
        return ''

    monkeypatch.setattr(routes._isolated_launch, 'held_tree_mismatch', check)
    monkeypatch.setattr(routes._cold_review_runner, 'needs_review',
                        lambda *a: calls.append('review') or False)
    monkeypatch.setattr(routes._agent_worktree, 'merge_back',
                        lambda *a: calls.append('merge') or ('awaiting_review', 'kept'))
    routes._worktree_merge_back_on_end(session)
    assert calls == ['identity', 'review', 'merge']
