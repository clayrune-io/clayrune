"""Unit tests for mc.characters.install_builtin_characters — the Claydo
built-in agent type install path (docs/AGENT_TYPES_DESIGN.md, MC-895 follow-up).

Determinism: mc.characters.GLOBAL_AGENTS_DIR is repointed at tmp_path; no real
~/.claude is touched. A synthetic builtin_root is used rather than the real
data/agents/builtin/ so these tests don't drift if claydo.md's body changes.
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
