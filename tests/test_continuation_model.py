"""Continuation must not reselect a model from changed project defaults."""
import inspect
from threading import RLock
from types import SimpleNamespace
from flask import Flask
import json

import pytest

from tests.test_revive_non_claude_character_ref import ar, PROJECT


@pytest.mark.parametrize('provider', ['codex', 'qwen'])
@pytest.mark.parametrize('field', ['pinned_model', 'agent_model', 'model'])
def test_cold_revive_keeps_recorded_model(ar, monkeypatch, provider, field):
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [{
        'session_id': 's1', 'provider': provider,
        'provider_session_id': 'native1', field: 'original-model',
    }])
    captured = {}
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *args, **kw: captured.update(kw) or 's1')
    assert ar._revive_non_claude_from_agent_log('p1', 's1', 'go', PROJECT) == 's1'
    assert captured['model_override'] == 'original-model'
    assert captured['provider_override'] == provider
    assert captured['resume_id'] == 'native1'


def test_model_lookup_prefers_live_choice_and_is_provider_scoped(ar, monkeypatch):
    ar.agent_sessions['s1'] = {
        'project_id': 'p1', 'provider': 'codex',
        'provider_session_id': 'native1', 'pinned_model': 'sol',
    }
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [{
        'provider': 'codex', 'provider_session_id': 'native1', 'model': 'astra',
    }])
    assert ar._prior_conversation_model('p1', 'native1', 'codex') == 'sol'
    assert ar._prior_conversation_model('p1', 'native1', 'qwen') == ''


def test_unknown_native_model_does_not_apply_current_defaults(ar, monkeypatch):
    captured = {}
    class Runtime:
        def build_command(self, **kwargs):
            captured.update(kwargs)
            return ['claude']
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda _: Runtime())
    monkeypatch.setattr(ar, '_resolve_project_mcp_config', lambda _: '')
    ar._build_claude_flags({'agent_model': 'new-default'}, model_override='')
    assert captured['model'] == ''
    ar._build_claude_flags({'agent_model': 'new-default'})
    assert captured['model'] == 'new-default'


def test_explicit_resume_model_is_not_discarded(ar):
    source = inspect.getsource(ar.agent_dispatch)
    assert "model_override = (data.get('model') or '').strip()" in source
    line = next(line for line in source.splitlines() if 'model_override =' in line)
    assert 'if not resume_id' not in line


def test_native_resume_bypasses_new_chat_model_resolution(ar):
    source = inspect.getsource(ar._dispatch_via_runtime)
    assert 'model_override if resume_id else _resolve_runtime_model' in source


def test_log_persists_model_before_completion(ar):
    source = inspect.getsource(ar._log_agent_dispatch_pending)
    assert "'agent_model':" in source
    assert "'pinned_model':" in source


@pytest.mark.parametrize('provider', ['claude', 'codex', 'qwen'])
def test_prior_provider_survives_changed_project_default(ar, monkeypatch, provider):
    key = 'claude_session_id' if provider == 'claude' else 'provider_session_id'
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [
        {'provider': provider, key: 'native1'},
    ])
    assert ar._prior_conversation_provider('p1', 'native1') == provider
    with pytest.raises(ValueError, match='another provider'):
        ar._prior_conversation_provider('p1', 'native1', 'different')


def test_prior_provider_rejects_ambiguous_native_id(ar, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [
        {'provider': p, 'provider_session_id': 'native1'} for p in ['codex', 'qwen']
    ])
    with pytest.raises(ValueError, match='ambiguous'):
        ar._prior_conversation_provider('p1', 'native1')
    assert ar._prior_conversation_provider('p1', 'native1', 'qwen') == 'qwen'


def test_latest_live_model_wins_and_other_project_is_excluded(ar, monkeypatch):
    for sid, project, date, model in [
        ('older', 'p1', '2026-09-01', 'sol'),
        ('newer', 'p1', '2026-09-02', 'astra'),
        ('foreign', 'p2', '2026-09-03', 'foreign'),
    ]:
        ar.agent_sessions[sid] = dict(project_id=project, provider='codex',
                                     provider_session_id='native1', started_at=date,
                                     model=model)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [])
    assert ar._prior_conversation_model('p1', 'native1', 'codex') == 'astra'


@pytest.mark.parametrize('model', ['claude-opus-5', ''])
def test_set_model_choice_is_durable_before_success(ar, monkeypatch, model):
    session = dict(project_id='p1', session_id='s1', provider='claude',
                   claude_session_id='native1', model='sonnet', status='idle')
    ar.agent_sessions['s1'] = session
    saved = {}
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=RLock()))
    monkeypatch.setattr(ar, '_agent_log_is_readable', lambda _: True)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [])
    monkeypatch.setattr(ar, '_save_agent_log', lambda pid, rows: saved.update(pid=pid, rows=rows))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)
    with Flask(__name__).test_request_context(json={'model': model}):
        response = ar.agent_set_model('p1', 's1')
    assert response.get_json()['ok']
    row = saved['rows'][0]
    assert row['pinned_model'] == model
    assert row['model_auto_requested'] is (not bool(model))
    assert row['claude_session_id'] == 'native1'
    assert session['pinned_model'] == model


def test_set_model_storage_failure_is_not_acknowledged(ar, monkeypatch):
    session = dict(project_id='p1', provider='claude', pinned_model='sonnet')
    ar.agent_sessions['s1'] = session
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=RLock()))
    monkeypatch.setattr(ar, '_agent_log_is_readable', lambda _: False)
    with Flask(__name__).test_request_context(json={'model': 'opus'}):
        _, status = ar.agent_set_model('p1', 's1')
    assert status == 503
    assert session['pinned_model'] == 'sonnet'


def test_model_switch_cannot_mutate_another_project(ar, monkeypatch):
    ar.agent_sessions['s1'] = dict(project_id='p2', provider='claude')
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=RLock()))
    with Flask(__name__).test_request_context(json={'model': 'opus'}):
        _, status = ar.agent_set_model('p1', 's1')
    assert status == 404
    assert 'pinned_model' not in ar.agent_sessions['s1']


def test_explicit_clear_does_not_resurrect_old_pin(ar, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [
        dict(provider='claude', claude_session_id='native1',
             model='opus', model_auto_requested=True),
        dict(provider='claude', claude_session_id='native1', pinned_model='opus'),
    ])
    assert ar._prior_conversation_model('p1', 'native1', 'claude') == ''
    assert ar._continuation_model(dict(model='opus', model_auto_requested=True),
                                  {'agent_model': 'sonnet'}) == 'sonnet'


def test_manual_pending_preserves_engine_and_native_id(ar, monkeypatch):
    saved = []
    monkeypatch.setattr(ar, '_agent_log_is_readable', lambda _: True)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [])
    monkeypatch.setattr(ar, '_save_agent_log', lambda _, rows: saved.extend(rows))
    ar._log_agent_dispatch_pending(dict(project_id='p1', session_id='s1',
                                       trigger_type='manual', provider='codex',
                                       provider_session_id='native1', agent_model='sol'))
    assert saved[0]['provider_session_id'] == 'native1'
    assert saved[0]['agent_model'] == 'sol'
    assert saved[0]['provider'] == 'codex'


@pytest.mark.parametrize('resume,override,expected', [
    ('native1', '', ''), ('native1', 'sol', 'sol'), ('', '', 'new-default'),
])
def test_runtime_dispatch_passes_resume_model_without_reselection(ar, monkeypatch,
                                                                resume, override, expected):
    captured = {}
    runtime = SimpleNamespace(build_command=lambda **kw: ['fake'],
                              dispatch=lambda **kw: captured.update(kw))
    manager = SimpleNamespace(lock=RLock(), ensure_guardian=lambda: None, session_ids=set())
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda _: runtime)
    monkeypatch.setattr(ar, 'get_manager', lambda _: manager)
    monkeypatch.setattr(ar, '_resolve_runtime_model', lambda *a: 'new-default')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **kw: '')
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)
    ar._dispatch_via_runtime(PROJECT, 'go', provider_name='codex',
                             resume_id=resume, model_override=override)
    assert captured['model'] == expected
    assert captured['resume_id'] == resume


def test_native_reader_persists_id_before_process_exit(ar, monkeypatch):
    from tests.test_mode_a_tool_call_parity import _FakeProc
    from mc.agent_runtime import CodexRuntime, SessionHandle, _mode_a_reader
    captured = [dict(session_id='s1', status='in_progress', usage={'input': 12})]
    monkeypatch.setattr(ar, '_update_agent_log', lambda _, mutate: mutate(captured))
    proc = _FakeProc([json.dumps({'type': 'thread.started', 'thread_id': 'native1'})])
    session = dict(project_id='p1', session_id='s1', provider='codex',
                   log_lines=[], proc=proc, provider_session_id='')
    handle = SessionHandle(mc_session_id='s1', provider='codex', mode='A',
                           project_path='/p', project_id='p1', session_dict=session,
                           meta={'callbacks': {'on_init': ar._RUNTIME_CALLBACKS['on_init']}})
    _mode_a_reader(proc, handle, CodexRuntime())
    assert captured[0]['provider_session_id'] == 'native1'
    assert captured[0]['usage'] == {'input': 12}


def test_manual_claude_init_backfills_native_id(ar, monkeypatch):
    rows = [dict(session_id='s1', status='in_progress')]
    saved = []
    monkeypatch.setattr(ar, '_live_owner_of_csid', lambda *a, **kw: None)
    monkeypatch.setattr(ar, '_agent_log_is_readable', lambda _: True)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: rows)
    monkeypatch.setattr(ar, '_save_agent_log', lambda _, records: saved.extend(records))
    ar._note_claude_sid(dict(project_id='p1', session_id='s1', trigger_type='manual'), 'native1')
    assert saved[0]['claude_session_id'] == 'native1'


def test_incognito_pending_never_writes_durable_log(ar, monkeypatch):
    def unexpected(*args):
        pytest.fail('incognito conversation must not write a durable row')
    monkeypatch.setattr(ar, '_save_agent_log', unexpected)
    ar._log_agent_dispatch_pending(dict(project_id='p1', session_id='s1', incognito=True))


def test_late_claude_init_preserves_completed_row(ar, monkeypatch):
    rows = [dict(session_id='s1', status='completed', summary='finished',
                 usage={'input': 42}, scribed=True)]
    monkeypatch.setattr(ar, '_live_owner_of_csid', lambda *a, **kw: None)
    monkeypatch.setattr(ar, '_update_agent_log', lambda _, mutate: mutate(rows))
    ar._note_claude_sid(dict(project_id='p1', session_id='s1', trigger_type='manual'), 'native1')
    assert rows == [dict(session_id='s1', status='completed', summary='finished',
                         usage={'input': 42}, scribed=True, claude_session_id='native1')]


def test_parallel_pending_transactions_cannot_lose_another_session(ar, monkeypatch):
    from threading import Event, Thread, get_ident
    from copy import deepcopy
    first_loaded, second_attempted = Event(), Event()
    storage = []
    real_lock = RLock()
    first_thread = []

    class InstrumentedLock:
        def __enter__(self):
            if first_thread and get_ident() != first_thread[0]:
                second_attempted.set()
            real_lock.acquire()
        def __exit__(self, *args):
            real_lock.release()

    monkeypatch.setitem(ar._agent_log_mutation_locks, 'p1', InstrumentedLock())
    monkeypatch.setattr(ar, '_agent_log_is_readable', lambda _: True)
    def load(_):
        snapshot = deepcopy(storage)
        if not first_thread:
            first_thread.append(get_ident())
            first_loaded.set()
            assert second_attempted.wait(2)
        return snapshot
    monkeypatch.setattr(ar, '_load_agent_log', load)
    monkeypatch.setattr(ar, '_save_agent_log', lambda _, rows: storage.__setitem__(slice(None), deepcopy(rows)))
    def write(sid):
        ar._log_agent_dispatch_pending(dict(project_id='p1', session_id=sid, provider='codex'))
    first = Thread(target=write, args=('s1',))
    second = Thread(target=write, args=('s2',))
    first.start()
    assert first_loaded.wait(2)
    second.start()
    first.join(3)
    second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert {row['session_id'] for row in storage} == {'s1', 's2'}


def test_runtime_start_failure_updates_only_pending_state(ar, monkeypatch):
    rows = [dict(session_id='s1', status='in_progress'),
            dict(session_id='s2', status='completed')]
    monkeypatch.setattr(ar, '_update_agent_log', lambda _, mutate: mutate(rows))
    ar._persist_runtime_start_failure(dict(project_id='p1', session_id='s1', log_lines=['missing CLI']))
    assert rows[0]['status'] == 'error'
    assert rows[0]['summary'] == 'missing CLI'
    ar._persist_runtime_start_failure(dict(project_id='p1', session_id='s2', log_lines=['late failure']))
    assert rows[1]['status'] == 'completed'
