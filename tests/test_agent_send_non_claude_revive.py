"""P0 regression: `/agent/send`'s fresh-dispatch branch never tried the
non-Claude revive (hm_d9c76579 f_1e1909aa).

`_revive_non_claude_from_agent_log` (mc/blueprints/agent_routes.py) existed
and worked correctly, but had exactly ONE caller — `agent_followup`
(`/api/.../agent/followup`, fixed by 73c4a05). The unified router the chat
composer actually uses, `/agent/send`, only tried `_revive_from_agent_log`
(the Claude `-r` path, which returns None for a non-Claude row by design —
it has no claude_session_id to resume with) and then fell straight to a
brand-new `_dispatch_agent_internal` call with no `reuse_session_id`, no
`provider_override`, and no `character`.

REPRODUCED LIVE 2026-09-08: a dead gemini/market-scout conversation, replied
to via `/agent/send`, came back as claude+no-persona under a BRAND NEW
session id. Four things changed silently: session_id, provider, character,
and the fact that it's a different conversation at all — nothing in the
response or the UI said so.

This is deliberately NOT `test_revive_non_claude_character_ref.py`'s trap:
that file calls `_revive_non_claude_from_agent_log` directly and proves the
helper is correct — which was already true before this fix, since nothing
called it from `/agent/send`. This test drives the ACTUAL route the chat
composer POSTs to (`agent/send`) end-to-end through the Flask test client,
the same way ws001's live repro did, and fails on the parent commit.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; agent_routes global-scope deps patched on the MODULE.
    Mirrors tests/test_agent_routes.py's `client` fixture."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


def test_agent_send_revives_dead_non_claude_conversation(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)

    dead_session_id = '830e481c6a6e'
    log_entry = {
        'session_id': dead_session_id,
        'provider': 'gemini',
        'claude_session_id': '',
        'character': {'name': 'market-scout', 'scope': 'global'},
        'ts': '2026-09-08T01:20:00Z',
    }
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [log_entry])

    captured = {}

    def _fake_dispatch(project_id, message, **kw):
        captured['project_id'] = project_id
        captured['message'] = message
        captured.update(kw)
        # The real function returns the freshly-minted session id; the
        # revive path passes reuse_session_id so it comes back as the SAME
        # id the caller asked about.
        return kw.get('reuse_session_id') or 'brand-new-wrong-session'

    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fake_dispatch)

    resp = client.post(f'/api/project/p1/agent/send', json={
        'session_id': dead_session_id,
        'message': 'Reply with exactly one word: ok',
    })

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    # The conversation must not be silently replaced by a different one.
    assert body.get('session_id') == dead_session_id, (
        f"conversation identity changed: asked about {dead_session_id!r}, "
        f"got back {body.get('session_id')!r} — this is the disappearing-"
        f"conversation symptom")

    # _dispatch_agent_internal must have been reached via the REVIVE path,
    # not the bare fresh-dispatch fallback: it carries reuse_session_id and
    # the provider/character recovered from the dead conversation's log row.
    assert captured.get('reuse_session_id') == dead_session_id, (
        "dispatch was not a revive (no reuse_session_id) — fell through to "
        "the fresh-dispatch branch that replaces the conversation")
    assert captured.get('provider_override') == 'gemini', (
        f"provider silently changed: expected 'gemini', dispatch got "
        f"{captured.get('provider_override')!r}")
    assert captured.get('character') == 'global:market-scout', (
        f"persona silently dropped: expected 'global:market-scout', "
        f"dispatch got {captured.get('character')!r}")
