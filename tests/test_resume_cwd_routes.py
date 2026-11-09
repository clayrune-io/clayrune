"""MC-1167: real routes refuse missing transcript cwd without a provider spawn."""
import json
import threading

import pytest

from mc import agent_runtime, resume_cwd
from test_one_process_per_conversation import env, _Proc  # noqa: F401
from resume_cwd_fixtures import make_tree, remove_tree


CSID = '2033f331-a034-4efe-a95c-d1fd89fabf0a'
OWNER = 'ea7620565ce1'
REAL_THREAD = threading.Thread


@pytest.fixture
def setup(env, tmp_path, monkeypatch):
    ar = env['ar']
    home = tmp_path / 'claude-projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', home)
    monkeypatch.setattr(agent_runtime.ClaudeRuntime, '_transform_dirs',
                        classmethod(lambda cls: []))
    runtime = agent_runtime.ClaudeRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtime)
    tree = env['pp'] / '.clayrune' / 'agents' / OWNER
    make_tree(env['pp'], tree, monkeypatch)
    transcript = home / runtime._encoded_dir_candidates(str(tree))[-1] / f'{CSID}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'user', 'cwd': str(tree),
                                      'sessionId': CSID}) + '\n', encoding='utf-8')
    logfile = tmp_path / 'agent_log.json'
    logfile.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(ar, '_agent_log_path', lambda pid: logfile)
    monkeypatch.setattr(ar, '_load_agent_log',
                        lambda pid: json.loads(logfile.read_text(encoding='utf-8')))
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_prior_conversation_model', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_maybe_isolate_worktree',
                        lambda *a, **k: pytest.fail('must not create a replacement worktree'))
    attempts = []

    def spawn(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError('provider spawn forbidden')

    monkeypatch.setattr(ar, 'spawn_engine_proc', spawn)
    return {**env, 'tree': tree, 'transcript': transcript, 'logfile': logfile,
            'attempts': attempts}


@pytest.mark.parametrize('route,logged', [
    ('send', True), ('send', False), ('followup', True), ('dispatch', False)])
def test_missing_tree_refused_and_request_retained(setup, route, logged):
    tree, transcript = setup['tree'], setup['transcript']
    original = transcript.read_bytes()
    remove_tree(tree)
    if logged:
        setup['logfile'].write_text(json.dumps([{
            'session_id': OWNER, 'claude_session_id': CSID,
            'provider': 'claude', 'task': 'original task', 'status': 'completed'}]),
            encoding='utf-8')
    payload = ({'task': 'keep this exact request', 'resume_conversation_id': CSID}
               if route == 'dispatch' else
               {'message': 'keep this exact request', 'session_id': OWNER if logged else CSID})
    response = setup['client'].post(f'/api/project/p1/agent/{route}', json=payload)
    assert response.status_code == 409, response.get_data(as_text=True)
    body = response.get_json()
    assert body['code'] == 'resume_cwd_missing'
    assert 'removed' in body['error'] and 'restore' in body['error']
    assert 'Start a new chat' in body['error']
    assert body['cwd'] == str(tree)
    assert body['request_message'] == 'keep this exact request'
    assert body['request_saved'] is True
    rows = json.loads(setup['logfile'].read_text(encoding='utf-8'))
    assert rows[0]['resume_refusals'][-1]['message'] == 'keep this exact request'
    if logged:
        assert rows[0]['task'] == 'original task'
        assert rows[0]['status'] == 'completed'
        assert rows[0]['claude_session_id'] == CSID
    assert setup['attempts'] == []
    assert setup['sessions'] == {}
    assert transcript.read_bytes() == original
    assert not tree.exists()


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('method', ['revive', 'dispatch'])
def test_live_tree_revive_keeps_isolation_without_claiming_cleanup(setup, monkeypatch, streaming, method):
    ar = setup['ar']
    setup['project']['use_streaming_agent'] = streaming
    setup['logfile'].write_text(json.dumps([{
        'session_id': OWNER, 'claude_session_id': CSID, 'provider': 'claude'}]),
        encoding='utf-8')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))
    monkeypatch.setattr(ar, '_sysprompt_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'unused-claude')
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_router_stat', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar.get_manager('p1'), 'ensure_guardian', lambda: None)
    inert_thread = type('InertThread', (), {
        '__init__': lambda *a, **k: None, 'start': lambda *a: None,
        'is_alive': lambda *a: False})

    def thread(*a, **k):
        # Git readers and the acknowledged fake-stdin writer must run.
        # Provider readers and guardian work stay inert.
        if (getattr(k.get('target'), '__module__', '') == 'subprocess'
                or k.get('name', '').startswith('revive-stdin-')):
            return REAL_THREAD(*a, **k)
        return inert_thread(*a, **k)

    monkeypatch.setattr(ar.threading, 'Thread', thread)
    spawned = []

    def spawn(*a, **k):
        spawned.append(k)
        return _Proc()

    monkeypatch.setattr(ar, 'spawn_engine_proc', spawn)
    if method == 'revive':
        session = ar._revive_from_agent_log('p1', OWNER, 'continue', setup['project'])
    else:
        sid = ar._dispatch_agent_internal(
            'p1', 'continue', resume_id=CSID,
            agent_cwd_override=str(setup['pp']), isolated_override=False)
        session = setup['sessions'][sid]
    assert session is not None
    assert spawned[0]['cwd'] == str(setup['tree'])
    assert session['_agent_cwd'] == str(setup['tree'])
    assert session['_worktree_isolated'] is True
    assert session['_worktree_borrowed'] is (method == 'dispatch')
    if method == 'dispatch':
        monkeypatch.setattr(ar._agent_worktree, 'merge_back',
                            lambda *a, **k: pytest.fail('borrowed worktree must not be merged'))
        ar._worktree_merge_back_on_end(session)


def test_missing_tree_wins_over_auto_fresh_and_cwd_override(setup, monkeypatch):
    remove_tree(setup['tree'])
    monkeypatch.setattr(setup['ar'], '_auto_fresh_trigger',
                        lambda *a, **k: pytest.fail('refuse before rollover'))
    with pytest.raises(resume_cwd.ResumeCwdMissing):
        setup['ar']._dispatch_agent_internal(
            'p1', 'keep me', resume_id=CSID,
            agent_cwd_override=str(setup['pp']), isolated_override=False)
    assert setup['attempts'] == []


def test_refusal_storage_failure_is_explicit_and_keeps_response_copy(setup, monkeypatch):
    remove_tree(setup['tree'])
    monkeypatch.setattr(setup['ar'], '_update_agent_log',
                        lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    response = setup['client'].post('/api/project/p1/agent/send', json={
        'session_id': CSID, 'message': 'retain me'})
    assert response.status_code == 409
    assert response.get_json()['request_saved'] is False
    assert response.get_json()['request_message'] == 'retain me'
    assert setup['attempts'] == []


def test_git_validation_error_is_a_refusal_instead_of_missing_provenance(setup, monkeypatch):
    def broken(*args):
        raise OSError('Git check unavailable')

    monkeypatch.setattr(setup['ar']._isolated_launch, '_usable', broken)
    response = setup['client'].post('/api/project/p1/agent/dispatch', json={
        'task': 'keep this request', 'resume_conversation_id': CSID})
    assert response.status_code == 409
    assert response.get_json()['code'] == 'resume_cwd_missing'
    assert 'OSError: Git check unavailable' in response.get_json()['error']
    assert response.get_json()['request_message'] == 'keep this request'
    assert setup['attempts'] == []


def test_incognito_refusal_only_returns_request_to_caller(setup):
    remove_tree(setup['tree'])
    response = setup['client'].post('/api/project/p1/agent/send', json={
        'session_id': CSID, 'message': 'private request', 'incognito': True})
    assert response.status_code == 409
    assert response.get_json()['request_message'] == 'private request'
    assert response.get_json()['request_saved'] is False
    assert setup['logfile'].read_text(encoding='utf-8') == '[]'
    assert setup['attempts'] == []


def test_refused_old_chat_is_kept_inside_log_retention(setup, monkeypatch):
    remove_tree(setup['tree'])
    setup['logfile'].write_text(json.dumps([
        {'session_id': 'newer', 'task': 'unrelated'},
        {'session_id': OWNER, 'claude_session_id': CSID, 'task': 'old chat'}]),
        encoding='utf-8')
    monkeypatch.setitem(setup['ar'].state.CONFIG, 'agent_log_max_entries', 1)
    response = setup['client'].post('/api/project/p1/agent/send', json={
        'session_id': OWNER, 'message': 'retained despite old row'})
    assert response.status_code == 409
    assert response.get_json()['request_saved'] is True
    rows = json.loads(setup['logfile'].read_text(encoding='utf-8'))
    assert rows[0]['session_id'] == OWNER
    assert rows[0]['resume_refusals'][0]['message'] == 'retained despite old row'
    assert setup['attempts'] == []
