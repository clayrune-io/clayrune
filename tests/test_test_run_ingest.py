"""Phase-3 boundary tests: isolated files, no server, process or route startup."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import test_run_ingest as ingest
from mc import test_run_store as runs


@pytest.fixture
def env(tmp_path):
    owners = {
        'alice': ingest.OwnerBinding('a', 'session-a', False, False, cwd=str(tmp_path),
                                     turn_id='turn-a', job_id='job-a', process_identity='pid:birth'),
        'bob': ingest.OwnerBinding('a', 'session-b', False, False),
        'other': ingest.OwnerBinding('b', 'session-a', False, False),
        'service': ingest.OwnerBinding('a', None, False, False, source='harness'),
    }
    now = [100.0]
    store = runs.TestRunStore(tmp_path / 'data')
    service = ingest.TestRunIngest(store, resolve_owner=lambda project, caller: owners.get(caller),
                                   clock=lambda: now[0], monotonic=lambda: now[0])
    yield service, store, owners, now
    service.close()


def enroll(env, caller='alice', project='a', **metadata):
    return env[0].enroll(project, caller, metadata)


def event(seq=1, kind='heartbeat', **payload):
    return dict(schema=1, producer_seq=seq, type=kind, payload=payload)


def report(seq=1, name='same-test', outcome='pass'):
    return event(seq, 'unit_report', unit_id=name, attempt=1, phase='call',
                 outcome=outcome, final=True, revision=1)


def submit(env, receipt, events, caller='alice', project='a'):
    return env[0].submit(project, caller, receipt.run_id, receipt.token, events)


def spool(env, receipt, caller='alice'):
    path = env[0].create_spool('a', caller, receipt.run_id, receipt.token)
    env[0].attach_spool('a', caller, receipt.run_id, receipt.token, str(path))
    return path


def poll(env, receipt, caller='alice'):
    return env[0].poll_spool('a', caller, receipt.run_id, receipt.token)


def test_server_binding_is_immutable_and_receipt_is_not_persisted(env):
    snapshot, receipt = enroll(env, runner='pytest', display_label='Tests')
    assert {key: snapshot[key] for key in ('project_id', 'owner_session_id', 'owner_turn_id',
                                          'job_id', 'process_identity', 'cwd')} == dict(
        project_id='a', owner_session_id='session-a', owner_turn_id='turn-a',
        job_id='job-a', process_identity='pid:birth', cwd=env[2]['alice'].cwd)
    assert receipt.run_id == snapshot['run_id']
    assert receipt.expires_at == 100 + ingest.RECEIPT_SECONDS
    persisted = env[1]._path('a', receipt.run_id).read_text()
    assert receipt.token not in persisted
    assert set(json.loads(persisted)) == {'schema', 'enrollment', 'events', 'snapshot'}


@pytest.mark.parametrize('field', [
    'owner_session_id', 'owner_turn_id', 'project_id', 'cwd', 'job_id', 'source',
    'process_identity', 'parent_run_id', 'command', 'env', 'incognito', 'sandboxed',
])
def test_enrollment_cannot_forge_authority(env, field):
    with pytest.raises(ValueError):
        env[0].enroll('a', 'alice', {field: 'forged'})
    assert not env[1].root.exists()


@pytest.mark.parametrize('caller,project', [('unknown', 'a'), ('alice', 'b'), ('other', 'a')])
def test_unavailable_or_mismatched_owner_never_becomes_project_only(env, caller, project):
    with pytest.raises(ingest.IngestRefused):
        enroll(env, caller, project)
    assert not env[1].root.exists()


@pytest.mark.parametrize('flag', ['incognito', 'sandboxed'])
def test_restricted_mode_refused_before_enrollment_or_spool_io(env, flag, monkeypatch):
    _, receipt = enroll(env)
    spool(env, receipt)
    env[2]['alice'] = replace(env[2]['alice'], **{flag: True})
    monkeypatch.setattr(os, 'open', lambda *a, **k: pytest.fail('restricted file open'))
    monkeypatch.setattr(os, 'read', lambda *a, **k: pytest.fail('restricted spool read'))
    with pytest.raises(ingest.IngestRefused):
        enroll(env)
    with pytest.raises(ingest.IngestRefused):
        submit(env, receipt, [report()])
    with pytest.raises(ingest.IngestRefused):
        spool(env, receipt)
    with pytest.raises(ingest.IngestRefused):
        env[0].producer('a', 'alice', receipt.run_id)
    with pytest.raises(ingest.IngestRefused):
        poll(env, receipt)
    assert env[1].get('a', receipt.run_id)['seq'] == 0


def test_incognito_pseudo_project_refused_even_with_bad_resolver_policy(env):
    env[2]['hidden'] = ingest.OwnerBinding('_incognito', None, False, False)
    with pytest.raises(ingest.IngestRefused):
        enroll(env, 'hidden', '_incognito')
    assert not env[1].root.exists()


def test_project_only_owner_is_explicit_and_session_cannot_write_it(env):
    run, receipt = enroll(env, 'service')
    assert run['owner_session_id'] is run['owner_turn_id'] is None
    assert run['source'] == 'harness'
    assert submit(env, receipt, [report()], caller='service')['completed'] == 1
    with pytest.raises(ingest.IngestRefused):
        submit(env, receipt, [report(2)], caller='alice')
    with pytest.raises(ingest.IngestRefused):
        env[0].producer('a', 'alice', receipt.run_id)


@pytest.mark.parametrize('caller,project,token,run_id', [
    ('bob', 'a', None, None), ('other', 'b', None, None),
    ('alice', 'a', 'forged', None), ('alice', 'a', None, '0' * 32),
])
def test_receipt_scope_covers_project_session_run_and_secret(env, caller, project, token, run_id):
    _, receipt = enroll(env)
    with pytest.raises(ingest.IngestRefused):
        env[0].submit(project, caller, run_id or receipt.run_id,
                      token or receipt.token, [report()])
    assert env[1].events('a', receipt.run_id) == []


def test_same_test_names_in_different_projects_stay_isolated(env):
    _, first = enroll(env)
    _, second = enroll(env, 'other', 'b')
    assert submit(env, first, [report(outcome='fail')])['counts']['fail'] == 1
    assert submit(env, second, [report()], caller='other', project='b')['counts']['pass'] == 1
    assert env[1].get('a', first.run_id)['counts']['pass'] == 0
    with pytest.raises(KeyError):
        env[1].get('b', first.run_id)


def test_producer_generation_and_owner_check(env):
    _, first = enroll(env)
    second = env[0].producer('a', 'alice', first.run_id)
    assert first.producer_id != second.producer_id
    submit(env, first, [report()])
    assert submit(env, second, [report()])['completed'] == 1
    with pytest.raises(ingest.IngestRefused):
        env[0].producer('a', 'bob', first.run_id)


def test_batch_uses_one_atomic_write_and_is_store_replay_compatible(env, monkeypatch):
    _, receipt = enroll(env)
    writes = []
    original = ingest.write_json_atomic
    monkeypatch.setattr(ingest, 'write_json_atomic', lambda *a, **k: (
        writes.append(a[0]), original(*a, **k))[-1])
    batch = [report(i + 1, str(i)) for i in range(64)]
    snapshot = submit(env, receipt, batch)
    assert snapshot['completed'] == snapshot['seq'] == 64
    assert len(writes) == 1
    assert runs.TestRunStore(env[1].root.parent).get('a', receipt.run_id) == snapshot
    env[3][0] += 1
    assert submit(env, receipt, batch) == snapshot
    assert len(writes) == 1
    assert len(env[1].events('a', receipt.run_id)) == 64


def test_timestamps_are_observer_owned_and_retries_preserve_them(env):
    _, receipt = enroll(env)
    first = submit(env, receipt, [event(output=True)])
    assert first['last_output_at'] == 100
    env[3][0] += 10
    assert submit(env, receipt, [event(output=True)]) == first
    assert submit(env, receipt, [event(2, output=True)])['last_output_at'] == 110


@pytest.mark.parametrize('field,value', [
    ('owner_session_id', 'bob'), ('project_id', 'b'), ('run_id', '0' * 32),
    ('producer_id', '_store_process'), ('observed_at', 1e200), ('command', 'echo nope'),
])
def test_event_cannot_supply_identity_or_timestamp(env, field, value):
    _, receipt = enroll(env)
    message = dict(report(), **{field: value})
    with pytest.raises(ValueError):
        submit(env, receipt, [message])
    assert env[1].events('a', receipt.run_id) == []


@pytest.mark.parametrize('message', [
    None, [], {}, event(schema=99),
    dict(event(), schema=True), dict(event(), schema=2),
    dict(event(), producer_seq=True), dict(event(), producer_seq=0),
    dict(event(), producer_seq=2**53), event(kind='unknown'),
    event(kind='unit_report'), event(kind='heartbeat', output='yes'),
    event(kind='diagnostic', code='../escape'), event(kind='diagnostic', code=['bad']),
    event(kind='run_finished', state='passed', exit_code=True),
    event(kind='collection', selected_total=float('nan'), revision=1),
    event(kind='collection', selected_total=float('inf'), revision=1),
    report(name='x' * 1025), event(kind='run_started', reporter_version='x' * 17000),
])
def test_malformed_event_rejects_whole_batch(env, message):
    _, receipt = enroll(env)
    with pytest.raises(ValueError):
        submit(env, receipt, [report(), message])
    assert env[1].events('a', receipt.run_id) == []


@pytest.mark.parametrize('batch', [[], [event()] * 65, {}, [dict(event(), payload={'x': 'y' * 270000})]])
def test_batch_limits(env, batch):
    _, receipt = enroll(env)
    with pytest.raises(ValueError):
        submit(env, receipt, batch)
    assert env[1].events('a', receipt.run_id) == []


def test_duplicate_conflict_and_revision_conflict_do_not_partially_commit(env):
    _, receipt = enroll(env)
    original = submit(env, receipt, [report()])
    for batch in ([report(2, name='new'), report(outcome='fail')],
                  [report(2, name='new'), report(3, outcome='fail')],
                  [report(4, name='same'), report(4, name='different')]):
        with pytest.raises(ValueError, match='Conflicting'):
            submit(env, receipt, batch)
        assert env[1].get('a', receipt.run_id) == original


def test_reordered_and_within_batch_duplicate_events(env):
    _, receipt = enroll(env)
    assert submit(env, receipt, [report(3, 'three'), report(1, 'one'),
                                 report(1, 'one')])['completed'] == 2
    assert submit(env, receipt, [report(2, 'two')])['completed'] == 3


def test_reporter_finish_never_mints_process_success_and_terminal_is_frozen(env):
    _, receipt = enroll(env)
    snapshot = submit(env, receipt, [report(), event(2, 'run_finished', state='passed', exit_code=0)])
    assert snapshot['state'] == 'running' and snapshot['reporter_state'] == 'passed'
    terminal = env[1].finish('a', receipt.run_id, state='failed', exit_code=1, observed_at=101)
    assert submit(env, receipt, [report(3, 'late')]) == terminal
    assert len(env[1].events('a', receipt.run_id)) == 3
    with pytest.raises(ingest.IngestRefused):
        env[0].producer('a', 'alice', receipt.run_id)


def test_failed_atomic_write_is_retryable_without_lost_history(env, monkeypatch):
    _, receipt = enroll(env)
    original = ingest.write_json_atomic
    def broken(*args, **kwargs):
        raise OSError('injected replace failure')
    monkeypatch.setattr(ingest, 'write_json_atomic', broken)
    with pytest.raises(OSError):
        submit(env, receipt, [report()])
    assert env[1].events('a', receipt.run_id) == []
    monkeypatch.setattr(ingest, 'write_json_atomic', original)
    assert submit(env, receipt, [report()])['completed'] == 1


def test_corrupt_store_is_not_replaced_by_ingest(env):
    _, receipt = enroll(env)
    path = env[1]._path('a', receipt.run_id)
    path.write_text('{broken')
    with pytest.raises(runs.StoreError):
        submit(env, receipt, [report()])
    assert path.read_text() == '{broken'


def test_expiry_revoke_capacity_and_restart_invalidation(env, monkeypatch):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    fd = next(iter(env[0]._grants.values())).spool_fd
    env[3][0] += ingest.RECEIPT_SECONDS
    with pytest.raises(ingest.IngestRefused):
        submit(env, receipt, [report()])
    with pytest.raises(OSError):
        os.fstat(fd)
    assert path.exists()  # No silent retention/deletion policy.
    _, fresh = enroll(env)
    env[0].revoke('a', 'alice', fresh.run_id, fresh.token)
    with pytest.raises(ingest.IngestRefused):
        submit(env, fresh, [report()])
    _, fresh = enroll(env)
    env[0].close()
    with pytest.raises(ingest.IngestRefused):
        submit(env, fresh, [report()])
    monkeypatch.setattr(ingest, 'MAX_RECEIPTS', 0)
    count = len(list(env[1].root.rglob('*.json')))
    with pytest.raises(ingest.IngestRefused):
        enroll(env)
    assert len(list(env[1].root.rglob('*.json'))) == count


@pytest.mark.parametrize('path', [
    '../secret', '/etc/passwd', 'C:\\Windows\\win.ini',
    '\\\\server\\share\\file', 'file.jsonl:stream', 'file://elsewhere',
])
def test_arbitrary_spool_paths_are_refused_without_open(env, path, monkeypatch):
    _, receipt = enroll(env)
    spool(env, receipt)
    monkeypatch.setattr(os, 'open', lambda *a, **k: pytest.fail('arbitrary path opened'))
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('arbitrary path opened'))
    with pytest.raises(ingest.IngestRefused):
        env[0].attach_spool('a', 'alice', receipt.run_id, receipt.token, path)


def test_other_producer_spool_cannot_be_attached(env):
    _, one = enroll(env)
    _, two = enroll(env)
    path = spool(env, one)
    with pytest.raises(ingest.IngestRefused):
        env[0].attach_spool('a', 'alice', two.run_id, two.token, str(path))
    with pytest.raises(ingest.IngestRefused):
        poll(env, two)


def test_spool_partial_utf8_and_multiple_polls(env):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    data = json.dumps(report(name='case-\u20ac'), ensure_ascii=False).encode()
    cut = data.index('\u20ac'.encode()) + 1
    path.write_bytes(data[:cut])
    assert poll(env, receipt) == 0
    with path.open('ab') as output:
        output.write(data[cut:] + b'\n' + json.dumps(report(2, 'two')).encode())
    assert poll(env, receipt) == 1
    assert poll(env, receipt) == 0
    with path.open('ab') as output:
        output.write(b'\n')
    assert poll(env, receipt) == 1
    assert env[1].get('a', receipt.run_id)['completed'] == 2
    assert poll(env, receipt) == 0


@pytest.mark.parametrize('data', [b'{bad}\n', b'\xff\n', b'{"schema":1,"schema":2}\n',
                                      b'x' * 17000, b'x' * 17000 + b'\n'])
def test_spool_malformed_oversized_and_duplicate_keys_do_not_advance(env, data):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    path.write_bytes(data)
    with pytest.raises(ValueError):
        poll(env, receipt)
    assert next(iter(env[0]._grants.values())).offset == 0
    assert env[1].events('a', receipt.run_id) == []


def test_spool_failed_commit_retries_same_offset(env, monkeypatch):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    path.write_text(json.dumps(report()) + '\n')
    original = ingest.write_json_atomic
    def broken(*args, **kwargs):
        raise OSError('disk failure')
    monkeypatch.setattr(ingest, 'write_json_atomic', broken)
    with pytest.raises(OSError):
        poll(env, receipt)
    assert next(iter(env[0]._grants.values())).offset == 0
    monkeypatch.setattr(ingest, 'write_json_atomic', original)
    assert poll(env, receipt) == 1
    assert env[1].get('a', receipt.run_id)['completed'] == 1


def test_spool_reads_are_bounded_and_backlog_stays_on_disk(env):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    path.write_text(''.join(json.dumps(report(i + 1, str(i))) + '\n' for i in range(150)))
    assert [poll(env, receipt) for _ in range(4)] == [64, 64, 22, 0]
    assert env[1].get('a', receipt.run_id)['completed'] == 150


def test_spool_truncation_is_not_silently_reset(env):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    path.write_text(json.dumps(report()) + '\n')
    assert poll(env, receipt) == 1
    path.write_bytes(b'')
    with pytest.raises(ingest.IngestRefused):
        poll(env, receipt)


def test_spool_cannot_read_replacement_path(env, monkeypatch):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    path.write_text(json.dumps(report()) + '\n')
    # Reject reopening the spool; normal store reads/atomic writes still work.
    original_os_open, original_path_open = os.open, Path.open
    def guarded_os_open(candidate, *args, **kwargs):
        assert Path(candidate) != path, 'spool path reopened'
        return original_os_open(candidate, *args, **kwargs)
    def guarded_path_open(candidate, *args, **kwargs):
        assert candidate != path, 'spool path reopened'
        return original_path_open(candidate, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded_os_open)
    monkeypatch.setattr(Path, 'open', guarded_path_open)
    assert poll(env, receipt) == 1


def test_hardlinked_spool_is_refused(env, tmp_path):
    _, receipt = enroll(env)
    path = spool(env, receipt)
    os.link(path, tmp_path / 'second-name')
    with pytest.raises(ingest.IngestRefused):
        poll(env, receipt)


def test_spool_directory_redirect_is_refused_before_file_open(env, monkeypatch):
    _, receipt = enroll(env)
    original = Path.resolve
    def redirected(path, *args, **kwargs):
        if path.name == 'test_run_spools':
            return path.parent / 'outside'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', redirected)
    monkeypatch.setattr(os, 'open', lambda *a, **k: pytest.fail('redirected open'))
    with pytest.raises(ingest.IngestRefused):
        spool(env, receipt)


def test_preexisting_spool_name_cannot_attach_an_outside_hardlink(env, tmp_path, monkeypatch):
    _, receipt = enroll(env)
    folder = env[1].root.parent / 'test_run_spools'
    folder.mkdir()
    outside = tmp_path / 'private-file'
    outside.write_bytes(b'private data')
    os.link(outside, folder / ('f' * 32 + '.jsonl'))
    monkeypatch.setattr(ingest.uuid, 'uuid4', lambda: SimpleNamespace(hex='f' * 32))
    with pytest.raises(FileExistsError):
        spool(env, receipt)
    assert outside.read_bytes() == b'private data'
    assert next(iter(env[0]._grants.values())).spool_fd is None


@pytest.mark.parametrize('changes', [
    {'incognito': None}, {'sandboxed': 'false'}, {'session_id': ''},
    {'session_id': 3}, {'session_id': None, 'turn_id': 'cannot-own-turn'},
])
def test_malformed_owner_binding_fails_closed(env, changes):
    env[2]['alice'] = replace(env[2]['alice'], **changes)
    with pytest.raises(ingest.IngestRefused):
        enroll(env)
    assert not env[1].root.exists()


def test_concurrent_ingest_and_trusted_store_append_keep_all_events(env):
    _, receipt = enroll(env)
    def structured():
        for i in range(10):
            submit(env, receipt, [report(i + 1, str(i))])
    def capture():
        for i in range(10):
            env[1].apply('a', receipt.run_id, dict(schema=1, run_id=receipt.run_id,
                producer_id='capture', producer_seq=i + 1, observed_at=100,
                type='heartbeat', payload={'output': True}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(structured), pool.submit(capture)]
        for future in futures:
            future.result()
    snapshot = env[1].get('a', receipt.run_id)
    assert snapshot['seq'] == 20 and snapshot['completed'] == 10
