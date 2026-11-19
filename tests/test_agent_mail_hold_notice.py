"""Held mail is visible to the human without waking or leaking to the agent."""
import json
from types import SimpleNamespace

from test_agent_mail_wake import setup, env, TOKEN  # noqa: F401
from mc import agent_mail_factory
from mc.agent_mail_hold_notice import by_session, HELD
from mc.agent_mail_reply_extraction import MailReplyExtraction

BODY = '> PRIVATE-BODY-NEVER-IN-CHAT'


def hold(setup, event='held-one', token=TOKEN):
    setup.store.insert_event(event_id=event, registration_id='installation', thread_token=token,
                             message_id=f'<{event}@example.invalid>', body=BODY)
    assert MailReplyExtraction(setup.store, enabled=True).process_next()['reason'] == 'empty_reply'


def test_held_event_emits_metadata_notice_without_body_or_turn(setup, monkeypatch):
    hold(setup)
    session = setup.session
    before_lines = list(session['log_lines'])
    before_questions = list(session.get('pending_questions', []))
    monkeypatch.setattr(agent_mail_factory, 'get', lambda: SimpleNamespace(
        open_store=lambda **kw: setup.store))
    monkeypatch.setattr(setup.ar._delegation_waiting, 'waiting_by_parent', lambda pid: {})
    setup.app.register_blueprint(setup.ar.bp)
    response = setup.app.test_client().get('/api/project/proj1/agent/status')
    assert response.status_code == 200
    notices = response.get_json()['agent_mail_holds']
    assert notices == {session['session_id']: {'count': 1, 'text': HELD}}
    assert BODY not in response.get_data(as_text=True)
    assert 'PRIVATE-BODY' not in response.get_data(as_text=True)
    assert session['proc'].stdin.getvalue() == ''
    assert not session.get('pending_followups')
    assert session['log_lines'] == before_lines
    assert session.get('pending_questions', []) == before_questions
    assert setup.store.get_event('held-one')['state'] == 'blocked'
    assert setup.store.get_event('held-one')['turn_id'] == ''


def test_archived_session_notice_is_scoped_and_clears_after_review(setup):
    hold(setup)
    setup.ar.agent_sessions.pop(setup.session['session_id'])
    read = lambda pid: by_session(pid, store_factory=lambda: setup.store)
    assert read('other-project') == {}
    assert read('proj1') == {setup.session['session_id']: {'count': 1, 'text': HELD}}
    setup.store.reject_event('held-one', 'operator_rejected')
    assert read('proj1') == {}


def test_unknown_hold_reason_never_reaches_notice(setup):
    hold(setup)
    with setup.store._tx() as db:
        db.execute('UPDATE events SET reason=?', (BODY,))
    result = by_session('proj1', store_factory=lambda: setup.store)
    assert result == {setup.session['session_id']: {'count': 1, 'text': HELD}}
    assert BODY not in json.dumps(result)


def test_old_instruction_hold_stays_held_with_generic_notice(setup):
    hold(setup)
    with setup.store._tx() as db:
        db.execute("UPDATE events SET reason='instruction_like'")
    assert by_session('proj1', store_factory=lambda: setup.store) == {
        setup.session['session_id']: {'count': 1, 'text': HELD}}
    assert setup.store.get_event('held-one')['state'] == 'blocked'


def test_never_enrolled_is_empty_and_storage_failure_is_not_clear():
    assert by_session('proj1', store_factory=lambda: None) == {}
    def broken():
        raise OSError('unavailable')
    assert by_session('proj1', store_factory=broken) is None
