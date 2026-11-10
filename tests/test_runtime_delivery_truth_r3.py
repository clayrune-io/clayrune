"""Review probes: real Codex reader/retry; process and pipe doubles only."""
import threading
import json
import queue

from test_runtime_delivery_truth import codex, _Proc, _Stdin, _session, _handle, _dispatch
import mc.agent_runtime as art
import pytest
from test_runtime_delivery_truth import revive_env, app_env


@pytest.mark.parametrize('first_turn', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_codex_terminal_turn_and_eof_wait_for_delivery(codex, monkeypatch, first_turn, fail):
    drained = threading.Event()
    observed = []
    session = _session()

    class AfterDrain(_Stdin):
        def write(self, text):
            assert drained.wait(3), 'reader must drain while delivery is pending'
            assert observed == []
            assert 'usage' not in session
            if fail:
                raise BrokenPipeError('rejected')
            return super().write(text)

    proc = _Proc(AfterDrain())
    proc.stdout = iter([json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 3}})])
    proc.wait = lambda: 0
    codex['holder']['proc'] = proc
    def reader(*args):
        try:
            REAL_READER(*args)
        finally:
            drained.set()
    monkeypatch.setattr(art, '_mode_a_reader', reader)
    monkeypatch.setattr(codex['rt'], 'turn_context_tokens', lambda *a: None)
    callbacks = {'on_turn_end': lambda *a: observed.append('turn'),
                 'on_process_exit': lambda *a: observed.append('exit')}
    handle = _handle(session)
    handle.meta['callbacks'] = callbacks
    def launch():
        if first_turn:
            return art._mode_a_dispatch(codex['rt'], ['codex', 'exec'], 'answer', '.', 'p',
                'answer', handle.mc_session_id, session, False, None, callbacks, None)
        return codex['rt']._spawn_turn(handle, ['codex', 'exec'], 'answer', '')
    if fail:
        with pytest.raises(art.PromptDeliveryError):
            launch()
        assert observed == [] and 'usage' not in session
    else:
        launch()
        assert observed == ['turn', 'exit']
        assert session['usage']['input_tokens'] == 3


def _start_call(call):
    done = threading.Event()
    outcome = {}
    def run():
        try:
            outcome['result'] = call()
        except BaseException as exc:
            outcome['error'] = exc
        finally:
            done.set()
    thread = threading.Thread(target=run)
    thread.start()
    return thread, done, outcome


@pytest.mark.parametrize('provider', ['claude', 'codex'])
@pytest.mark.parametrize('isolated', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_cold_parent_ack_waits_after_outer_project_lock(
        revive_env, codex, monkeypatch, provider, isolated, fail):
    """Real inbox -> revive/dispatch -> writer; acquire from a second thread.

    Cwd selection is stubbed here; the MC-1165 suite covers real worktrees.
    Admission/claim checks and publication must still be inside the outer lock.
    """
    from types import SimpleNamespace
    from mc import desk_dispatch
    from mc.delegation_delivery import DeliveryUncertain
    env = revive_env
    ar = env['ar']
    sid = 'revsid000001'
    mgr = ar.get_manager('p1')
    monkeypatch.setattr(mgr, 'ensure_guardian', lambda: None)
    monkeypatch.setattr(ar, '_delivery_shutdown_requested', threading.Event())
    monkeypatch.setattr(desk_dispatch, 'deliver', lambda *a: False)
    claims = []
    def claim(*a):
        claims.append(mgr.lock._is_owned())
        return True
    monkeypatch.setattr(ar, '_delivery_store', SimpleNamespace(revalidate_claim=claim))
    entry = {'session_id': sid, 'status': 'completed', 'provider': provider,
             'agent_model': '', 'worktree_isolated': isolated,
             'claude_session_id': 'native' if provider == 'claude' else '',
             'provider_session_id': 'native' if provider == 'codex' else ''}
    monkeypatch.setattr(ar, '_load_agent_log', lambda *a, **kwargs: [entry])
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_dispatch_clock_task', lambda p, pid, task, sid: task)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_runtime_mcp_config_json', lambda *a: '')
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_prior_conversation_provider', lambda *a: provider)
    monkeypatch.setattr(ar, '_resume_cwd_for', lambda pp, *a: None)
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda p, *a: (p['project_path'], isolated))
    monkeypatch.setattr(ar, '_guard_isolated_reuse', lambda *a: None)
    monkeypatch.setattr(ar._isolated_launch, 'owned_worktree',
                        lambda p, *a, **k: p['project_path'] if isolated else '')
    monkeypatch.setattr(ar._isolated_launch, 'launch_cwd',
                        lambda s, p, cwd: (p['project_path'], ''))
    monkeypatch.setattr(ar, '_isolated_resume_kwargs', lambda *a, **k: {})
    monkeypatch.setattr(ar._native_followup_context, 'refresh_stash', lambda *a, **k: None)
    monkeypatch.setitem(ar.state.CONFIG, 'vendor_context_sync_enabled', False)
    env['project']['provider'] = provider
    in_write, release = threading.Event(), threading.Event()
    class Pending(_Stdin):
        def write(self, text):
            in_write.set()
            assert release.wait(5), 'test writer was not released'
            if fail:
                raise BrokenPipeError('not accepted')
            return super().write(text)
    proc = _Proc(Pending())
    env['spawned']['proc'] = proc
    codex['holder']['proc'] = proc
    rt = codex['rt']
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda *a: rt)
    def dispatch(**kw):
        assert mgr.lock._is_owned(), 'runtime publication lost guarded admission'
        return art._mode_a_dispatch(rt, ['codex', 'exec'], kw['task'], kw['project_path'],
            kw['project_id'], kw['task'], kw['mc_session_id'], kw['session_dict'],
            False, None, kw['callbacks'], None)
    monkeypatch.setattr(rt, 'dispatch', dispatch)
    row = {'project_id': 'p1', 'parent_session_id': sid, 'event_id': 'child:turn:1',
           'fence_token': 'fence', 'payload': json.dumps({'payload': {'message': 'answer'}})}
    thread, done, outcome = _start_call(lambda: ar._process_inbox(row))
    try:
        assert in_write.wait(3), outcome
        assert not done.is_set(), 'handoff reported success before acknowledgment'
        assert sid in env['sessions'], 'process was not published'
        got = mgr.lock.acquire(timeout=1)
        if got:
            mgr.lock.release()
        assert got, 'outer manager.lock is held while awaiting delivery'
        assert claims == [False, True], 'receipt must be checked again inside admission'
    finally:
        release.set()
        thread.join(5)
    assert done.is_set()
    if fail:
        assert isinstance(outcome.get('error'), DeliveryUncertain), outcome
        assert 'result' not in outcome
    else:
        assert 'error' not in outcome, outcome
        assert outcome['result']['parent_session_id'] == sid
        assert len(proc.stdin.written) == 1

REAL_READER = art._mode_a_reader


@pytest.mark.parametrize('first_turn', [False, True])
def test_codex_exit_callback_precedes_failed_delivery(codex, monkeypatch, first_turn):
    finished = threading.Event()
    observed = []
    errors = []
    threads = []

    def reader(proc, handle, rt):
        threads.append(threading.current_thread())
        try:
            REAL_READER(proc, handle, rt)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    class LateFailure(_Stdin):
        def write(self, text):
            assert finished.wait(3), 'reader did not finish'
            raise BrokenPipeError('child exited before accepting prompt')

    proc = _Proc(LateFailure())
    proc.stdout = iter(())
    proc.wait = lambda: 1
    codex['holder']['proc'] = proc
    monkeypatch.setattr(art, '_mode_a_reader', reader)
    monkeypatch.setattr(art._codex_run_error, 'locate_rollout', lambda *a: None)
    monkeypatch.setattr(art._codex_run_error, 'capture', lambda *a: None)
    session = _session()
    handle = _handle(session)
    handle.meta['callbacks'] = {'on_process_exit': lambda ev, s: observed.append(s['status'])}
    with pytest.raises(art.PromptDeliveryError):
        if first_turn:
            art._mode_a_dispatch(codex['rt'], ['codex', 'exec'], 'answer', '.', 'p',
                'answer', handle.mc_session_id, session, False, None,
                handle.meta['callbacks'], None)
        else:
            codex['rt']._spawn_turn(handle, ['codex', 'exec'], 'answer', '')
    for thread in threads:
        thread.join(3)
    assert not errors, errors
    assert observed == [], 'a rejected delivery must not emit a terminal callback'


@pytest.mark.parametrize('first_turn', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_model_retry_delivers_while_original_reports_not_delivered(codex, monkeypatch, first_turn, fail):
    old_finished = threading.Event()
    retry_output = queue.Queue()
    threads = []
    errors = []

    class LateFailure(_Stdin):
        def write(self, text):
            assert old_finished.wait(3)
            if fail:
                raise BrokenPipeError('original child rejected stdin')
            return super().write(text)

    old = _Proc(LateFailure(), pid=737373)
    old.stdout = iter([json.dumps({'type': 'error', 'message':
        "The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account."})])
    old.wait = lambda: 1
    replacement = _Proc(_Stdin(), pid=737374)

    def output():
        retry_output.get(timeout=5)
        return
        yield

    replacement.stdout = output()
    replacement.wait = lambda: 0
    spawns = []

    def spawn(*a, deliver=None, **kw):
        proc = old if not spawns else replacement
        spawns.append(proc)
        deliver(proc)
        return proc

    def reader(proc, handle, rt):
        threads.append(threading.current_thread())
        try:
            REAL_READER(proc, handle, rt)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if proc is old:
                old_finished.set()

    rt = codex['rt']
    monkeypatch.setattr(art, 'spawn_engine_proc', spawn)
    monkeypatch.setattr(art, '_mode_a_reader', reader)
    monkeypatch.setattr(rt, 'build_command', lambda **kw: ['codex', 'exec'])
    monkeypatch.setattr(rt, '_installed_cli_version', lambda: 'test')
    session = _session(_codex_last_prompt='task text' if first_turn else '')
    try:
        def launch():
            if first_turn:
                _dispatch(rt, session)
            else:
                rt._spawn_turn(_handle(session), ['codex', 'exec'], 'answer', 'native-thread')
        if fail:
            with pytest.raises(art.PromptDeliveryError):
                launch()
            assert spawns == [old], 'a rejected delivery must not launch a retry'
            assert replacement.stdin.written == []
            assert session['proc'] is None
            assert session['process_alive'] is False
            assert codex['killed'] == [old.pid]
        else:
            launch()
            assert spawns == [old, replacement]
            assert replacement.stdin.written == ['task text' if first_turn else 'answer']
            assert session['proc'] is replacement and session['process_alive'] is True
            assert codex['killed'] == []
    finally:
        retry_output.put(None)
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    assert not errors, errors


def test_failed_codex_delivery_preserves_replacement_liveness(codex):
    from mc.pending_delivery import publication_boundary
    session = _session()
    old = _Proc(_Stdin(fail_on='write'))
    replacement = _Proc(_Stdin(), pid=737374)
    codex['holder']['proc'] = old
    with pytest.raises(art.PromptDeliveryError):
        with publication_boundary():
            codex['rt']._spawn_turn(_handle(session), ['codex', 'exec'], 'answer', '')
            session['proc'] = replacement
    assert session['proc'] is replacement and session['process_alive'] is True
    assert codex['killed'] == [old.pid]


@pytest.mark.parametrize('endpoint', ['send', 'followup', 'interrupt'])
@pytest.mark.parametrize('fail', [False, True])
def test_human_codex_followup_ack_is_outside_project_lock(app_env, codex, monkeypatch, endpoint, fail):
    ar = app_env['ar']
    sid = 'codexchat0001'
    session = _session(session_id=sid, project_id='p1', provider='codex', mode='A',
                       started_at='2026-11-09T00:00:00Z', agent_model='')
    app_env['sessions'][sid] = session
    mgr = ar.get_manager('p1')
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda *a: codex['rt'])
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar._native_followup_context, 'refresh_stash', lambda *a, **k: None)
    monkeypatch.setattr(ar._native_followup_context, 'message_for_turn', lambda p, s, m: m)
    monkeypatch.setattr(ar, '_mode_a_token_rollover', lambda *a, **k: a[-1])
    monkeypatch.setattr(codex['rt'], 'build_command', lambda **kw: ['codex', 'exec'])
    monkeypatch.setattr(codex['rt'], '_gate_model', lambda m: (m, ''))
    in_write, release = threading.Event(), threading.Event()
    class Pending(_Stdin):
        def write(self, text):
            in_write.set()
            assert release.wait(5)
            if fail:
                raise BrokenPipeError('not accepted')
            return super().write(text)
    codex['holder']['proc'] = _Proc(Pending())
    def call():
        with app_env['client'] as client:
            response = client.post(f'/api/project/p1/agent/{endpoint}',
                json={'session_id': sid, 'message': 'answer'})
            return response.status_code, response.get_json()
    thread, done, outcome = _start_call(call)
    try:
        assert in_write.wait(3), outcome
        assert not done.is_set()
        got = mgr.lock.acquire(timeout=1)
        if got:
            mgr.lock.release()
        assert got, 'HTTP caller awaited stdin under manager.lock'
    finally:
        release.set()
        thread.join(5)
    assert done.is_set() and 'error' not in outcome, outcome
    status, body = outcome['result']
    assert status == (502 if fail else 200), body
    assert body['session_id'] == sid
    if fail:
        assert body['ok'] is False and body['delivered'] is False


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('replace', [False, True])
def test_live_mode_b_parent_also_awaits_after_outer_lock(revive_env, monkeypatch, fail, replace):
    from types import SimpleNamespace
    from mc import desk_dispatch
    from mc.delegation_delivery import DeliveryUncertain
    import server
    ar = revive_env['ar']
    sid = 'liveparent'
    mgr = ar.get_manager('p1')
    monkeypatch.setattr(ar, '_delivery_shutdown_requested', threading.Event())
    monkeypatch.setattr(ar, '_delegation_app', server.app)
    monkeypatch.setattr(ar, '_pid_is_alive', lambda *a: True)
    monkeypatch.setattr(desk_dispatch, 'deliver', lambda *a: False)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a: {'block': ''})
    monkeypatch.setitem(ar.state.CONFIG, 'auto_model_enabled', False)
    responses = []
    def handoff(row, parent):
        assert mgr.lock._is_owned(), 'submission admission must remain guarded'
    def response(row, body, **kw):
        assert not mgr.lock._is_owned(), 'receipt evidence precedes lock release'
        responses.append(dict(body))
    monkeypatch.setattr(ar, '_delivery_store', SimpleNamespace(
        revalidate_claim=lambda *a: True, record_handoff=handoff,
        record_handoff_response=response))
    in_write, release = threading.Event(), threading.Event()
    class Pending(_Stdin):
        def write(self, text):
            in_write.set()
            assert release.wait(5)
            if fail:
                raise BrokenPipeError('uncertain write')
            return super().write(text)
    proc = _Proc(Pending())
    replacement = _Proc(_Stdin(), pid=737374)
    revive_env['sessions'][sid] = _session(session_id=sid, project_id='p1',
        provider='claude', mode='B', status='idle', proc=proc, process_alive=True,
        stdin_lock=threading.Lock(), claude_session_id='native', agent_model='')
    row = {'project_id': 'p1', 'parent_session_id': sid, 'event_id': 'child:turn:1',
           'fence_token': 'fence', 'payload': json.dumps({'payload': {'message': 'answer'}})}
    thread, done, outcome = _start_call(lambda: ar._process_inbox(row))
    try:
        assert in_write.wait(3), outcome
        assert not done.is_set() and responses == []
        got = mgr.lock.acquire(timeout=1)
        if got:
            if replace:
                revive_env['sessions'][sid]['proc'] = replacement
            mgr.lock.release()
        assert got, 'live parent acknowledgment held outer manager.lock'
    finally:
        release.set()
        thread.join(5)
    assert done.is_set()
    assert len(responses) == 1
    if fail:
        assert isinstance(outcome.get('error'), DeliveryUncertain), outcome
        assert responses[0]['stdin_write_ack'] == 'unknown'
    else:
        assert 'error' not in outcome, outcome
        assert outcome['result']['stdin_write_ack'] == responses[0]['stdin_write_ack'] == 'written'
    if replace:
        assert revive_env['sessions'][sid]['proc'] is replacement
        assert revive_env['sessions'][sid]['process_alive'] is True
        assert revive_env['sessions'][sid]['status'] == 'running'


def test_publication_exception_still_settles_each_launched_delivery():
    from mc.pending_delivery import publication_boundary, complete_or_defer
    observed = []
    with pytest.raises(ValueError, match='caller failed'):
        with publication_boundary():
            complete_or_defer(lambda: observed.append('first'))
            with publication_boundary():
                complete_or_defer(lambda: observed.append('second'))
            assert observed == []
            raise ValueError('caller failed')
    assert observed == ['first', 'second']


def test_stale_claim_after_lock_admission_never_launches(app_env, monkeypatch):
    from types import SimpleNamespace
    ar = app_env['ar']
    claims = iter([True, False])
    monkeypatch.setattr(ar, '_delivery_store', SimpleNamespace(revalidate_claim=lambda *a: next(claims)))
    monkeypatch.setattr(ar, '_revive_parent_for_delegation',
                        lambda *a, **k: pytest.fail('stale claim launched a parent'))
    row = {'project_id': 'p1', 'parent_session_id': 'parent', 'event_id': 'child:turn:1',
           'fence_token': 'fence', 'payload': json.dumps({'payload': {'message': 'answer'}})}
    with pytest.raises(ar.DeliveryNotHandedOff, match='expired while waiting'):
        ar._process_inbox(row)
