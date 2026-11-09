"""Queued replay refuses persistence failure before creating a provider child."""
import pytest

from test_runtime_completion_log import env, _mode_b_session, _assistant_line, _log_rows  # noqa: F401
from test_dispatch_spawn_save_race import env as dispatch_env  # noqa: F401
from mc.delegation_delivery import DeliveryStore


@pytest.mark.parametrize('failure', ['pending-save', 'turn-allocation'])
def test_reader_queue_reservation_refusal_retains_message_and_owner(env, monkeypatch, failure):
    ar = env['ar']
    store = DeliveryStore(env['tmp_path'] / 'delivery.sqlite')
    monkeypatch.setattr(ar, '_delivery_store', store)
    session = _mode_b_session(env, 'r11-queue')
    session.update(mode='A', _notify_session='parent',
                   pending_followups=['first work', 'second work'])
    ar._allocate_delegation_turn(session)
    ar._log_agent_dispatch_pending(session, strict=True)
    token, turn = session['_delivery_attempt_id'], session['_delegation_turn']
    launched = []

    class Proc:
        pid = -201
        stdout = [_assistant_line('previous answer')]

        def wait(self):
            return 0

        def poll(self):
            return 0

    previous = session['proc'] = Proc()
    save = ar._save_agent_log

    def guarded_save(pid, rows):
        if rows[0]['status'] == 'in_progress':
            raise OSError('prelaunch pending save refused')
        return save(pid, rows)

    def refuse_turn(*args):
        raise OSError('prelaunch allocation refused')

    if failure == 'pending-save':
        monkeypatch.setattr(ar, '_save_agent_log', guarded_save)
    else:
        monkeypatch.setattr(store, 'allocate_turn', refuse_turn)
    monkeypatch.setattr(ar, 'spawn_engine_proc', lambda *args, **kwargs: launched.append(args))
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake')
    prompt = env['tmp_path'] / 'system-prompt.txt'
    prompt.write_text('test context')
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *args: ([], str(prompt)))
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *args, **kwargs: [])
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar, '_run_runtime_callbacks', lambda *args: None)
    ar._read_agent_stream(previous, session)
    assert not launched
    assert session['proc'] is previous
    assert session['pending_followups'] == ['first work', 'second work']
    assert not session.get('_dispatching_followup')
    assert session['status'] == 'completed'
    assert session['_delivery_attempt_id'] == token
    assert session['_delegation_turn'] == turn
    assert _log_rows(env)[0]['delivery_attempt_id'] == token
    assert 'previous answer' in _log_rows(env)[0]['summary']
    assert any('reservation failed; message kept queued' in line for line in session['log_lines'])
    assert not prompt.exists()


def test_queue_refusal_preserves_turn_flags(env, monkeypatch):
    ar = env['ar']
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(env['tmp_path'] / 'delivery.sqlite'))
    session = _mode_b_session(env, 'r11-staged')
    session.update(status='completed', _empty_turn='previous empty turn', _turn_tool_calls=3,
                   _limit_resume_exhaustion={'reason': 'previous limit'})
    ar._allocate_delegation_turn(session)
    ar._log_agent_dispatch_pending(session, strict=True)
    before = dict(session)

    def refuse(*args):
        raise OSError('prelaunch storage refused')

    monkeypatch.setattr(ar, '_save_agent_log', refuse)
    monkeypatch.setattr(ar, 'spawn_engine_proc', lambda *args, **kwargs: pytest.fail('spawned'))
    ar._auto_dispatch_followup_locked(session, 'queued message', env['project'],
                                    env['project']['project_path'], ['fake'], None)
    for key in ('_empty_turn', '_turn_tool_calls', '_limit_resume_exhaustion',
                '_delivery_attempt_id', '_delegation_turn', '_delivery_generation', 'status'):
        assert session.get(key) == before.get(key)
    assert session['pending_followups'] == ['queued message']


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('metadata', [None, {'_delivery_attempt_id': 'stale-metadata'}])
def test_initial_enrichment_uses_the_reserved_token(dispatch_env, tmp_path, monkeypatch,
                                                   streaming, metadata):
    import server
    from mc import state

    ar = dispatch_env['ar']
    data = tmp_path / 'history'
    data.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data)
    monkeypatch.setattr(ar, '_load_agent_log', server._load_agent_log)
    monkeypatch.setattr(ar, '_register_process', lambda *args: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)
    monkeypatch.setitem(state.CONFIG, 'use_streaming_agent', streaming)
    pending_fields = ar._delivery_attempt.pending_fields
    minted = []

    def mint(session):
        result = pending_fields(session)
        minted.append(result['delivery_attempt_id'])
        return result

    monkeypatch.setattr(ar._delivery_attempt, 'pending_fields', mint)
    sid = ar._dispatch_agent_internal('p1', 'delegated task', notify_session='parent',
                                      session_metadata=metadata)
    session = dispatch_env['sessions'][sid]
    assert minted == [session['_delivery_attempt_id']]
    assert ar._load_agent_log('p1')[0]['delivery_attempt_id'] == minted[0]
