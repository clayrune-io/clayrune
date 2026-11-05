"""Phase-1 telemetry tests: no processes, routes, server imports or live state."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from mc import atomic_json
from mc import test_run_store as runs


@pytest.fixture
def store(tmp_path):
    return runs.TestRunStore(tmp_path / 'data')


@pytest.fixture
def run(store):
    return store.create('project-a', observed_at=100, owner_session_id='session-a')


def event(run, kind, seq=1, producer='reporter', at=101, **payload):
    return dict(schema=1, run_id=run['run_id'], producer_id=producer,
                producer_seq=seq, observed_at=at, type=kind, payload=payload)


def apply(store, run, kind, seq=1, **payload):
    return store.apply(run['project_id'], run['run_id'], event(run, kind, seq, **payload))


def report(store, run, seq, name='test_case', attempt=1, phase='call',
           outcome='pass', final=True, revision=1):
    return apply(store, run, 'unit_report', seq, unit_id=name, attempt=attempt,
                 phase=phase, outcome=outcome, final=final, revision=revision)


def test_empty_run_has_no_invented_counts_total_eta_or_percent(store, run):
    assert len(run['run_id']) == 32
    assert run['state'] == 'running'
    assert run['seq'] == run['completed'] == 0
    assert set(run['counts'].values()) == {0}
    assert run['selected_total'] is run['percent'] is run['eta_seconds'] is None
    assert store.get('project-a', run['run_id']) == run
    assert store.root.name == 'test_runs'
    assert store.root.parent.name == 'data'


def test_ids_are_per_launch_not_per_identical_command(store):
    first = store.create('a', observed_at=1, display_label='pytest')
    second = store.create('a', observed_at=1, display_label='pytest')
    assert first['run_id'] != second['run_id']


def test_reporter_finish_waits_for_process_exit_and_late_cleanup_can_fail(store, run):
    apply(store, run, 'collection', selected_total=1, revision=1)
    snapshot = report(store, run, 2)
    assert snapshot['percent'] == 100
    snapshot = apply(store, run, 'run_finished', 3, state='passed', exit_code=0)
    assert snapshot['state'] == 'running'
    assert snapshot['exit_code'] is None
    assert snapshot['reporter_state'] == 'passed'
    final = store.finish('project-a', run['run_id'], state='error', exit_code=3, observed_at=110)
    assert final['state'] == 'error'
    assert final['exit_code'] == 3


@pytest.mark.parametrize('state,code', [
    ('passed', 0), ('failed', 1), ('error', 3), ('cancelled', None),
    ('timeout', None), ('no_tests', 5), ('lost', None),
])
def test_all_terminal_states_are_immutable_even_after_reload(store, run, state, code):
    terminal = store.finish('project-a', run['run_id'], state=state,
                            exit_code=code, observed_at=110)
    restarted = runs.TestRunStore(store.root.parent)
    assert apply(restarted, run, 'run_started', 10, reporter_version='late') == terminal
    assert report(restarted, run, 11) == terminal
    assert apply(restarted, run, 'collection', 12, selected_total=500, revision=2) == terminal
    assert restarted.get('project-a', run['run_id']) == terminal
    assert len(restarted.events('project-a', run['run_id'])) == 1
    assert restarted.finish('project-a', run['run_id'], state='lost', observed_at=999) == terminal


@pytest.mark.parametrize('state,code', [('passed', None), ('passed', 1),
                                        ('failed', None), ('no_tests', None)])
def test_process_verdict_requires_exit_evidence(store, run, state, code):
    with pytest.raises(ValueError):
        store.finish('project-a', run['run_id'], state=state, exit_code=code, observed_at=110)
    assert store.get('project-a', run['run_id']) == run


def test_reporter_cannot_spoof_process_evidence(store, run):
    with pytest.raises(ValueError, match='finish'):
        store.apply('project-a', run['run_id'], event(
            run, 'run_finished', producer='_store_process', state='passed', exit_code=0))


def test_same_event_is_noop_and_conflicting_identity_raises_after_reload(store, run):
    message = event(run, 'unit_report', unit_id='one', attempt=1, phase='call',
                    outcome='pass', final=True, revision=1)
    first = store.apply('project-a', run['run_id'], message)
    restarted = runs.TestRunStore(store.root.parent)
    assert restarted.apply('project-a', run['run_id'], message) == first
    assert len(restarted.events('project-a', run['run_id'])) == 1
    message['payload']['outcome'] = 'fail'
    with pytest.raises(ValueError, match='Conflicting'):
        restarted.apply('project-a', run['run_id'], message)


def test_out_of_order_events_do_not_drop_earlier_unseen_units(store, run):
    report(store, run, 5, name='five')
    snapshot = report(store, run, 2, name='two')
    assert snapshot['completed'] == snapshot['counts']['pass'] == 2
    assert snapshot['seq'] == 2


def test_phase_replay_under_new_event_id_does_not_inflate_counts(store, run):
    report(store, run, 1)
    snapshot = report(store, run, 2)
    assert snapshot['completed'] == snapshot['counts']['pass'] == 1
    assert snapshot['seq'] == 2


def test_retry_replaces_logical_unit_and_ignores_delayed_previous_attempt(store, run):
    report(store, run, 1, outcome='fail')
    snapshot = apply(store, run, 'unit_started', 2, unit_id='test_case', attempt=2)
    assert snapshot['completed'] == snapshot['counts']['fail'] == 0
    assert snapshot['latest_failures'] == []
    snapshot = report(store, run, 3, attempt=2)
    assert snapshot['completed'] == snapshot['counts']['pass'] == 1
    snapshot = report(store, run, 4, attempt=1, outcome='error', revision=3)
    assert snapshot['completed'] == snapshot['counts']['pass'] == 1
    assert snapshot['counts']['error'] == 0
    # A delayed start for the same attempt cannot undo its final result.
    snapshot = apply(store, run, 'unit_started', 5, unit_id='test_case', attempt=2)
    assert snapshot['completed'] == 1


def test_report_revision_prevents_reordered_pass_overwriting_failure(store, run):
    report(store, run, 1, outcome='fail', revision=2)
    snapshot = report(store, run, 2, revision=1)
    assert snapshot['counts']['fail'] == 1


@pytest.mark.parametrize('bad_phase', ['setup', 'call', 'teardown'])
def test_failure_names_arrive_before_final_logical_completion(store, run, bad_phase):
    snapshot = report(store, run, 1, phase=bad_phase, outcome='error', final=False)
    assert snapshot['completed'] == 0
    assert snapshot['latest_failures'] == [{'name': 'test_case', 'phase': bad_phase, 'attempt': 1}]
    snapshot = report(store, run, 2, phase='finish')
    assert snapshot['completed'] == snapshot['counts']['error'] == 1
    assert snapshot['counts']['pass'] == 0


def test_passing_call_followed_by_failed_teardown_is_one_failed_unit(store, run):
    report(store, run, 1, final=False)
    snapshot = report(store, run, 2, phase='teardown', outcome='error')
    assert snapshot['counts']['error'] == snapshot['completed'] == 1
    terminal = store.finish('project-a', run['run_id'], state='passed', exit_code=0, observed_at=110)
    assert terminal['state'] == 'failed'


def test_unfinished_known_failure_cannot_be_finalized_as_pass(store, run):
    report(store, run, 1, outcome='fail', final=False)
    terminal = store.finish('project-a', run['run_id'], state='passed', exit_code=0, observed_at=110)
    assert terminal['state'] == 'failed'
    assert terminal['completed'] == 0


def test_conflicting_report_and_collection_revisions_are_rejected(store, run):
    report(store, run, 1)
    with pytest.raises(ValueError, match='revision'):
        report(store, run, 2, outcome='fail')
    apply(store, run, 'collection', 3, selected_total=1, revision=1)
    with pytest.raises(ValueError, match='revision'):
        apply(store, run, 'collection', 4, selected_total=2, revision=1)


@pytest.mark.parametrize('kind,payload', [
    ('collection', {}), ('unit_report', {'unit_id': 'one'}),
    ('diagnostic', {'code': []}), ('run_finished', {'state': []}),
    ('unit_report', dict(unit_id='one', attempt=1, phase='call', outcome=[], final=True, revision=1)),
])
def test_malformed_payloads_raise_value_error_without_state_changes(store, run, kind, payload):
    with pytest.raises(ValueError):
        apply(store, run, kind, **payload)
    assert store.get('project-a', run['run_id']) == run


def test_event_byte_limit_applies_before_persistence(store, run, monkeypatch):
    monkeypatch.setattr(runs, 'MAX_EVENT_BYTES', 128)
    with pytest.raises(ValueError, match='size limit'):
        apply(store, run, 'heartbeat')
    assert store.get('project-a', run['run_id']) == run


@pytest.mark.parametrize('outcome', runs.OUTCOMES)
def test_all_outcomes_count_once(store, run, outcome):
    snapshot = report(store, run, 1, outcome=outcome)
    assert snapshot['counts'][outcome] == snapshot['completed'] == 1


def test_totals_can_increase_decrease_or_become_unknown(store, run):
    apply(store, run, 'collection', selected_total=2, revision=1, deselected=3)
    assert report(store, run, 2)['percent'] == 50
    snapshot = apply(store, run, 'collection', 3, selected_total=4, revision=2)
    assert snapshot['percent'] == 25
    assert snapshot['total_recalculated'] is True
    assert snapshot['deselected'] == 3
    snapshot = apply(store, run, 'collection', 4, selected_total=1, revision=3)
    assert snapshot['percent'] == 100
    snapshot = apply(store, run, 'collection', 5, selected_total=99, revision=1)
    assert snapshot['selected_total'] == 1
    snapshot = apply(store, run, 'collection', 6, selected_total=None, revision=4)
    assert snapshot['percent'] is None
    assert snapshot['eta_seconds'] is None


def test_inconsistent_or_zero_totals_never_invent_percent(store, run):
    apply(store, run, 'collection', selected_total=0, revision=1)
    assert report(store, run, 2)['percent'] is None


def test_collection_errors_and_not_run_are_separate_from_completed(store, run):
    apply(store, run, 'collection', selected_total=200, revision=1, collection_errors=2)
    report(store, run, 2, outcome='fail')
    terminal = store.finish('project-a', run['run_id'], state='failed', exit_code=1,
                            observed_at=120, not_run=199)
    assert terminal['completed'] == 1
    assert terminal['collection_errors'] == 2
    assert terminal['not_run'] == 199
    assert terminal['percent'] == 0.5


def test_reporter_health_is_independent_of_process_verdict(store, run):
    apply(store, run, 'diagnostic', code='reporter_overflow', capture='partial')
    snapshot = store.finish('project-a', run['run_id'], state='passed', exit_code=0, observed_at=120)
    assert snapshot['state'] == 'passed'
    assert snapshot['capture'] == 'partial'


def test_heartbeat_and_completion_ages_are_separate_and_monotone(store, run):
    report(store, run, 1)
    heartbeat = event(run, 'heartbeat', 2, at=120, output=True)
    snapshot = store.apply('project-a', run['run_id'], heartbeat)
    assert snapshot['last_completion_at'] == 101
    assert snapshot['last_output_at'] == snapshot['updated_at'] == 120
    snapshot = store.apply('project-a', run['run_id'], event(run, 'heartbeat', 3, at=110, output=True))
    assert snapshot['last_output_at'] == snapshot['updated_at'] == 120


def test_replay_matches_persisted_state_and_is_deterministic(store, run):
    apply(store, run, 'run_started', reporter_version='1')
    apply(store, run, 'collection', 2, selected_total=1, revision=1)
    report(store, run, 3)
    snapshot = store.finish('project-a', run['run_id'], state='passed', exit_code=0, observed_at=120)
    events = store.events('project-a', run['run_id'])
    assert runs.replay(run_enrollment(store, run), events) == snapshot
    assert runs.replay(run_enrollment(store, run), events + events) == snapshot
    assert runs.TestRunStore(store.root.parent).get('project-a', run['run_id']) == snapshot


def run_enrollment(store, run):
    return json.loads(store._path('project-a', run['run_id']).read_text())['enrollment']


def test_cross_project_reads_writes_lists_and_parent_references_are_isolated(store, run):
    other = store.create('project-b', observed_at=100, owner_session_id='session-a')
    with pytest.raises(KeyError):
        store.get('project-b', run['run_id'])
    with pytest.raises(KeyError):
        store.apply('project-b', run['run_id'], event(run, 'heartbeat'))
    with pytest.raises(KeyError):
        store.create('project-b', observed_at=100, parent_run_id=run['run_id'])
    report(store, run, 1, name='identical_name')
    report(store, other, 1, name='identical_name', outcome='fail')
    assert store.list_runs('project-a')[0]['counts']['pass'] == 1
    assert store.list_runs('project-b')[0]['counts']['fail'] == 1
    assert store.list_runs('project-a', owner_session_id='other') == []


def test_getters_and_input_never_share_mutable_state(store, run):
    message = event(run, 'diagnostic', code='reporter_lost')
    result = store.apply('project-a', run['run_id'], message)
    result['diagnostics'].clear()
    message['payload']['code'] = 'changed'
    assert store.get('project-a', run['run_id'])['diagnostics'][0]['code'] == 'reporter_lost'


def test_atomic_replace_failure_preserves_previous_snapshot_and_dedupe(store, run, monkeypatch):
    target = store._path('project-a', run['run_id'])
    previous = target.read_bytes()
    def torn_replace(tmp, path):
        assert path == target
        from pathlib import Path
        assert json.loads(Path(tmp).read_text(encoding='utf-8'))['snapshot']['completed'] == 1
        raise OSError('injected crash before replace')
    with monkeypatch.context() as patch:
        patch.setattr(atomic_json, '_replace_with_retry', torn_replace)
        with pytest.raises(OSError, match='injected crash'):
            report(store, run, 1)
    assert target.read_bytes() == previous
    assert runs.TestRunStore(store.root.parent).get('project-a', run['run_id']) == run
    # Failed events were not marked seen in memory and can be retried verbatim.
    assert report(store, run, 1)['completed'] == 1


def test_torn_temp_file_is_ignored_not_replayed(store, run):
    target = store._path('project-a', run['run_id'])
    target.with_name('.' + target.name + '.orphan.tmp99999').write_text('{"snapshot":')
    assert store.list_runs('project-a') == [run]


@pytest.mark.parametrize('content', ['', '{"schema":', '\x00' * 50, '[]', '{}'])
def test_corrupt_record_surfaces_without_reset_or_success(store, run, content):
    path = store._path('project-a', run['run_id'])
    path.write_text(content)
    with pytest.raises(runs.StoreError):
        store.get('project-a', run['run_id'])
    with pytest.raises(runs.StoreError):
        store.list_runs('project-a')
    assert path.read_text() == content


def test_parseable_torn_or_tampered_snapshot_is_rejected(store, run):
    target = store._path('project-a', run['run_id'])
    record = json.loads(target.read_text())
    record['snapshot']['state'] = 'passed'
    target.write_text(json.dumps(record))
    with pytest.raises(runs.StoreError, match='Invalid'):
        store.get('project-a', run['run_id'])


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(schema=2), lambda e: e.update(run_id='f' * 32),
    lambda e: e.update(producer_seq=0), lambda e: e.update(producer_seq=True),
    lambda e: e.update(observed_at=float('nan')), lambda e: e.update(type='command'),
    lambda e: e['payload'].update(owner_session_id='intruder'),
    lambda e: e['payload'].update(stdout='secret'),
])
def test_invalid_events_cannot_mutate_snapshot(store, run, mutate):
    message = event(run, 'heartbeat')
    mutate(message)
    with pytest.raises(ValueError):
        store.apply('project-a', run['run_id'], message)
    assert store.get('project-a', run['run_id']) == run


def test_concurrent_store_instances_do_not_lose_events(store, run):
    another = runs.TestRunStore(store.root.parent)
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda i: report(
            store if i % 2 else another, run, i + 1, name=f'test_{i}'), range(16)))
    assert max(s['seq'] for s in snapshots) == 16
    assert store.get('project-a', run['run_id'])['completed'] == 16


def test_bounded_failure_details_and_diagnostics_disclose_omissions(store, run, monkeypatch):
    monkeypatch.setattr(runs, 'MAX_DETAILS', 2)
    for i in range(4):
        report(store, run, i + 1, name=f'test_{i}', outcome='fail')
        apply(store, run, 'diagnostic', i + 10, code='reporter_gap')
    snapshot = store.get('project-a', run['run_id'])
    assert snapshot['counts']['fail'] == snapshot['failure_total'] == 4
    assert len(snapshot['latest_failures']) == snapshot['failure_details_omitted'] == 2
    assert len(snapshot['diagnostics']) == snapshot['diagnostics_omitted'] == 2
    assert len(store.events('project-a', run['run_id'])) == 8


def test_path_injection_and_data_projects_root_are_refused(store, run, tmp_path):
    with pytest.raises(ValueError):
        store.get('project-a', '../elsewhere')
    with pytest.raises(ValueError):
        runs.TestRunStore(tmp_path / 'data' / 'projects')
    with pytest.raises(ValueError):
        runs.TestRunStore(tmp_path / 'data' / 'projects' / 'nested')
    assert store.list_runs('..\\project-a') == []


def test_project_symlink_cannot_escape_store(store, run, tmp_path):
    target = store._path('project-a', run['run_id'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    # Use a run-file symlink: no cleanup ever traverses a shared directory.
    link = store._path('project-a', 'f' * 32)
    try:
        link.symlink_to(outside / 'record.json')
    except OSError:
        pytest.skip('Symlink creation unavailable')
    with pytest.raises(runs.StoreError, match='escapes'):
        store.get('project-a', 'f' * 32)
    assert target.is_file()


def test_ancestor_named_projects_is_not_confused_with_records_dir(tmp_path):
    store = runs.TestRunStore(tmp_path / 'projects' / 'app' / 'data')
    assert store.create('a', observed_at=1)['state'] == 'running'


def test_list_pagination_and_limits(store):
    records = [store.create('a', observed_at=i) for i in range(4)]
    assert store.list_runs('a', limit=2, offset=1) == records[1:3][::-1]
    for kwargs in ({'limit': 101}, {'limit': 0}, {'offset': -1}):
        with pytest.raises(ValueError):
            store.list_runs('a', **kwargs)
