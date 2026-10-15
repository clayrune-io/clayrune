"""Tests for the built-in character installer and starter roster.

Determinism: mc.characters.GLOBAL_AGENTS_DIR is repointed at tmp_path; no real
~/.claude is touched. Synthetic fixtures exercise the install mechanics;
the real roster checks fresh installs, upgrades and user-owned collisions.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import characters as ch  # noqa: E402


CLAYDO_MD = """---
name: claydo
description: Clayrune's built-in base agent.
agent_name: Claydo
avatar: fig:newcomer
---
You are Claydo, the base agent that ships with every Clayrune install.
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')
    builtin_root = tmp_path / 'builtin'
    builtin_root.mkdir()
    (builtin_root / 'claydo.md').write_text(CLAYDO_MD, encoding='utf-8')
    return {'builtin_root': builtin_root, 'global_dir': tmp_path / 'agents-global'}


class TestInstall:
    def test_fresh_install(self, env):
        result = ch.install_builtin_characters(env['builtin_root'])
        assert result['installed'] == ['claydo']
        assert (env['global_dir'] / 'claydo.md').exists()
        assert (env['global_dir'] / 'claydo.md.mc-builtin-hash').exists()

    def test_installed_character_resolves_name_and_avatar(self, env):
        ch.install_builtin_characters(env['builtin_root'])
        rec = ch.read_character('global', 'claydo')
        assert rec is not None
        assert rec['name'] == 'claydo'
        assert rec['agent_name'] == 'Claydo'
        assert rec['avatar'] == 'fig:newcomer'

    def test_dispatchable_as_character_in_roster(self, env):
        ch.install_builtin_characters(env['builtin_root'])
        items = ch.list_characters()
        names = [r['name'] for r in items]
        assert 'claydo' in names

    def test_idempotent_second_run_is_noop(self, env):
        ch.install_builtin_characters(env['builtin_root'])
        result = ch.install_builtin_characters(env['builtin_root'])
        assert result == {'installed': [], 'updated': [], 'preserved': [],
                          'skipped': ['claydo']}

    def test_updates_when_source_changes(self, env):
        ch.install_builtin_characters(env['builtin_root'])
        (env['builtin_root'] / 'claydo.md').write_text(
            CLAYDO_MD + '\nAn extra line.\n', encoding='utf-8')
        result = ch.install_builtin_characters(env['builtin_root'])
        assert result['updated'] == ['claydo']
        assert 'An extra line.' in (env['global_dir'] / 'claydo.md').read_text(encoding='utf-8')

    def test_user_edit_after_install_is_preserved(self, env):
        ch.install_builtin_characters(env['builtin_root'])
        dest = env['global_dir'] / 'claydo.md'
        edited = dest.read_text(encoding='utf-8').replace('base agent', 'best agent')
        dest.write_text(edited, encoding='utf-8')

        (env['builtin_root'] / 'claydo.md').write_text(
            CLAYDO_MD + '\nSource changed too.\n', encoding='utf-8')
        result = ch.install_builtin_characters(env['builtin_root'])
        assert result['preserved'] == ['claydo']
        assert dest.read_text(encoding='utf-8') == edited

    def test_never_clobbers_a_preexisting_user_character_of_the_same_name(self, env):
        # Simulates a user who already has ~/.claude/agents/claydo.md of their
        # own (hand-written, no MC marker) BEFORE this install ever ran.
        env['global_dir'].mkdir(parents=True)
        user_text = '---\nname: claydo\ndescription: my own thing\n---\nMine.\n'
        (env['global_dir'] / 'claydo.md').write_text(user_text, encoding='utf-8')

        result = ch.install_builtin_characters(env['builtin_root'])
        assert result['skipped'] == ['claydo']
        assert (env['global_dir'] / 'claydo.md').read_text(encoding='utf-8') == user_text
        assert not (env['global_dir'] / 'claydo.md.mc-builtin-hash').exists()

    def test_missing_builtin_root_is_a_noop(self, env, tmp_path):
        result = ch.install_builtin_characters(tmp_path / 'does-not-exist')
        assert result == {'installed': [], 'updated': [], 'preserved': [], 'skipped': []}


BUILTIN_ROOT = PROJECT_ROOT / 'data' / 'agents' / 'builtin'
SPECIALISTS = (
    'builder', 'code-reviewer', 'silent-failure-diagnostician', 'ui-fixer',
    'security-privacy-auditor', 'researcher', 'prd-writer',
)
STARTER_ROSTER = ('claydo', 'brainstorm', *SPECIALISTS)


class TestStarterRoster:
    def test_fresh_install_resolves_every_starter(self, env, monkeypatch):
        monkeypatch.setattr(ch, 'AVATARS_DIR', PROJECT_ROOT / 'assets' / 'avatars')
        figures = ch.list_figures()
        result = ch.install_builtin_characters(BUILTIN_ROOT)
        assert set(result['installed']) == set(STARTER_ROSTER)
        assert not result['updated'] and not result['preserved'] and not result['skipped']
        assert {rec['name'] for rec in ch.list_characters()} == set(STARTER_ROSTER)
        for name in STARTER_ROSTER:
            rec = ch.read_character('global', name, include_body=True)
            assert rec is not None
            assert rec['name'] == name
            assert rec['agent_name'] and rec['description'] and rec['body']
            assert ch.avatar_figure(rec['avatar']) in figures
            assert (env['global_dir'] / f'{name}.md').read_bytes() == (
                BUILTIN_ROOT / f'{name}.md').read_bytes()
        assert ch.install_builtin_characters(BUILTIN_ROOT) == {
            'installed': [], 'updated': [], 'preserved': [],
            'skipped': sorted(STARTER_ROSTER),
        }

    @pytest.mark.parametrize('name', ('claydo', *SPECIALISTS))
    def test_starters_inherit_engine_without_pins(self, env, name):
        ch.install_builtin_characters(BUILTIN_ROOT)
        rec = ch.read_character('global', name, include_body=True)
        assert not rec.get('engine')
        assert len(rec['body'].encode('utf-8')) <= ch.MAX_BODY_BYTES

    @pytest.mark.parametrize('name', STARTER_ROSTER)
    def test_same_name_user_file_is_never_adopted_or_overwritten(self, env, name):
        env['global_dir'].mkdir(parents=True)
        dest = env['global_dir'] / f'{name}.md'
        user_bytes = (
            f'---\nname: {name}\ndescription: My own specialist\n'
            'agent_name: Custom\n---\nKeep my instructions.\n'
        ).encode('utf-8')
        dest.write_bytes(user_bytes)
        for _ in range(2):
            result = ch.install_builtin_characters(BUILTIN_ROOT)
            assert name in result['skipped']
            assert dest.read_bytes() == user_bytes
            assert not (env['global_dir'] / f'{name}.md.mc-builtin-hash').exists()
            assert ch.read_character('global', name)['agent_name'] == 'Custom'
        assert {rec['name'] for rec in ch.list_characters()} == set(STARTER_ROSTER)

    @pytest.mark.parametrize('name', STARTER_ROSTER)
    def test_roster_upgrade_preserves_user_edit_and_its_original_marker(self, env, name):
        ch.install_builtin_characters(BUILTIN_ROOT)
        dest = env['global_dir'] / f'{name}.md'
        marker = env['global_dir'] / f'{name}.md.mc-builtin-hash'
        original_marker = marker.read_bytes()
        edited = dest.read_bytes() + b'\nUser customization.\n'
        dest.write_bytes(edited)
        for src in BUILTIN_ROOT.glob('*.md'):
            (env['builtin_root'] / src.name).write_bytes(
                src.read_bytes() + b'\nUpdated distribution.\n')
        result = ch.install_builtin_characters(env['builtin_root'])
        assert result['preserved'] == [name]
        assert set(result['updated']) == set(STARTER_ROSTER) - {name}
        assert dest.read_bytes() == edited
        assert marker.read_bytes() == original_marker

    def test_existing_two_character_install_gains_specialists(self, env):
        # The previous release had only Claydo and Brainstorm. An unedited
        # Claydo must get the new delegation instructions on upgrade.
        ch.install_builtin_characters(env['builtin_root'])
        brainstorm = BUILTIN_ROOT / 'brainstorm.md'
        (env['builtin_root'] / brainstorm.name).write_bytes(brainstorm.read_bytes())
        ch.install_builtin_characters(env['builtin_root'])
        result = ch.install_builtin_characters(BUILTIN_ROOT)
        assert set(result['installed']) == set(SPECIALISTS)
        assert result['updated'] == ['claydo']
        assert result['skipped'] == ['brainstorm']
        assert not result['preserved']
        assert (env['global_dir'] / 'claydo.md').read_bytes() == (
            BUILTIN_ROOT / 'claydo.md').read_bytes()

    @pytest.mark.parametrize('name', SPECIALISTS)
    def test_nested_user_character_is_not_shadowed_by_a_new_builtin(self, env, name):
        nested = env['global_dir'] / 'community-pack' / f'{name}.md'
        nested.parent.mkdir(parents=True)
        user_bytes = (
            f'---\nname: {name}\ndescription: My imported specialist\n'
            'agent_name: Imported\n---\nKeep this imported persona.\n'
        ).encode('utf-8')
        nested.write_bytes(user_bytes)
        for _ in range(2):
            result = ch.install_builtin_characters(BUILTIN_ROOT)
            assert name in result['skipped']
            assert nested.read_bytes() == user_bytes
            assert not (env['global_dir'] / f'{name}.md').exists()
            assert not (env['global_dir'] / f'{name}.md.mc-builtin-hash').exists()
            assert ch.read_character('global', name)['agent_name'] == 'Imported'
        assert len([rec for rec in ch.list_characters() if rec['name'] == name]) == 1
