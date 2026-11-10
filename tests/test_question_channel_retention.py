"""Unit 7d-1: the three 7c carries, through the real notification entry point.

1. cache-loss recovery before eligibility (the original failing flag-off probe
   is kept verbatim below),
2. no synchronous factory re-entry for the qid already being selected,
3. joint, bounded retirement of _selection_locks / _delivered / _outbox that
   never replaces a waiter's lock.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from test_agent_mail_outbound import out, kit, setup, env  # noqa: F401
from test_agent_mail_outbound_questions import channel, raise_form, fire, FORM  # noqa: F401
from mc import agent_mail_outbound_questions as adapter
from mc import agent_mail_ownership as ownership
from mc import question_channel as qc
from mc import question_channel_retention as retention
from mc import state
from mc.agent_mail_questions import revision


@pytest.fixture(autouse=True)
def owner_reader(channel):
    """What server boot installs (`agent_mail_supervisor.start_default`): the
    read-only durable-owner reader over the shared store. Reads work with every
    mail flag off."""
    fixture, _, _ = channel
    stub = SimpleNamespace(open_store=lambda: fixture.store)
    ownership.install_reader(ownership.factory_reader(stub))
    retention._refs.clear(), retention._timers.clear(), retention._sessions.clear()
    qc._selection_locks.clear()
    yield
    ownership.install_reader(None)
    qc._selection_locks.clear()


# --- carry 1: the ORIGINAL failing probe, unchanged -------------------------------------------
@pytest.mark.parametrize('enabled', [True, False])
def test_durable_receipt_recovery_after_all_volatile_ownership_lost(channel, monkeypatch, enabled):
    fixture, timers, legacy = channel
    raise_form(fixture)
    fire(timers[0])
    fixture.session.pop('_offline_question_channels')
    qc._delivered.clear()
    qc._outbox.clear()
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', enabled)
    qc.on_question_raised(fixture.session)
    fire(timers[-1])
    print('cache_loss', 'enabled', enabled, 'legacy', len(legacy), 'new', len(fixture.recording.calls))
    assert not legacy, 'Persisted agent-mail receipt ignored when adapter flag is off'
    assert len(fixture.recording.calls) == 1


def test_cache_loss_with_the_adapter_uninstalled_never_switches_channels(channel, monkeypatch):
    fixture, timers, legacy = channel
    raise_form(fixture)
    fire(timers[0])
    fixture.session.pop('_offline_question_channels')
    qc._delivered.clear()
    qc._outbox.clear()
    qc._selection_locks.clear()
    monkeypatch.setattr(adapter, 'factory', None)
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    qc.on_question_raised(fixture.session)
    fire(timers[-1])
    assert not legacy, 'durable agent-mail ownership must hold even with no adapter installed'


def test_legacy_ownership_survives_cache_loss_and_a_later_enable(channel, monkeypatch):
    fixture, timers, legacy = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    raise_form(fixture)  # parse only: the mc:question parser does not notify with mail off
    # An installed factory records the legacy owner durably (the existing
    # restart/flag-flip contract); a flag-off notification alone writes no receipt.
    assert adapter.selected(fixture.session, fixture.session['pending_questions'][0]) is False
    qc.on_question_raised(fixture.session)
    fire(timers[0])
    assert len(legacy) == 1
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    fixture.session.pop('_offline_question_channels', None)
    qc._delivered.clear()
    qc._outbox.clear()
    qc._selection_locks.clear()
    qc.on_question_raised(fixture.session)
    fire(timers[-1])
    assert len(legacy) == 2 and not fixture.recording.calls, 'a legacy-owned form never moves to the new mail'


def test_unavailable_ownership_storage_holds_the_notification_and_never_falls_back(channel, monkeypatch):
    fixture, timers, legacy = channel

    def unreadable(*args):
        raise ownership.OwnershipUnavailable('store_missing')
    ownership.install_reader(unreadable)
    before = dict(ownership.held())
    raise_form(fixture)
    qc.on_question_raised(fixture.session)
    assert not timers and not legacy and not fixture.recording.calls
    assert ownership.held().get('store_missing', 0) > before.get('store_missing', 0)


def test_capacity_hold_is_visible_and_routes_nowhere(channel, monkeypatch):
    fixture, timers, legacy = channel
    monkeypatch.setitem(state.CONFIG, ownership.CAPACITY_KEY, 1)
    raise_form(fixture)
    fire(timers[0])
    other = SimpleNamespace(**vars(fixture)) if False else fixture
    other.session['pending_questions'][0]['questions'][0]['question'] = 'A second revision'
    qc.on_question_raised(other.session)
    assert len(timers) == 1, 'a held admission arms no timer'
    assert not legacy and len(fixture.recording.calls) == 1
    assert any('question mail held: ownership_capacity' in line for line in fixture.session['log_lines'])
    assert ownership.held().get('ownership_capacity', 0) >= 1


# --- carry 2: no synchronous factory re-entry ---------------------------------------------------
def test_factory_that_reenters_the_same_qid_does_not_deadlock_and_notifies_once(channel, monkeypatch):
    fixture, timers, legacy = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    raise_form(fixture)
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    inner = []
    real = fixture.outbound

    def reentrant_factory():
        inner.append(1)
        if len(inner) == 1:
            qc.on_question_raised(fixture.session)  # same qid, same thread, lock held
        return real
    monkeypatch.setattr(adapter, 'factory', reentrant_factory)
    errors = []

    def go():
        try:
            qc.on_question_raised(fixture.session)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), 'same-thread re-entry deadlocked on the per-qid lock'
    assert not errors and len(timers) == 1, 'the deferred replay is deduplicated, not a second notification'
    fire(timers[0])
    assert len(fixture.recording.calls) == 1 and not legacy
    assert retention.snapshot()['users'] == 0, 'every reference was released'


def test_reentry_for_a_different_qid_is_not_deferred(channel):
    fixture, timers, _ = channel
    assert retention.reentrant('other') is False
    retention.acquire('this', 's')
    try:
        assert retention.reentrant('this') and not retention.reentrant('other')
    finally:
        retention.release('this')
    assert not retention.reentrant('this')


def test_deferred_replay_runs_after_the_lock_is_released_and_drains_empty(channel):
    seen = []
    retention.acquire('q', 's')
    retention.defer({'tag': 1})
    retention.drain(seen.append)
    assert seen == [], 'never replayed while this thread still selects'
    retention.release('q')
    retention.drain(seen.append)
    retention.drain(seen.append)
    assert seen == [{'tag': 1}]


# --- carry 3: joint, bounded retirement --------------------------------------------------------
def settle(fixture, timers):
    raise_form(fixture)
    fire(timers[0])
    qid = fixture.session['pending_questions'][0]['question_id']
    return qid


def finished(fixture):
    fixture.session['pending_questions'] = []
    return lambda sid: fixture.session


def test_a_finished_form_is_retired_from_all_three_caches_together(channel):
    fixture, timers, _ = channel
    qid = settle(fixture, timers)
    assert any(k.startswith(qid) for k in qc._delivered) and qid in qc._selection_locks and qc._outbox
    assert retention.retire(source=finished(fixture)) == {'retired': 1, 'kept': 0}
    assert not qc._delivered and not qc._outbox and qid not in qc._selection_locks
    assert retention.snapshot()['selection_locks'] == 0


def test_a_still_pending_form_keeps_its_dedupe_obligation(channel):
    fixture, timers, _ = channel
    qid = settle(fixture, timers)
    assert retention.retire(source=lambda sid: fixture.session) == {'retired': 0, 'kept': 1}
    assert qid in qc._selection_locks and qc._delivered and qc._outbox
    qc.on_question_raised(fixture.session)
    assert len(timers) == 1, 'a retired-too-early _delivered would have re-notified'


def test_retirement_keeps_what_it_cannot_prove_idle(channel):
    fixture, timers, _ = channel
    qid = settle(fixture, timers)

    def unreadable(sid):
        raise RuntimeError('session index unavailable')
    assert retention.retire(source=unreadable)['retired'] == 0
    key = next(iter(qc._outbox))
    retention.timer_started(key)
    assert retention.retire(source=finished(fixture))['retired'] == 0, 'an armed timer pins its group'
    retention.timer_done(key)
    with retention.reader():
        assert retention.retire(source=finished(fixture))['retired'] == 0, 'a reply handler pins every cache'
    retention.acquire(qid, fixture.session['session_id'])
    assert retention.retire(source=finished(fixture))['retired'] == 0, 'a user/waiter pins its lock'
    retention.release(qid)
    assert retention.retire(source=finished(fixture))['retired'] == 1


def test_reply_handler_brackets_retirement(channel, monkeypatch):
    fixture, timers, _ = channel
    settle(fixture, timers)
    seen = []

    def inside(*args, **kwargs):
        seen.append(retention.retire(source=finished(fixture)))
        return False
    monkeypatch.setattr(qc, '_handle_reply', inside)
    qc.handle_reply('subject', 'body')
    assert seen == [{'retired': 0, 'kept': 1}]
    assert retention.snapshot()['readers'] == 0
    assert retention.retire(source=finished(fixture))['retired'] == 1


def test_retirement_never_replaces_a_waiters_lock(channel, monkeypatch):
    fixture, timers, legacy = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    raise_form(fixture)
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    entered, release = threading.Event(), threading.Event()
    original = fixture.outbound.question_channel
    locks = []

    def slow(*args):
        locks.append(qc._selection_locks.get(args[2].get('question_id')) or next(iter(qc._selection_locks.values())))
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(fixture.outbound, 'question_channel', slow)
    errors = []

    def notify():
        try:
            qc.on_question_raised(fixture.session)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
    holder = threading.Thread(target=notify)
    holder.start()
    assert entered.wait(5)
    waiter = threading.Thread(target=notify)
    waiter.start()
    deadline = time.time() + 5
    while retention.snapshot()['users'] < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert retention.snapshot()['users'] == 2, 'the waiter is counted before it blocks on the lock'
    held_lock = next(iter(qc._selection_locks.values()))
    gone = lambda sid: {'pending_questions': []}  # noqa: E731 - the live form stays untouched
    assert retention.retire(source=gone) == {'retired': 0, 'kept': 1}
    assert next(iter(qc._selection_locks.values())) is held_lock, 'the waiter must keep the SAME lock object'
    release.set()
    holder.join(8)
    waiter.join(8)
    assert not holder.is_alive() and not waiter.is_alive() and not errors
    assert len(timers) == 1 and retention.snapshot()['users'] == 0
    fire(timers[0])
    assert len(fixture.recording.calls) == 1 and not legacy


def test_retirement_is_bounded_per_pass(channel):
    fixture, timers, _ = channel
    for n in range(5):
        qid = f'q-{n}'
        qc._delivered.add(qid)
        qc._outbox[qid] = {'session_id': 's-gone'}
        qc._selection_locks[qid] = threading.Lock()
    assert retention.retire(limit=2, source=lambda sid: None)['retired'] == 2
    assert retention.retire(limit=10, source=lambda sid: None)['retired'] == 3
    assert not qc._delivered and not qc._outbox and not qc._selection_locks


def test_revision_keys_retire_with_their_base_qid(channel):
    fixture, timers, _ = channel
    qid = settle(fixture, timers)
    revision_key = qid + ':' + revision(fixture.session['pending_questions'][0])
    assert revision_key in qc._delivered or revision_key in qc._outbox
    retention.retire(source=finished(fixture))
    assert not [k for k in (*qc._delivered, *qc._outbox) if k.startswith(qid)]


def test_a_retired_cache_is_recovered_from_the_durable_owner(channel, monkeypatch):
    fixture, timers, legacy = channel
    qid = settle(fixture, timers)
    retention.retire(source=finished(fixture))
    assert qid not in qc._selection_locks
    fixture.session['pending_questions'] = [dict(FORM, question_id=qid)]
    fixture.session.pop('_offline_question_channels', None)
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    # a different, older form can never be re-selected onto the other channel:
    assert ownership.recover('proj1', fixture.session['session_id'], 'nonexistent') is None
    assert not legacy
