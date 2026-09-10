"""The secrets-write routes must refuse an unattended caller — F3/F5 of
docs/_review/2026-09-10_security.md: CLAUDE.md vault rule 3 ("Agents use
credentials; only humans create them. There is no agent-facing write path")
was enforced by nothing but convention. `PATCH /api/secrets/<name>` accepted
`allow_unattended` from any caller with no check — an agent could flip its
own policy backstop and then read the secret it just unlocked.

Asserts the EFFECT, not a flag: after a refused write, the stored record must
be byte-identical to what existed before the call (or absent, for create/
delete), per the "last bugs shipped green on the wrong assertion" instruction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    from mc.blueprints import secrets_routes
    from mc.state import agent_sessions
    secrets_store._dispensed.clear()
    agent_sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(secrets_routes.bp)
    yield app.test_client()
    agent_sessions.clear()


def _mark_unattended():
    from mc.state import agent_sessions
    agent_sessions['scheduled-1'] = {'status': 'running',
                                     'trigger_type': 'schedule',
                                     'project_id': 'mission_control'}


def _existing_record(name='reddit.password'):
    from mc import secrets_store
    return {s['name']: dict(s) for s in secrets_store.list_secrets()}.get(name)


def test_unattended_create_is_refused_and_writes_nothing(client):
    _mark_unattended()
    res = client.post('/api/secrets', json={'name': 'reddit.password',
                                            'value': SECRET})
    assert res.status_code == 403
    assert _existing_record() is None


def test_unattended_patch_cannot_flip_allow_unattended(client):
    """The exact F3 trigger: PATCH allow_unattended=true with no caller
    check. Create attended first, then try to flip the flag unattended."""
    client.post('/api/secrets', json={'name': 'reddit.password',
                                      'value': SECRET,
                                      'allow_unattended': False})
    before = _existing_record()
    assert before['allow_unattended'] is False

    _mark_unattended()
    res = client.patch('/api/secrets/reddit.password',
                       json={'allow_unattended': True})
    assert res.status_code == 403
    after = _existing_record()
    assert after == before
    assert after['allow_unattended'] is False


def test_unattended_delete_is_refused_and_secret_survives(client):
    client.post('/api/secrets', json={'name': 'reddit.password', 'value': SECRET})
    _mark_unattended()
    res = client.delete('/api/secrets/reddit.password')
    assert res.status_code == 403
    assert _existing_record() is not None


def test_unidentifiable_running_session_fails_closed(client):
    from mc.state import agent_sessions
    agent_sessions['unidentifiable-1'] = {'status': 'running',
                                          'trigger_type': None,
                                          'project_id': None}
    res = client.post('/api/secrets', json={'name': 'x.y', 'value': SECRET})
    assert res.status_code == 403
    assert _existing_record('x.y') is None


def test_manual_session_and_no_session_at_all_still_succeed(client):
    """Human path (no live session — the SPA) and a real interactive chat
    (trigger_type='manual') must both keep working."""
    res = client.post('/api/secrets', json={'name': 'a.b', 'value': SECRET})
    assert res.status_code == 200

    from mc.state import agent_sessions
    agent_sessions['manual-1'] = {'status': 'running', 'trigger_type': 'manual',
                                  'project_id': 'mission_control'}
    res = client.patch('/api/secrets/a.b', json={'description': 'renamed'})
    assert res.status_code == 200
    res = client.delete('/api/secrets/a.b')
    assert res.status_code == 200
