"""Request-level tests for the agent-characters family
(mc/blueprints/character_routes.py + mc/characters.py) — Prompt Builder
Phase 1 (docs/PROMPT_BUILDER_DESIGN.md §5.2).

Determinism: GLOBAL_AGENTS_DIR is repointed at tmp_path (never the real
~/.claude/agents), and load_project is patched on BOTH blueprint modules —
character_routes binds its own copy via wire(), while the shared
_resolve_project_path_or_400 helper reads skills_routes' module global.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server
    from mc import characters as ch
    from mc.blueprints import character_routes as cr
    from mc.blueprints import local_auth as la
    from mc.blueprints import skills_routes as sr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    global_dir = tmp_path / 'agents-global'
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    proj = {'id': 'tchar', 'name': 'Char Test', 'project_path': str(proj_path)}
    pathless = {'id': 'nopath', 'name': 'No Path'}

    def _load(pid):
        return {'tchar': proj, 'nopath': pathless}.get(pid)

    monkeypatch.setattr(cr, 'load_project', _load)
    monkeypatch.setattr(sr, 'load_project', _load)

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.global_dir = global_dir          # type: ignore[attr-defined]
    c.proj_agents = proj_path / '.claude' / 'agents'  # type: ignore[attr-defined]
    return c


def _payload(**over):
    base = {
        'name': 'code-reviewer',
        'description': 'Use for strict review of diffs before merge.',
        'body': 'You are a strict senior code reviewer. Be terse.',
        'scope': 'project',
        'project_id': 'tchar',
    }
    base.update(over)
    return base


class TestCreate:
    def test_project_scope_writes_standard_subagent_file(self, client):
        r = client.post('/api/characters', json=_payload())
        assert r.status_code == 201
        rec = r.get_json()
        assert rec['name'] == 'code-reviewer' and rec['scope'] == 'project'

        f = client.proj_agents / 'code-reviewer.md'
        text = f.read_text(encoding='utf-8')
        # Standard Claude Code subagent shape: frontmatter then body.
        assert text.startswith('---\nname: code-reviewer\n')
        assert 'description: ' in text
        assert text.rstrip().endswith('Be terse.')

    def test_global_scope(self, client):
        r = client.post('/api/characters', json=_payload(scope='global'))
        assert r.status_code == 201
        assert (client.global_dir / 'code-reviewer.md').is_file()

    @pytest.mark.parametrize('over,frag', [
        ({'name': 'Bad Name!'}, 'kebab-case'),
        ({'description': '  '}, 'description is required'),
        ({'body': ''}, 'body is required'),
        ({'body': 'x' * (6 * 1024 + 1)}, 'too large'),
        ({'scope': 'archive'}, 'scope must be'),
    ])
    def test_validation_400(self, client, over, frag):
        r = client.post('/api/characters', json=_payload(**over))
        assert r.status_code == 400
        assert frag in r.get_json()['error']

    def test_project_scope_requires_project(self, client):
        r = client.post('/api/characters', json=_payload(project_id=None))
        assert r.status_code == 400
        r = client.post('/api/characters', json=_payload(project_id='nopath'))
        assert r.status_code == 400
        assert 'project_path' in r.get_json()['error']

    def test_collision_409_then_overwrite(self, client):
        assert client.post('/api/characters', json=_payload()).status_code == 201
        r = client.post('/api/characters', json=_payload(body='v2'))
        assert r.status_code == 409
        r = client.post('/api/characters', json=_payload(body='v2', overwrite=True))
        assert r.status_code == 201
        text = (client.proj_agents / 'code-reviewer.md').read_text(encoding='utf-8')
        assert text.rstrip().endswith('v2')

    def test_lan_without_passcode_401(self, client):
        r = client.post('/api/characters', json=_payload(),
                        environ_overrides=LAN)
        assert r.status_code == 401


class TestListReadUpdateDelete:
    def test_roundtrip(self, client):
        client.post('/api/characters', json=_payload())

        r = client.get('/api/characters?project_id=tchar')
        names = [c['name'] for c in r.get_json()]
        assert 'code-reviewer' in names

        r = client.get('/api/characters/project/code-reviewer?project_id=tchar')
        rec = r.get_json()
        assert rec['body'].startswith('You are a strict senior code reviewer')

        r = client.put('/api/characters/project/code-reviewer',
                       json={'project_id': 'tchar', 'description': 'Updated desc.'})
        assert r.status_code == 200
        assert r.get_json()['description'] == 'Updated desc.'
        # Body untouched by a description-only PUT.
        r = client.get('/api/characters/project/code-reviewer?project_id=tchar')
        assert 'senior code reviewer' in r.get_json()['body']

        r = client.delete('/api/characters/project/code-reviewer?project_id=tchar')
        assert r.status_code == 200
        assert not (client.proj_agents / 'code-reviewer.md').exists()
        r = client.get('/api/characters/project/code-reviewer?project_id=tchar')
        assert r.status_code == 404

    def test_project_shadows_global_in_list(self, client):
        client.post('/api/characters', json=_payload(scope='global'))
        client.post('/api/characters', json=_payload())
        items = client.get('/api/characters?project_id=tchar').get_json()
        by_scope = {c['scope']: c for c in items if c['name'] == 'code-reviewer'}
        assert by_scope['global'].get('shadowed_by_project') is True
        assert 'shadowed_by_project' not in by_scope['project']

    def test_nested_community_file_found_and_deleted(self, client):
        # Imported packs may nest files in subfolders; CC scans recursively
        # and so do we (lookup by file stem).
        nested = client.global_dir / 'review' / 'security-auditor.md'
        nested.parent.mkdir(parents=True)
        nested.write_text('---\nname: security-auditor\ndescription: audits\n---\nYou audit.\n',
                          encoding='utf-8')
        r = client.get('/api/characters/global/security-auditor')
        assert r.status_code == 200
        assert r.get_json()['body'].strip() == 'You audit.'
        assert client.delete('/api/characters/global/security-auditor').status_code == 200
        assert not nested.exists()

    def test_q_filter(self, client):
        client.post('/api/characters', json=_payload(scope='global'))
        client.post('/api/characters', json=_payload(
            scope='global', name='docs-writer',
            description='Use for documentation work.'))
        items = client.get('/api/characters?q=documentation').get_json()
        assert [c['name'] for c in items] == ['docs-writer']


# ── Engine pins (agent types Phase 1, docs/AGENT_TYPES_DESIGN.md §3) ─────────
#
# A character may pin provider / model / effort so the TYPE carries its engine
# ("Fable writes PRDs") instead of inheriting whatever the project is set to.
# All three keys are optional and absent means "exactly as before", which is
# what makes this a no-migration change for characters already on disk.

def _mk(client, name='prd-writer', **engine):
    body = {'name': name, 'scope': 'project', 'project_id': 'tchar',
            'description': 'writes PRDs', 'body': 'You write PRDs.',
            'overwrite': True, **engine}
    return client.post('/api/characters', json=body)


def _read(client, name='prd-writer'):
    return client.get(f'/api/characters/project/{name}?project_id=tchar').get_json()


def test_engine_pins_round_trip(client):
    r = _mk(client, provider='claude', model='claude-fable-5', effort='high')
    assert r.status_code == 201
    assert _read(client)['engine'] == {'provider': 'claude',
                                       'model': 'claude-fable-5',
                                       'effort': 'high'}


def test_no_engine_keys_means_no_engine(client):
    assert _mk(client, name='plain').status_code == 201
    assert 'engine' not in _read(client, 'plain')


def test_unknown_provider_is_refused_not_silently_defaulted(client):
    """A character pinned to a provider that isn't registered would spawn on
    whatever the project default happens to be — running on the wrong engine
    while claiming otherwise. Refuse the save instead."""
    r = _mk(client, provider='grok')
    assert r.status_code == 400
    assert 'unknown provider' in r.get_json()['error']


def test_invalid_effort_is_refused(client):
    r = _mk(client, effort='ludicrous')
    assert r.status_code == 400
    assert 'effort must be one of' in r.get_json()['error']


def test_put_omitting_engine_keys_leaves_them_alone(client):
    """The editor sends all three every time, but other callers (and Claydo's
    save path) don't — an omitted key must not silently clear a pin."""
    _mk(client, model='claude-fable-5', effort='high')
    r = client.put('/api/characters/project/prd-writer',
                   json={'project_id': 'tchar', 'description': 'still writes PRDs'})
    assert r.status_code == 200
    assert _read(client)['engine'] == {'model': 'claude-fable-5', 'effort': 'high'}


def test_put_with_an_empty_value_clears_that_pin(client):
    """Absent and empty have to mean different things, or a pin set once could
    never be removed from the editor."""
    _mk(client, model='claude-fable-5', effort='high')
    r = client.put('/api/characters/project/prd-writer',
                   json={'project_id': 'tchar', 'model': ''})
    assert r.status_code == 200
    assert _read(client)['engine'] == {'effort': 'high'}


def test_provider_is_normalised_to_lower_case(client):
    assert _mk(client, provider='Claude').status_code == 201
    assert _read(client)['engine']['provider'] == 'claude'


def test_engine_survives_an_unrelated_body_edit(client):
    _mk(client, model='claude-fable-5')
    client.put('/api/characters/project/prd-writer',
               json={'project_id': 'tchar', 'body': 'Rewritten instructions.'})
    rec = _read(client)
    assert rec['engine'] == {'model': 'claude-fable-5'}
    assert 'Rewritten' in rec['body']


# ── Self-chosen names (Ron, 2026-08-22) ──────────────────────────────────────
#
# `name` is the file stem (an identifier). `agent_name` is what the agent calls
# ITSELF, and the agent picks it — POST .../name with no body asks the model.
# The two are separate keys because they answer different questions: the picker
# needs a browsable role, the chat header needs whoever is speaking.

def test_agent_name_round_trips_and_leads_the_record(client):
    _mk(client, name='reviewer')
    r = client.post('/api/characters/project/reviewer/name',
                    json={'project_id': 'tchar', 'agent_name': 'Vector'})
    assert r.status_code == 200
    assert _read(client, 'reviewer')['agent_name'] == 'Vector'


def test_model_chosen_name_is_cleaned_before_it_is_stored(client):
    """Asked for one word, a model very often answers with one in quotes. A
    pill reading '"Vector"' looks like a bug, so strip before persisting."""
    from mc.blueprints import character_routes as cr
    _mk(client, name='reviewer')
    cr._scribe_call = lambda *a, **k: '  \u201cVector\u201d.  '
    r = client.post('/api/characters/project/reviewer/name',
                    json={'project_id': 'tchar'})
    assert r.status_code == 200 and r.get_json()['agent_name'] == 'Vector'


def test_a_sentence_is_refused_rather_than_truncated(client):
    """Pilling a fragment of 'I would suggest the name Vector' is worse than
    leaving the type on its file name — so fail loudly and keep the old one."""
    from mc.blueprints import character_routes as cr
    _mk(client, name='reviewer')
    cr._scribe_call = lambda *a, **k: 'I would suggest the name Vector'
    r = client.post('/api/characters/project/reviewer/name',
                    json={'project_id': 'tchar'})
    assert r.status_code == 502
    assert 'usable name' in r.get_json()['error']
    assert 'agent_name' not in _read(client, 'reviewer')


def test_naming_runs_on_the_model_the_type_is_pinned_to(client):
    """A name is a voice decision — the engine that will do the talking is the
    one that should pick it."""
    from mc.blueprints import character_routes as cr
    _mk(client, name='reviewer', model='claude-fable-5')
    seen = {}

    def _fake(model, instruction, body):
        seen['model'] = model
        return 'Quill'
    cr._scribe_call = _fake
    assert client.post('/api/characters/project/reviewer/name',
                       json={'project_id': 'tchar'}).status_code == 200
    assert seen['model'] == 'claude-fable-5'


def test_naming_preserves_the_engine_and_the_body(client):
    from mc.blueprints import character_routes as cr
    _mk(client, name='reviewer', model='claude-fable-5', effort='high')
    cr._scribe_call = lambda *a, **k: 'Quill'
    client.post('/api/characters/project/reviewer/name', json={'project_id': 'tchar'})
    rec = _read(client, 'reviewer')
    # No provider was pinned, so none should appear — naming must not invent
    # engine keys, only carry the ones that were there.
    assert rec['engine'] == {'model': 'claude-fable-5', 'effort': 'high'}
    assert 'You write PRDs' in rec['body']


def test_blank_clears_the_chosen_name(client):
    _mk(client, name='reviewer')
    client.post('/api/characters/project/reviewer/name',
                json={'project_id': 'tchar', 'agent_name': 'Vector'})
    r = client.post('/api/characters/project/reviewer/name',
                    json={'project_id': 'tchar', 'agent_name': ''})
    assert r.status_code == 200
    assert 'agent_name' not in _read(client, 'reviewer')


def test_put_omitting_agent_name_leaves_it_alone(client):
    _mk(client, name='reviewer')
    client.post('/api/characters/project/reviewer/name',
                json={'project_id': 'tchar', 'agent_name': 'Vector'})
    client.put('/api/characters/project/reviewer',
               json={'project_id': 'tchar', 'description': 'edited'})
    assert _read(client, 'reviewer')['agent_name'] == 'Vector'


def test_naming_an_unknown_character_404s(client):
    assert client.post('/api/characters/project/nope/name',
                       json={'project_id': 'tchar'}).status_code == 404
