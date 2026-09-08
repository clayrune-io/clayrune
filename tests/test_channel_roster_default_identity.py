"""ws_005 — the Channel roster could not represent a session with no persona.

`docs/research/_ws005_channel_roster_audit.md`: `_convCharKey` (the frontend's
roster grouping key, `static/js/conversation.js`) returned `''` for any row
whose `character` was falsy, and `_channelRoster` skipped an empty key. Vector,
the operator's own default agent, carries `character: null` on every
conversation and live session — it is `state.CONFIG['agent_name']`, not a
hired persona — so it could never have a roster row. 14 of 15 real
`/conversations` rows and 7 of 8 real `/agent/status` sessions were affected
the day this was diagnosed live.

The Floor already resolved this correctly (`floor_routes._figure_name`/
`_figure_avatar`): explicit label, then persona's own name, then
`CONFIG['agent_name']` — with an 'unnamed' carve-out for a delegated session
with no persona (MC-925, `_delegated_unnamed`). The Channel simply never had
that last arm.

The fix extracts the shared tail of that precedence into `mc/identity.py`
(`resolve_default_identity` / `resolve_identity`) so the Floor and the
Channel roster cannot independently drift on what "no persona" means, and
emits an `identity` field — `{key, name, avatar, from}` — alongside (never
replacing) `character` on every `/conversations` row and every
`/agent/status` session. `static/js/conversation.js`'s `_convCharKey` reads
`identity.key` first, falling back to the old `character`-based key for any
payload that predates the field.

These tests exercise the real Flask routes (not the JS), and fail on the
parent commit: before `mc/identity.py` existed, a `character: null` row had
no `identity` field at all, and the two "no-character" server-level cases
below (`test_...`) would either KeyError on `row['identity']` or assert
against `None`.
"""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    monkeypatch.setitem(mc_state.CONFIG, 'agent_name', 'Vector')
    monkeypatch.setitem(mc_state.CONFIG, 'agent_avatar', 'fig:navigator')

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


CSID = 'b89b6a8e-a620-4cc7-be57-9168bbd89a58'


def _wire_transcript_row(monkeypatch, tmp_path, *, session_char, source=''):
    from mc.blueprints import agent_routes as ar
    from mc import state as mc_state

    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)} if pid == 'p1' else None))
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda path, limit=20, must_include_csids=None: [
        {'session_id': CSID, 'first_user': 'hi', 'last_user': 'hi',
         'turns': 2, 'size': 10, 'mtime': 1_760_000_000.0}])
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'claude_session_id': CSID, 'session_id': 'mc1', 'status': 'running',
         'character': None, 'source': source}])
    monkeypatch.setattr(ar, '_non_claude_conversation_rows', lambda *a, **k: [])
    mc_state.agent_sessions['mc1'] = {
        'project_id': 'p1', 'claude_session_id': CSID, 'session_id': 'mc1',
        'status': 'running', 'character': session_char, 'source': source,
    }


def test_a_no_persona_conversation_row_resolves_to_the_default_agent(client, tmp_path, monkeypatch):
    """The exact live-evidence shape from the audit: a running session with
    `character: null` must still get a roster identity — 'Vector', keyed
    constant so every such session collapses into the SAME roster row rather
    than each being individually unrepresentable."""
    _wire_transcript_row(monkeypatch, tmp_path, session_char=None)
    resp = client.get('/api/project/p1/conversations')
    rows = resp.get_json()
    assert len(rows) == 1
    row = rows[0]
    assert row['character'] is None, 'no persona was hired — character stays None'
    assert row['identity'] == {
        'key': 'default:', 'name': 'Vector', 'avatar': 'fig:navigator', 'from': 'default',
    }


def test_a_delegated_no_persona_row_does_not_claim_the_default_agent(client, tmp_path, monkeypatch):
    """MC-925: a session another agent dispatched (source='agent') with no
    persona must NOT resolve to Vector — its own prompt says it appears
    unnamed, so the roster has to agree or a delegated worker's activity gets
    misattributed to the operator's default agent."""
    _wire_transcript_row(monkeypatch, tmp_path, session_char=None, source='agent')
    resp = client.get('/api/project/p1/conversations')
    row = resp.get_json()[0]
    assert row['identity'] == {'key': 'unnamed:', 'name': 'unnamed', 'avatar': '', 'from': 'unnamed'}


def test_a_persona_row_keeps_its_existing_scope_name_key(client, tmp_path, monkeypatch):
    """A hired persona's grouping key is UNCHANGED by this fix — only the
    previously-empty key (no persona) gets a real bucket."""
    dave = {'name': 'dave', 'scope': 'global', 'agent_name': 'Dave', 'avatar': 'fig:helper'}
    _wire_transcript_row(monkeypatch, tmp_path, session_char=dave)
    resp = client.get('/api/project/p1/conversations')
    row = resp.get_json()[0]
    assert row['identity']['key'] == 'global:dave'
    assert row['identity']['name'] == 'Dave'
    assert row['identity']['from'] == 'character'


def test_agent_status_emits_identity_for_a_no_persona_live_session(client, tmp_path, monkeypatch):
    """`/agent/status` needed the same fallback — a session dispatched
    moments ago (before its first /conversations poll) still has to seed a
    roster row, and the frontend seeds bare groups straight from this
    endpoint's `character` (now `identity`) field."""
    from mc.blueprints import agent_routes as ar
    from mc import state as mc_state
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': 'p1', 'project_path': str(tmp_path)})
    mc_state.agent_sessions['mc1'] = {
        'project_id': 'p1', 'session_id': 'mc1', 'claude_session_id': '',
        'status': 'running', 'task': 'doing work', 'log_lines': [],
        'started_at': '2026-09-08T00:00:00Z', 'character': None, 'source': '',
    }
    resp = client.get('/api/project/p1/agent/status')
    sessions = resp.get_json()['sessions']
    assert len(sessions) == 1
    assert sessions[0]['character'] is None
    assert sessions[0]['identity'] == {
        'key': 'default:', 'name': 'Vector', 'avatar': 'fig:navigator', 'from': 'default',
    }
