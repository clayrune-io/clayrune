"""Cold-review regressions with real provider history and lifecycle stores."""
import json

import pytest

from test_agent_mail_wake import setup  # noqa: F401
from test_runtime_completion_log import env  # noqa: F401
from mc import agent_runtime
from mc.agent_mail_target import MailHeld, snapshot, validate
from mc.runtime_lifecycle_service import RuntimeLifecycleService


def codex_history(setup, monkeypatch, *, isolated=True):
    worktree = (setup.tmp_path / '.clayrune' / 'agents' / 'mail-session'
                if isolated else setup.tmp_path)
    worktree.mkdir(parents=True, exist_ok=True)
    codex_home = setup.tmp_path / 'codex-sessions'
    rollout = codex_home / '2026' / '11' / '09' / 'rollout-local-native-thread.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {
        'cwd': str(worktree), 'id': 'native-thread'}}) + '\n')
    monkeypatch.setattr(agent_runtime, '_CODEX_HOME', codex_home)
    runtime = agent_runtime.CodexRuntime()
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda *a: runtime)
    setup.session.update(provider='codex', provider_session_id='native-thread',
                         _agent_cwd=str(worktree), _worktree_isolated=isolated)
    assert runtime.transcript_path(str(worktree), 'native-thread') == rollout
    assert runtime.transcript_path(setup.project['project_path'], 'native-thread') == (None if isolated else rollout)
    return runtime


def accepted_event(setup, eid, pid, sid, *, uncertain=False):
    token = f'thread-{eid}'
    setup.store.bind_thread('installation', token, pid, sid)
    setup.store.insert_event(event_id=eid, registration_id='installation', thread_token=token,
                             message_id=f'<{eid}@example.invalid>', body='Friday works.')
    claim = setup.store.claim_for_laundering()
    setup.store.complete_laundering(eid, claim['fence_token'], {'reply_text': 'Friday works.'})
    claim = setup.store.claim_for_submission()
    assert claim['event_id'] == eid
    with setup.store._tx() as db:
        db.execute('INSERT INTO mail_turn_receipts VALUES(?,?,?,?,?,?,?,1,1,0)',
                   (eid, f'attempt-{eid}', pid, sid, 'codex', 'native-thread', 1))
    if uncertain:
        setup.store.mark_uncertain(eid, claim['fence_token'], 'ack_timeout')
    else:
        setup.store.mark_submitted(eid, claim['fence_token'], {'turn_id': f'attempt-{eid}'})


def test_codex_isolated_history_uses_real_recorded_cwd(setup, monkeypatch):
    codex_history(setup, monkeypatch)
    bound = snapshot(setup.project, setup.session)
    validate(setup.project, setup.session, bound)
    setup.store.bind_thread('installation', 'real-codex', 'proj1', 'mail-session')
    setup.wake.bind('real-codex')


def test_real_gemini_history_is_explicitly_unsupported(setup, monkeypatch):
    runtime = agent_runtime.GeminiRuntime()
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda *a: runtime)
    setup.session.update(provider='gemini', provider_session_id='gemini-native')
    assert runtime.transcript_path(setup.project['project_path'], 'gemini-native') is None
    setup.store.bind_thread('installation', 'gemini-thread', 'proj1', 'mail-session')
    with pytest.raises(MailHeld, match='^gemini_mail_wake_unsupported$'):
        setup.wake.bind('gemini-thread')
    # A snapshot created by an older build must refuse with the same reason.
    bound = dict(snapshot(setup.project, dict(setup.session, provider='codex')),
                 provider='gemini', native_id='gemini-native')
    with pytest.raises(MailHeld, match='^gemini_mail_wake_unsupported$'):
        validate(setup.project, setup.session, bound)
    assert setup.session['proc'].stdin.getvalue() == ''


@pytest.mark.parametrize('prior', ['submitted', 'revoked_session', 'uncertain'])
def test_real_generation_change_is_terminal_and_other_project_reconciles(setup, monkeypatch, prior):
    codex_history(setup, monkeypatch, isolated=False)
    setup.store.bind_thread('installation', 'real-codex', 'proj1', 'mail-session')
    setup.wake.bind('real-codex')  # Real runtime history proof, no locator double.
    accepted_event(setup, 'stale', 'proj1', 'mail-session', uncertain=prior == 'uncertain')
    if prior == 'revoked_session':
        setup.store.revoke_session('proj1', 'mail-session')
    accepted_event(setup, 'healthy', 'proj2', 'healthy-session')
    service = RuntimeLifecycleService(
        db_path=setup.tmp_path / 'lifecycle.sqlite', enabled=True, owner_id='review',
        authorize=lambda facts: None, source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    service.revoke_project('proj1')
    assert service.recreate_project('proj1') == 3
    monkeypatch.setattr(setup.ar, '_assert_runtime_project_generation', service.assert_project_generation)
    monkeypatch.setattr(setup.ar, 'load_project', lambda pid: dict(setup.project, id=pid))
    reads = []

    def logs(pid, *, strict=False):
        reads.append(pid)
        assert strict and pid == 'proj2'
        return [dict(session_id='healthy-session', delivery_attempt_id='attempt-healthy',
                     provider='codex', provider_session_id='native-thread', status='completed')]

    monkeypatch.setattr(setup.ar, '_load_agent_log', logs)
    assert setup.wake.process_next(setup.app)['state'] == 'idle'
    stale = setup.store.get_event('stale', include_content=True)
    assert stale['state'] in ('expired', 'revoked')
    assert stale['reason'] == 'project_generation_changed'
    assert stale['body'] is None and stale['laundered'] is None
    assert setup.store.get_event('healthy')['state'] == 'turn_completed'
    assert reads == ['proj2']
    with setup.store._read() as db:
        receipt = db.execute("SELECT * FROM mail_turn_receipts WHERE event_id='stale'").fetchone()
    assert receipt['accepted'] == 1 and receipt['completed'] == 0
    assert receipt['project_generation'] == 1  # Never adopt the recreated identity.
    setup.wake.accepted('stale', 'attempt-stale', None)  # Late acceptance cannot reopen it.
    assert setup.store.get_event('stale')['state'] == stale['state']
    assert setup.wake.process_next(setup.app)['state'] == 'idle'
    assert reads == ['proj2']  # Terminal rows are not repeatedly reconciled.


def test_row_read_error_is_logged_without_blocking_other_project(setup, monkeypatch):
    codex_history(setup, monkeypatch, isolated=False)
    validate(setup.project, setup.session, snapshot(setup.project, setup.session))
    accepted_event(setup, 'broken', 'proj1', 'mail-session')
    accepted_event(setup, 'healthy', 'proj2', 'healthy-session')
    monkeypatch.setattr(setup.ar, 'load_project', lambda pid: dict(setup.project, id=pid))

    def logs(pid, *, strict=False):
        assert strict
        if pid == 'proj1':
            raise OSError('synthetic history read failure')
        return [dict(session_id='healthy-session', delivery_attempt_id='attempt-healthy',
                     provider='codex', provider_session_id='native-thread', status='completed')]

    from mc import agent_mail_wake
    messages = []
    monkeypatch.setattr(agent_mail_wake, '_log', lambda message, **k: messages.append(message))
    monkeypatch.setattr(setup.ar, '_load_agent_log', logs)
    assert setup.wake.process_next(setup.app)['state'] == 'idle'
    assert setup.store.get_event('broken')['state'] == 'submitted'
    assert setup.store.get_event('healthy')['state'] == 'turn_completed'
    assert any('broken' in msg and 'synthetic history read failure' in msg for msg in messages)
