"""Agents may use emojis — opt-in (Ron, 2026-10-02).

Global `agent_emojis_enabled` (default OFF) x a character's own `emojis`
(inherit / on / off). When it resolves ON the agent's context gets ONE short
line in the character/voice section; OFF adds nothing.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LINE = ("You may use an occasional emoji where it adds warmth; never in code, "
        "commit messages, or public/published text.")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint deps
    from mc import characters as ch
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')
    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'agents').mkdir(parents=True)
    for name, em in (('inherit', None), ('on', 'on'), ('off', 'off')):
        ch.write_character('project', f'p-{name}', 'A persona.', 'You are terse.',
                           project_path=str(proj_path), emojis=em)
    project = {'id': 'te', 'name': 'TE', 'project_path': str(proj_path),
               'provider': 'claude'}

    def ctx(global_on, character):
        monkeypatch.setitem(ar.state.CONFIG, 'agent_emojis_enabled', global_on)
        meta, body = ar._resolve_character(str(proj_path), character, project)
        return ar._build_agent_context(project, character_body=body)

    return {'ar': ar, 'ctx': ctx}


@pytest.mark.parametrize('global_on,character,expected', [
    (False, 'project:p-inherit', False),
    (True,  'project:p-inherit', True),
    (False, 'project:p-on',      True),
    (True,  'project:p-on',      True),
    (False, 'project:p-off',     False),
    (True,  'project:p-off',     False),
])
def test_global_x_character_matrix(env, global_on, character, expected):
    text = env['ctx'](global_on, character)
    assert (LINE in text) is expected
    assert text.count(LINE) <= 1


def test_line_sits_inside_the_character_section(env):
    text = env['ctx'](True, 'project:p-inherit')
    head, _, tail = text.partition('--- CHARACTER (active persona for this chat) ---')
    assert LINE not in head
    # Voice section: before the "owns your VOICE" coda, after the persona body.
    assert tail.index('You are terse.') < tail.index(LINE) < tail.index('owns your VOICE')


def test_no_persona_follows_the_global_setting_alone(env, monkeypatch):
    ar = env['ar']
    project = {'id': 'te', 'name': 'TE', 'provider': 'claude'}
    monkeypatch.setitem(ar.state.CONFIG, 'agent_emojis_enabled', True)
    assert LINE in ar._build_agent_context(project)
    monkeypatch.setitem(ar.state.CONFIG, 'agent_emojis_enabled', False)
    assert LINE not in ar._build_agent_context(project)


def test_default_is_off_when_the_key_is_absent(env, monkeypatch):
    ar = env['ar']
    monkeypatch.delitem(ar.state.CONFIG, 'agent_emojis_enabled', raising=False)
    assert ar._emoji_line('') == ''


def test_config_default_and_editable():
    import ast
    from mc.blueprints import settings_routes as sr
    tree = ast.parse((PROJECT_ROOT / 'server.py').read_text(encoding='utf-8'))
    defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_load_config':
            for stmt in ast.walk(node):
                if (isinstance(stmt, ast.Assign)
                        and any(getattr(t, 'id', '') == 'defaults' for t in stmt.targets)
                        and isinstance(stmt.value, ast.Dict)):
                    for k, v in zip(stmt.value.keys, stmt.value.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            defaults[k.value] = v.value
    assert defaults.get('agent_emojis_enabled') is False
    assert 'agent_emojis_enabled' in sr._CONFIG_EDITABLE_KEYS
    assert 'agent_emojis_enabled' in sr._RESPAWN_TRIGGER_KEYS
