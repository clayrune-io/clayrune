"""`skills` on a character (mc/characters.py) — a declared toolkit.

Ron, 2026-08-24. It is a DECLARATION and never a gate: Claude Code decides which
skills it exposes and nothing here narrows that. What it buys is that an agent
is told which of them are its own, and that a bench card can say what a type can
reach for rather than only what it is for.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def chars(tmp_path, monkeypatch):
    from mc import characters as c
    monkeypatch.setattr(c, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents')
    (tmp_path / 'agents').mkdir()
    return c


def test_a_comma_separated_string_becomes_a_list(chars):
    """The file holds a string because the frontmatter parser has no list type —
    it hands `['a','b']` back as a string that iterates character by character,
    which is the bug `triggers` on a position note already avoids this way."""
    assert chars.clean_skills('audit-doc, frontend-design') == \
        ['audit-doc', 'frontend-design']


def test_a_json_caller_may_send_a_real_list(chars):
    """Both arrive — the editor sends a string, an API caller sends a list —
    and rejecting either would only move the bug to the caller."""
    assert chars.clean_skills(['audit-doc', 'frontend-design']) == \
        ['audit-doc', 'frontend-design']


def test_names_are_normalised_and_deduplicated(chars):
    assert chars.clean_skills('Audit-Doc , audit-doc,  FRONTEND-design ') == \
        ['audit-doc', 'frontend-design']


def test_a_toolkit_is_bounded(chars):
    assert len(chars.clean_skills(','.join(f's{i}' for i in range(40)))) == \
        chars.MAX_SKILLS


def test_it_round_trips_through_the_file(chars):
    chars.write_character('global', 'fenn', 'reviews code', 'body here',
                          skills='audit-doc, frontend-design')
    rec = chars.read_character('global', 'fenn')
    assert rec['skills'] == ['audit-doc', 'frontend-design']


def test_an_unrelated_edit_does_not_wipe_the_toolkit(chars):
    """Same three-state contract as agent_name and avatar. The file is rewritten
    whole, so "leave alone" has to be an explicit carry-forward — not setting
    the key DELETES it, which is how a plain description edit once wiped a name
    the editor never showed."""
    chars.write_character('global', 'fenn', 'reviews code', 'body',
                          skills='audit-doc')
    chars.write_character('global', 'fenn', 'reviews code better', 'body',
                          overwrite=True)
    assert chars.read_character('global', 'fenn')['skills'] == ['audit-doc']


def test_an_empty_value_clears_it(chars):
    chars.write_character('global', 'fenn', 'reviews code', 'body',
                          skills='audit-doc')
    chars.write_character('global', 'fenn', 'reviews code', 'body',
                          overwrite=True, skills='')
    assert 'skills' not in chars.read_character('global', 'fenn')


# ── the on-disk key (2026-10-12) ────────────────────────────────────────────
# Claude Code preloads the full SKILL.md body of every skill listed under a
# native `skills:` frontmatter key into the subagent, so the file must not use it.

def _frontmatter(path):
    from mc import skills as sk
    return sk.parse_skill_md(path.read_text(encoding='utf-8'))[0]


def test_the_file_holds_clayrune_skills_not_the_native_key(chars, tmp_path):
    chars.write_character('global', 'fenn', 'reviews code', 'body',
                          skills='audit-doc, frontend-design')
    meta = _frontmatter(tmp_path / 'agents' / 'fenn.md')
    assert meta.get('clayrune_skills') == 'audit-doc, frontend-design'
    assert 'skills' not in meta


def test_a_legacy_skills_key_is_read_and_warned_about(chars, tmp_path, monkeypatch):
    chars._legacy_skills_warned.clear()
    logged = []
    import mc.core
    monkeypatch.setattr(mc.core, '_log', lambda msg, **kw: logged.append(msg))
    f = tmp_path / 'agents' / 'old.md'
    f.write_text('---\nname: old\ndescription: d\nskills: audit-doc, frontend-design\n---\nbody\n',
                 encoding='utf-8')
    assert chars.read_character('global', 'old')['skills'] == \
        ['audit-doc', 'frontend-design']
    assert any('legacy' in m and 'old.md' in m for m in logged)
    # Reading never rewrites the user's file.
    assert 'skills: audit-doc' in f.read_text(encoding='utf-8')


def test_the_next_write_migrates_a_legacy_key(chars, tmp_path):
    f = tmp_path / 'agents' / 'old.md'
    f.write_text('---\nname: old\ndescription: d\nskills: audit-doc\n---\nbody\n',
                 encoding='utf-8')
    chars.write_character('global', 'old', 'd2', 'body', overwrite=True)
    meta = _frontmatter(f)
    assert meta.get('clayrune_skills') == 'audit-doc'
    assert 'skills' not in meta


def test_the_new_key_wins_over_a_stale_legacy_one(chars, tmp_path):
    f = tmp_path / 'agents' / 'both.md'
    f.write_text('---\nname: both\ndescription: d\nskills: stale\n'
                 'clayrune_skills: fresh\n---\nbody\n', encoding='utf-8')
    assert chars.read_character('global', 'both')['skills'] == ['fresh']


# ── what the agent is told ──────────────────────────────────────────────────

def test_the_prompt_names_a_types_own_skills(tmp_path):
    """A list of sixty available skills says nothing about who you are; three
    named ones do."""
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    ctx = ar._build_agent_context(
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)},
        task='anything', character_name='Fenn', session_id='s1',
        character_skills=['audit-doc', 'frontend-design'])
    assert '`audit-doc`' in ctx and '`frontend-design`' in ctx


def test_it_is_worded_as_a_declaration_not_a_fence(tmp_path):
    """Claude Code decides which skills exist and nothing here narrows that.
    Wording it as a restriction would make the prompt claim an enforcement the
    system does not have — and an agent that believes it cannot reach a skill
    it can reach is worse off than one told nothing."""
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    ctx = ar._build_agent_context(
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)},
        task='anything', character_name='Fenn', session_id='s1',
        character_skills=['audit-doc'])
    assert 'does not fence you in' in ctx


def test_no_toolkit_means_no_line(tmp_path):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    ctx = ar._build_agent_context(
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)},
        task='anything', character_name='Fenn', session_id='s1')
    assert 'Your own skills' not in ctx
