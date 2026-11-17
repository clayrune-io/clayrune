"""SF17 (MC-1179 pass 11): a character write keeps every frontmatter key it does
not own. A character file is also a Claude Code subagent file; `tools:` absent
means "all tools", so dropping it on a typo fix widens an imported read-only
agent. Every mutating route goes through mc.characters.write_character.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BROWSER = 'http://localhost:5199'

# The report's measured file, plus the nested forms the minimal parser cannot
# round-trip (a map and a list), which must come back byte-for-byte.
SUBAGENT = (
    '---\n'
    'name: ro-agent\n'
    'description: Read-only reviewer\n'
    'tools: Read, Grep, Glob\n'
    'disallowedTools: Bash\n'
    'permissionMode: plan\n'
    'color: blue\n'
    'mcpServers:\n'
    '  github:\n'
    '    command: gh-mcp\n'
    '    args: ["--ro"]\n'
    'hooks:\n'
    '  PreToolUse:\n'
    '    - matcher: Bash\n'
    '      hooks:\n'
    '        - type: command\n'
    '          command: ./check.sh\n'
    '---\n'
    'You review things and change nothing.\n'
)
KEPT = ['tools: Read, Grep, Glob', 'disallowedTools: Bash', 'permissionMode: plan', 'color: blue']
NESTED = SUBAGENT.split('color: blue\n')[1].split('---\n')[0]


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
    (global_dir / 'ro-agent.md').write_text(SUBAGENT, encoding='utf-8')
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)
    monkeypatch.setattr(ch, 'list_figures', lambda: ['courier', 'scholar'])

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    proj = {'id': 'tcarry', 'name': 'Carry Test', 'project_path': str(proj_path), 'roster': []}

    def _load(pid):
        return {'tcarry': proj}.get(pid)

    monkeypatch.setattr(cr, 'load_project', _load)
    monkeypatch.setattr(sr, 'load_project', _load)
    monkeypatch.setattr(pr, 'save_project', lambda pid, data: None)
    monkeypatch.setattr(cr, '_require_human_passcode', lambda data: None)

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.environ_base['HTTP_ORIGIN'] = BROWSER
    c.global_dir = global_dir            # type: ignore[attr-defined]
    c.proj_agents = proj_path / '.claude' / 'agents'  # type: ignore[attr-defined]
    return c


def _front(path: Path) -> str:
    return path.read_text(encoding='utf-8').split('---\n')[1]


def _assert_carried(path: Path):
    front = _front(path)
    for line in KEPT:
        assert line in front.splitlines(), (line, front)
    assert NESTED in front, front          # nested map and list, verbatim


def test_put_keeps_unmanaged_keys_and_still_applies_known_ones(client):
    r = client.put('/api/characters/global/ro-agent',
                   json={'description': 'Read-only reviewer (typo fixed)', 'model': 'claude-sonnet-5'})
    assert r.status_code == 200, r.get_json()
    path = client.global_dir / 'ro-agent.md'
    _assert_carried(path)
    front = _front(path)
    assert 'description: Read-only reviewer (typo fixed)' in front.splitlines()
    assert 'model: claude-sonnet-5' in front.splitlines()
    assert 'You review things and change nothing.' in path.read_text(encoding='utf-8')


def test_put_with_a_cleared_known_key_still_clears_it(client):
    client.put('/api/characters/global/ro-agent', json={'model': 'claude-sonnet-5'})
    client.put('/api/characters/global/ro-agent', json={'model': ''})
    path = client.global_dir / 'ro-agent.md'
    assert 'model:' not in _front(path)
    _assert_carried(path)


def test_name_route_keeps_unmanaged_keys(client):
    r = client.post('/api/characters/global/ro-agent/name', json={'agent_name': 'Reed'})
    assert r.status_code == 200, r.get_json()
    path = client.global_dir / 'ro-agent.md'
    assert 'agent_name: Reed' in _front(path).splitlines()
    _assert_carried(path)


def test_avatar_route_keeps_unmanaged_keys(client):
    r = client.post('/api/characters/global/ro-agent/avatar', json={'avatar': 'fig:courier'})
    assert r.status_code == 200, r.get_json()
    path = client.global_dir / 'ro-agent.md'
    assert 'avatar: "fig:courier"' in _front(path).splitlines() or 'avatar: fig:courier' in _front(path).splitlines()
    _assert_carried(path)


def test_move_carries_unmanaged_keys_to_the_new_scope(client):
    r = client.post('/api/characters/global/ro-agent/move',
                    json={'to_scope': 'project', 'to_project_id': 'tcarry'})
    assert r.status_code == 200, r.get_json()
    assert not (client.global_dir / 'ro-agent.md').exists()
    _assert_carried(client.proj_agents / 'ro-agent.md')


def test_team_overwrite_keeps_unmanaged_keys(client):
    member = {'mode': 'new', 'name': 'ro-agent', 'agent_name': 'Reed',
              'description': 'Replaced description', 'body': 'You are the replaced body.',
              'avatar': 'fig:courier', 'scope': 'global',
              'engine': {'provider': 'claude', 'model': 'claude-sonnet-5', 'effort': 'high'}}
    r = client.post('/api/characters/team', json={
        'members': [member], 'project_id': 'tcarry', 'overwrite': ['global:ro-agent']})
    assert r.status_code == 201, r.get_json()
    path = client.global_dir / 'ro-agent.md'
    assert 'description: Replaced description' in _front(path).splitlines()
    assert 'You are the replaced body.' in path.read_text(encoding='utf-8')
    _assert_carried(path)


def test_a_new_file_is_written_as_before(client):
    from mc import characters as ch
    ch.write_character('global', 'fresh-one', 'Fresh', 'Body.', engine={'model': 'claude-sonnet-5'})
    text = (client.global_dir / 'fresh-one.md').read_text(encoding='utf-8')
    assert text == '---\nname: fresh-one\ndescription: Fresh\nmodel: claude-sonnet-5\n---\nBody.\n'


def test_legacy_skills_key_is_still_rewritten_not_duplicated(client):
    path = client.global_dir / 'old-skills.md'
    path.write_text('---\nname: old-skills\ndescription: d\nskills: frontend-design\ntools: Read\n---\nB.\n',
                    encoding='utf-8')
    from mc import characters as ch
    ch.write_character('global', 'old-skills', 'd2', 'B.', overwrite=True)
    front = _front(path).splitlines()
    assert 'clayrune_skills: frontend-design' in front
    assert 'skills: frontend-design' not in front
    assert 'tools: Read' in front
