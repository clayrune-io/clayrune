"""HTTP-level tests for the backup-surface unattended gate.

``mc.blueprints.backup_routes._is_unattended`` used to read a request header
(``X-Clayrune-Trigger-Type``) that nothing in the codebase ever sent — the
function always returned False, so every unattended gate on this surface
(vault export, restore, import, rollback) was silently off. Any caller,
including an unattended steward/scheduled/hivemind session, could dump a
project's vault-scoped secrets via ``POST /api/backup/export-project/<id>``
regardless of a secret's ``allow_unattended`` flag.

The fix reuses MC-923's source of truth (the `trigger_type` MC itself
recorded on a session at dispatch time, in `mc.state.agent_sessions` —
see `agent_routes.py:3802`'s `/api/session/trigger-type`) instead of a
self-reported header. These tests drive the real Flask route and assert on
the actual behaviour (archive produced or not, response status), not on an
internal flag, per the "last two bugs got through on a test that asserted
the wrong property" instruction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import backup as bk  # noqa: E402
from mc.state import agent_sessions  # noqa: E402
from tests.test_backup_phase2 import _git, _make_machine  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    paths = _make_machine(tmp_path, 'unattended_gate', monkeypatch)
    root = paths['root']

    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-q')
    _git(checkout, 'config', 'user.email', 'a@b.c')
    _git(checkout, 'config', 'user.name', 'test')
    (checkout / 'tracked.txt').write_text('tracked', encoding='utf-8')
    _git(checkout, 'add', 'tracked.txt')
    _git(checkout, 'commit', '-q', '-m', 'init')

    (root / 'data' / 'projects' / 'proj_x.json').write_text(
        json.dumps({'id': 'proj_x', 'project_path': str(checkout), 'backlog': []}),
        encoding='utf-8')

    from mc import secrets_store as ss
    ss.set_secret('proj_x.token', 'PLAINTEXT-VAULT-SECRET', scope='proj_x')

    agent_sessions.clear()

    from mc.blueprints import backup_routes
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    yield app.test_client()
    agent_sessions.clear()


def _export(client, **body_over):
    body = {'vault': True, 'vault_passphrase': 'hunter2'}
    body.update(body_over)
    return client.post('/api/backup/export-project/proj_x', json=body)


def _vault_archives_on_disk() -> list[Path]:
    dest = Path(bk.effective_backup_dir())
    if not dest.is_dir():
        return []
    return list(dest.glob('*-SECRETS.crbackup'))


def test_unattended_session_refuses_vault_export_no_archive_written(client):
    """A live scheduled/steward/hivemind session for this project must block
    the vault export outright — checked by the ARCHIVE, not a status flag:
    _resolve_vault_choice raises before any file is written, so nothing with
    the secret in it should exist on disk afterward."""
    agent_sessions['unattended-1'] = {
        'status': 'running', 'project_id': 'proj_x', 'trigger_type': 'schedule',
    }
    res = _export(client)
    assert res.status_code != 200
    assert 'attended-only' in (res.get_json() or {}).get('error', '')
    assert _vault_archives_on_disk() == []


def test_unidentifiable_running_session_fails_closed(client):
    """A running session that can't prove it's manual (missing/blank
    trigger_type — e.g. a revived session that dropped it, see
    _note_claude_sid) must be treated as unattended, not given the lenient
    'manual' default the rest of agent_routes.py uses for display."""
    agent_sessions['unidentifiable-1'] = {
        'status': 'running', 'project_id': 'proj_x', 'trigger_type': None,
    }
    res = _export(client)
    assert res.status_code != 200
    assert _vault_archives_on_disk() == []


def test_interactive_manual_session_still_succeeds(client):
    """A real chat session (trigger_type='manual') driving this call must
    keep working — the gate targets unattended triggers, not agents."""
    agent_sessions['manual-1'] = {
        'status': 'running', 'project_id': 'proj_x', 'trigger_type': 'manual',
    }
    res = _export(client)
    assert res.status_code == 200
    archives = _vault_archives_on_disk()
    assert len(archives) == 1
    assert b'PLAINTEXT-VAULT-SECRET' not in archives[0].read_bytes()


def test_no_agent_session_at_all_is_the_spa_case_and_still_succeeds(client):
    """No Claude CLI session is running at all (nothing to find in
    agent_sessions) is the browser SPA's own shape of request — it must not
    start refusing just because the new gate exists."""
    res = _export(client)
    assert res.status_code == 200
    assert len(_vault_archives_on_disk()) == 1


def test_unattended_session_on_a_different_project_does_not_block_this_one(client):
    """The gate is scoped by project_id (this route already names one in its
    URL) — an unrelated unattended session on another project must not block
    an interactive export of THIS project."""
    agent_sessions['other-project-unattended'] = {
        'status': 'running', 'project_id': 'some_other_project', 'trigger_type': 'schedule',
    }
    res = _export(client)
    assert res.status_code == 200
    assert len(_vault_archives_on_disk()) == 1


def test_idle_unattended_session_does_not_block_export(client):
    """A session that finished its turn (status != 'running') cannot be the
    one making this HTTP call right now, so it must not count."""
    agent_sessions['idle-unattended'] = {
        'status': 'idle', 'project_id': 'proj_x', 'trigger_type': 'schedule',
    }
    res = _export(client)
    assert res.status_code == 200
    assert len(_vault_archives_on_disk()) == 1
