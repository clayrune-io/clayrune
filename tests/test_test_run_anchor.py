"""Durable anchor identity, enrollment wiring and failure isolation."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import threading
from types import SimpleNamespace

import pytest

from mc import state, test_run_anchor as anchor, test_run_detection as detection
from mc.core import TimestampedLines
from mc.test_run_capture import RunCapture
from mc.test_run_ingest import OwnerBinding, TestRunIngest
from mc.test_run_store import TestRunStore
from test_test_run_routes import env as route_env


@pytest.fixture
def env(tmp_path, monkeypatch):
    session = dict(project_id='p', session_id='s')
    monkeypatch.setattr(state, 'CONFIG', {anchor.FLAG: True})
    monkeypatch.setattr(state, 'agent_sessions', {'s': session})
    store = TestRunStore(tmp_path / 'data')
    return store, session


def create(store, owner='s'):
    return store.create('p', observed_at=100, owner_session_id=owner)


def test_one_timestamped_line_per_run_and_independent_ids(env):
    store, session = env
    first, second = create(store), create(store)
    for run in (first, second):
        assert anchor.post(store, 'p', run['run_id'])
        assert not anchor.post(store, 'p', run['run_id'])
        assert store.get('p', run['run_id']) == run  # No snapshot/replay change.
    lines = session['log_lines']
    assert isinstance(lines, TimestampedLines)
    assert lines == [f"[test-run:{r['run_id']}]" for r in (first, second)]
    assert len(lines.ts) == 2 and all(lines.ts)
    assert session['last_output_time'] > 0


def test_concurrent_store_instances_claim_only_once(env):
    store, session = env
    run = create(store)
    def deliver(_):
        return anchor.post(TestRunStore(store.root.parent), 'p', run['run_id'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(deliver, range(40))) == 1
    assert len(session['log_lines']) == 1


def test_restart_and_trim_do_not_reemit_claimed_run(env, monkeypatch):
    store, session = env
    run = create(store)
    assert anchor.post(store, 'p', run['run_id'])
    # Lose every live field and reload code; only the on-disk record survives.
    fresh = dict(project_id='p', session_id='s')
    monkeypatch.setattr(state, 'agent_sessions', {'s': fresh})
    importlib.reload(anchor)
    restarted = TestRunStore(store.root.parent)
    assert not anchor.post(restarted, 'p', run['run_id'])
    assert 'log_lines' not in fresh
    store.finish('p', run['run_id'], state='passed', exit_code=0, observed_at=101)
    assert not anchor.post(restarted, 'p', run['run_id'])
    assert store.list_runs('p')[0]['state'] == 'passed'


def test_existing_marker_gets_durable_claim_without_second_append(env):
    store, session = env
    run = create(store)
    session['log_lines'] = [f"[test-run:{run['run_id']}]"]
    assert not anchor.post(store, 'p', run['run_id'])
    session['log_lines'].clear()
    assert not anchor.post(TestRunStore(store.root.parent), 'p', run['run_id'])


@pytest.mark.parametrize('owner', [None, 'missing'])
def test_ownerless_or_unavailable_owner_emits_nothing(env, owner):
    store, session = env
    run = create(store, owner)
    before = store._path('p', run['run_id']).read_bytes()
    assert not anchor.post(store, 'p', run['run_id'])
    assert store._path('p', run['run_id']).read_bytes() == before
    assert 'log_lines' not in session


@pytest.mark.parametrize('policy', ['project', 'session', 'incognito', 'sandbox'])
def test_wrong_or_restricted_owner_untouched(env, policy):
    store, session = env
    run = create(store)
    session.update({'project_id': 'other'} if policy == 'project' else
                   {'session_id': 'other'} if policy == 'session' else
                   {'incognito': True} if policy == 'incognito' else
                   {'_codex_unattended_sandbox': True})
    before = store._path('p', run['run_id']).read_bytes()
    assert not anchor.post(store, 'p', run['run_id'])
    assert store._path('p', run['run_id']).read_bytes() == before
    assert 'log_lines' not in session


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_flag_off_never_reads_store_or_changes_session(env, monkeypatch, enabled):
    store, session = env
    monkeypatch.setitem(state.CONFIG, anchor.FLAG, enabled)
    def forbidden(*args):
        pytest.fail('Disabled emitter touched storage')
    monkeypatch.setattr(store, '_read', forbidden)
    assert not anchor.post(store, 'p', 'not-even-a-run-id')
    assert session == {'project_id': 'p', 'session_id': 's'}


def test_persistence_failure_does_not_append_and_can_retry(env, monkeypatch):
    store, session = env
    run = create(store)
    logs = []
    monkeypatch.setattr(anchor, '_log', lambda msg, **kw: logs.append(msg))
    def fail(*args, **kwargs):
        raise OSError('claim write refused')
    with monkeypatch.context() as patch:
        patch.setattr(anchor, 'write_json_atomic', fail)
        assert not anchor.post(store, 'p', run['run_id'])
    assert not session.get('log_lines')
    assert 'claim write refused' in logs[0]
    assert anchor.post(store, 'p', run['run_id'])


def test_append_failure_is_logged_and_durable_claim_prevents_duplicate(env, monkeypatch):
    store, session = env
    run = create(store)
    class BrokenLines(list):
        def append(self, line):
            super().append(line)  # Even a partial append must not duplicate.
            raise RuntimeError('append interrupted')
    session['log_lines'] = BrokenLines()
    logs = []
    monkeypatch.setattr(anchor, '_log', lambda msg, **kw: logs.append(msg))
    assert not anchor.post(store, 'p', run['run_id'])
    assert 'append interrupted' in logs[0]
    assert not anchor.post(TestRunStore(store.root.parent), 'p', run['run_id'])
    assert len(session['log_lines']) == 1
    assert store.get('p', run['run_id']) == run


def test_real_ingest_enrollment_receipt_retry_and_store_updates(env):
    store, session = env
    ingest = TestRunIngest(store, resolve_owner=lambda p, c: OwnerBinding(p, 's', False, False))
    run, receipt = ingest.enroll('p', None, {'runner': 'pytest'})
    ingest.producer('p', None, run['run_id'])
    ingest.submit('p', None, run['run_id'], receipt.token,
                  [dict(schema=1, producer_seq=1, type='heartbeat', payload={})])
    assert not anchor.post(store, 'p', run['run_id'])
    assert session['log_lines'] == [f"[test-run:{run['run_id']}]"]
    assert store.get('p', run['run_id'])['seq'] == 1
    assert json.loads(store._path('p', run['run_id']).read_text())['chat_anchor_claimed'] is True


def test_real_passive_detection_seam_uses_same_anchor(env):
    store, session = env
    observer = detection.DetectionObserver(store.root.parent, lambda p: {})
    observer.load_project = lambda p: {'id': p}
    item = detection.ToolObservation('call', 'candidate')
    observer._apply(session, 'launch', 'claude', item, 100)
    observer._apply(session, 'launch', 'claude', item, 101)
    run = observer.store.list_runs('p')[0]
    assert session['log_lines'] == [f"[test-run:{run['run_id']}]"]


@pytest.mark.parametrize('fail', [False, True])
def test_job_capture_emits_or_fails_without_changing_job_result(env, tmp_path, monkeypatch, fail):
    from mc import agent_jobs as jobs
    store, session = env
    run = create(store)
    logs = []
    monkeypatch.setattr(anchor, '_log', lambda msg, **kw: logs.append(msg))
    if fail:
        def broken(*args, **kwargs):
            raise OSError('anchor disk error')
        monkeypatch.setattr(anchor, 'write_json_atomic', broken)
    # No OS process: exercise real start_job/watch with a supplied fake child.
    proc = SimpleNamespace(pid=999999, wait=lambda **kw: 0, poll=lambda: 0)
    done, results = threading.Event(), []
    monkeypatch.setattr(jobs, 'start_capture', lambda *a, **kw: None)
    def completed(result):
        results.append(result)
        done.set()
    job = jobs.start_job(project_id='p', session_id='s', command='unchanged',
                         cwd=str(tmp_path), timeout_minutes=1, log_dir=tmp_path / 'logs',
                         direct_argv=['fake'], popen=lambda *a, **kw: proc,
                         capture=RunCapture(store, 'p', run['run_id']), on_complete=completed)
    assert done.wait(3)
    assert results[0]['exit_code'] == 0 and not results[0]['timed_out']
    assert jobs.get_job(job['job_id'])['status'] == 'completed'
    if fail:
        assert not session.get('log_lines') and 'anchor disk error' in logs[0]
    else:
        assert session['log_lines'] == [f"[test-run:{run['run_id']}]"]


def test_http_enrollment_still_succeeds_when_anchor_fails(route_env, monkeypatch):
    logs = []
    monkeypatch.setattr(anchor, '_log', lambda msg, **kw: logs.append(msg))
    def broken(*args, **kwargs):
        raise OSError('anchor write failed')
    monkeypatch.setattr(anchor, 'write_json_atomic', broken)
    response = route_env.client.post('/api/project/a/test-runs/enroll', json={'metadata': {'runner': 'pytest'}})
    assert response.status_code == 201
    assert response.get_json()['receipt']['token']
    assert 'anchor write failed' in logs[0]


def test_http_enrollment_posts_into_attributed_session_only(route_env):
    response = route_env.client.post('/api/project/a/test-runs/enroll', json={'metadata': {'runner': 'pytest'}})
    assert response.status_code == 201
    run_id = response.get_json()['run']['run_id']
    assert route_env.sessions['s1']['log_lines'] == [f'[test-run:{run_id}]']
    assert 'log_lines' not in route_env.sessions['s2']
    assert 'log_lines' not in route_env.sessions['s3']


def test_ownerless_ingest_preserves_record_and_receipt_without_chat(env):
    store, session = env
    ingest = TestRunIngest(store, resolve_owner=lambda p, c: OwnerBinding(p, None, False, False))
    run, receipt = ingest.enroll('p', None, {'runner': 'pytest'})
    assert run['owner_session_id'] is None and receipt.token
    assert 'log_lines' not in session
    assert 'chat_anchor_claimed' not in json.loads(store._path('p', run['run_id']).read_text())


def test_detection_continues_after_anchor_write_failure(env, monkeypatch):
    store, session = env
    logs = []
    monkeypatch.setattr(anchor, '_log', lambda msg, **kw: logs.append(msg))
    def broken(*args, **kwargs):
        raise OSError('anchor unavailable')
    monkeypatch.setattr(anchor, 'write_json_atomic', broken)
    observer = detection.DetectionObserver(store.root.parent, lambda p: {'id': p})
    observer._apply(session, 'launch', 'claude', detection.ToolObservation(
        'call', 'candidate', completed=True, exit_code=7), 100)
    assert observer.store.list_runs('p')[0]['state'] == 'failed'
    assert logs and not session.get('log_lines')
