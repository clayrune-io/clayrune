"""One-prompt team creation (POST /api/characters/team) and the human-only
guard on every character mutation route (mc/blueprints/character_routes.py).

Determinism: GLOBAL_AGENTS_DIR is repointed at tmp_path, load_project is
patched on both blueprint modules, and project_routes.save_project is replaced
with an in-memory recorder, so no real ~/.claude or data/projects is touched.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BROWSER = 'http://localhost:5199'
EXISTING = '---\nname: code-reviewer\ndescription: reviews diffs\nmodel: claude-sonnet-5\n---\nYou review diffs.\n'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server
    from mc import characters as ch
    from mc.blueprints import character_routes as cr
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr
    from mc.blueprints import skills_routes as sr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    global_dir = tmp_path / 'agents-global'
    global_dir.mkdir()
    (global_dir / 'code-reviewer.md').write_text(EXISTING, encoding='utf-8')
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)
    monkeypatch.setattr(ch, 'list_figures', lambda: ['courier', 'scholar', 'wizard'])

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    proj = {'id': 'tteam', 'name': 'Team Test', 'project_path': str(proj_path), 'roster': []}

    def _load(pid):
        return {'tteam': proj}.get(pid)

    saves = []
    monkeypatch.setattr(cr, 'load_project', _load)
    monkeypatch.setattr(sr, 'load_project', _load)
    monkeypatch.setattr(pr, 'save_project', lambda pid, data: saves.append((pid, [dict(r) for r in data.get('roster', [])])))

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.environ_base['HTTP_ORIGIN'] = BROWSER
    c.global_dir = global_dir            # type: ignore[attr-defined]
    c.proj_agents = proj_path / '.claude' / 'agents'  # type: ignore[attr-defined]
    c.proj = proj                        # type: ignore[attr-defined]
    c.saves = saves                      # type: ignore[attr-defined]
    return c


def _member(name, **over):
    m = {'mode': 'new', 'name': name, 'agent_name': name.split('-')[0].title(),
         'description': f'Use for {name} work.', 'body': f'You are the {name}.',
         'avatar': 'fig:courier', 'scope': 'project',
         'engine': {'provider': 'claude', 'model': 'claude-sonnet-5', 'effort': 'high'}}
    m.update(over)
    return m


def _team(members, **over):
    body = {'members': members, 'project_id': 'tteam'}
    body.update(over)
    return body


class TestCreateNew:
    def test_every_member_is_created_with_its_fields(self, client):
        r = client.post('/api/characters/team', json=_team(
            [_member('game-designer'), _member('level-artist', avatar='fig:scholar'),
             _member('sound-designer', scope='global')]))
        assert r.status_code == 201, r.get_json()
        assert len(r.get_json()['created']) == 3
        text = (client.proj_agents / 'level-artist.md').read_text(encoding='utf-8')
        assert 'agent_name: Level' in text and 'fig:scholar' in text
        assert 'effort: high' in text and 'model: claude-sonnet-5' in text
        assert (client.global_dir / 'sound-designer.md').exists()

    def test_role_persona_and_flat_engine_aliases_are_accepted(self, client):
        m = {'name': 'writer', 'role': 'Use for dialogue.', 'persona': 'You write dialogue.',
             'provider': 'claude', 'scope': 'project'}
        r = client.post('/api/characters/team', json=_team([m]))
        assert r.status_code == 201, r.get_json()
        assert 'provider: claude' in (client.proj_agents / 'writer.md').read_text(encoding='utf-8')

    def test_one_invalid_member_creates_nothing(self, client):
        r = client.post('/api/characters/team', json=_team(
            [_member('a-one'), _member('b-two'),
             _member('c-three', engine={'effort': 'turbo'})]))
        assert r.status_code == 400
        errs = r.get_json()['member_errors']
        assert [e['index'] for e in errs] == [2] and 'effort' in errs[0]['error']
        assert not client.proj_agents.exists() or not list(client.proj_agents.glob('*.md'))

    def test_unknown_provider_is_the_same_refusal_as_single_create(self, client):
        r = client.post('/api/characters/team', json=_team([_member('x-one', engine={'provider': 'nope'})]))
        assert r.status_code == 400
        assert 'unknown provider' in r.get_json()['member_errors'][0]['error']

    def test_unknown_figure_is_refused(self, client):
        r = client.post('/api/characters/team', json=_team([_member('x-one', avatar='fig:dragon')]))
        assert r.status_code == 400

    def test_a_name_twice_in_one_team_is_refused(self, client):
        r = client.post('/api/characters/team', json=_team([_member('same'), _member('same')]))
        assert r.status_code == 400
        assert 'twice' in r.get_json()['member_errors'][0]['error']


class TestConflicts:
    def test_existing_name_is_409_and_nothing_written(self, client):
        r = client.post('/api/characters/team', json=_team(
            [_member('fresh-one'), _member('code-reviewer', scope='global')]))
        assert r.status_code == 409
        assert r.get_json()['conflicts'][0]['key'] == 'global:code-reviewer'
        assert not (client.proj_agents / 'fresh-one.md').exists()
        assert (client.global_dir / 'code-reviewer.md').read_text(encoding='utf-8') == EXISTING

    def test_confirmed_overwrite_writes_it(self, client):
        r = client.post('/api/characters/team', json=_team(
            [_member('fresh-one'), _member('code-reviewer', scope='global')],
            overwrite=['global:code-reviewer']))
        assert r.status_code == 201, r.get_json()
        assert 'You are the code-reviewer.' in (client.global_dir / 'code-reviewer.md').read_text(encoding='utf-8')


class TestRollback:
    def test_a_write_failing_midway_keeps_nothing(self, client, monkeypatch):
        from mc import characters as ch
        real = ch.write_character
        calls = {'n': 0}

        def flaky(*a, **k):
            calls['n'] += 1
            if calls['n'] == 3:
                raise OSError('disk full')
            return real(*a, **k)

        monkeypatch.setattr(ch, 'write_character', flaky)
        r = client.post('/api/characters/team', json=_team(
            [_member('new-one'), _member('code-reviewer', scope='global'), _member('new-two')],
            overwrite=['global:code-reviewer']))
        assert r.status_code == 500
        assert not (client.proj_agents / 'new-one.md').exists()
        assert (client.global_dir / 'code-reviewer.md').read_text(encoding='utf-8') == EXISTING

    def test_a_failed_roster_save_rolls_the_files_back(self, client, monkeypatch):
        from mc.blueprints import project_routes as pr

        def boom(pid, data):
            raise OSError('project write failed')

        monkeypatch.setattr(pr, 'save_project', boom)
        r = client.post('/api/characters/team', json=_team(
            [{'mode': 'reuse', 'ref': 'global:code-reviewer'}, _member('new-one')]))
        assert r.status_code == 500
        assert not (client.proj_agents / 'new-one.md').exists()


class TestReuse:
    def test_one_reuse_two_new_is_one_hire_two_creates_and_the_existing_is_untouched(self, client):
        before = (client.global_dir / 'code-reviewer.md').read_bytes()
        r = client.post('/api/characters/team', json=_team(
            [{'mode': 'reuse', 'ref': 'global:code-reviewer'},
             _member('game-designer'), _member('level-artist')]))
        assert r.status_code == 201, r.get_json()
        data = r.get_json()
        assert len(data['created']) == 2
        assert data['hired'] == ['global:code-reviewer']
        assert (client.global_dir / 'code-reviewer.md').read_bytes() == before
        assert [row['character'] for row in client.saves[-1][1]] == ['global:code-reviewer']
        assert (client.proj_agents / 'game-designer.md').exists()

    def test_hire_new_hires_the_created_ones_too(self, client):
        r = client.post('/api/characters/team', json=_team(
            [{'mode': 'reuse', 'ref': 'global:code-reviewer'}, _member('game-designer')],
            hire_new=True))
        assert r.status_code == 201
        assert r.get_json()['hired'] == ['global:code-reviewer', 'project:game-designer']

    def test_reusing_a_missing_agent_creates_nothing(self, client):
        r = client.post('/api/characters/team', json=_team(
            [{'mode': 'reuse', 'ref': 'global:ghost'}, _member('game-designer')]))
        assert r.status_code == 400
        assert not (client.proj_agents / 'game-designer.md').exists()
        assert client.saves == []

    def test_reuse_needs_a_project(self, client):
        r = client.post('/api/characters/team', json=_team(
            [{'mode': 'reuse', 'ref': 'global:code-reviewer'}], project_id=None))
        assert r.status_code == 400


MUTATIONS = [
    ('post', '/api/characters', {'name': 'x', 'description': 'd', 'body': 'b', 'scope': 'global'}),
    ('post', '/api/characters/team', {'members': [{'name': 'x', 'description': 'd', 'body': 'b', 'scope': 'global'}]}),
    ('post', '/api/characters/voice', {'description': 'd', 'body': 'b'}),
    ('post', '/api/characters/identity', {'description': 'd', 'body': 'b'}),
    ('put', '/api/characters/global/code-reviewer', {'body': 'hijacked'}),
    ('post', '/api/characters/global/code-reviewer/name', {'agent_name': 'Evil'}),
    ('post', '/api/characters/global/code-reviewer/avatar', {'avatar': 'fig:wizard'}),
    ('post', '/api/characters/global/code-reviewer/move', {'scope': 'project', 'project_id': 'tteam'}),
    ('delete', '/api/characters/global/code-reviewer', None),
]


class TestAskClaydoSeesExistingAgents:
    def test_the_guide_prompt_lists_existing_agents_with_their_engine(self, client, monkeypatch):
        from mc.blueprints import guide_routes as gr
        monkeypatch.setattr(gr, 'load_project', lambda pid: client.proj if pid == 'tteam' else None)
        block = gr._claydo_existing_agents_block('tteam')
        assert 'global:code-reviewer' in block and 'claude-sonnet-5' in block

    def test_no_agents_is_an_empty_block(self, client, monkeypatch, tmp_path):
        from mc import characters as ch
        from mc.blueprints import guide_routes as gr
        monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'empty')
        monkeypatch.setattr(gr, 'load_project', lambda pid: None)
        assert gr._claydo_existing_agents_block(None) == ''


class TestAgentCallerGuard:
    @pytest.mark.parametrize('method,url,body', MUTATIONS)
    def test_an_agent_caller_is_refused(self, client, method, url, body):
        kwargs = {'environ_overrides': {'HTTP_ORIGIN': ''}}
        if body is not None:
            kwargs['json'] = body
        r = getattr(client, method)(url, **kwargs)
        assert r.status_code == 403, (url, r.get_json())
        assert (client.global_dir / 'code-reviewer.md').read_text(encoding='utf-8') == EXISTING
        assert not (client.global_dir / 'x.md').exists()

    def test_a_browser_caller_is_allowed(self, client):
        r = client.post('/api/characters', json=MUTATIONS[0][2])
        assert r.status_code == 201

    def test_an_agent_can_still_read_the_roster(self, client):
        r = client.get('/api/characters?project_id=tteam', environ_overrides={'HTTP_ORIGIN': ''})
        assert r.status_code == 200
        assert any(c['name'] == 'code-reviewer' for c in r.get_json())
