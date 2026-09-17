"""Offline semantics only: database transaction/CAS enforcement is NOT wired."""
from dataclasses import replace

import pytest

from mc.execution_lifecycle import (
    AttemptStatus as S, ConversationState, EvidenceDisposition as D,
    LifecycleConflict, Request, accept_request, bind_native_handle, claim_attempt,
    claim_owner, change_engine, evidence_disposition, reconcile_attempt, record_coverage,
    record_evidence, set_deleted, snapshot, transition_attempt,
    validate_derivation, validate_snapshot,
)


def running():
    state, owner = claim_owner(ConversationState('chat', 'project'), 'server-1', expected_revision=0)
    state = accept_request(state, Request('request-a', 'input-and-engine-a', 'interactive'),
                           expected_revision=state.revision)
    state, token = claim_attempt(state, owner, 'request-a', 'attempt-a',
                                 expected_revision=state.revision)
    state = transition_attempt(state, token, S.RUNNING, expected_attempt_revision=0)
    return state, owner, token


def attempt_revision(state, token):
    return next(a.revision for a in state.attempts if a.attempt_id == token.attempt_id)


def covered(state, owner, token, through):
    while state.high_water < through:
        state, _ = record_evidence(state, token, sequence=state.high_water + 1)
    return record_coverage(state, owner, high_water=through, complete=True,
                           expected_revision=state.revision)


def test_accept_while_running_neither_claims_nor_fences_receipts():
    state, owner, token = running()
    previous = state
    state = accept_request(state, Request('request-b', 'input-b'), expected_revision=state.revision)
    assert state.active_attempt == token.attempt_id
    assert state.attempts == previous.attempts
    assert evidence_disposition(state, token) == D.AUTHORITATIVE
    with pytest.raises(LifecycleConflict):
        claim_attempt(state, owner, 'request-b', 'attempt-b', expected_revision=state.revision)
    assert len(previous.requests) == 1  # reducers preserve their input


def test_request_identity_freezes_content_and_origin():
    state, _, _ = running()
    original = state.requests[0]
    assert accept_request(state, original, expected_revision=state.revision) is state
    for changed in (replace(original, content_key='other'), replace(original, origin='unattended')):
        with pytest.raises(LifecycleConflict):
            accept_request(state, changed, expected_revision=state.revision)
    with pytest.raises(ValueError):
        accept_request(state, Request('b', 'b', 'invented'), expected_revision=state.revision)


def test_two_claimants_must_reread_state_and_cannot_both_commit():
    state, owner = claim_owner(ConversationState('chat', 'project'), 'server', expected_revision=0)
    state = accept_request(state, Request('a', 'a'), expected_revision=state.revision)
    before = state.revision
    state, _ = claim_attempt(state, owner, 'a', 'attempt-a', expected_revision=before)
    with pytest.raises(LifecycleConflict):
        claim_attempt(state, owner, 'a', 'attempt-b', expected_revision=before)


def test_launch_intent_failure_is_not_a_running_completion():
    state, owner = claim_owner(ConversationState('chat', 'project'), 'server', expected_revision=0)
    state = accept_request(state, Request('a', 'a'), expected_revision=state.revision)
    state, token = claim_attempt(state, owner, 'a', 'attempt-a', expected_revision=state.revision)
    with pytest.raises(LifecycleConflict):
        transition_attempt(state, token, S.COMPLETED, expected_attempt_revision=0)
    state = transition_attempt(state, token, S.FAILED_BEFORE_LAUNCH, expected_attempt_revision=0)
    assert state.active_attempt is None
    assert state.attempts[0].status == S.FAILED_BEFORE_LAUNCH


def test_cancel_request_is_not_acknowledgement_and_blocks_new_claim():
    state, owner, token = running()
    state = transition_attempt(state, token, S.CANCEL_REQUESTED, expected_attempt_revision=1)
    assert state.active_attempt == token.attempt_id
    assert evidence_disposition(state, token) == D.AUTHORITATIVE
    with pytest.raises(LifecycleConflict):
        claim_attempt(state, owner, 'request-a', 'retry', expected_revision=state.revision)
    state = transition_attempt(state, token, S.CANCELLED, expected_attempt_revision=2)
    assert state.active_attempt is None
    assert evidence_disposition(state, token) == D.LATE


@pytest.mark.parametrize('outcome', [S.COMPLETED, S.FAILED])
def test_exit_may_win_cancellation_race(outcome):
    state, _, token = running()
    state = transition_attempt(state, token, S.CANCEL_REQUESTED, expected_attempt_revision=1)
    state = transition_attempt(state, token, outcome, expected_attempt_revision=2)
    with pytest.raises(LifecycleConflict):
        transition_attempt(state, token, S.CANCELLED, expected_attempt_revision=2)


def test_terminal_is_final_but_late_evidence_survives():
    state, owner, token = running()
    state = transition_attempt(state, token, S.COMPLETED, expected_attempt_revision=1)
    state, newer = claim_attempt(state, owner, 'request-a', 'retry', expected_revision=state.revision)
    before = state
    state, disposition = record_evidence(state, token, sequence=1)
    assert disposition == D.LATE
    assert state.active_attempt == newer.attempt_id
    assert state.attempts == before.attempts
    with pytest.raises(LifecycleConflict):
        transition_attempt(state, token, S.RUNNING, expected_attempt_revision=2)


@pytest.mark.parametrize('started', [False, True])
def test_takeover_marks_launch_or_running_uncertain_never_cancelled(started):
    state, old_owner, token = running()
    if not started:
        state = replace(state, attempts=(replace(state.attempts[0], status=S.LAUNCH_INTENT),))
    state, owner = claim_owner(state, 'server-2', expected_revision=state.revision)
    assert state.attempts[0].status == S.UNCERTAIN
    assert state.active_attempt == token.attempt_id
    assert evidence_disposition(state, token) == D.LATE
    with pytest.raises(LifecycleConflict):
        claim_attempt(state, owner, 'request-a', 'retry', expected_revision=state.revision)
    with pytest.raises(LifecycleConflict):
        reconcile_attempt(state, old_owner, token.attempt_id, S.FAILED,
                          expected_attempt_revision=attempt_revision(state, token), resolution='receipt')
    state = reconcile_attempt(state, owner, token.attempt_id, S.FAILED,
                              expected_attempt_revision=attempt_revision(state, token), resolution='exit receipt')
    assert state.active_attempt is None
    state, _ = claim_attempt(state, owner, 'request-a', 'retry', expected_revision=state.revision)
    assert state.active_attempt == 'retry'


def test_uncertain_cannot_be_blindly_adopted_or_reconciled_without_evidence():
    state, _, token = running()
    state, owner = claim_owner(state, 'new-owner', expected_revision=state.revision)
    with pytest.raises(LifecycleConflict):
        reconcile_attempt(state, owner, token.attempt_id, S.RUNNING,
                          expected_attempt_revision=2, resolution='guess')
    with pytest.raises(ValueError):
        reconcile_attempt(state, owner, token.attempt_id, S.FAILED,
                          expected_attempt_revision=2, resolution='')


def test_native_handle_is_immutable_and_idempotent():
    state, _, token = running()
    state = bind_native_handle(state, token, 'thread-a', expected_attempt_revision=1)
    assert bind_native_handle(state, token, 'thread-a', expected_attempt_revision=2) is state
    with pytest.raises(LifecycleConflict):
        bind_native_handle(state, token, 'thread-b', expected_attempt_revision=2)
    with pytest.raises(LifecycleConflict):
        transition_attempt(state, token, S.COMPLETED, expected_attempt_revision=1)


def test_delete_restore_revokes_owners_capture_and_snapshots():
    state, owner, token = running()
    state = covered(state, owner, token, 4)
    snap = snapshot(state)
    state = set_deleted(state, True, expected_revision=state.revision)
    with pytest.raises(LifecycleConflict):
        validate_snapshot(state, snap)
    with pytest.raises(LifecycleConflict):
        record_evidence(state, token, sequence=5)
    state = set_deleted(state, False, expected_revision=state.revision)
    assert state.privacy_generation == 2
    assert state.attempts[0].status == S.UNCERTAIN
    for operation in (
        lambda: validate_snapshot(state, snap),
        lambda: evidence_disposition(state, token),
        lambda: claim_attempt(state, owner, 'request-a', 'retry', expected_revision=state.revision),
    ):
        with pytest.raises(LifecycleConflict):
            operation()


def test_snapshot_has_fixed_highwater_while_new_events_arrive():
    state, owner, token = running()
    state = covered(state, owner, token, 2)
    snap = snapshot(state, after=1)
    state, _ = record_evidence(state, token, sequence=3)
    validate_snapshot(state, snap)
    assert snap.high_water == 2
    assert state.high_water == 3 and state.covered_through == 2
    assert snapshot(state).high_water == 2  # newly captured event 3 is NOT covered
    with pytest.raises(ValueError):
        snapshot(state, after=3)
    with pytest.raises(LifecycleConflict):
        validate_snapshot(state, replace(snap, high_water=3))
    validate_derivation(state, snap, current_cursor=1, processed_through=2, complete=True)
    with pytest.raises(LifecycleConflict):
        validate_derivation(state, snap, current_cursor=1, processed_through=3, complete=True)


def test_coverage_change_revokes_snapshots_and_incomplete_source_is_not_ready():
    state, owner, token = running()
    with pytest.raises(LifecycleConflict):
        snapshot(state)
    state = covered(state, owner, token, 2)
    snap = snapshot(state)
    state = record_coverage(state, owner, high_water=2, complete=False, expected_revision=state.revision)
    with pytest.raises(LifecycleConflict):
        validate_snapshot(state, snap)
    with pytest.raises(LifecycleConflict):
        snapshot(state)


@pytest.mark.parametrize('cursor,through,complete', [(0, 3, True), (1, 2, True), (0, 2, False)])
def test_partial_or_competing_derivation_never_acknowledges(cursor, through, complete):
    state, owner, token = running()
    state = covered(state, owner, token, 2)
    snap = snapshot(state)
    with pytest.raises(LifecycleConflict):
        validate_derivation(state, snap, current_cursor=cursor, processed_through=through, complete=complete)


def test_invalid_transition_or_forged_evidence_scope_refused():
    state, _, token = running()
    for target in (S.CANCELLED, S.FAILED_BEFORE_LAUNCH, S.LAUNCH_INTENT):
        with pytest.raises(LifecycleConflict):
            transition_attempt(state, token, target, expected_attempt_revision=1)
    for invalid in (replace(token, project_id='other'), replace(token, conversation_id='other'), replace(token, owner_epoch=9),
                    replace(token, attempt_id='unknown')):
        with pytest.raises(LifecycleConflict):
            evidence_disposition(state, invalid)
    with pytest.raises(LifecycleConflict):
        record_evidence(state, token, sequence=2)


def test_new_request_cannot_silently_change_requested_engine_or_settings():
    state, _, _ = running()
    for request in (Request('b', 'b', engine_key='observed-model'),
                    Request('b', 'b', settings_revision=1)):
        with pytest.raises(LifecycleConflict):
            accept_request(state, request, expected_revision=state.revision)
    with pytest.raises(ValueError):
        change_engine(state, 'other', consent_reference='', expected_revision=state.revision)
    assert state.requested_engine_key == '' and state.settings_revision == 0


def test_explicit_engine_change_freezes_active_and_queued_requests():
    state, owner, active = running()
    queued = Request('queued-old', 'old-input')
    state = accept_request(state, queued, expected_revision=state.revision)
    before_revision = state.revision
    state = change_engine(state, 'provider/model/effort/account-v2',
                          consent_reference='user-settings-event-1', expected_revision=state.revision)
    assert state.settings_revision == 1
    assert state.engine_changes[-1].consent_reference == 'user-settings-event-1'
    assert state.attempts[0].engine_key == ''
    # A retry of acceptance remains idempotent, even after settings change.
    assert accept_request(state, queued, expected_revision=state.revision) is state
    with pytest.raises(LifecycleConflict):
        change_engine(state, 'racing-choice', consent_reference='other-event',
                      expected_revision=before_revision)
    state = transition_attempt(state, active, S.COMPLETED, expected_attempt_revision=1)
    state, queued_token = claim_attempt(state, owner, queued.request_id, 'queued-attempt',
                                        expected_revision=state.revision)
    assert state.attempts[-1].engine_key == ''
    assert state.attempts[-1].settings_revision == 0
    with pytest.raises(LifecycleConflict):
        accept_request(state, Request('new', 'input', engine_key=''), expected_revision=state.revision)
    new = Request('new', 'input', engine_key=state.requested_engine_key, settings_revision=1)
    state = accept_request(state, new, expected_revision=state.revision)
    state = transition_attempt(state, queued_token, S.FAILED_BEFORE_LAUNCH,
                                expected_attempt_revision=0)
    state, _ = claim_attempt(state, owner, new.request_id, 'new-attempt', expected_revision=state.revision)
    assert state.attempts[-1].engine_key == state.requested_engine_key
    assert state.attempts[-1].settings_revision == 1


def test_coverage_cannot_attest_events_not_captured():
    state, owner, token = running()
    with pytest.raises(ValueError):
        record_coverage(state, owner, high_water=1, complete=True, expected_revision=state.revision)
    state, _ = record_evidence(state, token, sequence=1)
    state = record_coverage(state, owner, high_water=1, complete=True, expected_revision=state.revision)
    assert snapshot(state).high_water == 1
