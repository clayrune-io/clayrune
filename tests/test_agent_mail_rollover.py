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


# --- stale snapshots (a thread silent across more than one rollover) ----------------
ANCIENT = 'native-ancient'


def pin(store, native_id, provider=None):
    """Pin the thread's snapshot to an id, as if it was last bound/rebound back then."""
    with store._tx() as db:
        snap = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
        snap['native_id'] = native_id
        snap['provider'] = provider or snap['provider']
        db.execute('UPDATE mail_continuations SET snapshot=?',
                   (json.dumps(snap, sort_keys=True, separators=(',', ':')),))


def test_a_snapshot_two_rollovers_old_is_rebound_on_an_expected_rollover(wired):
    ready(wired)
    pin(wired.store, ANCIENT)
    wired.session['_claude_transcript_history'] = [ANCIENT]      # ANCIENT -> OLD -> NEW
    assert roll(wired) == 1
    assert snapshot_native(wired.store) == NEW
    assert wired.wake.process_next(wired.app)['state'] == SUBMITTED


def test_a_history_id_without_an_expectation_does_not_rebind(wired):
    ready(wired)
    pin(wired.store, ANCIENT)
    wired.session['_claude_transcript_history'] = [ANCIENT, OLD]
    wired.session['claude_session_id'] = NEW                      # id changed, no rollover behind it
    assert rollover.native_learned(wired.session) == 0
    assert snapshot_native(wired.store) == ANCIENT
    result = wired.wake.process_next(wired.app)
    assert result['state'] == 'blocked' and result['reason'] == 'conversation_identity_changed'


def test_an_id_not_in_the_session_history_does_not_rebind(wired):
    ready(wired)
    pin(wired.store, 'native-stranger')
    wired.session['_claude_transcript_history'] = [ANCIENT]
    assert roll(wired) == 0
    assert snapshot_native(wired.store) == 'native-stranger'


def test_other_providers_keep_the_exact_left_id_match(wired):
    ready(wired)
    pin(wired.store, 'thread-stale', provider='codex')
    wired.session.update(provider='codex', provider_session_id='thread-2',
                         _claude_transcript_history=['thread-stale'])
    rollover.expect('proj1', wired.session['session_id'], 'codex', 'thread-1')
    assert rollover.native_learned(wired.session) == 0            # history is Claude-only
    assert snapshot_native(wired.store) == 'thread-stale'
    pin(wired.store, 'thread-1', provider='codex')
    rollover.expect('proj1', wired.session['session_id'], 'codex', 'thread-1')
    assert rollover.native_learned(wired.session) == 1
    assert snapshot_native(wired.store) == 'thread-2'


# --- a human Release of an identity hold rebinds that one thread first ----------------
@pytest.fixture
def release_web(wired, monkeypatch):
    from flask import Flask
    from mc.blueprints import agent_mail_holds_routes as routes, local_auth
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda v: v == 'pass')
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(store_factory=lambda: wired.store))
    client = app.test_client()
    return SimpleNamespace(client=client, routes=routes, setup=wired,
                           release=lambda: client.post(f'{routes.BASE}/mail-one/release', json={'passcode': 'pass'}))


from types import SimpleNamespace  # noqa: E402


def hold_identity(wired):
    ready(wired)
    pin(wired.store, ANCIENT)
    wired.session['claude_session_id'] = NEW
    assert wired.wake.process_next(wired.app)['reason'] == 'conversation_identity_changed'


def test_release_of_an_identity_hold_rebinds_then_delivers(release_web):
    w = release_web.setup
    hold_identity(w)
    result = release_web.release()
    assert (result.status_code, result.json['ok']) == (200, True)
    assert snapshot_native(w.store) == NEW
    assert w.wake.process_next(w.app)['state'] == SUBMITTED       # not re-blocked


def test_release_without_the_rebind_would_re_block(wired):
    hold_identity(wired)
    assert wired.store.release_hold('mail-one') == 'ready'        # the old, bare release
    assert wired.wake.process_next(wired.app)['reason'] == 'conversation_identity_changed'


def test_release_of_another_hold_reason_does_not_rebind(release_web):
    w = release_web.setup
    ready(w)
    pin(w.store, ANCIENT)
    with w.store._tx() as db:
        w.store._move(db, 'mail-one', 'ready', 'blocked', reason='session_closed')
    assert release_web.release().status_code == 200
    assert snapshot_native(w.store) == ANCIENT


def test_release_refuses_when_the_session_cannot_be_rebound(release_web):
    w = release_web.setup
    hold_identity(w)
    w.session['provider'] = 'codex'                               # snapshot is claude: no rebind possible
    result = release_web.release()
    assert (result.status_code, result.json['error']) == (409, 'cannot_rebind')
    assert w.store.get_event('mail-one')['state'] == 'blocked'
    assert snapshot_native(w.store) == ANCIENT


def test_rebind_thread_is_scoped_to_its_own_session(release_web):
    w = release_web.setup
    hold_identity(w)
    ev = w.store.get_event('mail-one')
    assert rollover.rebind_thread(w.store, ev['thread_hash'], 'proj1', 'other-session', 'claude', NEW) is False
    assert rollover.rebind_thread(w.store, ev['thread_hash'], 'proj1', ev['session_id'], 'codex', NEW) is False
    assert snapshot_native(w.store) == ANCIENT


def test_unattended_release_is_refused_and_changes_nothing(release_web, monkeypatch):
    w = release_web.setup
    hold_identity(w)
    monkeypatch.setattr(release_web.routes, 'is_unattended_caller', lambda: True)
    result = release_web.release()
    assert (result.status_code, result.json['error']) == (403, 'human_required')
    assert snapshot_native(w.store) == ANCIENT
    assert w.store.get_event('mail-one')['state'] == 'blocked'
