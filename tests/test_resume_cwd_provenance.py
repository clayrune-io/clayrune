"""MC-1167 probes: transcript readability does not establish resume cwd.

Use the real ClaudeRuntime lookup and a private transcript store. No provider
is launched. Dave selected refusal for a missing worktree on 2026-11-09.
"""
import json

import pytest

from mc import agent_runtime
from mc.blueprints import agent_routes as ar
from resume_cwd_fixtures import make_tree, remove_tree


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


@pytest.mark.parametrize('tree_exists', [True, False], ids=['live-tree', 'deleted-tree'])
def test_worktree_transcript_must_not_resolve_resume_to_main(
        transcript_store, tree_exists, monkeypatch):
    project, home, rt = transcript_store
    tree = project / '.clayrune' / 'agents' / OWNER
    make_tree(project, tree, monkeypatch)
    transcript = _write_transcript(home, rt, tree)
    if not tree_exists:
        remove_tree(tree)
    assert tree.is_dir() is tree_exists
    # This broad lookup is intentional: cleaned-up chats must remain readable.
    assert rt.transcript_path(str(project), CSID) == transcript
    assert rt.transcript_path(str(tree), CSID) == transcript

    if tree_exists:
        assert ar._resume_cwd_for(str(project), CSID) == str(tree)
    else:
        with pytest.raises(ar._resume_cwd.ResumeCwdMissing, match='Start a new chat') as exc:
            ar._resume_cwd_for(str(project), CSID)
        assert exc.value.cwd == str(tree)
        assert transcript.is_file()


def test_main_transcript_keeps_main_cwd(transcript_store):
    project, home, rt = transcript_store
    _write_transcript(home, rt, project)
    assert ar._resume_cwd_for(str(project), CSID) == str(project)


def test_absent_transcript_has_no_resume_cwd(transcript_store):
    project, _, _ = transcript_store
    assert ar._resume_cwd_for(str(project), CSID) is None


def test_first_recorded_cwd_wins_over_later_directory_changes(transcript_store, monkeypatch):
    project, home, rt = transcript_store
    tree = project / '.clayrune' / 'agents' / OWNER
    make_tree(project, tree, monkeypatch)
    transcript = _write_transcript(home, rt, tree)
    with transcript.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'user', 'cwd': str(project)}) + '\n')
    assert ar._resume_cwd_for(str(project), CSID) == str(tree)


def test_recorded_cwd_skips_metadata_without_cwd_and_partial_rows(transcript_store):
    project, home, rt = transcript_store
    transcript = _write_transcript(home, rt, project)
    transcript.write_text('null\n[]\n{"type":"summary"}\n{"unfinished":\n' +
                          transcript.read_text(encoding='utf-8'), encoding='utf-8')
    assert ar._resume_cwd_for(str(project), CSID) == str(project)


def test_codex_metadata_refusal_keeps_vendor_native_identity(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    home = tmp_path / 'codex-sessions'
    monkeypatch.setattr(agent_runtime, '_CODEX_HOME', home)
    rt = agent_runtime.CodexRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda provider: rt)
    tree = project / '.clayrune' / 'agents' / OWNER
    make_tree(project, tree, monkeypatch)
    transcript = home / '2026' / '11' / '09' / f'rollout-now-{CSID}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {
        'cwd': str(tree), 'id': CSID}}) + '\n', encoding='utf-8')
    assert ar._resume_cwd_for(str(project), CSID, 'codex') == str(tree)
    remove_tree(tree)
    with pytest.raises(ar._resume_cwd.ResumeCwdMissing) as exc:
        ar._resume_cwd_for(str(project), CSID, 'codex')
    rows = []

    def update(pid, mutate):
        return mutate(rows)

    body = ar._resume_cwd.refusal(exc.value, {'message': 'keep me'}, 'p1',
                                  update, lambda message: None, 'now')
    assert body['request_saved'] is True
    assert rows[0]['provider'] == 'codex'
    assert rows[0]['provider_session_id'] == CSID
    assert 'claude_session_id' not in rows[0]
