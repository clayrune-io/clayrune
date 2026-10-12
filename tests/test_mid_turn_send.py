"""MC-1086: a Send to a running session steers / queues / interrupts.

Unit tests drive mc.mid_turn_send directly; the route tests drive the real
`/agent/send` through the Flask client (same fixture shape as
test_agent_send_non_claude_revive.py) with a fake process.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import mid_turn_send as mts  # noqa: E402


class FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.pid = 4242

    def poll(self):
        return None if self._alive else 1


def _sess(**kw):
    s = {'status': 'running', 'provider': 'claude', 'mode': 'B',
         'proc': FakeProc(), 'log_lines': [], 'project_id': 'p1', 'session_id': 's1'}
    s.update(kw)
    return s


# ── decide(): steer vs queue vs interrupt per provider/state ────────────────

@pytest.mark.parametrize('session,kind,intent,want', [
    # Claude Mode B, live process: steer by default
    (_sess(), 'native', 'steer', mts.STEER),
    # explicit Interrupt always wins, for every provider
    (_sess(), 'native', 'interrupt', mts.INTERRUPT),
    (_sess(provider='codex', mode='A'), 'none', 'interrupt', mts.INTERRUPT),
    # vendors with no live stdin: queue
    (_sess(provider='codex', mode='A'), 'none', 'steer', mts.QUEUE),
    (_sess(provider='gemini', mode='A'), 'none', 'steer', mts.QUEUE),
    (_sess(provider='qwen', mode='A'), 'none', 'steer', mts.QUEUE),
    (_sess(provider='opencode', mode='A'), 'none', 'steer', mts.QUEUE),
    (_sess(provider='goose', mode='A'), 'none', 'steer', mts.QUEUE),
    (_sess(provider='aider', mode='A'), 'none', 'steer', mts.QUEUE),
    (_sess(provider='kiro', mode='A'), 'none', 'steer', mts.QUEUE),
    # Claude Mode A has no stdin into a running turn either
    (_sess(mode='A'), 'native', 'steer', mts.QUEUE),
    # states we cannot steer into keep the old interrupt-and-resume
    (_sess(waiting_for_question=True), 'native', 'steer', mts.INTERRUPT),
    (_sess(waiting_for_plan_approval=True), 'native', 'steer', mts.INTERRUPT),
    (_sess(pending_questions=[{'q': 1}]), 'native', 'steer', mts.INTERRUPT),
    (_sess(_interrupting=True), 'native', 'steer', mts.INTERRUPT),
    (_sess(_respawn_in_flight=1.0), 'native', 'steer', mts.INTERRUPT),
    (_sess(proc=FakeProc(alive=False)), 'native', 'steer', mts.INTERRUPT),
    (_sess(proc=None), 'native', 'steer', mts.INTERRUPT),
    (_sess(provider='codex', mode='A', proc=FakeProc(alive=False)), 'none', 'steer', mts.INTERRUPT),
    # capability unreadable -> never guess
    (_sess(provider='codex', mode='A'), None, 'steer', mts.INTERRUPT),
])
def test_decide(session, kind, intent, want):
    assert mts.decide(session, kind, intent)[0] == want


def test_normalize_intent():
    assert mts.normalize_intent('interrupt') == mts.INTENT_INTERRUPT
    assert mts.normalize_intent(' Interrupt ') == mts.INTENT_INTERRUPT
    for raw in (None, '', 'steer', 'queue', 7, {}):
        assert mts.normalize_intent(raw) == mts.INTENT_STEER


def test_claude_and_vendors_report_expected_mode_b_kind():
    assert mts.provider_mode_b_kind('claude') == 'native'
    for v in ('codex', 'gemini', 'qwen', 'opencode', 'goose', 'aider', 'kiro'):
        assert mts.provider_mode_b_kind(v) == 'none', v


# ── actions ─────────────────────────────────────────────────────────────────

def test_queue_message_logs_queued_line_and_keeps_text():
    s = _sess(_send_already_logged=True)
    mts.queue_message(s, 'do X', 'Ron')
    assert s['pending_followups'] == ['do X']
    assert s['log_lines'] == ['> [queued] Ron: do X']
    assert '_send_already_logged' not in s


def test_steer_live_writes_stream_json_user_message_and_takes_turn_owner():
    seen = {}

    def write(proc, payload, **kw):
        seen['proc'], seen['payload'], seen['kw'] = proc, payload, kw
        return {'ack': 'written'}

    s = _sess(_send_already_logged=True, waiting_for_question=True)
    ack = mts.steer_live(s, content='change course', write=write, owner=('_turn_owner', 'human'))
    assert ack['ack'] == 'written'
    assert seen['proc'] is s['proc']
    assert seen['payload'].endswith('\n')
    assert json.loads(seen['payload']) == {
        'type': 'user', 'message': {'role': 'user', 'content': 'change course'}}
    assert s['_turn_owner'] == 'human'
    assert '_send_already_logged' not in s
    assert s['status'] == 'running'  # same turn: no status flip


def test_steer_live_failed_write_leaves_owner_alone():
    s = _sess()
    ack = mts.steer_live(s, content='x', write=lambda *a, **k: {'ack': 'failed', 'error': 'pipe'},
                         owner=('_turn_owner', 'human'))
    assert ack['ack'] == 'failed'
    assert '_turn_owner' not in s


# ── vendor queue drain ──────────────────────────────────────────────────────

def test_drain_delivers_oldest_to_finished_vendor_turn():
    got = []
    s = _sess(provider='codex', mode='A', status='completed', pending_followups=['a', 'b'])
    assert mts.drain_next(s, lambda sess, m: (got.append(m) or True, ''))
    assert got == ['a'] and s['pending_followups'] == ['b']


@pytest.mark.parametrize('kw', [
    dict(provider='claude'),                 # Claude Mode A drains in its own reader
    dict(provider='codex', status='stopped'),
    dict(provider='codex', status='running'),
    dict(provider='codex', status='completed', _interrupted=True),
])
def test_drain_leaves_queue_alone(kw):
    s = _sess(mode='A', pending_followups=['a'], **{'status': 'completed', **kw})
    assert not mts.drain_next(s, lambda *_: (True, ''))
    assert s['pending_followups'] == ['a']


def test_drain_failure_is_visible_not_silent():
    s = _sess(provider='codex', mode='A', status='completed', pending_followups=['keep me'])
    mts.drain_next(s, lambda sess, m: (False, 'allowance exhausted'))
    assert 'keep me' in s['log_lines'][-1] and 'allowance exhausted' in s['log_lines'][-1]


def test_runtime_completion_hook_drains_vendor_queue(monkeypatch):
    """The wiring: finishing a vendor turn through `_runtime_log_completion`
    hands the queued message to /agent/followup. Identity of the hook
    (`_RUNTIME_CALLBACKS['on_process_exit']`) is pinned elsewhere."""
    from mc.blueprints import agent_routes as ar
    delivered = []
    monkeypatch.setattr(ar._mid_turn_send, 'deliver_via_followup',
                        lambda app, fn, sess, m: (delivered.append(m) or True, ''))
    s = _sess(provider='codex', mode='A', status='completed', incognito=True,
              pending_followups=['queued one'])
    ar._runtime_log_completion(None, s)
    assert delivered == ['queued one']


# ── /agent/send end to end ──────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc.blueprints import local_auth as la
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    snap = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snap)


def _route_setup(monkeypatch, tmp_path, session):
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    pp = tmp_path / 'proj'
    pp.mkdir()
    monkeypatch.setattr(ar, 'load_project',
                        lambda pid: {'id': 'p1', 'project_path': str(pp)})
    mc_state.agent_sessions['s1'] = session
    calls = {'interrupt': 0, 'followup': 0, 'writes': []}

    def _interrupt(project_id, *, _internal=None):
        calls['interrupt'] += 1
        return ar.jsonify({'ok': True})

    def _followup(project_id):
        calls['followup'] += 1
        return ar.jsonify({'ok': True})
    monkeypatch.setattr(ar, 'agent_interrupt', _interrupt)
    monkeypatch.setattr(ar, 'agent_followup', _followup)

    def _write(proc, data, **kw):
        calls['writes'].append(data)
        return {'ack': 'written'}
    monkeypatch.setattr(ar, '_bounded_stdin_write', _write)
    return calls


def _post(client, body):
    return client.post('/api/project/p1/agent/send', json={'session_id': 's1', **body})


def test_send_to_running_claude_mode_b_steers_without_interrupt(client, tmp_path, monkeypatch):
    s = _sess(provider='claude', mode='B')
    calls = _route_setup(monkeypatch, tmp_path, s)
    r = _post(client, {'message': 'new direction'})
    j = r.get_json()
    assert r.status_code == 200 and j['route'] == 'steer', j
    assert calls['interrupt'] == 0 and calls['followup'] == 0
    assert len(calls['writes']) == 1
    assert json.loads(calls['writes'][0])['message']['content'].endswith('new direction')
    assert any('new direction' in l for l in s['log_lines'])
    assert s['status'] == 'running'


def test_send_with_interrupt_mode_keeps_old_behaviour(client, tmp_path, monkeypatch):
    s = _sess(provider='claude', mode='B')
    calls = _route_setup(monkeypatch, tmp_path, s)
    r = _post(client, {'message': 'stop and do this', 'send_mode': 'interrupt'})
    assert r.get_json()['route'] == 'interrupt'
    assert calls['interrupt'] == 1 and calls['writes'] == []


def test_send_to_running_vendor_queues(client, tmp_path, monkeypatch):
    s = _sess(provider='codex', mode='A')
    calls = _route_setup(monkeypatch, tmp_path, s)
    r = _post(client, {'message': 'later please'})
    j = r.get_json()
    assert j['route'] == 'queue' and j['queued'] is True, j
    assert calls['interrupt'] == 0 and calls['writes'] == []
    assert s['pending_followups'] == ['later please']
    assert '[queued]' in s['log_lines'][-1]


def test_failed_steer_write_falls_back_to_interrupt(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    s = _sess(provider='claude', mode='B')
    calls = _route_setup(monkeypatch, tmp_path, s)
    monkeypatch.setattr(ar, '_bounded_stdin_write',
                        lambda *a, **k: {'ack': 'failed', 'error': 'broken pipe'})
    r = _post(client, {'message': 'must not be lost'})
    assert r.get_json()['route'] == 'interrupt'
    assert calls['interrupt'] == 1
    assert s.get('_send_already_logged') is True


def test_idle_session_still_goes_to_followup(client, tmp_path, monkeypatch):
    s = _sess(provider='claude', mode='B', status='idle')
    calls = _route_setup(monkeypatch, tmp_path, s)
    r = _post(client, {'message': 'hi'})
    assert r.get_json()['route'] == 'followup'
    assert calls['followup'] == 1 and calls['writes'] == []


# ── MC-1088: what a roll must carry is recorded at the send ────────────────

@pytest.fixture()
def rollover_on(monkeypatch):
    from mc import state as mc_state
    monkeypatch.setitem(mc_state.CONFIG, 'midturn_rollover_enabled', True)


def test_steered_message_is_recorded_as_unanswered_and_latest(client, tmp_path, monkeypatch, rollover_on):
    s = _sess(provider='claude', mode='B')
    _route_setup(monkeypatch, tmp_path, s)
    assert _post(client, {'message': 'what happens with this msg?'}).get_json()['route'] == 'steer'
    assert [m['text'] for m in s['_mt_unanswered_msgs']] == ['what happens with this msg?']
    assert s['_mt_latest_user_msg'] == 'what happens with this msg?'


def test_queued_message_is_recorded_as_unanswered(client, tmp_path, monkeypatch, rollover_on):
    s = _sess(provider='claude', mode='A')    # Claude Mode A: queue, not steer
    _route_setup(monkeypatch, tmp_path, s)
    assert _post(client, {'message': 'later please'}).get_json()['route'] == 'queue'
    assert [m['text'] for m in s['_mt_unanswered_msgs']] == ['later please']


def test_failed_steer_is_not_recorded_as_absorbed(client, tmp_path, monkeypatch, rollover_on):
    from mc.blueprints import agent_routes as ar
    s = _sess(provider='claude', mode='B')
    _route_setup(monkeypatch, tmp_path, s)
    monkeypatch.setattr(ar, '_bounded_stdin_write',
                        lambda *a, **k: {'ack': 'failed', 'error': 'broken pipe'})
    assert _post(client, {'message': 'not delivered by stdin'}).get_json()['route'] == 'interrupt'
    assert not s.get('_mt_unanswered_msgs'), 'the interrupt delivers it as the turn\'s own prompt'
    assert s['_mt_latest_user_msg'] == 'not delivered by stdin'
