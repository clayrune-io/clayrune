"""Route-entry budget reaches the last boundary before transport; no real mail."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path

import pytest
from flask import Flask

from test_agent_mail_outbound import out, kit, setup, env, request  # noqa: F401
from test_agent_mail_note_routes import URL, payload
from test_agent_mail_outbound_questions import raise_form
from mc import state
from mc.blueprints import agent_mail_note_routes as routes


@pytest.mark.parametrize('stage', ['factory', 'prepare', 'sign', 'admission_commit'])
@pytest.mark.parametrize('elapsed', [14.999, 15, 15.001, 25])
def test_note_route_deadline_covers_all_work_before_transport(out, monkeypatch, stage, elapsed):
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    now = [100.0]

    def slow(original):
        def run(*args, **kwargs):
            result = original(*args, **kwargs)
            now[0] += elapsed
            return result
        return run

    factory = lambda: out.outbound
    if stage == 'factory':
        factory = slow(factory)
    elif stage == 'prepare':
        monkeypatch.setattr(out.outbound, 'prepare', slow(out.outbound.prepare))
    elif stage == 'sign':
        monkeypatch.setattr(out.core.seal, 'sign_pull', slow(out.core.seal.sign_pull))
    else:
        original = out.store._tx

        @contextmanager
        def slow_commit():
            with original() as db:
                yield db
                sending = db.execute("SELECT 1 FROM mail_outbound_intents WHERE state='sending'").fetchone()
            if sending:
                now[0] += elapsed  # committed already: catch the budget spent exiting the transaction

        monkeypatch.setattr(out.store, '_tx', slow_commit)

    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=factory, clock=lambda: now[0]))
    client = app.test_client()
    body = payload(session_id=out.session['session_id'])
    result = client.post(URL, json=body)
    assert result.status_code == 200, result.json
    expected = 'accepted' if elapsed <= routes.NOTE_SEND_DEADLINE_SECONDS else 'abandoned'
    assert result.json['state'] == result.json['transport_state'] == expected
    assert len(out.recording.calls) == (1 if expected == 'accepted' else 0)
    if expected == 'abandoned':
        assert result.json['reason'] == 'deadline_exceeded'
        assert result.json['message_id'] == ''
        with out.store._read() as db:
            row = db.execute('SELECT * FROM mail_outbound_intents').fetchone()
            assert row['payload'] is None
            assert db.execute('SELECT revoked_at FROM thread_bindings WHERE thread_hash=?',
                              (row['thread_hash'],)).fetchone()[0] is not None
        # Reopen Outbound, reset request time, and retry the exact request id:
        # a fresh route budget must never revive the expired attempt.
        monkeypatch.setattr(out, 'outbound', out.kit.outbound())
    now[0] = 100.0
    again = client.post(URL, json=body)
    assert again.status_code == 200 and again.json == result.json
    assert len(out.recording.calls) == (1 if expected == 'accepted' else 0)


def test_question_send_ignores_note_deadline(out, monkeypatch):
    monkeypatch.setitem(state.CONFIG, routes.FLAG, False)
    raise_form(out)
    question = out.session['pending_questions'][0]
    prepared = out.outbound.prepare('proj1', out.session['session_id'],
                                    request(kind='question'), question=question)
    result = out.outbound.send(prepared['intent_id'], deadline=115, clock=lambda: 125)
    assert result['state'] == 'accepted' and len(out.recording.calls) == 1


def test_note_deadline_stays_below_send_mail_client_timeout():
    path = Path(__file__).resolve().parents[1] / 'tools' / 'night-review' / 'agent_mail_route.py'
    spec = importlib.util.spec_from_file_location('note_deadline_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert 0 < routes.NOTE_SEND_DEADLINE_SECONDS < module.TIMEOUT_S == 20
