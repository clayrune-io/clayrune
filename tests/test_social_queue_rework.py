"""Push-back closes the loop: rejecting a Desk-origin draft immediately
dispatches Posy to redraft it (Ron, 2026-09-11, "when we pushback article it
immediately trigger the relevant agent to re-work on this story").

What these guard, worst-to-lose first:

  1. THE EFFECT, not a flag. A push-back with a note on a Desk-origin draft
     (has signal_id + voice) dispatches exactly one rework, and the brief
     handed to that agent contains the rejected body and the note VERBATIM.
  2. THE CASES THAT MUST NOT BREAK. A pre-Desk draft (no signal_id/voice)
     still gets pushed back successfully — status changes, note stored — and
     plainly reports that no rework was dispatched and why. No exception.
  3. A dispatch failure must not cost the push-back: the note and
     needs_changes status survive even when dispatch raises.
  4. NO DOUBLE DISPATCH. A second push-back on an item already mid-rework
     returns the in-flight session instead of starting a second agent.
"""
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers blueprints on first import)
    from mc import desk
    from mc.blueprints import desk_routes, local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    monkeypatch.setattr(desk_routes, 'load_project', pr.load_project)
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')

    calls = []

    def fake_dispatch(project_id, task, resume_id, **kw):
        calls.append({'project_id': project_id, 'task': task, **kw})
        return 'rework-sess-1'

    monkeypatch.setattr(desk_routes, 'dispatch_agent', fake_dispatch)

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.data_dir = data_dir
    c.dispatch_calls = calls
    return c


def _make_project(client, pid='proj1'):
    (client.data_dir / f'{pid}.json').write_text('{"id": "%s", "name": "Proj 1"}' % pid,
                                                   encoding='utf-8')


def _desk_signal(**kw):
    from mc import desk
    return desk.append_signal(
        kw.pop('project_id', 'proj1'), kw.pop('kind', 'release'),
        kw.pop('summary', 'Shipped drag-to-hire, now live'),
        ref=kw.pop('ref', 'abc123'), **kw)


def _desk_draft_item(client, *, signal, voice, body='Original draft body', pid='proj1'):
    return client.post(f'/api/project/{pid}/social/queue', json={
        'platform': 'x', 'body': body, 'signal_id': signal['id'], 'voice': voice,
        'teaching': 'because it shipped',
    }).get_json()['item']


def test_pushback_on_desk_draft_dispatches_one_rework_with_body_and_note(client):
    _make_project(client)
    sig = _desk_signal()
    item = _desk_draft_item(client, signal=sig, voice='personal',
                            body='We are thrilled to announce drag-to-hire')

    res = client.post(f"/api/project/proj1/social/queue/{item['id']}/reject",
                       json={'note': 'too corporate, make it sound like a person shipped this'})
    assert res.status_code == 200
    body = res.get_json()
    assert body['item']['status'] == 'needs_changes'
    assert body['rework_dispatched'] is True
    assert body['rework_session_id'] == 'rework-sess-1'
    assert body['item']['rework_session_id'] == 'rework-sess-1'

    assert len(client.dispatch_calls) == 1
    call = client.dispatch_calls[0]
    assert call['character'] == 'global:social-media-strategist'
    assert call['strict_character'] is True
    assert call['source'] == 'agent'
    assert 'We are thrilled to announce drag-to-hire' in call['task']
    assert 'too corporate, make it sound like a person shipped this' in call['task']


def test_pushback_with_no_signal_id_stores_note_reports_no_rework(client):
    """Pre-Desk drafts (clayrune_website's facebook/discord items) have no
    signal_id or voice. Push-back must still succeed, not crash, and say
    plainly that no rework was dispatched."""
    _make_project(client)
    item = client.post('/api/project/proj1/social/queue', json={
        'platform': 'discord', 'body': 'draft', 'originated': False,
    }).get_json()['item']

    res = client.post(f"/api/project/proj1/social/queue/{item['id']}/reject",
                       json={'note': 'wrong tone'})
    assert res.status_code == 200
    body = res.get_json()
    assert body['item']['status'] == 'needs_changes'
    assert body['item']['note'] == 'wrong tone'
    assert body['rework_dispatched'] is False
    assert 'signal_id' in body['rework_reason'] or 'voice' in body['rework_reason']
    assert client.dispatch_calls == []


def test_dispatch_failure_leaves_pushback_intact(client):
    _make_project(client)
    sig = _desk_signal()
    item = _desk_draft_item(client, signal=sig, voice='personal')

    from mc.blueprints import desk_routes

    def boom(*a, **kw):
        raise RuntimeError('agent unavailable')
    desk_routes.dispatch_agent = boom

    res = client.post(f"/api/project/proj1/social/queue/{item['id']}/reject",
                       json={'note': 'try again'})
    assert res.status_code == 200
    body = res.get_json()
    assert body['item']['status'] == 'needs_changes'
    assert body['item']['note'] == 'try again'
    assert body['rework_dispatched'] is False
    assert 'agent unavailable' in body['rework_reason']

    still = client.get('/api/project/proj1/social/queue').get_json()[0]
    assert still['status'] == 'needs_changes'
    assert still['note'] == 'try again'


def test_second_pushback_while_rework_in_flight_does_not_redispatch(client):
    _make_project(client)
    sig = _desk_signal()
    item = _desk_draft_item(client, signal=sig, voice='personal')

    first = client.post(f"/api/project/proj1/social/queue/{item['id']}/reject",
                        json={'note': 'first note'})
    assert first.get_json()['rework_dispatched'] is True
    assert len(client.dispatch_calls) == 1

    second = client.post(f"/api/project/proj1/social/queue/{item['id']}/reject",
                         json={'note': 'second note, still not right'})
    assert second.status_code == 200
    body = second.get_json()
    assert body['item']['status'] == 'needs_changes'
    assert body['item']['note'] == 'second note, still not right'
    assert body['rework_dispatched'] is False
    assert body['rework_session_id'] == 'rework-sess-1'
    assert 'already in flight' in body['rework_reason']
    # Still exactly one dispatch call — the second push-back did not start another.
    assert len(client.dispatch_calls) == 1


def test_reworked_draft_can_be_posted_with_reworked_from_link(client):
    """The new draft references the one it supersedes; the superseded original
    stays visible in the queue (amber badge is a frontend concern, but the
    backend must not delete or hide it)."""
    _make_project(client)
    sig = _desk_signal()
    original = _desk_draft_item(client, signal=sig, voice='personal')
    client.post(f"/api/project/proj1/social/queue/{original['id']}/reject",
               json={'note': 'redo it'})

    reworked = client.post('/api/project/proj1/social/queue', json={
        'platform': 'x', 'body': 'a better draft', 'signal_id': sig['id'],
        'voice': 'personal', 'teaching': 'fixed the tone',
        'reworked_from': original['id'],
    }).get_json()['item']
    assert reworked['reworked_from'] == original['id']

    listing = client.get('/api/project/proj1/social/queue').get_json()
    ids = {i['id']: i for i in listing}
    assert original['id'] in ids
    assert ids[original['id']]['status'] == 'needs_changes'
    assert reworked['id'] in ids
