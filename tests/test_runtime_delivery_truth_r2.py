"""MC-1168 review regressions, ported from Fenn's isolated real-reader probes.

Engine processes/pipes are doubles. The route, revival helper, Mode B reader
and failed-resume recovery are production code; no engine is launched/killed.
"""
import json
import queue
import threading

import pytest

from test_runtime_delivery_truth import app_env, revive_env, _Proc, _Stdin
import mc.agent_runtime as art
from mc.blueprints import agent_routes as routes

REAL_READER_B = routes._read_agent_stream_b


@pytest.mark.parametrize('endpoint', ['send', 'followup'])
def test_cold_codex_delivery_error_stays_on_original_conversation(app_env, monkeypatch, endpoint):
    ar = app_env['ar']
    sid = 'coldcodex001'
    entry = {'session_id': sid, 'provider': 'codex',
             'provider_session_id': 'native-thread', 'task': 'old chat'}
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_find_transcript_file', lambda *a: None)
    calls = []

    def dispatch(pid, message, **kw):
        calls.append((message, kw))
        if kw.get('reuse_session_id'):
            raise art.PromptDeliveryError('BrokenPipeError: original conversation not delivered')
        pytest.fail('fresh dispatch after failed delivery to the original conversation')

    monkeypatch.setattr(ar, '_dispatch_agent_internal', dispatch)
    response = app_env['client'].post(f'/api/project/p1/agent/{endpoint}',
                                     json={'session_id': sid, 'message': 'answer'})
    assert response.status_code == 502, response.get_json()
    body = response.get_json()
    assert body['ok'] is False and body['delivered'] is False
    assert body['session_id'] == sid and 'BrokenPipeError' in body['error']
    assert len(calls) == 1
    assert calls[0][0] == 'answer'
    assert calls[0][1]['resume_id'] == 'native-thread'
    assert calls[0][1]['reuse_session_id'] == sid
    assert calls[0][1]['provider_override'] == 'codex'


@pytest.fixture()
def real_reader_env(revive_env, monkeypatch):
    ar = revive_env['ar']
    observed = []
    finished = threading.Event()
    errors = []
    readers = []

    def reader(proc, session):
        readers.append(threading.current_thread())
        try:
            REAL_READER_B(proc, session)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(ar, '_read_agent_stream_b', reader)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a: None)
    monkeypatch.setattr(ar, '_log_agent_completion',
                        lambda s: observed.append(('completion', s['status'])))
    monkeypatch.setattr(ar, '_run_runtime_callbacks',
                        lambda s: observed.append(('callback', s['status'])))
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_session_skills', lambda *a, **k: [])
    # Record the terminal effects; leave the production reader and recovery live.
    monkeypatch.setattr(ar, '_apply_mc_tool_blocks_for_turn', lambda s: None)
    monkeypatch.setattr(ar, '_auto_snapshot_notes_on_turn', lambda s: None)
    monkeypatch.setattr(ar, '_emit_coverage_advisory', lambda s: None)
    monkeypatch.setattr(ar, '_scan_result_event_for_auth', lambda msg: None)
    monkeypatch.setattr(ar, '_record_permission_denials', lambda *a: None)
    monkeypatch.setattr(ar._delegation_settlement, 'note_claude_turn',
                        lambda s, msg, *a: observed.append(('result', msg['marker'])))
    monkeypatch.setattr(ar, '_complete_agent_turn',
                        lambda s: observed.append(('turn', s['status'])))
    monkeypatch.setattr(ar._limit_resume, 'hold_completion', lambda *a, **k: False)
    monkeypatch.setattr(ar, '_maybe_checkpoint', lambda s: None)
    monkeypatch.setattr(ar, '_write_usage_breakdown_turn_checkpoint', lambda s: None)
    monkeypatch.setattr(ar, '_handle_push_signal',
                        lambda p, sid, msg: observed.append(('push', msg['marker']))
                        if msg.get('type') == 'result' else None)
    yield {**revive_env, 'observed': observed, 'finished': finished, 'errors': errors}
    for thread in readers:
        thread.join(5)
        assert not thread.is_alive(), 'real reader did not stop'
    assert not errors, errors


def _result(marker):
    return json.dumps({'type': 'result', 'marker': marker, 'num_turns': 1}) + '\n'


def _early_terminal_proc(env, *, lines=(), fail=True, rc=1, after_drain=None, fail_on='write'):
    class BeforeWrite(_Stdin):
        def write(self, text):
            # The production reader must finish draining before delivery resolves.
            assert env['finished'].wait(5), 'reader blocked on pending delivery'
            assert env['observed'] == [], 'terminal effects escaped pending delivery'
            assert env['sessions']['revsid000001']['status'] == 'running'
            if after_drain is not None:
                after_drain()
            if fail and fail_on == 'write':
                raise BrokenPipeError('child exited before stdin write')
            return super().write(text)

        def flush(self):
            if fail and fail_on == 'flush':
                raise BrokenPipeError('child exited before stdin flush')

    proc = _Proc(BeforeWrite(), pid=515198)
    proc.stdout = iter(lines)
    proc.wait = lambda: rc
    env['spawned']['proc'] = proc
    return proc


def _revive(env):
    return env['ar']._revive_from_agent_log(
        'p1', 'revsid000001', 'new answer', env['project'])


def test_eof_before_failed_write_never_recovers_or_emits_callbacks(real_reader_env, monkeypatch):
    env = real_reader_env
    old = _early_terminal_proc(env)
    fresh = _Proc(_Stdin(), pid=515199)
    spawns = []

    def spawn(*a, **k):
        spawns.append(k)
        return old if len(spawns) == 1 else fresh

    monkeypatch.setattr(env['ar'], 'spawn_engine_proc', spawn)
    with pytest.raises(routes.ReviveDeliveryError):
        _revive(env)
    assert len(spawns) == 1, 'production recovery spawned a replacement before delivery'
    assert fresh.stdin.written == []
    assert env['observed'] == []
    assert env['killed'] == [old.pid]
    assert 'revsid000001' not in env['sessions']


@pytest.mark.parametrize('rc', [0, 1])
@pytest.mark.parametrize('fail_on', ['write', 'flush'])
def test_rejected_results_and_eof_have_no_terminal_effects(real_reader_env, rc, fail_on):
    env = real_reader_env
    proc = _early_terminal_proc(env, lines=[_result('first'), _result('second')],
                                rc=rc, fail_on=fail_on)
    with pytest.raises(routes.ReviveDeliveryError):
        _revive(env)
    assert env['observed'] == []
    assert env['killed'] == [proc.pid]
    assert 'revsid000001' not in env['sessions']


@pytest.mark.parametrize('rc', [0, 1])
def test_acceptance_releases_results_then_eof_in_order(real_reader_env, rc):
    env = real_reader_env
    proc = _early_terminal_proc(env, lines=[_result('first'), _result('second')],
                                fail=False, rc=rc)
    session = _revive(env)
    assert env['observed'] == [
        ('result', 'first'), ('turn', 'idle'), ('push', 'first'),
        ('result', 'second'), ('turn', 'idle'), ('push', 'second'),
        ('completion', 'completed'), ('callback', 'completed')]
    assert session['status'] == 'completed' and session['process_alive'] is False
    assert 'new answer' in proc.stdin.written[0]
    assert env['killed'] == []


def test_accepted_eof_without_result_still_completes(real_reader_env):
    env = real_reader_env
    _early_terminal_proc(env, fail=False, rc=0)
    session = _revive(env)
    assert env['observed'] == [('completion', 'completed'), ('callback', 'completed')]
    assert session['status'] == 'completed'


@pytest.mark.parametrize('replace_object', [False, True])
def test_failed_revive_preserves_replacement_ownership_and_membership(real_reader_env, replace_object):
    env = real_reader_env
    replacement_proc = _Proc(_Stdin(), pid=515199)
    owner = []

    def replace():
        session = env['sessions']['revsid000001']
        if replace_object:
            session = {'proc': replacement_proc, 'process_alive': True}
            env['sessions']['revsid000001'] = session
        else:
            session['proc'] = replacement_proc
        owner.append(session)

    proc = _early_terminal_proc(env, after_drain=replace)
    mgr = env['ar'].get_manager('p1')
    mgr.session_ids.discard('revsid000001')
    try:
        with pytest.raises(routes.ReviveDeliveryError):
            _revive(env)
        assert env['sessions']['revsid000001'] is owner[0]
        assert owner[0]['proc'] is replacement_proc and owner[0]['process_alive'] is True
        assert 'revsid000001' in mgr.session_ids
        assert env['observed'] == []
        assert env['killed'] == [proc.pid]
    finally:
        mgr.session_ids.discard('revsid000001')


def test_failed_revive_restores_prior_membership_with_real_reader(real_reader_env):
    env = real_reader_env
    prior = {'session_id': 'revsid000001', 'status': 'completed', 'proc': None}
    env['sessions']['revsid000001'] = prior
    mgr = env['ar'].get_manager('p1')
    mgr.session_ids.add('revsid000001')
    _early_terminal_proc(env, lines=[_result('rejected')])
    try:
        with pytest.raises(routes.ReviveDeliveryError):
            _revive(env)
        assert env['sessions']['revsid000001'] is prior
        assert 'revsid000001' in mgr.session_ids
        assert env['observed'] == []
    finally:
        mgr.session_ids.discard('revsid000001')


def test_pending_result_is_released_without_waiting_for_process_eof(real_reader_env):
    env = real_reader_env
    stdout = queue.Queue()
    result_drained = threading.Event()

    def lines():
        yield _result('before-accept')
        result_drained.set()  # Reader has submitted the result and asks for more.
        while True:
            line = stdout.get(timeout=5)
            if line is None:
                return
            yield line

    class PendingResult(_Stdin):
        def write(self, text):
            assert result_drained.wait(5), 'reader stopped draining at pending result'
            assert env['observed'] == []
            return super().write(text)

    proc = _Proc(PendingResult())
    proc.stdout = lines()
    proc.wait = lambda: 0
    env['spawned']['proc'] = proc
    try:
        session = _revive(env)
        assert not env['finished'].is_set(), 'persistent reader exited unexpectedly'
        assert env['observed'] == [
            ('result', 'before-accept'), ('turn', 'idle'), ('push', 'before-accept')]
        stdout.put(_result('after-accept'))
        stdout.put(None)
        assert env['finished'].wait(5)
        assert env['observed'] == [
            ('result', 'before-accept'), ('turn', 'idle'), ('push', 'before-accept'),
            ('result', 'after-accept'), ('turn', 'idle'), ('push', 'after-accept'),
            ('completion', 'completed'), ('callback', 'completed')]
        assert session['status'] == 'completed'
    finally:
        stdout.put(None)


def test_results_arriving_during_acceptance_stay_behind_queued_result(real_reader_env, monkeypatch):
    env = real_reader_env
    stdout = queue.Queue()
    first_drained = threading.Event()
    second_drained = threading.Event()

    def lines():
        yield _result('first')
        first_drained.set()
        yield stdout.get(timeout=5)
        second_drained.set()

    class PendingResult(_Stdin):
        def write(self, text):
            assert first_drained.wait(5)
            assert env['observed'] == []
            return super().write(text)

    proc = _Proc(PendingResult())
    proc.stdout = lines()
    proc.wait = lambda: 0
    env['spawned']['proc'] = proc

    def settle(session, msg, *a):
        env['observed'].append(('result', msg['marker']))
        if msg['marker'] == 'first':
            # The writer is replaying the first result while the real reader
            # drains a second one and EOF. Their callbacks must stay queued.
            stdout.put(_result('second'))
            assert second_drained.wait(5)
            assert env['finished'].wait(5)
            assert env['observed'] == [('result', 'first')]

    monkeypatch.setattr(env['ar']._delegation_settlement, 'note_claude_turn', settle)
    try:
        _revive(env)
        assert env['observed'] == [
            ('result', 'first'), ('turn', 'idle'), ('push', 'first'),
            ('result', 'second'), ('turn', 'idle'), ('push', 'second'),
            ('completion', 'completed'), ('callback', 'completed')]
    finally:
        stdout.put(_result('cleanup'))
