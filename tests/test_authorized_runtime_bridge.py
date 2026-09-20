"""Real-store composition tests for the optional runtime lifecycle bridge."""
import json
from types import SimpleNamespace

import pytest

from mc.conversation_store import ConversationStore, LaunchUncertain
from mc.execution_lifecycle import AttemptStatus
from mc.runtime_attempt_owner import (
    AuthorizedRuntimeLifecycleBridge, RuntimeAttemptOwner,
)
from tests.test_runtime_lifecycle_bridge import env  # noqa: F401


def _factory(tmp_path, captured, authorize=lambda: None):
    store_path = tmp_path / 'lifecycle.sqlite'

    def factory(facts):
        engine = {
            'provider': facts.provider,
            'model': facts.model,
            'effort': facts.effort,
            'resume_id': facts.resume_id,
            'settings': dict(facts.provenance),
        }
        owner = RuntimeAttemptOwner(
            store=ConversationStore(store_path), project_id=facts.project_id,
            conversation_id=facts.mc_session_id, owner_id='route-owner')
        bridge = AuthorizedRuntimeLifecycleBridge(
            owner=owner, facts=facts, authorize=authorize,
            launch_facts={
                'provider': facts.provider,
                'project_path': facts.project_path,
                'mc_session_id': facts.mc_session_id,
                'requested_engine_json': json.dumps(
                    engine, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':'), allow_nan=False),
                'incognito': False,
                'source_id': f'{facts.mc_session_id}:rollout',
                'source_incarnation': 'one',
                'format_version': 'codex-rollout-jsonl-0.153',
            })
        captured.append(bridge)
        return bridge

    return store_path, factory


def test_route_store_bridge_roundtrip_and_restart_reconstruction(env):
    ar, runtime, sessions, tmp = env
    bridges = []
    authorization_checks = []
    store_path, factory = _factory(
        tmp, bridges, authorize=lambda: authorization_checks.append('checked'))
    sid = ar._dispatch_via_runtime(
        {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        model_override='', effort_override='', resume_id='prior-native',
        lifecycle_bridge_factory=factory)
    session = sessions[sid]
    init = SimpleNamespace(payload={'session_id': 'native-1'})
    runtime.callbacks['on_init'](init, session)
    runtime.callbacks['on_process_exit'](SimpleNamespace(payload={'rc': 0}), session)

    bridge = bridges[0]
    reopened = RuntimeAttemptOwner(
        store=ConversationStore(store_path), project_id='p',
        conversation_id=sid, owner_id='route-owner')
    recovered = reopened.reconstruct(attempt_id=bridge.prepared.attempt.attempt_id)
    assert recovered.status is AttemptStatus.COMPLETED
    assert recovered.native_handle == 'native-1'
    assert recovered.engine['model'] == ''
    assert recovered.engine['effort'] == ''
    assert recovered.engine['resume_id'] == 'prior-native'
    assert recovered.launch_facts['transcript_path'] == str(tmp / 'rollout.jsonl')
    assert recovered.request['user_message'] == {'text': 'task'}
    assert authorization_checks == ['checked', 'checked']


def test_route_store_bridge_spawn_failure_is_uncertain_and_not_retried(env):
    ar, runtime, _sessions, tmp = env
    bridges = []
    store_path, factory = _factory(tmp, bridges)
    calls = 0

    def fail_dispatch(**kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError('spawn failed')

    runtime.dispatch = fail_dispatch
    with pytest.raises(LaunchUncertain):
        ar._dispatch_via_runtime(
            {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
            lifecycle_bridge_factory=factory)
    assert calls == 1
    state = ConversationStore(store_path).lifecycle_state('p', bridges[0].facts.mc_session_id)
    assert state.attempts[0].status is AttemptStatus.UNCERTAIN


@pytest.mark.parametrize('threaded', [False, True])
def test_callbacks_during_dispatch_wait_for_launch_commit(env, threaded):
    """Mode A starts its reader before dispatch returns its handle."""
    from threading import Thread
    ar, runtime, sessions, tmp = env
    bridges = []
    store_path, factory = _factory(tmp, bridges)
    dispatch = runtime.dispatch

    def early_dispatch(**kwargs):
        handle = dispatch(**kwargs)
        def emit():
            runtime.callbacks['on_init'](
                SimpleNamespace(payload={'session_id': 'early-native'}), kwargs['session_dict'])
            runtime.callbacks['on_process_exit'](
                SimpleNamespace(payload={'rc': 0}), kwargs['session_dict'])
        if threaded:
            reader = Thread(target=emit)
            reader.start()
            reader.join(timeout=2)
            assert not reader.is_alive(), 'callback must not wait on launch transaction'
        else:
            emit()
        return handle

    runtime.dispatch = early_dispatch
    sid = ar._dispatch_via_runtime(
        {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        lifecycle_bridge_factory=factory)
    state = ConversationStore(store_path).lifecycle_state('p', sid)
    assert state.attempts[0].status is AttemptStatus.COMPLETED
    assert state.attempts[0].native_handle == 'early-native'
    assert not sessions[sid].get('_lifecycle_errors')


def test_terminal_without_native_binding_remains_running_and_visible(env):
    ar, runtime, sessions, tmp = env
    bridges = []
    store_path, factory = _factory(tmp, bridges)
    sid = ar._dispatch_via_runtime(
        {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        lifecycle_bridge_factory=factory)
    session = sessions[sid]
    runtime.callbacks['on_process_exit'](SimpleNamespace(payload={'rc': 0}), session)
    state = ConversationStore(store_path).lifecycle_state('p', sid)
    assert state.attempts[0].status is AttemptStatus.RUNNING
    assert 'terminal result arrived before native binding' in session['_lifecycle_errors']


def test_incognito_bypasses_store_but_still_spawns_once(env):
    ar, runtime, sessions, tmp = env
    bridges = []
    store_path, factory = _factory(tmp, bridges)
    sid = ar._dispatch_via_runtime(
        {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        incognito=True, lifecycle_bridge_factory=factory)
    assert sid in sessions and len(runtime.calls) == 1
    assert not store_path.exists()


@pytest.mark.parametrize('revocation', ['delete', 'takeover'])
def test_init_after_privacy_or_owner_revocation_fails_closed(env, revocation):
    ar, runtime, sessions, tmp = env
    bridges = []
    store_path, factory = _factory(tmp, bridges)
    sid = ar._dispatch_via_runtime(
        {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        lifecycle_bridge_factory=factory)
    session = sessions[sid]
    store = ConversationStore(store_path)
    state = store.lifecycle_state('p', sid)
    if revocation == 'delete':
        store.set_lifecycle_deleted(
            'p', sid, True, expected_revision=state.revision,
            event_id='privacy-delete')
    else:
        store.claim_owner(
            'p', sid, owner_id='replacement', expected_revision=state.revision,
            event_id='owner-takeover')
    runtime.callbacks['on_init'](
        SimpleNamespace(payload={'session_id': 'native-after-revoke'}), session)
    assert session['_lifecycle_errors']
    state = store.lifecycle_state('p', sid)
    assert state.attempts[0].native_handle is None
