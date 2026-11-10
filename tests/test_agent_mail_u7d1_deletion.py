"""Unit 7d-1: deletion hooks revoke agent-mail authority before destructive steps."""
import json
from types import SimpleNamespace

from flask import Flask
import pytest

from test_agent_mail_enrollment import NOW
from test_agent_mail_outbound import out, kit, setup, env, prepare  # noqa: F401
from test_project_routes import client, _seed  # noqa: F401
from mc import agent_mail_deletion as deletion
from mc import agent_mail_ownership as ownership
from mc import state
from mc.agent_mail_factory import MailFactory, StoreHeld
from mc.agent_mail_outbound_state import initialize
from mc.agent_mail_store import MailStore


def fake(store, root):
    return SimpleNamespace(open_store=lambda: store, paths=SimpleNamespace(root=root))


@pytest.fixture
def mail(tmp_path):
    store = MailStore(tmp_path / 'mail.sqlite3', clock=lambda: NOW / 1000)
    store.register('install1')
    initialize(store)
    for token, project, session in (('t1', 'p1', 's1'), ('t2', 'p1', 's2'), ('t3', 'p2', 's1')):
        store.bind_thread('install1', token, project, session)
        store.insert_event(event_id='e-' + token, registration_id='install1', thread_token=token,
                           message_id=f'<{token}@example.invalid>', body='hello ' + token)
        with store._tx() as db:
            db.execute('INSERT INTO mail_outbound_question_channels VALUES(?,?,?,?,?)',
                       (project, session, 'rev-' + token, 'agent_mail', 9e12))
    return SimpleNamespace(store=store, factory=fake(store, tmp_path / 'root'), tmp=tmp_path)


def revoked(store):
    with store._read() as db:
        return {r['project_id'] + '/' + r['session_id']: r['revoked_at'] is not None
                for r in db.execute('SELECT * FROM thread_bindings')}


def event_states(store):
    with store._read() as db:
        return {r['event_id']: r['state'] for r in db.execute('SELECT event_id,state FROM events')}


def test_session_revoke_is_scoped_to_project_and_session_and_releases_its_tombstones(mail):
    assert deletion.revoke_session('p1', ['s1'], factory=mail.factory) == 'revoked'
    assert revoked(mail.store) == {'p1/s1': True, 'p1/s2': False, 'p2/s1': False}
    states = event_states(mail.store)
    assert states['e-t1'] == 'revoked' and states['e-t2'] != 'revoked' and states['e-t3'] != 'revoked'
    with mail.store._read() as db:
        left = {tuple(r) for r in db.execute('SELECT project_id,session_id FROM mail_outbound_question_channels')}
    assert left == {('p1', 's2'), ('p2', 's1')}


def test_project_revoke_fences_only_that_project_never_the_installation(mail):
    assert deletion.revoke_project('p1', factory=mail.factory) == 'revoked'
    assert revoked(mail.store) == {'p1/s1': True, 'p1/s2': True, 'p2/s1': False}
    assert event_states(mail.store)['e-t3'] != 'revoked'
    assert ownership.count(mail.store) == 1
    with mail.store._read() as db:
        assert db.execute('SELECT enabled FROM registrations').fetchone()[0] in (0, 1)
        assert db.execute("SELECT COUNT(*) FROM registrations WHERE registration_id='install1'").fetchone()[0] == 1


def test_revoke_is_idempotent_and_alias_list_includes_runtime_row_ids(mail):
    row = {'session_id': 's1', 'claude_session_id': 'native-1', 'id': 's1'}
    assert deletion.session_aliases('s1', row) == ['native-1', 's1']
    assert deletion.session_aliases('s1', None) == ['s1']
    mail.store.bind_thread('install1', 't4', 'p1', 'native-1')
    for _ in range(2):
        deletion.revoke_session('p1', deletion.session_aliases('s1', row), factory=mail.factory)
    assert revoked(mail.store)['p1/native-1'] is True and revoked(mail.store)['p1/s2'] is False


def test_unsent_outbound_intents_are_revoked_and_their_payload_removed(out):
    result = prepare(out)
    sid = out.session['session_id']
    with out.store._read() as db:
        assert db.execute('SELECT payload FROM mail_outbound_intents').fetchone()[0] is not None
    assert deletion.revoke_session('proj1', [sid], factory=fake(out.store, out.store.path.parent / 'root')) == 'revoked'
    with out.store._read() as db:
        row = db.execute('SELECT state,payload FROM mail_outbound_intents').fetchone()
    assert row['state'] == 'revoked' and row['payload'] is None
    assert result['state'] in ('ready', 'draft', 'retryable')


def test_never_enrolled_store_has_nothing_to_revoke_and_is_not_an_error(tmp_path):
    f = MailFactory(tmp_path / 'agent-mail', config={})
    assert deletion.revoke_session('p', ['s'], factory=f) == 'none'
    assert deletion.revoke_project('p', factory=f) == 'none'
    assert not (tmp_path / 'agent-mail').exists()


def test_held_store_records_a_durable_intent_then_recovery_applies_it_once(mail):
    root = mail.tmp / 'root'
    broken = SimpleNamespace(paths=SimpleNamespace(root=root),
                             open_store=lambda: (_ for _ in ()).throw(StoreHeld('store_corrupt')))
    assert deletion.revoke_session('p1', ['s1'], factory=broken) == 'deferred'
    assert deletion.revoke_project('p2', factory=broken) == 'deferred'
    assert deletion.revoke_project('p2', factory=broken) == 'deferred'  # same intent, one record
    intents = json.loads((root / deletion.INTENTS_NAME).read_text())
    assert len(intents) == 2 and revoked(mail.store) == {'p1/s1': False, 'p1/s2': False, 'p2/s1': False}
    assert deletion.apply_intents(broken) == {'applied': 0, 'pending': 2}
    assert len(json.loads((root / deletion.INTENTS_NAME).read_text())) == 2, 'nothing lost while held'
    assert deletion.apply_intents(mail.factory) == {'applied': 2, 'pending': 0}
    assert revoked(mail.store) == {'p1/s1': True, 'p1/s2': False, 'p2/s1': True}
    assert json.loads((root / deletion.INTENTS_NAME).read_text()) == []
    assert deletion.apply_intents(mail.factory) == {'applied': 0, 'pending': 0}


def test_unwritable_intent_refuses_the_destructive_step(mail):
    blocker = mail.tmp / 'a-file'
    blocker.write_text('x')
    broken = SimpleNamespace(paths=SimpleNamespace(root=blocker / 'sub'),
                             open_store=lambda: (_ for _ in ()).throw(StoreHeld('store_missing')))
    with pytest.raises(deletion.DeletionRefused) as info:
        deletion.revoke_project('p1', factory=broken)
    assert info.value.reason == 'deletion_intent_unwritable'


def test_locked_store_defers_rather_than_losing_the_revocation(mail):
    import sqlite3
    blocker = sqlite3.connect(mail.store.path, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        mail.store.config = mail.store.config.__class__(busy_timeout_ms=50) \
            if hasattr(mail.store.config, 'busy_timeout_ms') else mail.store.config
        assert deletion.revoke_session('p1', ['s1'], factory=mail.factory) == 'deferred'
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
    assert deletion.apply_intents(mail.factory)['applied'] == 1
    assert revoked(mail.store)['p1/s1'] is True


# --- the real routes -----------------------------------------------------------------
def test_session_delete_route_revokes_before_removal_and_refuses_on_failure(monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    app, order = Flask(__name__), []
    state.agent_sessions['mail-del'] = dict(project_id='pdel', status='completed', claude_session_id='nat-1')
    try:
        def revoke(project_id, aliases, **kwargs):
            order.append(('revoke', project_id, tuple(aliases), 'mail-del' in state.agent_sessions))
            return 'revoked'
        monkeypatch.setattr(deletion, 'revoke_session', revoke)
        with app.test_request_context(json={'session_id': 'mail-del'}):
            assert ar.agent_session_delete('pdel').get_json() == {'ok': True}
        assert order[0] == ('revoke', 'pdel', ('mail-del', 'nat-1'), True), 'revoke must precede removal'
        assert 'mail-del' not in state.agent_sessions

        state.agent_sessions['mail-keep'] = dict(project_id='pdel', status='completed')

        def refuse(*args, **kwargs):
            raise deletion.DeletionRefused('deletion_intent_unwritable')
        monkeypatch.setattr(deletion, 'revoke_session', refuse)
        with app.test_request_context(json={'session_id': 'mail-keep'}):
            response, code = ar.agent_session_delete('pdel')
        assert code == 503 and response.get_json()['error'] == 'agent_mail_revocation_failed'
        assert 'mail-keep' in state.agent_sessions, 'refused deletion must leave the session intact'
    finally:
        state.agent_sessions.pop('mail-del', None)
        state.agent_sessions.pop('mail-keep', None)


def test_session_delete_route_revokes_even_when_runtime_row_is_already_gone(monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    seen = []
    monkeypatch.setattr(deletion, 'revoke_session', lambda p, a, **k: seen.append((p, list(a))) or 'revoked')
    with Flask(__name__).test_request_context(json={'session_id': 'ghost'}):
        assert ar.agent_session_delete('pghost').get_json() == {'ok': True}
    assert seen == [('pghost', ['ghost'])]


def test_project_delete_route_refuses_and_keeps_the_project_when_revocation_fails(client, monkeypatch):
    _seed(client)
    calls = []

    def refuse(project_id, **kwargs):
        calls.append(project_id)
        raise deletion.DeletionRefused('deletion_intent_unwritable')
    monkeypatch.setattr(deletion, 'revoke_project', refuse)
    response = client.delete('/api/project/tproj')
    assert response.status_code == 503
    assert response.get_json() == {'error': 'agent_mail_revocation_failed', 'reason': 'deletion_intent_unwritable'}
    assert (client.data_dir / 'tproj.json').exists(), 'a refused revocation must not delete the project'
    assert calls == ['tproj']


def test_project_delete_route_revokes_only_that_project_then_deletes(client, monkeypatch):
    _seed(client)
    calls = []
    monkeypatch.setattr(deletion, 'revoke_project', lambda project_id, **k: calls.append(project_id) or 'revoked')
    assert client.delete('/api/project/tproj').status_code == 200
    assert calls == ['tproj', 'tproj'], 'one fence before side effects, one after to close a raced bind'
    assert not (client.data_dir / 'tproj.json').exists()
