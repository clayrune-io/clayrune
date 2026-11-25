"""A message that never reached the agent is never "NOT answered yet"
(2026-11-25 review, F6).

`_mt_turn_prompt` is set when a send is accepted for routing, before the turn
is certain to run, and was cleared only by a `result` event. A refused send,
or a stdin write that failed, left it set; the CLI then started a turn of its
own (a background-job wake), a mid-turn roll fired, and the state block told
the fresh session to finish a message the agent had never received.
"""
import importlib
import json
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import midturn_rollover as mt
from mc import state

REFUSED = 'child report that was refused'
OLD = 'the message the running turn is answering'


@pytest.fixture(autouse=True)
def rollover_on(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'midturn_rollover_enabled', True)


# ── the note is taken back ──────────────────────────────────────────────────

def test_undelivered_prompt_is_removed():
    s = {}
    mt.note_user_message(s, REFUSED)
    mt.note_message_undelivered(s, REFUSED)
    assert '_mt_turn_prompt' not in s


def test_refused_interrupt_gives_the_running_turn_its_prompt_back():
    s = {'_mt_turn_prompt': OLD}
    mt.note_user_message(s, REFUSED)
    mt.note_message_undelivered(s, REFUSED, previous_prompt=OLD)
    assert s['_mt_turn_prompt'] == OLD


def test_a_newer_turn_prompt_is_left_alone():
    s = {}
    mt.note_user_message(s, REFUSED)
    mt.note_user_message(s, 'a later message that did open a turn')
    mt.note_message_undelivered(s, REFUSED)
    assert s['_mt_turn_prompt'] == 'a later message that did open a turn'


def test_undelivered_mid_turn_message_leaves_the_running_turn_prompt():
    # The same report text delivered twice: the second write fails while the
    # first one's turn is still running.
    s = {}
    mt.note_user_message(s, REFUSED)
    mt.note_user_message(s, 'steer that was written', mid_turn=True)
    mt.note_user_message(s, REFUSED, mid_turn=True)
    mt.note_message_undelivered(s, REFUSED, mid_turn=True)
    assert s['_mt_turn_prompt'] == REFUSED
    assert [m['text'] for m in s['_mt_unanswered_msgs']] == ['steer that was written']


@pytest.mark.parametrize('status,body,kept', [
    (409, {'error': 'native context pending'}, False),
    (409, {'error': 'allowance', 'allowance_exhausted': True}, False),
    (503, {'ok': False, 'stdin_write_ack': 'failed'}, False),
    (502, {'ok': False, 'delivered': False}, False),
    (504, {'ok': False, 'stdin_write_ack': 'unknown'}, True),   # may have landed
    (502, {'ok': False, 'delivered': None}, True),              # may have landed
    (200, {'ok': True}, True),
])
def test_send_response_decides_whether_the_note_stands(status, body, kept):
    s = {}
    mt.note_user_message(s, REFUSED)
    mt.note_send_response(s, REFUSED, None, status, body)
    assert ('_mt_turn_prompt' in s) is kept


def test_state_block_after_a_refused_send_has_no_inflight_section():
    s = {'task': 'original task'}
    mt.note_user_message(s, REFUSED)
    mt.note_send_response(s, REFUSED, None, 409, {'error': 'refused'})
    assert 'NOT answered yet' not in mt.build_state_block(s, '/nonexistent-cwd')


# ── /agent/send: a handler refusal after the note ───────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import local_auth as la
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    # After the import: the first `import server` in a process loads CONFIG
    # and would turn the flag the autouse fixture set back off.
    monkeypatch.setitem(state.CONFIG, 'midturn_rollover_enabled', True)
    snap = dict(state.agent_sessions)
    state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        state.agent_sessions.clear()
        state.agent_sessions.update(snap)


def _send(client, monkeypatch, tmp_path, session, handler, status, body, **extra):
    from mc.blueprints import agent_routes as ar
    pp = tmp_path / 'proj'
    pp.mkdir()
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': 'p1', 'project_path': str(pp)})
    state.agent_sessions['s1'] = session
    monkeypatch.setattr(ar, handler, lambda project_id, **kw: (ar.jsonify(body), status))
    return client.post('/api/project/p1/agent/send',
                       json={'session_id': 's1', 'message': REFUSED, **extra})


def _session(**kw):
    s = {'status': 'idle', 'provider': 'claude', 'mode': 'B', 'log_lines': [],
         'proc': SimpleNamespace(poll=lambda: None, stdin=None, pid=-1),
         'project_id': 'p1', 'session_id': 's1'}
    s.update(kw)
    return s


def test_followup_refused_after_the_note_clears_the_prompt(client, tmp_path, monkeypatch):
    s = _session()
    r = _send(client, monkeypatch, tmp_path, s, 'agent_followup', 409,
              {'error': 'allowance exhausted', 'allowance_exhausted': True})
    assert r.status_code == 409
    assert '_mt_turn_prompt' not in s


def test_interrupt_refused_after_the_note_restores_the_running_turns_prompt(
        client, tmp_path, monkeypatch):
    s = _session(status='running', _mt_turn_prompt=OLD)
    r = _send(client, monkeypatch, tmp_path, s, 'agent_interrupt', 409,
              {'error': 'native context pending'}, send_mode='interrupt')
    assert r.status_code == 409
    assert s['_mt_turn_prompt'] == OLD


def test_unknown_write_outcome_keeps_the_prompt(client, tmp_path, monkeypatch):
    s = _session()
    r = _send(client, monkeypatch, tmp_path, s, 'agent_followup', 504,
              {'ok': False, 'stdin_write_ack': 'unknown'})
    assert r.status_code == 504
    assert s['_mt_turn_prompt'] == REFUSED


# ── agent_followup: the durable stdin write fails after the note ────────────

class _Stdin:
    def __init__(self):
        self.writes = []

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass


def _payload_build_fails(*args):
    # The one outcome the writer reports as 'failed': it broke before any
    # byte was written. A break during the write itself is 'unknown'.
    raise RuntimeError('memory refresh failed')


def _durable_followup(monkeypatch, tmp_path, parent):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': parent})
    monkeypatch.setattr(ar, 'load_project', lambda _: {'id': 'p', 'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_pid_is_alive', lambda _: True)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, '_memory_turn', SimpleNamespace(refresh_for_turn=_payload_build_fails))
    monkeypatch.setattr(ar, '_behavior_tail', SimpleNamespace(render=lambda session=None: ''))
    monkeypatch.setattr(ar, '_apply_mobile_brief', lambda message, data: message)
    monkeypatch.setattr(ar, '_clock_wrap', lambda project, session, content, message=None: content)
    monkeypatch.setattr(ar, '_rearm_notify_for_new_turn', lambda _: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)
    monkeypatch.setattr(ar, '_kill_proc_background', lambda proc: None)
    app = Flask('f6')
    app.register_blueprint(ar.bp)
    with app.test_client() as c:
        return c.post('/api/project/p/agent/followup', json={
            'message': REFUSED, 'session_id': 'parent', '_durable_delivery_ack': True})


def _parent(**kw):
    p = {'project_id': 'p', 'status': 'completed', 'mode': 'B', 'provider': 'claude',
         'agent_model': 'sonnet', 'incognito': False, 'process_alive': True,
         'proc': SimpleNamespace(stdin=_Stdin(), pid=4242, poll=lambda: None),
         'stdin_lock': threading.Lock(), 'log_lines': []}
    p.update(kw)
    return p


def test_failed_durable_write_to_an_idle_parent_clears_the_prompt(monkeypatch, tmp_path):
    parent = _parent()
    response = _durable_followup(monkeypatch, tmp_path, parent)
    assert response.status_code == 503, response.get_json()
    assert response.get_json()['stdin_write_ack'] == 'failed'
    assert parent['proc'].stdin.writes == []
    assert '_mt_turn_prompt' not in parent


def test_failed_durable_write_into_a_running_turn_is_not_left_unanswered(monkeypatch, tmp_path):
    parent = _parent(status='running', _mt_turn_prompt=OLD)
    response = _durable_followup(monkeypatch, tmp_path, parent)
    assert response.status_code == 503, response.get_json()
    assert parent['_mt_turn_prompt'] == OLD
    assert not parent.get('_mt_unanswered_msgs')


# ── the stream readers ──────────────────────────────────────────────────────

SID = 'cli-sess-f6'


def _assistant(text):
    return json.dumps({'type': 'assistant', 'session_id': SID, 'parent_tool_use_id': None,
                       'message': {'content': [{'type': 'text', 'text': text}]}})


def _result(text):
    return json.dumps({'type': 'result', 'subtype': 'success', 'session_id': SID,
                       'result': text, 'num_turns': 1,
                       'usage': {'input_tokens': 10, 'output_tokens': 5}})


class _Proc:
    pid = -1

    def __init__(self, lines):
        self.stdout = self._gen(lines)
        self.terminated = False

    @staticmethod
    def _gen(lines):
        for line in lines:
            if callable(line):     # runs between stream lines; not output
                line()
                continue
            yield line + '\n'

    def wait(self):
        return 0

    def poll(self):
        return None

    def kill(self):
        pass

    def terminate(self):
        self.terminated = True


@pytest.fixture
def ar(tmp_data_dir, tmp_path, monkeypatch):
    from mc.delegation_delivery import DeliveryStore
    server = importlib.import_module('server')
    importlib.reload(server)
    mod = importlib.import_module('mc.blueprints.agent_routes')
    monkeypatch.setattr(mod, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(mod, '_log_agent_completion', lambda s: None)
    monkeypatch.setattr(mod, '_maybe_checkpoint', lambda s: None)
    monkeypatch.setattr(mod, '_delivery_store', DeliveryStore(tmp_path / 'd.db'))
    monkeypatch.setitem(state.CONFIG, 'midturn_rollover_enabled', True)
    return mod


def _stream_session(**extra):
    s = {'project_id': 'p-f6', 'session_id': 'sess-f6', 'status': 'running',
         'mode': 'B', 'provider': 'claude', 'log_lines': [], 'task': 'build it',
         'last_output_time': 0.0, 'last_status_change_time': 0.0}
    s.update(extra)
    return s


def test_self_started_turn_drops_a_prompt_left_by_a_refused_send(ar):
    session = _stream_session()
    seen = {}

    def refused_send_while_idle():
        # What a refused / undelivered send used to leave behind.
        session['_mt_turn_prompt'] = REFUSED

    def during_the_woken_turn():
        seen['prompt'] = session.get('_mt_turn_prompt')
        seen['status'] = session.get('status')

    proc = _Proc([_assistant('first answer'), _result('first answer'),
                  refused_send_while_idle,
                  _assistant('woken by a background job'), during_the_woken_turn,
                  _result('woken by a background job')])
    session['proc'] = proc
    ar._read_agent_stream_b(proc, session)

    assert not [l for l in session['log_lines'] if l.startswith('[stream error')], session['log_lines']
    assert seen == {'prompt': None, 'status': 'running'}


@pytest.mark.parametrize('reader', ['_read_agent_stream_b', '_read_agent_stream'])
def test_billing_halt_still_ends_the_turn(ar, monkeypatch, reader):
    # The result arrived: the turn's prompt and its absorbed steers were answered.
    monkeypatch.setattr(ar._credits_session, 'record_result', lambda *a, **k: True)
    session = _stream_session(
        mode='B' if reader.endswith('_b') else 'A',
        _mt_turn_prompt=OLD,
        _mt_unanswered_msgs=[{'ts': '07:41:00Z', 'text': 'a steer'}])
    proc = _Proc([_assistant('answer'), _result('answer')])
    session['proc'] = proc
    getattr(ar, reader)(proc, session)

    assert proc.terminated
    assert '_mt_turn_prompt' not in session
    assert '_mt_unanswered_msgs' not in session
