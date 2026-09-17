"""Schema-2 reducer integration: actual temporary SQLite, no runtime/CLI."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from mc.conversation_store import ConversationStore, ConversationUnavailable, EventConflict, SchemaError, AttemptToken
from mc import execution_lifecycle as life


ENGINE = {'provider':'fake','model':'pinned','effort':'high','account_reference':'opaque'}


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / 'history.sqlite')


def create(store):
    return store.create_lifecycle_conversation('p','c',engine=ENGINE,event_id='created')


def accept(store, name='r', text='original user text'):
    return store.accept_request('p','c',request_id=name,user_message={'text':text},engine=ENGINE,
                                provenance={'origin':'interactive','trigger':'manual'},
                                expected_revision=store.lifecycle_state('p','c').revision,event_id='accept-'+name)


def running(store):
    create(store)
    state = accept(store)
    state,owner = store.claim_owner('p','c',owner_id='owner1',expected_revision=state.revision,event_id='owner')
    state,attempt = store.claim_attempt(owner,request_id='r',attempt_id='a',expected_revision=state.revision,event_id='claim')
    state = store.transition_attempt(attempt,life.AttemptStatus.RUNNING,expected_attempt_revision=0,event_id='running')
    return state,owner,attempt


def evidence(store, attempt, event_id='text'):
    return store.append_evidence(attempt,event_id=event_id,kind='assistant_message',
        payload={'message_id':'msg','block_id':'block','text':'complete answer','completeness':'final'})


def test_accepted_input_keeps_active_attempt_and_original_provenance(store):
    state,owner,attempt = running(store)
    state = accept(store,'b','queued input')
    assert state.active_attempt == 'a'
    assert len(state.requests) == 2
    assert evidence(store,attempt)[1] == life.EvidenceDisposition.AUTHORITATIVE
    with pytest.raises(life.LifecycleConflict):
        store.claim_attempt(owner,request_id='b',attempt_id='b-attempt',expected_revision=store.lifecycle_state('p','c').revision,event_id='b-claim')
    reopened = ConversationStore(store.db_path)
    request = reopened.read_request('p','c','b')
    assert request['user_message'] == {'text':'queued input'}
    assert request['engine'] == ENGINE
    assert request['provenance'] == {'origin':'interactive','trigger':'manual'}
    assert reopened.lifecycle_state('p','c').owner_epoch == owner.owner_epoch


def test_two_instances_race_same_owner_revision_one_wins(store):
    create(store)
    def claim(index):
        try:
            return ConversationStore(store.db_path).claim_owner('p','c',owner_id=f'owner-{index}',expected_revision=0,event_id=f'owner-{index}')
        except life.LifecycleConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim,range(8)))
    assert len([r for r in results if r is not None]) == 1
    assert len(store.read_events('p','c')) == 2


def test_owner_takeover_is_explicit_and_keeps_old_evidence(store):
    state,owner,attempt = running(store)
    reopened = ConversationStore(store.db_path)
    assert reopened.lifecycle_state('p','c') == state
    state,new_owner = reopened.claim_owner('p','c',owner_id='new-owner',expected_revision=state.revision,event_id='takeover')
    assert state.attempts[0].status == life.AttemptStatus.UNCERTAIN
    assert evidence(reopened,attempt)[1] == life.EvidenceDisposition.LATE
    with pytest.raises(life.LifecycleConflict):
        reopened.transition_attempt(attempt,life.AttemptStatus.COMPLETED,expected_attempt_revision=2,event_id='stale-done')
    state = reopened.reconcile_attempt(new_owner,'a',life.AttemptStatus.FAILED,expected_attempt_revision=2,resolution='operator inspected failed process',event_id='resolved')
    assert state.active_attempt is None


def test_terminal_result_race_keeps_receipt_without_reopening(store):
    _,_,attempt = running(store)
    def finish():
        return ConversationStore(store.db_path).transition_attempt(attempt,life.AttemptStatus.COMPLETED,expected_attempt_revision=1,event_id='completed')
    def receipt():
        return evidence(ConversationStore(store.db_path),attempt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish),pool.submit(receipt)]
        [f.result() for f in futures]
    state = store.lifecycle_state('p','c')
    assert state.attempts[0].status == life.AttemptStatus.COMPLETED
    assert state.active_attempt is None
    late,disposition = evidence(store,attempt,'late')
    assert disposition == life.EvidenceDisposition.LATE
    assert late.disposition == 'late' and late.protocol_version == 1
    assert len({e.sequence for e in store.read_events('p','c')}) == state.high_water+1


def test_delete_restore_revokes_capture_owner_and_snapshot(store):
    state,owner,attempt = running(store)
    state = store.record_coverage(owner,high_water=state.high_water,complete=True,source_reference='source-verified',expected_revision=state.revision,event_id='coverage')
    snapshot = store.snapshot('p','c')
    state = store.set_lifecycle_deleted('p','c',True,expected_revision=state.revision,event_id='delete')
    assert store.read_events('p','c') == []
    with pytest.raises(life.LifecycleConflict):
        store.read_snapshot(snapshot)
    with pytest.raises(life.LifecycleConflict):
        evidence(store,attempt)
    state = store.set_lifecycle_deleted('p','c',False,expected_revision=state.revision,event_id='restore')
    assert state.active_attempt == 'a' and state.attempts[0].status == life.AttemptStatus.UNCERTAIN
    with pytest.raises(life.LifecycleConflict):
        evidence(store,attempt)
    with pytest.raises(life.LifecycleConflict):
        store.claim_attempt(owner,request_id='r',attempt_id='new',expected_revision=state.revision,event_id='new')


def test_snapshot_highwater_is_fixed_and_coverage_change_revokes(store):
    state,owner,attempt = running(store)
    covered = state.high_water
    state = store.record_coverage(owner,high_water=covered,complete=True,source_reference='source',expected_revision=state.revision,event_id='coverage')
    token = store.snapshot('p','c',after=1)
    evidence(store,attempt)
    page = store.read_snapshot(token,limit=2)
    rest = store.read_snapshot(token,after=page[-1].sequence)
    assert [e.sequence for e in page+rest] == list(range(2,covered+1))
    assert all(e.protocol_version == 1 for e in page+rest)
    state = store.lifecycle_state('p','c')
    store.record_coverage(owner,high_water=state.high_water,complete=False,source_reference='capture-gap',expected_revision=state.revision,event_id='gap')
    with pytest.raises(life.LifecycleConflict):
        store.read_snapshot(token)


def test_event_and_state_write_are_atomic(store,monkeypatch):
    state = create(store)
    def fail(*args,**kwargs):
        raise OSError('injected event failure')
    monkeypatch.setattr(store,'_lifecycle_event',fail)
    with pytest.raises(OSError):
        accept(store)
    assert store.lifecycle_state('p','c') == state
    assert store.read_request('p','c','r') is None


def test_legacy_mutators_cannot_bypass_managed_lifecycle(store):
    state,owner,attempt = running(store)
    for operation in [
        lambda:store.begin_attempt('p','c',user_message={'text':'bypass'},engine={},event_id='bad'),
        lambda:store.append_event(AttemptToken('p','c','a'),event_id='bad',kind='assistant_message',payload={'text':'bypass'}),
        lambda:store.delete_conversation('p','c'),
        lambda:store.restore_conversation('p','c')]:
        with pytest.raises(life.LifecycleConflict):
            operation()
    assert store.lifecycle_state('p','c') == state
    with pytest.raises(ValueError):
        store.append_evidence(attempt,event_id='fake-control',kind='lifecycle.attempt_transitioned',payload={})


def test_engine_change_freezes_queued_input(store):
    state,owner,attempt = running(store)
    new_engine = dict(ENGINE,model='new')
    state = store.change_engine('p','c',engine=new_engine,consent_reference='user-event-42',expected_revision=state.revision,event_id='engine-change')
    assert store.read_request('p','c','r')['engine'] == ENGINE
    assert state.attempts[0].engine_key != state.requested_engine_key
    with pytest.raises(life.LifecycleConflict):
        accept(store,'old-engine-request')


def test_duplicate_evidence_idempotency_and_conflict(store):
    _,_,attempt = running(store)
    first = evidence(store,attempt)
    assert evidence(store,attempt) == first
    with pytest.raises(EventConflict):
        store.append_evidence(attempt,event_id='text',kind='assistant_message',payload={'message_id':'msg','block_id':'block','text':'changed','completeness':'final'})


def make_schema1(store):
    store.begin_attempt('p','legacy',user_message={'text':'legacy content'},engine=ENGINE,event_id='legacy-start')
    with sqlite3.connect(store.db_path) as db:
        for table in ('capture_spans','capture_sources','lifecycle_event_meta','lifecycle_engine_changes','lifecycle_attempts','lifecycle_requests','lifecycle_conversations'):
            db.execute(f'DROP TABLE {table}')
        db.execute('PRAGMA user_version=1')


def test_explicit_backed_up_migration_preserves_legacy_events(store,tmp_path):
    make_schema1(store)
    before = store.read_events('p','legacy')
    with pytest.raises(SchemaError):
        create(store)
    backup = tmp_path / 'before.sqlite'
    store.migrate_schema1(backup_path=backup)
    assert store.read_events('p','legacy') == before
    assert ConversationStore(backup).read_events('p','legacy') == before
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    create(store)
    with pytest.raises(life.LifecycleConflict):
        store.create_lifecycle_conversation('p','legacy',engine=ENGINE,event_id='convert')


def test_migration_rejects_existing_backup_and_wrong_shape(store,tmp_path):
    make_schema1(store)
    backup = tmp_path / 'existing.sqlite'
    backup.write_bytes(b'keep existing backup')
    with pytest.raises(ValueError):
        store.migrate_schema1(backup_path=backup)
    with sqlite3.connect(store.db_path) as db:
        db.execute('ALTER TABLE requests ADD COLUMN unexpected TEXT')
    before = store.db_path.read_bytes()
    with pytest.raises(SchemaError):
        store.migrate_schema1(backup_path=tmp_path/'new.sqlite')
    assert store.db_path.read_bytes() == before
    assert not (tmp_path/'new.sqlite').exists()


def test_incognito_does_not_create_store(store):
    assert store.create_lifecycle_conversation('p','c',engine=ENGINE,event_id='x',incognito=True) is None
    assert not store.db_path.exists()


def test_foreign_project_token_cannot_append(store):
    _,_,attempt = running(store)
    with pytest.raises(ConversationUnavailable):
        evidence(store,replace(attempt,project_id='foreign'))
    assert store.read_events('foreign','c') == []


def test_migration_failure_rolls_back_source_and_leaves_valid_backup(store,tmp_path,monkeypatch):
    make_schema1(store)
    before = store.read_events('p','legacy')
    original = store._create_lifecycle_schema
    def fail(db):
        original(db)
        raise OSError('injected after schema creation')
    monkeypatch.setattr(store,'_create_lifecycle_schema',fail)
    backup = tmp_path/'before.sqlite'
    with pytest.raises(OSError):
        store.migrate_schema1(backup_path=backup)
    assert store.read_events('p','legacy') == before
    assert ConversationStore(backup).read_events('p','legacy') == before
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'lifecycle_%'").fetchall()


def test_full_history_includes_protocol_metadata_without_complete_coverage(store):
    _,_,attempt = running(store)
    evidence(store,attempt)
    events = store.read_events('p','c')
    assert events[-1].protocol_version == 1
    assert events[-1].disposition == 'authoritative'
    assert events[-1].payload['text'] == 'complete answer'
    with pytest.raises(life.LifecycleConflict):
        store.snapshot('p','c')


def test_terminal_race_allows_only_one_outcome(store):
    _,_,attempt = running(store)
    def finish(status):
        try:
            return ConversationStore(store.db_path).transition_attempt(attempt,status,expected_attempt_revision=1,event_id=status.value)
        except life.LifecycleConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish,[life.AttemptStatus.COMPLETED,life.AttemptStatus.FAILED]))
    assert len([r for r in results if r is not None]) == 1
    assert store.lifecycle_state('p','c').active_attempt is None
