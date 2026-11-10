"""MC-1168 r12: owner-checked launch settlement and the complete route inventory."""
import ast
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import agent_spawn, launch_transaction as transaction
from test_runtime_completion_log import env, _mode_b_session, _log_rows  # noqa: F401


def _completed(env):
    ar = env['ar']
    session = _mode_b_session(env, 'transaction')
    ar._log_agent_dispatch_pending(session, strict=True)
    ar._update_agent_log('proj1', lambda rows: rows[0].update(
        status='completed', summary='previous answer', usage={'input_tokens': 123}))
    session.update(status='completed', _empty_turn='old empty flag', _turn_tool_calls=4,
                   _notify_session_sent=True)
    return session


def test_refusal_restores_the_complete_row_and_turn_state(env):
    ar = env['ar']
    session = _completed(env)
    prior = _log_rows(env)
    old_owner = session['_delivery_attempt_id']
    tx = transaction.start_turn(session, None)
    assert tx.attempt.attempt_id != old_owner
    assert _log_rows(env)[0]['status'] == 'in_progress'

    def refuse():
        raise OSError('no executable, no child')

    with pytest.raises(OSError, match='no executable'):
        transaction.launch(tx, refuse)
    assert tx.restored
    assert _log_rows(env) == prior
    assert session['_delivery_attempt_id'] == old_owner
    assert session['_empty_turn'] == 'old empty flag'
    assert session['_turn_tool_calls'] == 4
    assert session['_notify_session_sent'] is True


@pytest.mark.parametrize('successor', ['durable', 'live', 'both'])
def test_refusal_cannot_restore_over_a_successor(env, successor):
    ar = env['ar']
    session = _completed(env)
    tx = transaction.start_turn(session, None)

    def refuse():
        if successor in ('durable', 'both'):
            ar._update_agent_log('proj1', lambda rows: rows[0].update(
                delivery_attempt_id='successor', status='completed', summary='successor answer'))
        if successor in ('live', 'both'):
            session['_delivery_attempt_id'] = 'successor'
        raise OSError('refused after successor takeover')

    with pytest.raises(OSError):
        tx.launch(refuse)
    assert not tx.restored
    if successor in ('durable', 'both'):
        assert _log_rows(env)[0]['summary'] == 'successor answer'
        assert _log_rows(env)[0]['delivery_attempt_id'] == 'successor'
    if successor in ('live', 'both'):
        assert session['_delivery_attempt_id'] == 'successor'


@pytest.mark.parametrize('error', [RuntimeError, OSError])
def test_ambiguous_launch_keeps_owner_and_refuses_future_launches(env, error):
    session = _completed(env)
    tx = transaction.start_turn(session, None)
    owner = tx.attempt.attempt_id

    @transaction.spawn_boundary
    def unknown():
        raise error('launch outcome unknown')

    with pytest.raises(error):
        tx.launch(unknown)
    assert not tx.restored
    assert session['_delivery_attempt_id'] == owner
    assert _log_rows(env)[0]['delivery_attempt_id'] == owner
    assert _log_rows(env)[0]['launch_outcome'] == 'unknown'
    assert session['_launch_uncertain']
    assert session['pending_recovery_message'] is None
    with pytest.raises(RuntimeError, match='requires review'):
        transaction.start_turn(session, None)
    # Durable refusal also survives rebuilding the live dictionary.
    with pytest.raises(RuntimeError, match='durable launch outcome'):
        transaction.claim({'project_id': 'proj1', 'session_id': session['session_id']})


def test_restore_failure_keeps_unretryable_reserved_owner(env, monkeypatch):
    ar = env['ar']
    session = _completed(env)
    tx = transaction.start_turn(session, None)

    def storage_refused(*args):
        raise OSError('restoration disk fault')

    monkeypatch.setattr(ar, '_save_agent_log', storage_refused)
    with pytest.raises(OSError, match='no child'):
        tx.launch(lambda: (_ for _ in ()).throw(OSError('no child')))
    assert not tx.restored
    assert session['_launch_uncertain']
    assert session['_delivery_attempt_id'] == tx.attempt.attempt_id
    assert _log_rows(env)[0]['delivery_attempt_id'] == tx.attempt.attempt_id


def test_settlement_failure_keeps_the_prelaunch_owner(env, monkeypatch):
    ar = env['ar']
    session = _completed(env)
    tx = transaction.start_turn(session, None)
    proc = object()
    assert tx.launch(lambda: proc) is proc
    saved = tx.attempt.attempt_id

    def refuse(*args):
        raise OSError('post-child storage fault')

    monkeypatch.setattr(ar, '_save_agent_log', refuse)
    published = {'project_id': 'proj1', 'session_id': session['session_id']}
    tx.settle(published)
    assert published['_delivery_attempt_id'] == saved
    assert _log_rows(env)[0]['delivery_attempt_id'] == saved
    assert not tx.restore()


def test_running_rearm_failure_preserves_the_rearmed_callback(env, monkeypatch):
    ar = env['ar']
    session = _completed(env)
    session['status'] = 'running'
    old_owner = session['_delivery_attempt_id']

    def refuse(*args):
        raise OSError('already-running rearm save fault')

    monkeypatch.setattr(ar, '_save_agent_log', refuse)
    with pytest.raises(OSError):
        ar._rearm_notify_for_new_turn(session, postlaunch=True)
    assert session['_delivery_attempt_id'] == old_owner
    assert _log_rows(env)[0]['delivery_attempt_id'] == old_owner
    assert not session.get('_notify_session_sent')
    assert session['status'] == 'running'


def test_new_delegated_row_is_removed_only_after_proven_refusal(env):
    ar = env['ar']
    session = {'project_id': 'proj1', 'session_id': 'new-delegated'}
    tx = transaction.reserve(session, lambda: ar._log_agent_dispatch_pending(session, strict=True))
    assert tx.prior_row is None
    with pytest.raises(OSError):
        tx.launch(lambda: (_ for _ in ()).throw(OSError('no child')))
    assert tx.restored
    assert not _log_rows(env)
    assert '_delivery_attempt_id' not in session


def test_fresh_no_row_launch_does_not_add_a_prelaunch_history_write(env, monkeypatch):
    ar = env['ar']
    session = {'project_id': 'proj1', 'session_id': 'fresh'}
    saves = []
    monkeypatch.setattr(ar, '_save_agent_log', lambda *args: saves.append(True))
    tx = transaction.reserve(session, lambda: None)
    assert tx.launch(lambda: 'child') == 'child'
    assert saves == []
    assert '_delivery_attempt_id' not in session


def test_late_destination_cannot_turn_a_fresh_launch_into_unowned_reuse(env):
    ar = env['ar']
    session = {'project_id': 'proj1', 'session_id': 'late-destination'}
    tx = transaction.reserve(session, lambda: None)
    ar._update_agent_log('proj1', lambda rows: rows.append({
        'session_id': session['session_id'], 'delivery_attempt_id': 'successor',
        'status': 'completed', 'summary': 'successor answer'}))
    with pytest.raises(transaction.ReservationLost):
        tx.launch(lambda: pytest.fail('unowned spawn'))
    assert _log_rows(env)[0]['summary'] == 'successor answer'


def test_stale_recovery_cannot_take_a_successor_row(env):
    ar = env['ar']
    session = _completed(env)
    ar._update_agent_log('proj1', lambda rows: rows[0].update(
        delivery_attempt_id='successor', summary='successor answer'))
    with pytest.raises(transaction.ReservationLost):
        transaction.claim(session)
    assert _log_rows(env)[0]['summary'] == 'successor answer'
    assert _log_rows(env)[0]['delivery_attempt_id'] == 'successor'


@pytest.mark.parametrize('ambiguous', [False, True])
def test_queue_retains_input_only_for_proven_prechild_refusal(env, monkeypatch, ambiguous):
    ar = env['ar']
    session = _completed(env)
    session['pending_followups'] = ['second message']
    prior = _log_rows(env)

    def refusal(*args, **kwargs):
        raise OSError('launch failed')

    spawn = transaction.spawn_boundary(refusal) if ambiguous else refusal
    monkeypatch.setattr(ar, 'spawn_engine_proc', spawn)
    ar._auto_dispatch_followup_locked(session, 'first message', env['project'],
                                     env['project']['project_path'], ['fake'], None)
    assert session['pending_followups'] == (['second message'] if ambiguous
                                           else ['first message', 'second message'])
    if ambiguous:
        assert session['_launch_uncertain']
        assert _log_rows(env)[0]['launch_outcome'] == 'unknown'
    else:
        assert _log_rows(env) == prior


@pytest.mark.parametrize('failure', ['popen', 'popen-ambiguous', 'delivery', 'shim'])
def test_factory_marks_only_proven_prechild_oserrors_safe(env, monkeypatch, failure):
    from mc import agent_shim_client as client
    error = FileNotFoundError(failure) if failure == 'popen' else OSError(failure)

    def refused(*args, **kwargs):
        raise error

    monkeypatch.setattr(agent_spawn.subprocess, 'Popen', refused if failure.startswith('popen')
                        else lambda *args, **kwargs: SimpleNamespace())
    config = {'agent_survive_restart_enabled': False}
    deliver = refused if failure == 'delivery' else None
    if failure == 'shim':
        # Force the real shim launch branch; no provider or shim is started.
        monkeypatch.setattr(client, 'enabled', lambda cfg: True)
        monkeypatch.setattr(client, 'launch', refused)
        monkeypatch.setattr(client, 'inspect', lambda *args: None)
        from mc import agent_shim_adopt
        monkeypatch.setattr(agent_shim_adopt, 'ensure_checkpointer', lambda cfg: None)
    with pytest.raises(OSError) as caught:
        agent_spawn.spawn_engine_proc(['fake'], session_id='factory', engine='claude',
                                      root=env['tmp_path'] / 'shim', cfg=config, deliver=deliver)
    assert caught.value is error
    assert bool(getattr(error, '_mc_launch_ambiguous', False)) == (failure != 'popen')


def _inventory(source):
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    counts, popen = Counter(), Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name == 'spawn_engine_proc':
                    assert node.module == 'mc.agent_spawn'
                    assert imported.asname in (None, 'spawn_engine_proc'), 'factory import alias'
        if isinstance(node, ast.Attribute) and node.attr == 'spawn_engine_proc':
            raise AssertionError(f'module-qualified factory bypass at {node.lineno}')
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == 'spawn_engine_proc':
            parent = parents[node]
            assert isinstance(parent, ast.Call), f'factory alias/reference at {node.lineno}'
            assert isinstance(parent.func, ast.Attribute) and parent.func.attr == 'launch'
            assert isinstance(parent.func.value, ast.Name) and parent.func.value.id == '_launch_transaction'
            assert len(parent.args) >= 2 and parent.args[1] is node
            scope = parent
            while not isinstance(scope, ast.FunctionDef):
                scope = parents[scope]
            counts[scope.name] += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'Popen':
            scope = node
            while not isinstance(scope, ast.FunctionDef):
                scope = parents[scope]
            popen[scope.name] += 1
    assert counts == Counter({'_auto_recover_failed_resume': 2, '_revive_from_agent_log': 2,
                              '_auto_dispatch_followup_locked': 1, '_dispatch_agent_internal': 2,
                              '_do_respawn_b': 1, '_start_followup': 1, '_do_respawn': 2})
    # Explicit exclusions: three platform terminal launchers and remote auth.
    assert popen == Counter({'_launch_terminal_for_binary': 3, 'agent_auth_login_remote': 1})


def test_all_eleven_route_spawns_use_the_transaction():
    root = Path(__file__).resolve().parents[1]
    _inventory((root / 'mc/blueprints/agent_routes.py').read_text(encoding='utf-8'))


@pytest.mark.parametrize('bypass', [
    'def bypass():\n    return spawn_engine_proc([])\n',
    'from mc.agent_spawn import spawn_engine_proc as naked\ndef bypass():\n    return naked([])\n',
    'import mc.agent_spawn as factory\ndef bypass():\n    return factory.spawn_engine_proc([])\n',
])
def test_inventory_guard_rejects_a_direct_spawn_bypass(bypass):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'mc/blueprints/agent_routes.py').read_text(encoding='utf-8')
    source += '\n' + bypass
    with pytest.raises(AssertionError):
        _inventory(source)
