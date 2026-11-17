"""The startup backfill must know every transcript a conversation has used (MC-1179 S1).

A real agent-log row carries `claude_session_id` (the CURRENT transcript) and
`claude_session_ids` (every transcript the conversation has used: one more per
context rollover or fresh-session resume). The backfill and the commit-side
guard in `mc/agent_log_file.merge_rows` only read the first key, so each earlier
transcript of a conversation still in the log looked unknown and was re-imported
as a fake `synthesized` / `interrupted` row, evicting real rows from the capped
log and queueing a bogus `_(interrupted)_` scribe call per row.

Safety: tmp data dir, ephemeral MC_PORT, transcripts are stubbed.
"""
from __future__ import annotations

import json
import sys

import pytest

from mc import agent_log_file


@pytest.fixture
def server_module(tmp_path, monkeypatch):
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MC_PORT', '0')
    sys.modules.pop('server', None)
    import server
    return server


def _real_row(current, history):
    return {'ts': '2026-11-16T10:00:00Z', 'task': 'real chat', 'status': 'completed',
            'session_id': 'mcsid1', 'claude_session_id': current,
            'claude_session_ids': history, 'scribed': True}


def _transcript(csid):
    return {'session_id': csid, 'mtime': 9e9, 'first_user': 'hi',
            'last_user': 'bye', 'turns': 3, 'size': 10}


def _run_backfill(server, tmp_path, rows, transcripts):
    log = server.DATA_DIR / 'pid_agent_log.json'
    log.write_text(json.dumps(rows), encoding='utf-8')
    server._recent_claude_transcripts = lambda *a, **k: transcripts
    project_dir = tmp_path / 'proj'
    project_dir.mkdir(exist_ok=True)
    added = server._backfill_agent_log_from_transcripts(
        'pid', {'project_path': str(project_dir)})
    return added, json.loads(log.read_text(encoding='utf-8'))


def test_backfill_skips_an_earlier_transcript_of_a_rolled_over_conversation(server_module, tmp_path):
    """The S1 failure: 'old' is in the real row's claude_session_ids, so it is
    known; only the current id was being checked."""
    added, rows = _run_backfill(
        server_module, tmp_path,
        [_real_row('new', ['old', 'new'])],
        [_transcript('new'), _transcript('old')])
    assert added == 0
    assert [r['claude_session_id'] for r in rows] == ['new']
    assert not any(r.get('synthesized') for r in rows)


def test_backfill_still_imports_a_transcript_no_row_lists(server_module, tmp_path):
    """Positive control: the fix widens what counts as known, not what is skipped."""
    added, rows = _run_backfill(
        server_module, tmp_path,
        [_real_row('new', ['old', 'new'])],
        [_transcript('new'), _transcript('old'), _transcript('stranger')])
    assert added == 1
    synthesized = [r for r in rows if r.get('synthesized')]
    assert [r['claude_session_id'] for r in synthesized] == ['stranger']


def test_commit_guard_refuses_a_synthesized_row_for_a_listed_transcript():
    """merge_rows is the second blind spot: the insert guard compared only
    `claude_session_id`."""
    real = _real_row('new', ['old', 'new'])
    fake = {'ts': '2026-11-16T09:00:00Z', 'claude_session_id': 'old',
            'synthesized': True, 'session_id': '', 'status': 'interrupted'}
    rows = [dict(real)]
    applied = agent_log_file.merge_rows(rows, [], [fake])
    assert applied == 0
    assert len(rows) == 1


def test_commit_guard_still_inserts_an_unlisted_synthesized_row():
    real = _real_row('new', ['old', 'new'])
    fresh = {'ts': '2026-11-16T09:00:00Z', 'claude_session_id': 'stranger',
             'synthesized': True, 'session_id': '', 'status': 'interrupted'}
    rows = [dict(real)]
    assert agent_log_file.merge_rows(rows, [], [fresh]) == 1
    assert len(rows) == 2
