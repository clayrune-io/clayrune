"""A resumed session must pick up config that changed while it was alive.

Ron, 2026-08-24, right after 22 skills were relocated out of the global pool:
*"we need to guarantee there is a way in which when invoking Dave for a project
on same another time it will grab any changes that happened."*

`_respawn_sysprompt_args` preferred a blob stashed ONCE at spawn, so a
long-lived chat froze its rules, memory index, roster and skill list at the
moment it started — forever. The stash exists for a real reason (byte-identical
content keeps the resumed prefix cache-friendly), so the fix is invalidate-on-
change rather than rebuild-every-turn. These tests pin both halves: it must NOT
rebuild when nothing moved, and it MUST when something did.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    from mc import memory as mem
    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'skills').mkdir(parents=True)
    memdir = tmp_path / 'mem'
    memdir.mkdir()
    (memdir / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: memdir / 'MEMORY.md')
    return ar, {'id': 'p1', 'name': 'P1', 'project_path': str(proj_path)}, tmp_path


# ── the fingerprint ─────────────────────────────────────────────────────────

def test_it_is_stable_when_nothing_changes(env):
    """If this drifted on its own, every turn would rebuild and the prompt cache
    would never hit — which is the whole reason the stash exists."""
    ar, project, _ = env
    assert ar._context_fingerprint(project) == ar._context_fingerprint(project)


def test_a_relocated_skill_moves_it(env):
    """The exact case that prompted this: 22 skills left the global pool and
    every live session kept listing them."""
    ar, project, tmp = env
    before = ar._context_fingerprint(project)
    (Path(project['project_path']) / '.claude' / 'skills' / 'new-skill').mkdir()
    assert ar._context_fingerprint(project) != before


def test_editing_the_rules_moves_it(env):
    ar, project, _ = env
    rules = Path(project['project_path']) / 'AGENT_RULES.md'
    rules.write_text('be careful\n', encoding='utf-8')
    before = ar._context_fingerprint(project)
    rules.write_text('be careful, and also this\n', encoding='utf-8')
    assert ar._context_fingerprint(project) != before


def test_a_new_memory_note_moves_it(env):
    ar, project, tmp = env
    before = ar._context_fingerprint(project)
    (tmp / 'mem' / 'position_something.md').write_text('---\nname: x\n---\n',
                                                       encoding='utf-8')
    assert ar._context_fingerprint(project) != before


def test_a_config_value_that_reaches_the_prompt_moves_it(env):
    ar, project, _ = env
    from mc import state
    before = ar._context_fingerprint(project)
    state.CONFIG['agent_name'] = 'Somebody Else'
    try:
        assert ar._context_fingerprint(project) != before
    finally:
        state.CONFIG.pop('agent_name', None)


def test_it_never_raises_on_a_project_with_no_paths(env):
    """Best-effort: a fingerprint failure must degrade to reusing the stash,
    never block a turn."""
    ar, _, _ = env
    assert isinstance(ar._context_fingerprint({}), str)
    assert isinstance(ar._context_fingerprint(None), str)


# ── the respawn decision ────────────────────────────────────────────────────

def test_an_unchanged_project_reuses_the_stash_byte_for_byte(env):
    """Rebuilding when nothing moved would break the resumed prefix cache on
    every turn, for nothing."""
    ar, project, _ = env
    sess = {'session_id': 's1', '_system_prompt': 'STASHED CONTEXT',
            '_system_prompt_fp': ar._context_fingerprint(project)}
    ar._respawn_sysprompt_args(sess, project, 'next message')
    assert sess['_system_prompt'] == 'STASHED CONTEXT'


def test_a_changed_project_rebuilds_and_re_stamps(env):
    ar, project, _ = env
    sess = {'session_id': 's1', '_system_prompt': 'STALE CONTEXT',
            '_system_prompt_fp': 'a-fingerprint-from-before'}
    ar._respawn_sysprompt_args(sess, project, 'next message')
    assert sess['_system_prompt'] != 'STALE CONTEXT'
    assert sess['_system_prompt_fp'] == ar._context_fingerprint(project)


def test_a_rebuild_keeps_the_sessions_persona(env, monkeypatch):
    """The rebuild path had never really run, because the stash was almost
    always present — and it rebuilt with NO character. A resumed persona chat
    that ever fell through would have silently lost its persona mid-thread."""
    ar, project, _ = env
    monkeypatch.setattr(ar, '_resolve_character',
                        lambda pp, spec, proj=None: (
                            {'agent_name': 'Fenn', 'skills': ['audit-doc']},
                            'YOU ARE FENN'))
    sess = {'session_id': 's1', 'character': {'name': 'code-reviewer',
                                              'scope': 'global'},
            '_system_prompt': 'STALE', '_system_prompt_fp': 'old'}
    ar._respawn_sysprompt_args(sess, project, 'next message')
    assert 'YOU ARE FENN' in sess['_system_prompt']
    assert 'Fenn' in sess['_system_prompt']
    assert 'audit-doc' in sess['_system_prompt']


def test_a_rebuild_keeps_the_session_id_so_it_can_still_name_itself(env):
    ar, project, _ = env
    sess = {'session_id': 'sid-42', '_system_prompt': 'STALE',
            '_system_prompt_fp': 'old'}
    ar._respawn_sysprompt_args(sess, project, 'next message')
    assert 'sid-42' in sess['_system_prompt']


def test_a_failed_rebuild_falls_back_to_the_stale_context(env, monkeypatch):
    """A stale context beats no context: the alternative is a turn with no
    rules and no memory at all."""
    ar, project, _ = env

    def boom(*a, **kw):
        raise RuntimeError('build broke')
    monkeypatch.setattr(ar, '_build_agent_context', boom)
    sess = {'session_id': 's1', '_system_prompt': 'STALE CONTEXT',
            '_system_prompt_fp': 'old'}
    args, path = ar._respawn_sysprompt_args(sess, project, 'msg')
    assert sess['_system_prompt'] == 'STALE CONTEXT'
    assert args, 'the turn went out with no context at all'
