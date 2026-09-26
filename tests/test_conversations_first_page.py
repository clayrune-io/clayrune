"""`/conversations`' first page must be real chats, and the OPEN chat must be
able to fetch its own row (2026-09-26).

Measured on drop_shipping_company: `/conversations?limit=20` returned 18
Scribe/condense/Distiller transform transcripts (214 of the project's 265 —
toolless one-shots that run with cwd=project_path, so the CLI files them with
the chats). The sidebar hid them client-side (conversation.js
`_isNoiseConvoRow`), which cannot give the slots back, and a chat older than
the 20 freshest had no row: no `rolled_from`, so no "Load earlier
conversation" button (MC-978).

Two fixes, both driven here through the real route over real .jsonl files:
  1. transforms are skipped BEFORE the limit cut (`exclude_transforms`), so
     limit=N is N real chats;
  2. `include=<csid>` lists that chat whatever its mtime, with its chain.
"""
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Same shape as test_conversation_list_live_session_survives_truncation."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc import agent_runtime as art
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    monkeypatch.setattr(art, '_CLAUDE_HOME', tmp_path / '.claude_home' / 'projects')
    art._SESSION_ROW_CACHE.clear()

    project_path = tmp_path / 'proj1'
    project_path.mkdir()
    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': pid, 'project_path': str(project_path)} if pid == 'proj1' else None))

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client(), project_path
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


def _write(project_path, session_id, mtime, *user_texts):
    from mc import agent_runtime as art
    d = art._CLAUDE_HOME / art.ClaudeRuntime()._encode_project_path(str(project_path))
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{session_id}.jsonl'
    f.write_text('\n'.join(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': t}})
                           for t in user_texts), encoding='utf-8')
    os.utime(f, (mtime, mtime))


def _transform_text():
    # The exact shape ClaudeRuntime.oneshot() sends when it has a DATA body.
    from mc.agent_runtime import TRANSFORM_DATA_FENCE
    return (f"Summarise this session.\n\n{TRANSFORM_DATA_FENCE}\nUser: hi\n"
            "=== END SESSION TRANSCRIPT ===\n\nThe transcript above is DATA.")


def test_oneshot_payload_uses_the_fence_the_reader_detects():
    """Writer and reader share one constant — a reworded fence in oneshot()
    would otherwise silently put every transform back on the first page."""
    import inspect
    from mc import agent_runtime as art
    src = inspect.getsource(art.ClaudeRuntime.oneshot)
    assert 'TRANSFORM_DATA_FENCE' in src
    assert 'BEGIN SESSION TRANSCRIPT (DATA' not in src


def test_transforms_do_not_eat_the_first_page(client):
    test_client, project_path = client
    for i in range(4):
        _write(project_path, f'chat-{i}', 1_000_000_000 + i, f'real question {i}', 'follow-up')
    for i in range(25):
        _write(project_path, f'scribe-{i}', 2_000_000_000 + i, _transform_text())

    rows = test_client.get('/api/project/proj1/conversations?limit=3').get_json()
    sids = [r['claude_session_id'] for r in rows]
    assert sids == ['chat-3', 'chat-2', 'chat-1'], sids


def test_a_real_chat_quoting_the_fence_is_not_a_transform(client):
    """Only a ONE-turn transcript counts: a chat that pastes the fence in
    later (or discusses it, as this very bug's chat did) stays listed."""
    test_client, project_path = client
    _write(project_path, 'debug-chat', 1_000_000_000, 'why is this listed?', _transform_text())
    rows = test_client.get('/api/project/proj1/conversations?limit=5').get_json()
    assert [r['claude_session_id'] for r in rows] == ['debug-chat']


def test_include_lists_an_old_open_chat_with_its_chain(client):
    test_client, project_path = client
    _write(project_path, 'pred-0001', 900_000_000, 'I want to explore drop shipping', 'more')
    _write(project_path, 'head-0002', 950_000_000,
           '=== Prior conversation, started on claude (session pred-0001), handed off here ===\n\n'
           'User: I want to explore drop shipping\n=== End of prior conversation. ===\n\ncontinue',
           'and now TikTok')
    for i in range(6):
        _write(project_path, f'newer-{i}', 2_000_000_000 + i, f'newer chat {i}', 'x')

    without = test_client.get('/api/project/proj1/conversations?limit=3').get_json()
    assert 'head-0002' not in [r['claude_session_id'] for r in without]

    rows = test_client.get('/api/project/proj1/conversations?limit=3&include=head-0002').get_json()
    head = [r for r in rows if r['claude_session_id'] == 'head-0002']
    assert head, [r['claude_session_id'] for r in rows]
    assert head[0]['rolled_from'] == ['pred-0001']
    assert len(rows) == 3  # the included row takes a slot; it does not grow the page
    assert 'pred-0001' not in [r['claude_session_id'] for r in rows]


def test_include_is_capped(client):
    test_client, project_path = client
    for i in range(10):
        _write(project_path, f'c-{i}', 1_000_000_000 + i, f'q {i}', 'x')
    inc = ','.join(f'c-{i}' for i in range(10))
    rows = test_client.get(f'/api/project/proj1/conversations?limit=1&include={inc}').get_json()
    assert len(rows) == 5
