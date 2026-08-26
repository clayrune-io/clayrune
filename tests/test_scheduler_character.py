"""A scheduled task names WHO runs it (Ron, 2026-08-26).

A schedule used to be a project-level task: it dispatched as whatever the
project happened to default to, and nothing on the row said so. With the Floor
making agents a thing you can point at, a schedule pins a persona the same way
a chat does — `character` is the same "scope:name" string `_dispatch_agent_internal`
already accepts.

The load-bearing part is that the persona is validated at WRITE time. At fire
time `_resolve_character` is deliberately best-effort (a deleted persona must
never block a run), so the write is the only place a bad value can still be
reported to anybody.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server
    from mc import characters as ch
    from mc.blueprints import scheduler_routes as sr

    global_dir = tmp_path / 'agents-global'
    global_dir.mkdir()
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)

    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'agents').mkdir(parents=True)
    other_path = tmp_path / 'other'
    (other_path / '.claude' / 'agents').mkdir(parents=True)

    alpha = {'id': 'alpha', 'name': 'Alpha', 'project_path': str(proj_path)}
    beta = {'id': 'beta', 'name': 'Beta', 'project_path': str(other_path)}
    pathless = {'id': 'nopath', 'name': 'No Path'}
    projects = {'alpha': alpha, 'beta': beta, 'nopath': pathless}

    monkeypatch.setattr(sr, 'load_project', lambda pid: projects.get(pid))
    monkeypatch.setattr(sr, 'load_projects', lambda: list(projects.values()))
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', tmp_path / 'schedules.json')

    # Personas: one global, one in alpha only.
    ch.write_character('global', 'archivist', 'Files things', 'You file things.',
                       project_path=None, overwrite=True,
                       agent_name='Quill', avatar='fig:archivist')
    ch.write_character('project', 'scout', 'Looks around', 'You look around.',
                       project_path=str(proj_path), overwrite=True,
                       agent_name='Posy', avatar='fig:courier')

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.projects = projects          # type: ignore[attr-defined]
    c.proj_path = proj_path        # type: ignore[attr-defined]
    return c


def _mk(client, **over):
    body = {'project_id': 'alpha', 'task': 'Do the thing',
            'schedule_type': 'daily', 'time': '09:00'}
    body.update(over)
    return client.post('/api/schedules', json=body)


def _row(client, sid):
    return next(s for s in client.get('/api/schedules').get_json()
                if s['id'] == sid)


class TestPinningAPersona:

    def test_a_global_persona_round_trips(self, client):
        r = _mk(client, character='global:archivist')
        assert r.status_code == 201
        assert r.get_json()['character'] == 'global:archivist'
        assert _row(client, r.get_json()['id'])['character'] == 'global:archivist'

    def test_a_project_persona_round_trips(self, client):
        r = _mk(client, character='project:scout')
        assert r.status_code == 201
        assert _row(client, r.get_json()['id'])['character'] == 'project:scout'

    def test_no_persona_means_inherit_not_error(self, client):
        """Empty is a real value — it inherits the project default, exactly as
        a manual dispatch does."""
        r = _mk(client)
        assert r.status_code == 201 and r.get_json()['character'] == ''

    def test_a_persona_that_does_not_exist_is_refused_at_write_time(self, client):
        r = _mk(client, character='global:nobody')
        assert r.status_code == 400
        assert 'nobody' in r.get_json()['error']
        assert client.get('/api/schedules').get_json() == []

    def test_a_project_persona_from_another_project_is_refused(self, client):
        """`scout` lives in alpha. Scheduling it under beta would store fine,
        read `enabled`, and then run with no persona at all."""
        r = _mk(client, project_id='beta', character='project:scout')
        assert r.status_code == 400
        assert 'scout' in r.get_json()['error']

    def test_a_project_with_no_folder_cannot_hold_a_project_persona(self, client):
        r = _mk(client, project_id='nopath', character='project:scout')
        assert r.status_code == 400
        assert 'no folder' in r.get_json()['error']

    @pytest.mark.parametrize('bad', ['archivist', 'weird:archivist', 'global:', ':x'])
    def test_a_malformed_character_is_refused(self, client, bad):
        assert _mk(client, character=bad).status_code == 400


class TestEditingIt:

    def test_put_changes_the_persona(self, client):
        sid = _mk(client, character='global:archivist').get_json()['id']
        r = client.put(f'/api/schedules/{sid}', json={'character': 'project:scout'})
        assert r.status_code == 200
        assert _row(client, sid)['character'] == 'project:scout'

    def test_put_can_clear_the_persona_back_to_the_default(self, client):
        sid = _mk(client, character='global:archivist').get_json()['id']
        assert client.put(f'/api/schedules/{sid}', json={'character': ''}).status_code == 200
        assert _row(client, sid)['character'] == ''

    def test_moving_the_project_revalidates_the_persona(self, client):
        """THE trap this guards. `scout` is alpha's. Moving the row to beta
        orphans it, and the PUT is the last moment anything can say so."""
        sid = _mk(client, character='project:scout').get_json()['id']
        r = client.put(f'/api/schedules/{sid}', json={'project_id': 'beta'})
        assert r.status_code == 400
        assert _row(client, sid)['project_id'] == 'alpha'

    def test_an_unrelated_put_leaves_the_persona_alone(self, client):
        sid = _mk(client, character='global:archivist').get_json()['id']
        assert client.put(f'/api/schedules/{sid}', json={'time': '11:30'}).status_code == 200
        row = _row(client, sid)
        assert row['character'] == 'global:archivist' and row['time'] == '11:30'


class TestWhoRunsIt:
    """GET enriches each row for display. Read-only: baking a face into 24
    schedule rows would go stale the first time a persona is renamed."""

    def test_a_pinned_persona_shows_its_chosen_name_and_face(self, client):
        sid = _mk(client, character='project:scout').get_json()['id']
        cd = _row(client, sid)['character_display']
        assert cd['name'] == 'Posy' and cd['avatar'] == 'fig:courier'
        assert cd['inherited'] is False

    def test_an_inherited_persona_is_marked_as_inherited(self, client):
        """A row that looks pinned but isn't changes who it runs as when
        someone edits the project — the list has to show the difference."""
        client.projects['alpha']['default_character'] = 'global:archivist'
        sid = _mk(client).get_json()['id']
        cd = _row(client, sid)['character_display']
        assert cd['name'] == 'Quill' and cd['inherited'] is True

    def test_no_persona_anywhere_shows_nothing(self, client):
        sid = _mk(client).get_json()['id']
        assert _row(client, sid)['character_display'] is None

    def test_a_persona_deleted_afterwards_reads_as_missing_not_as_default(self, client):
        from mc import characters as ch
        sid = _mk(client, character='project:scout').get_json()['id']
        ch.delete_character('project', 'scout', project_path=str(client.proj_path))
        cd = _row(client, sid)['character_display']
        assert cd['missing'] is True and cd['name'] == 'scout'


class TestItReachesDispatch:

    def test_run_now_passes_the_persona_through(self, client, monkeypatch):
        from mc.blueprints import scheduler_routes as sr
        seen = {}

        def _fake(pid, task, **kw):
            seen.update(kw)
            seen['pid'] = pid
            return 'sess1'
        monkeypatch.setattr(sr, '_dispatch_agent_internal', _fake)
        monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda *a: '')
        monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda *a: '')
        sid = _mk(client, character='global:archivist').get_json()['id']
        r = client.post(f'/api/schedule/{sid}/run-now')
        assert r.status_code == 200, r.get_json()
        assert seen['character'] == 'global:archivist'
        assert seen['trigger_type'] == 'schedule'

    def test_run_now_on_an_unpinned_schedule_sends_no_persona(self, client, monkeypatch):
        """Empty must reach dispatch as empty, so `_resolve_character` can
        apply the project default — not be dropped into a different code path."""
        from mc.blueprints import scheduler_routes as sr
        seen = {}
        monkeypatch.setattr(sr, '_dispatch_agent_internal',
                            lambda pid, task, **kw: (seen.update(kw), 'sess1')[1])
        monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda *a: '')
        monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda *a: '')
        sid = _mk(client).get_json()['id']
        assert client.post(f'/api/schedule/{sid}/run-now').status_code == 200
        assert seen['character'] == ''
