"""Channel/Conversations list after a restart (Ron, 2026-09-24): Dave's list
showed one rolled-over chat as many rows, and most rows were labelled
"=== Prior conversation, sta..." instead of Ron's own message.

Root causes pinned here (the third, the scheduled-run drop, is client-side and
lives in tools/smoke/conversation-persona-filter.mjs):

  #2 duplicates — auto-fresh / mid-task rollover continues ONE chat in a NEW
     claude transcript and recorded no link, so the restart backfill gave the
     old transcript its own agent-log row and it listed as its own chat.
     `_rollover_lineage` now links successor → predecessor (native id in the
     handoff header; legacy handoffs by their replayed final turn) and both
     /conversations and /agent/log collapse the chain onto its head.
  #3 labels — `strip_injected_preamble` stripped only bracketed markers; the
     handoff block, the per-turn STANDING POSITIONS / RELEVANT MEMORY block,
     the REPLY SHAPE tail and the mid-task rollover state all survived into
     the label.
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
from mc.midturn_rollover import ROLL_MESSAGE  # noqa: E402

HDR_LEGACY = '=== Prior conversation, started on claude, handed off here ==='


def _handoff(body, native_id=None):
    hdr = (f'=== Prior conversation, started on claude (session {native_id}), handed off here ==='
           if native_id else HDR_LEGACY)
    return f'{hdr}\n\n{body}\n\n{art.HANDOFF_FOOTER}'


# ── #3: labels come from Ron's message, not MC's injected blocks ─────────────

def test_strip_removes_handoff_block_and_keeps_the_message():
    text = _handoff('User: earlier ask\n\nAssistant: earlier reply') + '\n\nmission_control BUG: list is wrong'
    assert art.strip_injected_preamble(text) == 'mission_control BUG: list is wrong'


def test_strip_is_greedy_to_the_last_footer_when_a_handoff_quotes_one():
    inner = _handoff('User: first\n\nAssistant: ok')
    text = _handoff('User: ' + inner + '\n\nAssistant: fine') + '\n\nreal message'
    assert art.strip_injected_preamble(text) == 'real message'


def test_strip_removes_per_turn_memory_and_reply_shape_blocks():
    text = ('--- STANDING POSITIONS (already decided) ---\n  • SPLIT-BY-LIFESPAN: ...\n\n'
            '--- RELEVANT MEMORY (re-surfaced for this message) ---\n  • [naming.md] mascot\n\n'
            'give me the SSH installation command for windows PS\n\n'
            '--- REPLY SHAPE (binding) ---')
    out = art.strip_injected_preamble(text)
    assert out.startswith('give me the SSH installation command for windows PS'), out


def test_strip_removes_mid_task_rollover_state_and_roll_message():
    text = ('=== Mid-task rollover state ===\n--- Original task ---\nfix it\n'
            '=== End of mid-task rollover state ===\n\n' + ROLL_MESSAGE)
    assert art.strip_injected_preamble(text) == ''


def test_strip_leaves_a_plain_message_alone():
    msg = 'ok, another try, what model are you running now?'
    assert art.strip_injected_preamble(msg) == msg


def test_handoff_lineage_reads_both_header_forms():
    assert art.handoff_lineage(_handoff('User: a', native_id='abc-123'))[:2] == ('claude', 'abc-123')
    prov, nid, body = art.handoff_lineage(_handoff('User: a\n\nAssistant: b'))
    assert (prov, nid) == ('claude', '') and body.strip() == 'User: a\n\nAssistant: b'
    assert art.handoff_lineage('just a message') is None


# ── #2: lineage links a rollover's transcripts into one chain ───────────────

def _row(sid, mtime, **kw):
    return {'session_id': sid, 'mtime': mtime, **kw}


def test_lineage_by_explicit_native_id_and_chain_head():
    from mc.blueprints.agent_routes import _rollover_lineage
    rows = [_row('A', 1), _row('B', 2, handoff={'provider': 'claude', 'native_id': 'A', 'body_tail': ''}),
            _row('C', 3, handoff={'provider': 'claude', 'native_id': 'B', 'body_tail': ''})]
    assert _rollover_lineage(rows) == {'A': 'C', 'B': 'C'}


def test_legacy_handoff_matched_by_replayed_final_turn():
    from mc.blueprints.agent_routes import _rollover_lineage
    tail = art.lineage_tail('Assistant: the 44-row backlog table')
    rows = [_row('OLD', 1, last_turn_tail=tail),
            _row('OTHER', 1, last_turn_tail=art.lineage_tail('Assistant: unrelated')),
            _row('NEW', 2, handoff={'provider': 'claude', 'native_id': '',
                                    'body_tail': art.lineage_tail('User: x\n\nAssistant: the 44-row backlog table')})]
    assert _rollover_lineage(rows) == {'OLD': 'NEW'}


def test_legacy_match_never_links_to_a_newer_transcript():
    from mc.blueprints.agent_routes import _rollover_lineage
    tail = art.lineage_tail('Assistant: done')
    rows = [_row('NEWER', 5, last_turn_tail=tail),
            _row('SUCC', 2, handoff={'provider': 'claude', 'native_id': '', 'body_tail': tail})]
    assert _rollover_lineage(rows) == {}


# ── route level: one row per chain, real labels ─────────────────────────────

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
    log = []
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [dict(e) for e in log])
    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client(), project_path, log
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)
        art._SESSION_ROW_CACHE.clear()


def _write(project_path, sid, mtime, turns):
    """turns: [(role, text)] as a real claude transcript."""
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


def _rolled_pair(project_path, legacy):
    _write(project_path, 'pred', 1_000_000_000,
           [('user', 'Few things about the channel list'), ('assistant', 'Here is the 44-row table')])
    handoff = _handoff('User: Few things about the channel list\n\nAssistant: Here is the 44-row table',
                       native_id=None if legacy else 'pred')
    _write(project_path, 'head', 1_000_000_100,
           [('user', handoff + '\n\n' + ROLL_MESSAGE), ('assistant', 'continuing'),
            ('user', 'give me the SSH command')])


@pytest.mark.parametrize('legacy', [False, True])
def test_conversations_lists_a_rolled_chat_once_with_real_labels(client, legacy):
    test_client, project_path, _log = client
    _rolled_pair(project_path, legacy)
    rows = test_client.get('/api/project/proj1/conversations?limit=50').get_json()
    sids = [r['claude_session_id'] for r in rows]
    assert sids.count('head') == 1 and 'pred' not in sids, sids
    head = next(r for r in rows if r['claude_session_id'] == 'head')
    assert head['rolled_from'] == ['pred']
    assert head['first_user'] == 'Few things about the channel list'
    assert head['last_user'] == 'give me the SSH command'
    assert not head['label'].startswith('===')


def test_agent_log_marks_the_older_link_and_labels_rows(client):
    test_client, project_path, log = client
    _rolled_pair(project_path, legacy=True)
    log += [{'session_id': 'm1', 'claude_session_id': 'pred', 'task': 'Few things', 'ts': '2026-09-22T00:00:00Z'},
            {'session_id': 'm2', 'claude_session_id': 'head', 'synthesized': True,
             'task': HDR_LEGACY + '\n...', 'ts': '2026-09-23T00:00:00Z'}]
    rows = {e['claude_session_id']: e for e in test_client.get('/api/project/proj1/agent/log').get_json()}
    assert rows['pred'].get('rolled_into') == 'head'
    assert 'rolled_into' not in rows['head']
    assert rows['head']['last_user'] == 'give me the SSH command'
    assert rows['head']['first_user'] == 'Few things about the channel list'


def test_agent_log_keeps_an_older_link_whose_head_has_no_row(client):
    """Hiding a predecessor whose head nothing lists would make the chat vanish."""
    test_client, project_path, log = client
    _rolled_pair(project_path, legacy=False)
    log += [{'session_id': 'm1', 'claude_session_id': 'pred', 'task': 'Few things', 'ts': '2026-09-22T00:00:00Z'}]
    rows = {e['claude_session_id']: e for e in test_client.get('/api/project/proj1/agent/log').get_json()}
    assert 'rolled_into' not in rows['pred']
