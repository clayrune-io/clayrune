"""MC-1213: a held inbound reply always ends somewhere the owner can see.

The pinned incident: a reply held `worktree_unavailable` on 2026-11-15, one day
before the MC-1177 spawner fallback shipped. `blocked` rows are never claimed
again, so the fallback never saw it and nothing told the owner.
"""
from types import SimpleNamespace

import pytest

from mc import agent_mail_hold_alerts as alerts
from mc import agent_mail_spawner_fallback as spawner_fallback
from mc import agent_mail_supervisor
from mc.agent_mail_recovery import Recovery
from mc.agent_mail_store import BLOCKED, EXPIRED
from mc.blueprints import push_mobile
from test_agent_mail_spawner_fallback import spawned  # noqa: F401
from test_agent_mail_wake import TEXT, ready, setup  # noqa: F401
from test_runtime_completion_log import env  # noqa: F401


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    """The real Inbox writer on a temp file; push delivery is captured, not run."""
    monkeypatch.setattr(push_mobile, 'PUSH_NOTIF_PATH', tmp_path / 'notifications.json')
    pushed = []
    monkeypatch.setattr(push_mobile, '_push_queue', SimpleNamespace(submit=pushed.append))
    monkeypatch.setattr(push_mobile, 'load_project', lambda pid: {'name': pid})
    return SimpleNamespace(rows=push_mobile._load_notifications, pushed=pushed)


def held_before_the_fallback(spawned, monkeypatch):
    """Seq 3 as it sat: held by code with no fallback, spawner on record."""
    with monkeypatch.context() as old:
        old.setattr(spawner_fallback, 'try_forward', lambda *a: False)
        ready(spawned)
        result = spawned.wake.process_next(spawned.app)
    assert (result['state'], result['reason']) == (BLOCKED, 'worktree_unavailable')


def test_old_hold_is_never_rescanned_and_the_owner_is_told_once(spawned, inbox, monkeypatch):
    held_before_the_fallback(spawned, monkeypatch)
    # With the fallback back and the spawner idle, nothing picks the row up again.
    assert spawned.wake.process_next(spawned.app)['state'] == 'idle'
    assert inbox.rows() == []

    assert alerts.sweep(spawned.store) == 1
    [row] = inbox.rows()
    assert row['title'] == alerts.HELD_TITLE and row['project_id'] == 'proj1'
    assert 'working copy is gone' in row['body'] and alerts.REVIEW in row['body']
    assert 'deleted undelivered' in row['body'] and len(row['body']) <= 280
    assert row['session_id'] == ''            # a later turn in that chat cannot replace it
    assert TEXT not in str(row)
    assert len(inbox.pushed) == 1
    assert alerts.sweep(spawned.store) == 0 and len(inbox.rows()) == 1
    # Telling the owner never releases, forwards or edits the hold.
    assert spawned.store.get_event('mail-one')['state'] == BLOCKED
    assert spawned.spawner['proc'].stdin.getvalue() == ''
    assert spawned.session['proc'].stdin.getvalue() == ''


def test_expiry_of_a_hold_is_told(setup, inbox):
    ready(setup)
    setup.session['_agent_cwd'] = 'missing-tree'
    setup.wake.process_next(setup.app)
    alerts.sweep(setup.store)
    with setup.store._tx() as db:
        db.execute('UPDATE events SET expires_at=0')
    setup.store.reconcile()
    assert setup.store.get_event('mail-one')['state'] == EXPIRED
    assert alerts.sweep(setup.store) == 1
    assert [r['title'] for r in inbox.rows()].count(alerts.DROPPED_TITLE) == 1
    assert alerts.sweep(setup.store) == 0


def test_expiry_while_nothing_was_sweeping_is_still_told(setup, inbox):
    ready(setup)
    with setup.store._tx() as db:
        db.execute('UPDATE events SET expires_at=0')
    setup.store.reconcile()
    assert alerts.sweep(setup.store) == 1
    assert inbox.rows()[0]['title'] == alerts.DROPPED_TITLE
    assert len(inbox.rows()[0]['body']) <= 280


def test_revoking_a_told_hold_is_told_but_an_owner_rejection_is_not(setup, inbox):
    ready(setup)
    setup.session['_agent_cwd'] = 'missing-tree'
    setup.wake.process_next(setup.app)
    alerts.sweep(setup.store)
    setup.store.revoke_session('proj1', 'mail-session')
    assert alerts.sweep(setup.store) == 1
    assert 'revoked' in inbox.rows()[0]['body']


def test_owner_rejection_says_nothing_more(setup, inbox):
    ready(setup)
    setup.session['_agent_cwd'] = 'missing-tree'
    setup.wake.process_next(setup.app)
    alerts.sweep(setup.store)
    setup.store.reject_event('mail-one', 'rejected_by_owner')
    assert alerts.sweep(setup.store) == 0 and len(inbox.rows()) == 1
    assert alerts._load(setup.store.path.with_name(alerts.LEDGER_NAME)) == {}


def test_released_and_held_again_is_told_again(setup, inbox):
    ready(setup)
    setup.session['_agent_cwd'] = 'missing-tree'
    setup.wake.process_next(setup.app)
    alerts.sweep(setup.store)
    setup.store.clock = lambda: __import__('time').time() + 5
    setup.store.release_hold('mail-one')
    setup.wake.process_next(setup.app)
    assert alerts.sweep(setup.store) == 1


def test_failed_inbox_write_is_retried(setup, inbox, monkeypatch):
    ready(setup)
    setup.session['_agent_cwd'] = 'missing-tree'
    setup.wake.process_next(setup.app)
    with monkeypatch.context() as broken:
        broken.setattr(push_mobile, '_save_notifications',
                       lambda items: (_ for _ in ()).throw(OSError('disk')))
        assert alerts.sweep(setup.store) == 0
    assert alerts.sweep(setup.store) == 1 and len(inbox.rows()) == 1


def test_unknown_reason_text_never_reaches_the_inbox(setup, inbox):
    ready(setup)
    setup.session['_agent_cwd'] = 'missing-tree'
    setup.wake.process_next(setup.app)
    with setup.store._tx() as db:
        db.execute("UPDATE events SET reason='PRIVATE body text'")
    alerts.sweep(setup.store)
    assert 'PRIVATE' not in str(inbox.rows()) and 'unknown reason' in inbox.rows()[0]['body']


def test_the_server_recovery_runs_the_sweep_even_with_ingress_off():
    recovery = agent_mail_supervisor._default_recovery(object(), None)
    assert recovery.alerts is alerts.sweep


def test_recovery_pass_calls_the_sweep_and_reports_its_failure():
    store = SimpleNamespace(reconcile=lambda: None)
    seen = []
    assert Recovery(object(), alerts=seen.append)._inbound(store).state == 'skipped'
    assert seen == [store]
    boom = Recovery(object(), alerts=lambda s: (_ for _ in ()).throw(OSError('x')))
    assert boom._inbound(store).state == 'failed'
