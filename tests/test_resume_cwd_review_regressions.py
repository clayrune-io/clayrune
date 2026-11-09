"""MC-1167 Fenn regressions, using real private stores and Git worktrees."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from mc import agent_runtime, resume_cwd
from mc.blueprints import agent_routes as ar
from test_one_process_per_conversation import env
from test_resume_cwd_routes import setup, CSID, OWNER
from test_resume_cwd_routes import test_live_tree_revive_keeps_isolation_without_claiming_cleanup as exercise_live_dispatch
from resume_cwd_fixtures import make_tree, remove_tree
REAL_POPEN = subprocess.Popen


@pytest.mark.parametrize('streaming', [False, True])
def test_plain_directory_is_not_an_isolated_worktree(setup, monkeypatch, streaming):
    (setup['tree'] / '.git').unlink()
    # A removed worktree path was recreated by restore/copy without its .git.
    with monkeypatch.context() as git_only:
        git_only.setattr(subprocess, 'Popen', REAL_POPEN)
        subprocess.run(['git', 'init', '-q', str(setup['pp'])], check=True)
        root = subprocess.check_output(
            ['git', '-C', str(setup['tree']), 'rev-parse', '--show-toplevel'], text=True).strip()
    assert Path(root).resolve() == setup['pp'].resolve()
    assert not (setup['tree'] / '.git').exists()
    # Dispatch must refuse before the provider spawn in either Claude mode.
    with pytest.raises(resume_cwd.ResumeCwdMissing):
        exercise_live_dispatch(setup, monkeypatch, streaming, 'dispatch')


@pytest.mark.parametrize('lookup', ['exact-runtime', 'resolver'])
def test_qwen_live_transcript_without_cwd_keeps_original_tree(tmp_path, monkeypatch, lookup):
    project = tmp_path / 'project'
    tree = project / '.clayrune' / 'agents' / OWNER
    make_tree(project, tree, monkeypatch)
    home = tmp_path / 'home'
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    runtime = agent_runtime.QwenRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda provider: runtime)
    encoded = agent_runtime.ClaudeRuntime._encoded_dir_candidates(str(tree))[0].lower()
    transcript = home / '.qwen' / 'projects' / encoded / 'chats' / f'{CSID}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'message', 'provenance': 'real_user',
        'message': {'role': 'user', 'parts': [{'text': 'continue'}]}}) + '\n', encoding='utf-8')
    assert runtime.transcript_path(str(project), CSID) is None
    assert runtime.transcript_path(str(tree), CSID) == transcript
    if lookup == 'exact-runtime':
        assert runtime.transcript_path(str(tree), CSID) == transcript
    else:
        assert ar._resume_cwd_for(str(project), CSID, 'qwen') == str(tree)


def test_real_codex_removed_tree_refuses(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    tree = project / '.clayrune' / 'agents' / OWNER
    make_tree(project, tree, monkeypatch)
    home = tmp_path / 'codex-sessions'
    transcript = home / '2026' / '11' / '09' / f'rollout-now-{CSID}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {
        'cwd': str(tree), 'id': CSID}}) + '\n', encoding='utf-8')
    monkeypatch.setattr(agent_runtime, '_CODEX_HOME', home)
    runtime = agent_runtime.CodexRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda provider: runtime)
    assert ar._resume_cwd_for(str(project), CSID, 'codex') == str(tree)
    remove_tree(tree)
    assert runtime.transcript_path(str(tree), CSID) == transcript
    with pytest.raises(resume_cwd.ResumeCwdMissing):
        ar._resume_cwd_for(str(project), CSID, 'codex')
