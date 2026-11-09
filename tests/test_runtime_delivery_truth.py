"""MC-1168: two runtime-layer paths reported a chat message delivered when nothing was.

(A) Codex. `CodexRuntime._spawn_turn` (and `_mode_a_dispatch` for a first turn)
    handed `spawn_engine_proc` a `deliver` that only STARTED a daemon thread
    writing the prompt, so `write_followup` returned before a byte was written
    and a BrokenPipe/OSError on that thread was invisible. `/agent/followup`
    answered `ok: true`, and the emailed-answer relay (`question_channel._answer`)
    treats a 2xx as "answered". Now the call waits (bounded) for the write and
    raises `PromptDeliveryError` when it did not happen.

(B) Claude Mode B revive. `_revive_from_agent_log` wrote the revival message to
    the new process's stdin inside `except Exception: log_lines.append(...)` and
    returned the session as revived. A dead pipe now rolls the revive back and
    raises `ReviveDeliveryError`, which the callers must not answer with a fresh
    dispatch of the same message under a new session.
"""
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as art  # noqa: E402
from mc.resume_context import write_context_prompt  # noqa: E402


# ── doubles ──────────────────────────────────────────────────────────────────

class _Stdin:
    """Text-mode pipe stand-in. `fail_on` is 'write' | 'close' | None; `gate`
    (an Event) makes write() block until set, to prove the caller waits."""

    def __init__(self, fail_on=None, gate=None):
        self.fail_on = fail_on
        self.gate = gate
        self.written = []
        self.closed = False
        self.write_started = threading.Event()

    def write(self, s):
        self.write_started.set()
        if self.gate is not None:
            assert self.gate.wait(10), 'test gate never released'
        if self.fail_on == 'write':
            raise BrokenPipeError(32, 'Broken pipe')
        self.written.append(s)
        return len(s)

    def flush(self):
        pass

    def close(self):
        if self.fail_on == 'close':
            raise BrokenPipeError(32, 'Broken pipe')
        self.closed = True


class _Proc:
    def __init__(self, stdin, pid=737373):
        self.stdin = stdin
        self.pid = pid

    def poll(self):
        return None


def _handle(session):
    return art.SessionHandle(
        mc_session_id=f'sid-{uuid.uuid4().hex[:8]}', provider='codex', mode='A',
        project_path='.', project_id='p', session_dict=session)


@pytest.fixture()
def codex(monkeypatch):
    """CodexRuntime with the spawn, reader and kill replaced by recorders."""
    killed = []
    holder = {}

    def fake_spawn(cmd, *, deliver=None, **kw):
        holder['deliver_called'] = deliver is not None
        proc = holder['proc']
        if deliver is not None:
            deliver(proc)
        return proc

    monkeypatch.setattr(art, 'spawn_engine_proc', fake_spawn)
    monkeypatch.setattr(art, '_mode_a_reader', lambda *a, **k: None)
    monkeypatch.setattr(art, '_kill_pid', lambda pid, *a, **k: killed.append(pid) or True)
    monkeypatch.setattr(art.vendor_fence, 'prepare_env', lambda *a, **k: None)
    monkeypatch.setattr(art.vendor_fence, 'prepare_command', lambda name, s, cmd, *a, **k: cmd)
    from mc import session_mcp_launch
    monkeypatch.setattr(session_mcp_launch, 'prepare',
                        lambda name, cmd, **kw: (cmd, kw.get('env_extra') or {}))
    return {'rt': art.CodexRuntime(), 'killed': killed, 'holder': holder}


def _session(**extra):
    s = {'log_lines': [], 'status': 'idle', '_system_prompt': ''}
    s.update(extra)
    return s


# ── write_context_prompt reports its outcome ─────────────────────────────────

def test_write_context_prompt_returns_true_only_when_written_and_closed():
    stdin = _Stdin()
    assert write_context_prompt(stdin, 'hello', _session(), '') is True
    assert stdin.written == ['hello'] and stdin.closed


@pytest.mark.parametrize('fail_on', ['write', 'close'])
def test_write_context_prompt_returns_false_and_reports_the_error(fail_on):
    errs = []
    session = _session()
    assert write_context_prompt(_Stdin(fail_on=fail_on), 'hello', session, '',
                                on_error=errs.append) is False
    assert len(errs) == 1 and isinstance(errs[0], BrokenPipeError)
    assert any('prompt delivery failed' in l for l in session['log_lines'])


def test_write_context_prompt_none_stream_is_a_failure():
    assert write_context_prompt(None, 'hello', _session(), '') is False


# ── (A) Codex follow-up turn ─────────────────────────────────────────────────

@pytest.mark.parametrize('fail_on', ['write', 'close'])
def test_spawn_turn_raises_when_stdin_breaks(codex, fail_on):
    session = _session()
    codex['holder']['proc'] = _Proc(_Stdin(fail_on=fail_on))
    with pytest.raises(art.PromptDeliveryError) as ei:
        codex['rt']._spawn_turn(_handle(session), ['codex', 'exec'], 'the answer', '')
    assert 'BrokenPipeError' in str(ei.value)
    assert codex['killed'] == [737373], 'the half-started codex process must be stopped'
    assert session['proc'] is None, 'proc must be disowned so its reader writes no status'
    assert session['process_alive'] is False


def test_spawn_turn_does_not_return_before_the_write_completes(codex):
    gate = threading.Event()
    stdin = _Stdin(gate=gate)
    codex['holder']['proc'] = _Proc(stdin)
    session = _session()
    done = threading.Event()
    box = {}

    def run():
        try:
            codex['rt']._spawn_turn(_handle(session), ['codex', 'exec'], 'the answer', '')
        except BaseException as e:      # surfaced below, never swallowed
            box['err'] = e
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert stdin.write_started.wait(5), 'delivery thread never began writing'
    time.sleep(0.3)
    assert not done.is_set(), '_spawn_turn returned while the prompt write was still pending'
    gate.set()
    assert done.wait(5)
    assert 'err' not in box, box
    assert stdin.written == ['the answer'] and stdin.closed


def test_spawn_turn_times_out_a_write_that_never_finishes(codex, monkeypatch):
    monkeypatch.setattr(art, '_PROMPT_DELIVERY_WAIT_S', 0.2)
    gate = threading.Event()               # never set before the timeout
    codex['holder']['proc'] = _Proc(_Stdin(gate=gate))
    session = _session()
    try:
        with pytest.raises(art.PromptDeliveryError) as ei:
            codex['rt']._spawn_turn(_handle(session), ['codex', 'exec'], 'x', '')
        assert 'did not finish' in str(ei.value)
        assert codex['killed'] == [737373]
    finally:
        gate.set()


def test_spawn_turn_success_keeps_the_proc_and_stamps_context(codex):
    stdin = _Stdin()
    proc = _Proc(stdin)
    codex['holder']['proc'] = proc
    session = _session(_system_prompt='RULES')
    codex['rt']._spawn_turn(_handle(session), ['codex', 'exec'],
                            'RULES\n\n---\n\ntask', '')
    assert session['proc'] is proc and session['status'] == 'running'
    assert stdin.written == ['RULES\n\n---\n\ntask'] and stdin.closed
    assert codex['killed'] == []


def test_write_followup_raises_instead_of_returning_success(codex, monkeypatch):
    rt = codex['rt']
    monkeypatch.setattr(rt, 'build_command', lambda **kw: ['codex', 'exec'])
    monkeypatch.setattr(rt, 'session_model', lambda h: '')
    monkeypatch.setattr(rt, '_gate_model', lambda m: (m, ''))
    codex['holder']['proc'] = _Proc(_Stdin(fail_on='write'))
    session = _session(provider_session_id='thread-1')
    with pytest.raises(art.PromptDeliveryError):
        rt.write_followup(_handle(session), 'the emailed answer')


def test_retry_without_model_hands_the_session_back_when_the_retry_cannot_deliver(codex, monkeypatch):
    rt = codex['rt']
    monkeypatch.setattr(rt, 'build_command', lambda **kw: ['codex', 'exec'])
    codex['holder']['proc'] = _Proc(_Stdin(fail_on='write'))
    old = _Proc(_Stdin(), pid=111)
    session = _session(proc=old, _codex_model_refused='gpt-x', _codex_last_prompt='p')
    assert rt.retry_without_model(_handle(session), old) is False
    assert session['proc'] is old, 'the original reader must still own the session to write the final status'


# ── (A) Codex first turn (dispatch) ──────────────────────────────────────────

def _dispatch(rt, session):
    return art._mode_a_dispatch(
        rt, ['codex', 'exec'], 'task text', '.', 'p', 'task text',
        f'sid-{uuid.uuid4().hex[:8]}', session, False, None, None, None,
        prompt_via_stdin=True, system_prompt='')


def test_dispatch_raises_when_codex_stdin_breaks(codex):
    session = _session()
    codex['holder']['proc'] = _Proc(_Stdin(fail_on='write'))
    with pytest.raises(art.PromptDeliveryError):
        _dispatch(codex['rt'], session)
    assert codex['killed'] == [737373]
    assert session['proc'] is None


def test_dispatch_returns_a_handle_once_the_prompt_is_written(codex):
    stdin = _Stdin()
    codex['holder']['proc'] = _Proc(stdin)
    handle = _dispatch(codex['rt'], _session())
    assert handle.session_dict['status'] == 'running'
    assert stdin.written == ['task text'] and stdin.closed


# ── (A) the follow-up route must not answer 200 for a failed delivery ────────

@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'ar': ar, 'project': project,
               'sessions': mc_state.agent_sessions}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def test_followup_route_reports_failure_when_the_runtime_could_not_deliver(app_env, monkeypatch):
    ar = app_env['ar']
    sid = 'codexchat0001'
    app_env['sessions'][sid] = {
        'session_id': sid, 'project_id': 'p1', 'provider': 'codex', 'mode': 'A',
        'status': 'idle', 'log_lines': [], 'task': 'chat',
        'started_at': '2026-11-09T00:00:00Z', 'last_status_change_time': 0.0,
        'last_output_time': 0.0, 'agent_model': ''}

    class _FailingRuntime:
        def write_followup(self, handle, message, attachments=None):
            raise art.PromptDeliveryError('codex prompt was not delivered to the CLI (test)')

    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: _FailingRuntime())
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar._native_followup_context, 'refresh_stash', lambda *a, **k: None)
    monkeypatch.setattr(ar._native_followup_context, 'message_for_turn', lambda p, s, m: m)
    monkeypatch.setattr(ar, '_mode_a_token_rollover', lambda *a, **k: a[-1])
    monkeypatch.setattr(ar, '_clock_wrap', lambda p, s, m, orig: m)
    monkeypatch.setattr(ar, '_bridge_images_for_blind_model', lambda m, *a, **k: m)
    monkeypatch.setattr(ar, '_followup_cwd_for', lambda *a, **k: '.')

    resp = app_env['client'].post('/api/project/p1/agent/followup',
                                  json={'session_id': sid, 'message': 'answer'})
    body = resp.get_json()
    assert resp.status_code == 502, (resp.status_code, body)
    assert body['ok'] is False and body['delivered'] is False


# ── (B) Claude Mode B revive ─────────────────────────────────────────────────

CSID = '3f6c790f-9ec2-4ded-9793-970a6a2f340b'


@pytest.fixture()
def revive_env(app_env, monkeypatch):
    """Everything `_revive_from_agent_log` touches before and after the spawn,
    stubbed; the spawned process is the double the test supplies."""
    ar = app_env['ar']
    from mc import state as mc_state
    p = app_env['project']
    p['use_streaming_agent'] = True
    monkeypatch.setitem(mc_state.CONFIG, 'agent_revive_from_log', True)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': 'revsid000001', 'claude_session_id': CSID, 'provider': 'claude',
         'task': 'chat', 'trigger_type': 'manual'}])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_continuation_model', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_continuation_effort', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_refuse_duplicate_spawn', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resume_cwd_for', lambda pp, csid: pp)
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('', ''))
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_revive_history_lines', lambda *a, **k: ([], []))
    monkeypatch.setattr(ar, '_clock_wrap', lambda p, s, m, orig: m)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda ctx: ([], None))
    monkeypatch.setattr(ar, '_sysprompt_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_usage_breakdown_turn_start_checkpoint', lambda s: None)
    monkeypatch.setattr(ar._memory_turn, 'seed_delivered', lambda *a, **k: None)
    monkeypatch.setattr(ar._launch_marker, 'launch_cmd', lambda cmd, *a, **k: cmd)
    monkeypatch.setattr(ar._launch_marker, 'launch_env', lambda *a, **k: {})
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda proc, session: None)
    killed = []
    monkeypatch.setattr(ar, '_kill_pid', lambda pid, tree=False: killed.append(pid) or True)
    spawned = {}
    monkeypatch.setattr(ar, 'spawn_engine_proc',
                        lambda cmd, **k: spawned['proc'])
    from mc.session_provenance import note_resume  # noqa: F401
    import mc.session_provenance as sp
    monkeypatch.setattr(sp, 'note_resume', lambda *a, **k: None)
    return {**app_env, 'killed': killed, 'spawned': spawned}


def test_mode_b_revive_with_a_dead_pipe_raises_and_rolls_back(revive_env):
    ar = revive_env['ar']
    proc = _Proc(_Stdin(fail_on='write'), pid=515151)
    revive_env['spawned']['proc'] = proc
    with pytest.raises(ar.ReviveDeliveryError):
        ar._revive_from_agent_log('p1', 'revsid000001', 'the answer', revive_env['project'])
    assert 'revsid000001' not in revive_env['sessions'], 'dead revived session left registered'
    assert revive_env['killed'] == [515151], 'the revived process must be stopped'


def test_mode_b_revive_restores_a_prior_session_entry_on_failure(revive_env):
    ar = revive_env['ar']
    prior = {'session_id': 'revsid000001', 'project_id': 'p1', 'status': 'completed',
             'log_lines': ['old']}
    revive_env['sessions']['revsid000001'] = prior
    revive_env['spawned']['proc'] = _Proc(_Stdin(fail_on='close' if False else 'write'), pid=515152)
    with pytest.raises(ar.ReviveDeliveryError):
        ar._revive_from_agent_log('p1', 'revsid000001', 'x', revive_env['project'])
    assert revive_env['sessions']['revsid000001'] is prior


def test_mode_b_revive_success_still_returns_the_session(revive_env):
    ar = revive_env['ar']
    stdin = _Stdin()
    revive_env['spawned']['proc'] = _Proc(stdin, pid=515153)
    session = ar._revive_from_agent_log('p1', 'revsid000001', 'the answer', revive_env['project'])
    assert session and session['status'] == 'running'
    assert len(stdin.written) == 1 and 'the answer' in stdin.written[0]
    assert revive_env['killed'] == []


def test_agent_send_does_not_fall_through_to_a_fresh_dispatch(revive_env, monkeypatch):
    ar = revive_env['ar']
    revive_env['spawned']['proc'] = _Proc(_Stdin(fail_on='write'), pid=515154)
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **k: pytest.fail('fresh dispatch after a failed revive delivery'))
    monkeypatch.setattr(ar, '_revive_non_claude_from_agent_log',
                        lambda *a, **k: pytest.fail('non-claude revive after a failed delivery'))
    resp = revive_env['client'].post('/api/project/p1/agent/send',
                                     json={'session_id': 'revsid000001', 'message': 'hi'})
    body = resp.get_json()
    assert resp.status_code == 502, (resp.status_code, body)
    assert body['ok'] is False and body['delivered'] is False
    assert 'revsid000001' not in revive_env['sessions']


def test_agent_followup_reports_a_failed_revive_delivery(revive_env, monkeypatch):
    ar = revive_env['ar']
    revive_env['spawned']['proc'] = _Proc(_Stdin(fail_on='write'), pid=515155)
    monkeypatch.setattr(ar, '_revive_non_claude_from_agent_log',
                        lambda *a, **k: pytest.fail('non-claude revive after a failed delivery'))
    resp = revive_env['client'].post('/api/project/p1/agent/followup',
                                     json={'session_id': 'revsid000001', 'message': 'hi'})
    body = resp.get_json()
    assert resp.status_code == 502, (resp.status_code, body)
    assert body['ok'] is False and body['delivered'] is False


def test_scheduled_continue_does_not_report_revived_on_a_failed_delivery(revive_env, monkeypatch):
    """A failed addressed revival propagates; None would authorize replay."""
    from mc.blueprints import scheduler_routes as sr
    ar = revive_env['ar']
    revive_env['spawned']['proc'] = _Proc(_Stdin(fail_on='write'), pid=515156)
    monkeypatch.setattr(ar.get_manager('p1'), 'ensure_guardian', lambda: None)
    monkeypatch.setattr(sr, 'get_manager', ar.get_manager)
    monkeypatch.setattr(sr, '_revive_from_agent_log', ar._revive_from_agent_log)
    monkeypatch.setattr(sr, '_revive_non_claude_from_agent_log',
                        lambda *a, **k: pytest.fail('unexpected'))
    with pytest.raises(ar.ReviveDeliveryError, match='BrokenPipeError'):
        sr._scheduled_continue(revive_env['project'], 'p1', 'revsid000001', 'task')


def test_delegation_parent_revive_fails_closed_on_a_failed_delivery(app_env, monkeypatch):
    """`_revive_parent_for_delegation` wraps the revive in `except Exception` ->
    DeliveryUncertain, so a ReviveDeliveryError can never come back as revived."""
    from mc.delegation_delivery import DeliveryUncertain
    ar = app_env['ar']
    entry = {'session_id': 'parent000001', 'status': 'completed', 'provider': 'claude',
             'claude_session_id': CSID, 'agent_model': 'sonnet', 'ts': '2026-11-09T00:00:00Z'}
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: None)

    def _boom(*a, **k):
        raise ar.ReviveDeliveryError('pipe closed')

    monkeypatch.setattr(ar, '_revive_from_agent_log', _boom)
    with pytest.raises(DeliveryUncertain):
        ar._revive_parent_for_delegation('p1', 'parent000001', 'child report', app_env['project'])
