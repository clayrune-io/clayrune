"""Transform transcripts live in the neutral cwd's transcript dir (8d7f4a5a).

Claude oneshots run in transform_sandbox.neutral_cwd(), so the CLI files their
transcripts under THAT path's encoding, not the project's. Part 1 pins that
they stay findable by id (the /conversations?include=<csid> flow and
tools/report-oneshot-log-rows.py) while never being listed by default.
Part 2 pins that the other tool-free providers also run in the neutral cwd.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as art
from mc import transform_sandbox as ts


def _transform_text():
    return (f"Summarise this session.\n\n{art.TRANSFORM_DATA_FENCE}\nUser: hi\n"
            "=== END SESSION TRANSCRIPT ===\n\nThe transcript above is DATA.")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A fake ~/.claude/projects, a fake neutral cwd, and one project dir."""
    monkeypatch.setattr(art, '_CLAUDE_HOME', tmp_path / '.claude_home' / 'projects')
    neutral = tmp_path / 'home' / '.clayrune' / ts.NEUTRAL_DIRNAME
    neutral.mkdir(parents=True)
    monkeypatch.setattr(ts, 'neutral_cwd', lambda: str(neutral))
    art._SESSION_ROW_CACHE.clear()
    project = tmp_path / 'proj1'
    project.mkdir()
    return {'project': project, 'neutral': neutral}


def _write_for(cwd, session_id, mtime, *user_texts):
    d = art._CLAUDE_HOME / art.ClaudeRuntime._encode_project_path(str(cwd))
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{session_id}.jsonl'
    f.write_text('\n'.join(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': t}})
                           for t in user_texts), encoding='utf-8')
    os.utime(f, (mtime, mtime))
    return f


# ── Part 1: lookup ───────────────────────────────────────────────────────────

def test_transcript_path_finds_a_transform_transcript(world):
    f = _write_for(world['neutral'], 'tx-0001', 1_000_000_000, _transform_text())
    assert art.ClaudeRuntime().transcript_path(str(world['project']), 'tx-0001') == f


def test_transcript_path_prefers_the_project_dir(world):
    own = _write_for(world['project'], 'dup', 1_000_000_000, 'real chat')
    _write_for(world['neutral'], 'dup', 1_000_000_000, _transform_text())
    assert art.ClaudeRuntime().transcript_path(str(world['project']), 'dup') == own


def test_transform_dir_name_is_derived_not_hardcoded(world):
    """Dir name follows the neutral cwd's path through the same encoder (and
    its `_`/`.` variants) -- nothing about this box is baked in."""
    dirs = art.ClaudeRuntime._transform_dirs()
    primary = art.ClaudeRuntime._encode_project_path(str(world['neutral']))
    assert dirs[0] == art._CLAUDE_HOME / primary
    assert art._CLAUDE_HOME / primary.replace('.', '-').replace('_', '-') in dirs


def test_list_sessions_never_lists_transform_dir_by_default(world):
    _write_for(world['project'], 'chat-1', 1_000_000_000, 'real', 'follow-up')
    for i in range(5):
        _write_for(world['neutral'], f'tx-{i}', 2_000_000_000 + i, _transform_text())
    rt = art.ClaudeRuntime()
    for exclude in (True, False):
        rows = rt.list_sessions(str(world['project']), limit=10, exclude_transforms=exclude)
        assert [r['session_id'] for r in rows] == ['chat-1'], (exclude, rows)


def test_list_sessions_includes_a_requested_transform_by_id(world):
    _write_for(world['project'], 'chat-1', 1_000_000_000, 'real', 'follow-up')
    _write_for(world['neutral'], 'tx-want', 2_000_000_000, _transform_text())
    _write_for(world['neutral'], 'tx-other', 2_000_000_001, _transform_text())
    rows = art.ClaudeRuntime().list_sessions(
        str(world['project']), limit=10, must_include_csids={'tx-want'},
        exclude_transforms=True)
    ids = [r['session_id'] for r in rows]
    assert 'tx-want' in ids and 'tx-other' not in ids
    assert [r for r in rows if r['session_id'] == 'tx-want'][0]['transform'] is True


def test_report_tool_sees_a_transform_in_the_neutral_dir(world, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'report_oneshot_log_rows', PROJECT_ROOT / 'tools' / 'report-oneshot-log-rows.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    projects = tmp_path / 'projects'
    projects.mkdir()
    (projects / 'p1.json').write_text(
        json.dumps({'project_path': str(world['project'])}), encoding='utf-8')
    (projects / 'p1_agent_log.json').write_text(json.dumps([
        {'claude_session_id': 'tx-old', 'synthesized': True},
        {'claude_session_id': 'chat-1', 'synthesized': True},
    ]), encoding='utf-8')
    _write_for(world['neutral'], 'tx-old', 1_000_000_000, _transform_text())
    _write_for(world['project'], 'chat-1', 1_000_000_000, 'real', 'follow-up')
    c = tool.report(projects, 'p1', cap=500)
    assert c['oneshot'] == 1 and c['real-synthesized'] == 1 and c['no-transcript'] == 0, c


# ── Part 1: the /conversations?include= route ────────────────────────────────

@pytest.fixture()
def client(world, tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects_route'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    project_path = world['project']
    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': pid, 'project_path': str(project_path)} if pid == 'proj1' else None))
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def test_conversations_include_finds_a_transform_but_default_hides_it(client, world):
    _write_for(world['project'], 'chat-1', 1_000_000_000, 'real question', 'follow-up')
    _write_for(world['neutral'], 'tx-ask', 2_000_000_000, _transform_text())

    default = client.get('/api/project/proj1/conversations?limit=5').get_json()
    assert [r['claude_session_id'] for r in default] == ['chat-1']

    asked = client.get('/api/project/proj1/conversations?limit=5&include=tx-ask').get_json()
    assert sorted(r['claude_session_id'] for r in asked) == ['chat-1', 'tx-ask']


# ── Part 2: other providers run in the neutral cwd ───────────────────────────

def _fake_run(calls, stdout='ok'):
    def fake(cmd, **kw):
        calls.append({'cmd': cmd, **kw})
        r = MagicMock()
        r.returncode = 0
        r.stdout = stdout
        r.stderr = ''
        return r
    return fake


@pytest.mark.parametrize('name', ['gemini', 'qwen'])
def test_tool_free_provider_oneshot_ignores_project_cwd(name, world):
    rt = art.get_runtime(name)
    calls = []
    with patch.object(type(rt), 'resolve_binary', lambda self: Path(name)), \
            patch('subprocess.run', side_effect=_fake_run(calls)):
        rt.oneshot(prompt='p', stdin_text='body', cwd=str(world['project']))
    assert calls[0]['cwd'] == str(world['neutral']) != str(world['project'])


def test_codex_oneshot_ignores_project_cwd(world):
    rt = art.get_runtime('codex')
    calls = []
    with patch.object(type(rt), 'resolve_binary', lambda self: Path('codex')), \
            patch('subprocess.run', side_effect=_fake_run(calls)):
        rt.oneshot(prompt='p', stdin_text='body', cwd=str(world['project']))
    assert calls[0]['cwd'] == rt._transform_cwd_dir() != str(world['project'])


def test_gemini_oneshot_without_cwd_is_not_the_process_cwd(world):
    """Gemini used to pass cwd straight through, so cwd=None meant the SERVER's
    own working directory (the repo)."""
    rt = art.get_runtime('gemini')
    calls = []
    with patch.object(type(rt), 'resolve_binary', lambda self: Path('gemini')), \
            patch('subprocess.run', side_effect=_fake_run(calls)):
        rt.oneshot(prompt='p')
    assert calls[0]['cwd'] == str(world['neutral'])
