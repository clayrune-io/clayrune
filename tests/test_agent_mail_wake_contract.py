"""Unit 6 pre-integration contract: late acknowledgments cross a lease boundary.

This exercises the real durable store, not a provider or the future wake adapter.
"""
import pytest

from mc.agent_mail_store import (IllegalTransition, MailStore, SUBMITTED,
                                 TURN_COMPLETED, UNCERTAIN)


@pytest.mark.parametrize('startup', [False, True])
def test_late_ack_requires_positive_receipt_reconciliation(tmp_path, startup):
    now = [1_000_000.0]
    store = MailStore(tmp_path / 'mail.sqlite', clock=lambda: now[0])
    store.register('installation')
    store.bind_thread('installation', 'synthetic-thread-token', 'project', 'session')
    store.insert_event(event_id='reply', registration_id='installation',
                       thread_token='synthetic-thread-token', message_id='<reply@example.invalid>',
                       body='Friday works for me.')
    extraction = store.claim_for_laundering()
    assert extraction is not None
    store.complete_laundering('reply', extraction['fence_token'],
                              {'reply_text': 'Friday works for me.'})
    submission = store.claim_for_submission()
    assert submission is not None
    now[0] = submission['lease_until']

    assert store.reconcile(startup=startup)['uncertain'] == 1
    event = store.get_event('reply', include_content=True)
    assert event is not None and event['state'] == UNCERTAIN
    assert event['laundered']['reply_text'] == 'Friday works for me.'
    assert store.claim_for_submission() is None  # no blind replay
    with pytest.raises(IllegalTransition):
        store.mark_submitted('reply', submission['fence_token'], {'turn_id': 'actual-attempt'})
    with pytest.raises(ValueError):
        store.reconcile_uncertain('reply', {'turn_id': 'actual-attempt', 'queued': True})

    # A future adapter must persist/correlate this real attempt before calling
    # the existing recovery seam. HTTP success alone does not provide it.
    store.reconcile_uncertain('reply', {'turn_id': 'actual-attempt'})
    assert store.get_event('reply')['state'] == SUBMITTED
    with pytest.raises(ValueError):
        store.complete_turn('reply', 'unrelated-attempt')
    store.complete_turn('reply', 'actual-attempt')
    assert store.get_event('reply')['state'] == TURN_COMPLETED
