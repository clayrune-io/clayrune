"""HR-3 exact post-merge review evidence, without git or live runtimes."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import caller_attribution as ca, hivemind_review as hr, review_lineage, state
from mc.blueprints import agent_routes as ar
from mc.merge_review_gate import ReviewRefused


@pytest.fixture
def candidate():
    return hr.Candidate(
        project_id='project', project_generation=7, hivemind_id='hm_run',
        workstream_id='ws_one', integration_attempt=1, workstream_attempt=2,
        pre_merge_sha='a' * 40, incoming_branch='clayrune/agent/hm_worker',
        incoming_tip='b' * 40, merged_sha='c' * 40, merged_tree='d' * 40,
        contributing_sessions=('earlier_worker', 'hm_worker'))


@pytest.fixture
def store(tmp_path):
    return hr.ReviewStore(tmp_path / 'data' / 'hiveminds' / 'hm_run')


@pytest.fixture
def http_request():
    return SimpleNamespace(remote_addr='127.0.0.1', environ={
        'REMOTE_PORT': '43871', 'SERVER_PORT': '5199'},
        json={'reviewer_session': 'reviewer'})


@pytest.fixture
def caller(monkeypatch):
    def set_caller(sid='reviewer', status=ca.ATTRIBUTED):
        monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(status, sid))
    set_caller()
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(state, 'tracked_processes', {})
    return set_caller


def tree(**parents):
    def lookup(sid):
        if sid not in parents:
            raise review_lineage.LineageUnreadable(f'unknown {sid}')
        return parents[sid]
    return lookup


SIBLINGS = tree(reviewer=['coordinator'], hm_worker=['coordinator'],
                earlier_worker=['coordinator'], coordinator=[])


def designate(store, candidate, reviewer='reviewer'):
    return store.designate(candidate, reviewer_session=reviewer,
                           reviewer_character='global:code-reviewer')


def record(store, candidate, http_request, *, verdict='pass', lineage=SIBLINGS, report='docs/review.md'):
    return store.record(candidate, http_request, verdict=verdict, report_path=report, spawner_of=lineage)


def test_sibling_reviewer_records_exact_pins_and_consumes_once(store, candidate, http_request, caller):
    pending = designate(store, candidate)
    assert pending['candidate'] == candidate.pins()
    assert pending['result'] is None and pending['consumed_at'] == ''
    assert store.hold_reason(candidate) == 'awaiting designated reviewer verdict'
    saved = record(store, candidate, http_request)
    assert saved['result'] == {'verdict': 'pass', 'report_path': 'docs/review.md',
                              'recorded_at': saved['result']['recorded_at'], 'attribution': 'attributed'}
    assert saved['reviewer_session'] == 'reviewer'
    assert saved['reviewer_character'] == 'global:code-reviewer'
    assert not store.hold_reason(candidate)
    assert store.consume_pass(candidate) is True
    assert store.consume_pass(candidate) is False
    assert hr.ReviewStore(store.directory.parent).consume_pass(candidate) is False


@pytest.mark.parametrize('author', ['hm_worker', 'earlier_worker'])
def test_same_author_refused_even_if_caller_body_names_reviewer(store, candidate, http_request, caller, author):
    designate(store, candidate)
    caller(author)
    with pytest.raises(ReviewRefused, match='self-review') as exc:
        record(store, candidate, http_request)
    assert exc.value.status == 403
    assert store.find(candidate)['result'] is None


@pytest.mark.parametrize('author', ['hm_worker', 'earlier_worker'])
@pytest.mark.parametrize('chain', [1, 2])
def test_descendant_of_any_contributing_author_refused(store, candidate, http_request, caller, author, chain):
    designate(store, candidate)
    parents = {'reviewer': [author], author: []}
    if chain == 2:
        parents.update(reviewer=['child'], child=[author])
    lineage = tree(**parents)
    with pytest.raises(ReviewRefused, match='descends'):
        record(store, candidate, http_request, lineage=lineage)
    assert store.find(candidate)['result'] is None


def test_native_helper_attributes_to_its_author_owner(store, candidate, http_request, caller, monkeypatch):
    designate(store, candidate)
    # OS ownership/parent walk is real. Fake only the OS process table/socket
    # reader: curl -> native child -> the managed worker process.
    class Proc:
        def __init__(self, pid):
            self.pid = pid
        def poll(self):
            return None
        def parents(self):
            return [Proc(pid) for pid in {333: [222, 111], 222: [111], 111: []}[self.pid]]
    monkeypatch.setattr(state, 'agent_sessions', {
        'hm_worker': {'session_id': 'hm_worker', 'proc': Proc(111)}})
    monkeypatch.setattr(ca, '_load_psutil', lambda: SimpleNamespace(Process=Proc))
    monkeypatch.setattr(ca, '_owner_pid_system_wide', lambda *args: 333)
    # Restore the production function replaced by caller fixture.
    monkeypatch.setattr(ca, 'attribute_caller', ORIGINAL_ATTRIBUTE)
    with pytest.raises(ReviewRefused, match='self-review'):
        record(store, candidate, http_request)


ORIGINAL_ATTRIBUTE = ca.attribute_caller


@pytest.mark.parametrize('durable', [False, True])
def test_managed_native_child_uses_existing_live_or_durable_lineage(
        store, candidate, http_request, caller, monkeypatch, tmp_path, durable):
    designate(store, candidate)
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    rows = [{'session_id': 'native_child', 'dispatched_by_session_id': 'hm_worker'},
            {'session_id': 'hm_worker', 'dispatched_by_session_id': ''}]
    if durable:
        rows.append({'session_id': 'reviewer', 'dispatched_by_session_id': 'native_child'})
    else:
        state.agent_sessions['reviewer'] = {'_spawned_by': 'native_child'}
    (tmp_path / 'project_agent_log.json').write_text(json.dumps(rows), encoding='utf-8')
    with pytest.raises(ReviewRefused, match='descends'):
        record(store, candidate, http_request, lineage=None)


@pytest.mark.parametrize('status', [ca.UNATTRIBUTED, ca.UNAVAILABLE])
def test_unknown_os_caller_refused(store, candidate, http_request, caller, status):
    designate(store, candidate)
    caller('reviewer', status)
    with pytest.raises(ReviewRefused, match='not attributed') as exc:
        record(store, candidate, http_request)
    assert exc.value.status == 403


def test_os_lookup_error_cannot_be_replaced_by_http_request_identity(store, candidate, http_request, caller, monkeypatch):
    designate(store, candidate)
    def broken(*args):
        raise RuntimeError('lookup unavailable')
    monkeypatch.setattr(ca, 'attribute_caller', broken)
    with pytest.raises(ReviewRefused, match='not attributed'):
        record(store, candidate, http_request)


@pytest.mark.parametrize('lineage', [tree(), tree(reviewer=['unknown'])])
def test_unknown_ancestry_refused(store, candidate, http_request, caller, lineage):
    designate(store, candidate)
    with pytest.raises(ReviewRefused, match='cannot establish'):
        record(store, candidate, http_request, lineage=lineage)


def test_wrong_designated_session_cannot_record_or_replay(store, candidate, http_request, caller):
    designate(store, candidate)
    record(store, candidate, http_request)
    before = store.path(candidate).read_bytes()
    caller('other_reviewer')
    with pytest.raises(ReviewRefused, match='not the designated'):
        record(store, candidate, http_request, lineage=tree(other_reviewer=[]))
    assert store.path(candidate).read_bytes() == before


PIN_CHANGES = [
    ('project_id', 'other_project'), ('project_generation', 8),
    ('hivemind_id', 'other_run'), ('workstream_id', 'other_ws'),
    ('integration_attempt', 2), ('workstream_attempt', 3),
    ('pre_merge_sha', 'e' * 40), ('incoming_branch', 'clayrune/agent/other'),
    ('incoming_tip', 'e' * 40), ('merged_sha', 'e' * 40),
    ('merged_tree', 'e' * 40), ('contributing_sessions', ('other_worker',)),
]


@pytest.mark.parametrize('field,value', PIN_CHANGES)
def test_wrong_pin_cannot_submit_or_consume_old_pass(store, candidate, http_request, caller, field, value):
    designate(store, candidate)
    record(store, candidate, http_request)
    before = store.path(candidate).read_bytes()
    changed = replace(candidate, **{field: value})
    assert changed.key != candidate.key
    assert store.hold_reason(changed)
    with pytest.raises(ReviewRefused, match='missing'):
        record(store, changed, http_request)
    with pytest.raises(ReviewRefused):
        store.consume_pass(changed)
    assert store.path(candidate).read_bytes() == before


@pytest.mark.parametrize('field,value', PIN_CHANGES)
def test_misfiled_valid_old_record_refuses_new_candidate(store, candidate, http_request, caller, field, value):
    designate(store, candidate)
    record(store, candidate, http_request)
    changed = replace(candidate, **{field: value})
    store.path(changed).write_bytes(store.path(candidate).read_bytes())
    assert 'mismatched' in store.hold_reason(changed)
    with pytest.raises(ReviewRefused):
        store.consume_pass(changed)


def test_new_attempt_does_not_inherit_earlier_pass(store, candidate, http_request, caller):
    designate(store, candidate)
    record(store, candidate, http_request)
    assert store.consume_pass(candidate)
    newer = replace(candidate, integration_attempt=2)
    designate(store, newer)
    assert store.hold_reason(newer) == 'awaiting designated reviewer verdict'
    with pytest.raises(ReviewRefused):
        store.consume_pass(newer)


def test_identical_result_replay_neither_rewrites_nor_advances(store, candidate, http_request, caller):
    designate(store, candidate)
    first = record(store, candidate, http_request)
    assert record(store, candidate, http_request) == first
    assert store.consume_pass(candidate)
    consumed = store.path(candidate).read_bytes()
    replay = record(store, candidate, http_request)
    assert replay['consumed_at']
    assert store.path(candidate).read_bytes() == consumed
    assert not store.consume_pass(candidate)


@pytest.mark.parametrize('consumed', [False, True])
@pytest.mark.parametrize('verdict,report', [('changes_requested', 'docs/review.md'), ('pass', 'other.md')])
def test_conflicting_replay_cannot_replace_pass(store, candidate, http_request, caller, consumed, verdict, report):
    designate(store, candidate)
    record(store, candidate, http_request)
    if consumed:
        store.consume_pass(candidate)
    before = store.path(candidate).read_bytes()
    with pytest.raises(ReviewRefused, match='conflicting review replay'):
        record(store, candidate, http_request, verdict=verdict, report=report)
    assert store.path(candidate).read_bytes() == before


def test_changes_requested_retained_and_never_consumable(store, candidate, http_request, caller):
    designate(store, candidate)
    record(store, candidate, http_request, verdict='changes_requested')
    assert 'provisional candidate held' in store.hold_reason(candidate)
    with pytest.raises(ReviewRefused, match='no exact passing'):
        store.consume_pass(candidate)
    with pytest.raises(ReviewRefused, match='conflicting'):
        record(store, candidate, http_request)


def test_designation_is_idempotent_but_cannot_replace_owner(store, candidate):
    first = designate(store, candidate)
    assert designate(store, candidate) == first
    with pytest.raises(ReviewRefused, match='already has'):
        designate(store, candidate, 'replacement')
    with pytest.raises(ReviewRefused, match='already has'):
        store.designate(candidate, reviewer_session='reviewer', reviewer_character='global:other')


def test_author_cannot_be_designated(store, candidate):
    with pytest.raises(ReviewRefused, match='self-review'):
        designate(store, candidate, 'hm_worker')
    assert not store.path(candidate).exists()


@pytest.mark.parametrize('payload', ['', '{truncated', '[]', '{}', 'null', '"pass"',
                                    '{"schema_version":1,"schema_version":1}',
                                    '{"schema_version": NaN}'])
def test_corrupt_store_holds_and_is_never_silently_replaced(store, candidate, http_request, caller, payload):
    store.directory.mkdir(parents=True)
    store.path(candidate).write_text(payload, encoding='utf-8')
    assert store.hold_reason(candidate)
    for operation in (lambda: designate(store, candidate), lambda: record(store, candidate, http_request),
                      lambda: store.consume_pass(candidate)):
        with pytest.raises(ReviewRefused):
            operation()
    assert store.path(candidate).read_text(encoding='utf-8') == payload


def test_missing_store_never_means_pass(store, candidate, http_request, caller):
    assert store.hold_reason(candidate)
    with pytest.raises(ReviewRefused):
        record(store, candidate, http_request)
    with pytest.raises(ReviewRefused):
        store.consume_pass(candidate)
    assert not store.path(candidate).exists()


def test_deleted_pass_after_restart_holds(store, candidate, http_request, caller):
    designate(store, candidate)
    record(store, candidate, http_request)
    store.path(candidate).unlink()
    recovered = hr.ReviewStore(store.directory.parent)
    assert recovered.hold_reason(candidate)
    with pytest.raises(ReviewRefused):
        recovered.consume_pass(candidate)


@pytest.mark.parametrize('field,value', [('schema_version', 2), ('reviewer_session', ''),
    ('reviewer_character', 'project:reviewer'), ('designated_at', ''),
    ('result', {'verdict': 'pass'}), ('consumed_at', True), ('consumed_at', 'without verdict')])
def test_structurally_corrupt_record_holds_even_with_recomputed_digest(store, candidate, field, value):
    rec = designate(store, candidate)
    rec.pop('content_digest')
    rec[field] = value
    store._write(candidate, rec)
    assert store.hold_reason(candidate)
    with pytest.raises(ReviewRefused):
        store.consume_pass(candidate)


def test_mutated_pass_digest_holds(store, candidate, http_request, caller):
    designate(store, candidate)
    rec = record(store, candidate, http_request)
    rec['result']['verdict'] = 'changes_requested'
    store.path(candidate).write_text(json.dumps(rec), encoding='utf-8')
    assert 'corrupt' in store.hold_reason(candidate)


@pytest.mark.parametrize('stage', ['designation', 'result', 'consumption'])
@pytest.mark.parametrize('failure', ['sync', 'replace'])
def test_atomic_write_failure_retains_previous_record_and_holds(
        store, candidate, http_request, caller, monkeypatch, stage, failure):
    from mc import atomic_json
    if stage != 'designation':
        designate(store, candidate)
    if stage == 'consumption':
        record(store, candidate, http_request)
    before = store.path(candidate).read_bytes() if store.path(candidate).exists() else None
    def fail(*args):
        raise OSError(f'{failure} failed')
    monkeypatch.setattr(atomic_json.os, 'fsync' if failure == 'sync' else 'replace', fail)
    with pytest.raises(ReviewRefused, match='unavailable'):
        if stage == 'designation':
            designate(store, candidate)
        elif stage == 'result':
            record(store, candidate, http_request)
        else:
            store.consume_pass(candidate)
    assert (store.path(candidate).read_bytes() if store.path(candidate).exists() else None) == before
    assert not list(store.directory.glob('*.tmp*'))


def test_atomic_write_syncs_before_replace(store, candidate, monkeypatch):
    from mc import atomic_json
    events = []
    sync, swap = atomic_json.os.fsync, atomic_json.os.replace
    def fsync(fd):
        events.append('sync')
        sync(fd)
    def replace_file(source, target):
        assert events[-1] == 'sync'
        assert Path(source).parent == Path(target).parent == store.directory
        json.loads(Path(source).read_text(encoding='utf-8'))
        events.append('replace')
        swap(source, target)
    monkeypatch.setattr(atomic_json.os, 'fsync', fsync)
    monkeypatch.setattr(atomic_json.os, 'replace', replace_file)
    designate(store, candidate)
    assert events == ['sync', 'replace']


def test_concurrent_consumers_claim_pass_only_once(store, candidate, http_request, caller):
    designate(store, candidate)
    record(store, candidate, http_request)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: hr.ReviewStore(store.directory.parent).consume_pass(candidate), range(24)))
    assert results.count(True) == 1 and results.count(False) == 23


def test_os_lock_contention_holds_instead_of_advancing(store, candidate, http_request, caller):
    designate(store, candidate)
    record(store, candidate, http_request)
    with store._transaction():
        # A second independently opened handle cannot enter while the first
        # owns the OS byte lock, as during server-process overlap.
        with pytest.raises(ReviewRefused, match='busy'):
            hr.ReviewStore(store.directory.parent).consume_pass(candidate)
    assert store.find(candidate)['consumed_at'] == ''
    assert store.consume_pass(candidate)


def test_candidate_boolean_pin_is_corruption_even_with_matching_python_equality(store, candidate):
    candidate = replace(candidate, project_generation=1)
    rec = designate(store, candidate)
    rec.pop('content_digest')
    rec['candidate']['project_generation'] = True
    store._write(candidate, rec)
    assert 'corrupt' in store.hold_reason(candidate)


@pytest.mark.parametrize('field,value', [('project_id', '../escape'), ('project_generation', True),
    ('integration_attempt', 0), ('workstream_attempt', '2'), ('merged_sha', 'abc'),
    ('merged_tree', 'D' * 40), ('contributing_sessions', ()),
    ('contributing_sessions', ('worker', 'worker')), ('incoming_branch', '-main')])
def test_invalid_candidate_refused(candidate, field, value):
    with pytest.raises(ReviewRefused):
        replace(candidate, **{field: value})


@pytest.mark.parametrize('verdict,report', [('PASS', ''), ('pass', None), ('pass', 'x' * 2001)])
def test_invalid_result_refused(store, candidate, http_request, caller, verdict, report):
    designate(store, candidate)
    with pytest.raises(ReviewRefused) as exc:
        record(store, candidate, http_request, verdict=verdict, report=report)
    assert exc.value.status == 400


def test_sha256_git_object_format_is_supported(store, candidate, http_request, caller):
    candidate = replace(candidate, pre_merge_sha='a' * 64, incoming_tip='b' * 64,
                        merged_sha='c' * 64, merged_tree='d' * 64)
    designate(store, candidate)
    record(store, candidate, http_request)
    assert store.consume_pass(candidate)


def test_contributor_order_does_not_change_identity(candidate):
    assert replace(candidate, contributing_sessions=tuple(reversed(candidate.contributing_sessions))).key == candidate.key


def test_old_branch_review_gate_still_rejects_hivemind_branch():
    from mc import merge_review_gate as gate
    assert gate.BRANCH_PREFIX == 'clayrune/agent/'
    with pytest.raises(ReviewRefused, match='not an agent branch'):
        gate.record({'id': 'project'}, 'hivemind/hm_run', sha='c' * 40,
                    verdict='pass', reviewer_session='reviewer')
