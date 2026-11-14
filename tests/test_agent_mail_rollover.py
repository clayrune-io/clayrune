"""A mail thread bound to a chat survives Clayrune's own auto-fresh rollover.

Reuses the unit-6 wake fixture (real follow-up routes, synthetic SQLite).
Live incident 2026-11-14: a reply cleared laundering, then was held with
conversation_identity_changed because the chat had rolled to a new native id.
"""
import json

import pytest

from test_runtime_completion_log import env  # noqa: F401  (fixture)
from test_agent_mail_wake import TOKEN, setup, ready  # noqa: F401  (fixture + helpers)
from mc import agent_mail_rollover as rollover
from mc.agent_mail_store import MailStore, SUBMITTED, hash_thread_token
from mc.agent_mail_wake import MailWake

OLD, NEW = 'native-original', 'native-rolled'


@pytest.fixture(autouse=True)
def _clean_pending(monkeypatch):
    rollover._pending.clear()
    yield
    rollover._pending.clear()


@pytest.fixture
def wired(setup, monkeypatch):
    monkeypatch.setattr(rollover, '_store', lambda: setup.store)
    return setup


def snapshot_native(store):
    with store._read() as db:
        row = db.execute('SELECT snapshot FROM mail_continuations WHERE thread_hash=?',
                         (hash_thread_token(TOKEN),)).fetchone()
    return json.loads(row['snapshot'])['native_id']


def roll(setup, new=NEW):
    """What a real rollover does: expect at the handoff, then the fresh id lands."""
    rollover.expect('proj1', setup.session['session_id'], 'claude', OLD)
    setup.session['claude_session_id'] = new
    return rollover.native_learned(setup.session)


def test_rollover_rebinds_and_the_reply_is_delivered(wired):
    ready(wired)
    assert roll(wired) == 1
    assert snapshot_native(wired.store) == NEW
    assert wired.wake.process_next(wired.app)['state'] == SUBMITTED


def test_without_rollover_a_native_id_change_still_holds(wired):
    ready(wired)
    wired.session['claude_session_id'] = NEW      # no expect(): not a rollover
    assert rollover.native_learned(wired.session) == 0
    assert snapshot_native(wired.store) == OLD
    result = wired.wake.process_next(wired.app)
    assert result['state'] == 'blocked' and result['reason'] == 'conversation_identity_changed'
    assert wired.session['proc'].stdin.getvalue() == ''


@pytest.mark.parametrize('session_id,provider,old', [
    ('some-other-session', 'claude', OLD),     # Clayrune session id differs
    ('mail-session', 'codex', OLD),            # provider differs
    ('mail-session', 'claude', 'not-the-bound-id'),   # left a different native id
])
def test_rebind_only_touches_an_exact_match(wired, session_id, provider, old):
    ready(wired)
    assert rollover.rebind(wired.store, 'proj1', session_id, provider, old, NEW) == 0
    assert snapshot_native(wired.store) == OLD
    wired.session['claude_session_id'] = NEW
    assert wired.wake.process_next(wired.app)['reason'] == 'conversation_identity_changed'


def test_expectation_is_per_session_provider_and_expires(wired, monkeypatch):
    ready(wired)
    rollover.expect('proj1', 'some-other-session', 'claude', OLD)   # another chat rolled
    wired.session['claude_session_id'] = NEW
    assert rollover.native_learned(wired.session) == 0
    rollover.expect('proj1', wired.session['session_id'], 'codex', OLD)   # wrong provider
    assert rollover.native_learned(wired.session) == 0
    monkeypatch.setattr(rollover, 'PENDING_TTL_SECONDS', -1)           # stale expectation
    rollover.expect('proj1', wired.session['session_id'], 'claude', OLD)
    assert rollover.native_learned(wired.session) == 0
    assert snapshot_native(wired.store) == OLD


def test_old_id_re_announced_does_not_consume_the_expectation(wired):
    ready(wired)
    rollover.expect('proj1', wired.session['session_id'], 'claude', OLD)
    assert rollover.native_learned(wired.session) == 0        # still OLD: fresh id not here yet
    wired.session['claude_session_id'] = NEW
    assert rollover.native_learned(wired.session) == 1


def test_rebind_is_durable_across_a_store_reopen(wired):
    ready(wired)
    assert roll(wired) == 1
    reopened = MailStore(wired.store.path)
    assert snapshot_native(reopened) == NEW
    assert MailWake(reopened, enabled=True).process_next(wired.app)['state'] == SUBMITTED


def test_blocked_event_is_not_auto_released_but_can_be_after_rebind(wired):
    ready(wired)
    wired.session['claude_session_id'] = NEW
    assert wired.wake.process_next(wired.app)['reason'] == 'conversation_identity_changed'
    wired.session['claude_session_id'] = OLD       # replay the order: rebind after the hold
    assert roll(wired) == 1
    assert wired.store.get_event('mail-one')['state'] == 'blocked'   # rebind never releases
    assert wired.wake.process_next(wired.app)['state'] == 'idle'
    assert wired.store.release_hold('mail-one') == 'ready'
    assert wired.wake.process_next(wired.app)['state'] == SUBMITTED


def test_a_second_rollover_rebinds_again(wired):
    ready(wired)
    assert roll(wired) == 1
    rollover.expect('proj1', wired.session['session_id'], 'claude', NEW)
    wired.session['claude_session_id'] = 'native-rolled-again'
    assert rollover.native_learned(wired.session) == 1
    assert snapshot_native(wired.store) == 'native-rolled-again'


def test_rebind_keeps_a_later_bind_idempotent(wired):
    ready(wired)
    assert roll(wired) == 1
    wired.wake.bind(TOKEN)          # re-snapshots the live session: must equal the rebound one


def test_real_handoff_and_stream_reader_drive_the_rebind(wired, monkeypatch):
    ar = wired.ar
    monkeypatch.setattr(ar, '_build_handoff_context', lambda *a, **kw: ('handoff', {}))
    monkeypatch.setattr(ar, '_in_flight_children', lambda *a: [])
    ready(wired)
    ar._auto_fresh_handoff(str(wired.tmp_path), 'claude', OLD, 'proj1',
                           wired.session['session_id'], reason='tokens', detail=200000)
    assert snapshot_native(wired.store) == OLD            # nothing moves before the fresh id exists
    with monkeypatch.context() as m:     # the agent-log write is not under test here
        m.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
        ar._note_claude_sid(wired.session, NEW)
    assert snapshot_native(wired.store) == NEW
    assert wired.wake.process_next(wired.app)['state'] == SUBMITTED


def test_non_claude_init_hook_rebinds_on_provider_session_id(wired, monkeypatch):
    ar = wired.ar
    monkeypatch.setattr(ar, '_update_agent_log', lambda *a, **k: None)
    with wired.store._tx() as db:     # a codex-shaped snapshot for the same chat
        snap = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
        snap.update(provider='codex', native_id='thread-1')
        db.execute('UPDATE mail_continuations SET snapshot=?',
                   (json.dumps(snap, sort_keys=True, separators=(',', ':')),))
    wired.session.update(provider='codex', provider_session_id='thread-2')
    rollover.expect('proj1', wired.session['session_id'], 'codex', 'thread-1')
    ar._runtime_note_init(None, wired.session)
    assert snapshot_native(wired.store) == 'thread-2'
