"""MC-978: fetching one PRIOR LINK of a rollover chain, for the "Load earlier
conversation" control (static/js/conversation.js `loadEarlierConversationPart`).

That control reuses the existing GET .../transcript/<csid>/full-buffer route
(shipped for chat-search, test_chat_search_transcript.py) unmodified: it
already renders ANY given claude_session_id through
_transcript_buffer_lines_and_ts, so a link transcript's own leading handoff
block — present whenever that link is ITSELF a rollover successor of an even
older link, not just on the head — is stripped by
agent_runtime.strip_injected_preamble the same way the head's is. This test
pins that for a THREE-transcript chain (oldest -> middle -> head), so a
regression that only strips the head's own handoff (and not a middle link's)
is caught before it ships a "Load earlier conversation" button that reveals
"=== Prior conversation..." headers as if they were content.
"""
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import agent_runtime as art  # noqa: E402

_HDR = '=== Prior conversation, started on claude (session {native_id}), handed off here ==='


def _handoff(body, native_id):
    return f'{_HDR.format(native_id=native_id)}\n\n{body}\n\n{art.HANDOFF_FOOTER}'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('MC_REMOTE_ENABLED', '0')
    import server  # noqa: F401
    from mc import state as mc_state
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
        art._SESSION_ROW_CACHE.clear()


def _write(project_path, sid, mtime, turns):
    """turns: [(role, text)] as a real claude transcript, same shape
    test_conversation_rollover_lineage.py's `_write` uses."""
    d = art._CLAUDE_HOME / art.ClaudeRuntime()._encode_project_path(str(project_path))
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{sid}.jsonl'
    lines = []
    for role, text in turns:
        if role == 'user':
            lines.append({'type': 'user', 'message': {'role': 'user', 'content': text}})
        else:
            lines.append({'type': 'assistant', 'message': {'role': 'assistant',
                                                            'content': [{'type': 'text', 'text': text}]}})
    f.write_text('\n'.join(json.dumps(x) for x in lines), encoding='utf-8')
    os.utime(f, (mtime, mtime))


def _three_link_chain(project_path):
    """oldest -> middle -> head, each a real rollover (native-id handoff)."""
    _write(project_path, 'oldest', 1_000_000_000,
           [('user', 'What is our GTM channel list?'),
            ('assistant', 'Here is the list of channels.')])
    handoff_1 = _handoff(
        'User: What is our GTM channel list?\n\nAssistant: Here is the list of channels.',
        native_id='oldest')
    _write(project_path, 'middle', 1_000_000_100,
           [('user', handoff_1), ('assistant', 'continuing from the channel list'),
            ('user', 'add Reddit to it')])
    handoff_2 = _handoff(
        'User: add Reddit to it\n\nAssistant: continuing from the channel list',
        native_id='middle')
    _write(project_path, 'head', 1_000_000_200,
           [('user', handoff_2), ('assistant', 'Reddit added'),
            ('user', 'what about LinkedIn')])


def test_head_conversation_row_names_both_older_links_oldest_first(client):
    test_client, project_path = client
    _three_link_chain(project_path)
    rows = test_client.get('/api/project/proj1/conversations?limit=50').get_json()
    head = next(r for r in rows if r['claude_session_id'] == 'head')
    assert head['rolled_from'] == ['oldest', 'middle']


def test_full_buffer_of_a_middle_link_strips_its_own_handoff_header(client):
    """'middle' is ITSELF a rollover successor of 'oldest' — fetching it
    directly must not leak its own leading '=== Prior conversation...' block
    or the injected footer into what the UI renders as real chat content."""
    test_client, project_path = client
    _three_link_chain(project_path)
    resp = test_client.get('/api/project/proj1/transcript/middle/full-buffer')
    assert resp.status_code == 200
    body = resp.get_json()
    joined = '\n'.join(body['log_lines'])
    assert '=== Prior conversation' not in joined
    assert art.HANDOFF_FOOTER not in joined
    assert 'continuing from the channel list' in joined
    assert 'add Reddit to it' in joined


def test_full_buffer_of_the_oldest_link_returns_its_real_first_turn(client):
    test_client, project_path = client
    _three_link_chain(project_path)
    resp = test_client.get('/api/project/proj1/transcript/oldest/full-buffer')
    assert resp.status_code == 200
    joined = '\n'.join(resp.get_json()['log_lines'])
    assert 'What is our GTM channel list?' in joined
    assert 'Here is the list of channels.' in joined


def test_every_link_csid_in_the_chain_is_individually_fetchable(client):
    """Walks all three csids the way loadEarlierConversationPart does, one
    fetch per click — none may 404 or come back empty."""
    test_client, project_path = client
    _three_link_chain(project_path)
    for csid in ('oldest', 'middle', 'head'):
        resp = test_client.get(f'/api/project/proj1/transcript/{csid}/full-buffer')
        assert resp.status_code == 200, csid
        assert resp.get_json()['log_lines'], csid


def test_handoff_longer_than_the_5000_char_user_cap_is_still_stripped(client):
    """A real handoff turn is routinely longer than 5000 chars (Dave's
    drop_shipping_company head 55d11a4a: 7726). parse_transcript_file capped
    user text at 5000 BEFORE the renderer's strip, cutting off the footer the
    strip anchors on, so the whole replayed handoff rendered as a "> Ron:"
    bubble and the head's own first line read "=== Prior conversation...".
    The row must still chain to its predecessor, and the head must render
    only its own turns."""
    test_client, project_path = client
    _write(project_path, 'prior', 1_000_000_000,
           [('user', 'first question'), ('assistant', 'first answer')])
    long_body = 'User: first question\n\nAssistant: ' + ('first answer padding ' * 400)
    handoff = _handoff(long_body, native_id='prior')
    assert len(handoff) > 5000 and handoff.find(art.HANDOFF_FOOTER) > 5000
    _write(project_path, 'head', 1_000_000_100,
           [('user', handoff), ('assistant', 'answer after the handoff'),
            ('user', 'next real question')])
    rows = test_client.get('/api/project/proj1/conversations?limit=50').get_json()
    assert next(r for r in rows if r['claude_session_id'] == 'head')['rolled_from'] == ['prior']
    lines = test_client.get('/api/project/proj1/transcript/head/full-buffer').get_json()['log_lines']
    joined = '\n'.join(lines)
    assert '=== Prior conversation' not in joined
    assert 'padding' not in joined
    assert 'answer after the handoff' in joined
    assert 'next real question' in joined


def test_user_text_cap_still_applies_to_the_rendered_turn_and_other_callers(tmp_path):
    """Only the renderer opts out of the pre-strip cap; it re-caps after the
    strip, and the default for every other caller (handoff builder, FTS) is
    unchanged."""
    f = tmp_path / 't.jsonl'
    f.write_text(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'x' * 9000}}),
                 encoding='utf-8')
    rt = art.ClaudeRuntime()
    assert len(rt.parse_transcript_file(f)[0]['text']) == 5000
    assert len(rt.parse_transcript_file(f, user_text_cap=None)[0]['text']) == 9000
