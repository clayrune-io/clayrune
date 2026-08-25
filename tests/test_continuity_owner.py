"""Continuity is per-AGENT working state (mc/memory.py, DAVE_DESIGN §3/§7).

Notes and positions are shared across every agent on a project, deliberately: a
ruling Vector recorded must bind Dave, or positions would not work at all. But
"what I was part-way through" is worker state, and one shared set of five slots
meant every agent's write silently replaced the others'. Measured on this
project the day this shipped: five threads from four different sessions, none
marked done, two of them describing work that had already landed — and each
session was served all five as its own.

These tests pin the split, and the two things that make it safe to upgrade into:
a pre-owner record keeps working, and a claimed line does not duplicate.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

P = {'id': 'p1'}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, tmp_path


# ── the defect this exists to fix ───────────────────────────────────────────

def test_one_agents_write_does_not_erase_anothers(env):
    """THE regression test. Before owners, the second write won outright."""
    mem, _ = env
    mem.write_continuity(P, threads=['Dave: shipping phase 4'], owner='Dave')
    mem.write_continuity(P, threads=['Vector: rewriting the floor'], owner='Vector')

    assert mem.read_continuity(P, owner='Dave')['threads'] == \
        ['Dave: shipping phase 4']
    assert mem.read_continuity(P, owner='Vector')['threads'] == \
        ['Vector: rewriting the floor']
    merged = mem.read_continuity(P)['threads']
    assert sorted(merged) == ['Dave: shipping phase 4', 'Vector: rewriting the floor']


def test_an_agent_clears_only_its_own_slot(env):
    mem, _ = env
    mem.write_continuity(P, threads=['dave thread'], owner='Dave')
    mem.write_continuity(P, threads=['vector thread'], owner='Vector')
    mem.write_continuity(P, threads=[], owner='Dave')
    assert mem.read_continuity(P, owner='Dave')['threads'] == []
    assert mem.read_continuity(P, owner='Vector')['threads'] == ['vector thread']


def test_caps_are_per_agent_not_shared(env):
    """Five slots each. A busy agent must not starve a quiet one."""
    mem, _ = env
    mem.write_continuity(P, threads=[f'd{i}' for i in range(9)], owner='Dave')
    mem.write_continuity(P, threads=['v1'], owner='Vector')
    assert len(mem.read_continuity(P, owner='Dave')['threads']) == mem._CONT_MAX_THREADS
    assert mem.read_continuity(P, owner='Vector')['threads'] == ['v1']


# ── the shared bucket: the project's, not a rival agent's ───────────────────

def test_a_pre_owner_record_still_reaches_every_agent(env):
    """Upgrade safety. A record written before owners existed parses into the
    '' bucket, and exiling that to the capped "another agent" block would have
    made every existing install lose its continuity on the day this shipped."""
    mem, _ = env
    mem.write_continuity(P, threads=['legacy thread'],
                         understanding='where things stood')  # no owner
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['threads'] == ['legacy thread']
    assert rec['understanding'] == 'where things stood'
    assert 'CONTINUITY' in mem.render_continuity(P, owner='Dave')


def test_claiming_a_shared_line_moves_it_out_of_shared(env):
    """An agent rewrites the record it was SHOWN, which includes the shared
    lines. Leaving the original in place would duplicate it forever."""
    mem, _ = env
    mem.write_continuity(P, threads=['legacy thread'])
    mem.write_continuity(P, threads=['legacy thread', 'dave thread'], owner='Dave')
    by_owner = mem.read_continuity(P)['by_owner']
    assert by_owner.get('', {}).get('threads', []) == []
    assert by_owner['Dave']['threads'] == ['legacy thread', 'dave thread']
    assert mem.read_continuity(P, owner='Dave')['threads'] == \
        ['legacy thread', 'dave thread']       # not duplicated


def test_a_human_edit_never_overwrites_an_agents_slots(env):
    """The Memory modal writes with no owner. Correcting the record must not
    silently blank what Dave is part-way through."""
    mem, _ = env
    mem.write_continuity(P, threads=['dave thread'], owner='Dave')
    mem.write_continuity(P, threads=['a human note'])   # the modal
    assert mem.read_continuity(P, owner='Dave')['threads'] == \
        ['dave thread', 'a human note']
    assert mem.read_continuity(P)['by_owner']['Dave']['threads'] == ['dave thread']


# ── rendering: yours in full, theirs named ──────────────────────────────────

def test_another_agents_work_is_shown_but_never_as_yours(env):
    mem, _ = env
    mem.write_continuity(P, threads=['dave is refactoring memory.py'], owner='Dave')
    out = mem.render_continuity(P, owner='Vector')
    assert 'ANOTHER AGENT' in out
    assert 'Dave — dave is refactoring memory.py' in out
    assert 'IN FLIGHT — dave is refactoring memory.py' not in out


def test_other_agents_lines_are_capped(env):
    """Context, not your list."""
    mem, _ = env
    for who in ('Dave', 'Quill', 'Fenn'):
        mem.write_continuity(P, threads=[f'{who} a', f'{who} b'], owner=who)
    out = mem.render_continuity(P, owner='Vector')
    assert out.count('  • ') == mem._CONT_MAX_OTHER_LINES


def test_render_with_no_owner_keeps_the_merged_view(env):
    """The human surface and older callers still see everything."""
    mem, _ = env
    mem.write_continuity(P, threads=['dave thread'], owner='Dave')
    out = mem.render_continuity(P)
    assert 'IN FLIGHT — dave thread' in out
    assert 'ANOTHER AGENT' not in out


# ── eviction stays structural ───────────────────────────────────────────────

def test_only_the_most_recent_agents_keep_a_bucket(env):
    """Same lever as the slot caps — no remover, no curator."""
    mem, _ = env
    for i in range(mem._CONT_MAX_OWNERS + 3):
        mem.write_continuity(P, threads=[f'thread {i}'], owner=f'Agent{i}')
    by_owner = mem.read_continuity(P)['by_owner']
    assert len(by_owner) == mem._CONT_MAX_OWNERS
    assert 'Agent0' not in by_owner
    assert f'Agent{mem._CONT_MAX_OWNERS + 2}' in by_owner


# ── the two sides must agree on who the owner is ────────────────────────────

def test_session_owner_matches_the_name_the_prompt_uses(env):
    """If the write side files a bucket the read side never asks for, every
    agent silently gets an empty record — and nothing anywhere would say so."""
    mem, _ = env
    from mc import state
    assert mem._session_owner({'character': {'name': 'dave-file',
                                             'agent_name': 'Dave'}}) == 'Dave'
    state.CONFIG['agent_name'] = 'Vector'
    try:
        assert mem._session_owner({}) == 'Vector'
        assert mem._session_owner({'character': None}) == 'Vector'
    finally:
        state.CONFIG.pop('agent_name', None)


def test_the_prompt_builder_asks_for_the_same_bucket(env):
    """End to end: what `_session_owner` files, `_build_agent_context` reads."""
    from mc.blueprints import agent_routes as ar
    from mc import state
    mem, tmp = env
    owner = mem._session_owner({'character': {'agent_name': 'Dave'}})
    mem.write_continuity(P, threads=['dave is mid-refactor'], owner=owner)

    state.CONFIG['agent_name'] = 'Vector'
    try:
        ctx = ar._build_agent_context(
            {'id': 'p1', 'name': 'P1', 'project_path': str(tmp)},
            task='anything', character_name='Dave')
        assert 'IN FLIGHT — dave is mid-refactor' in ctx
        assert 'ANOTHER AGENT' not in ctx

        other = ar._build_agent_context(
            {'id': 'p1', 'name': 'P1', 'project_path': str(tmp)},
            task='anything')                      # no character → Vector
        assert 'ANOTHER AGENT' in other
        assert 'Dave — dave is mid-refactor' in other
    finally:
        state.CONFIG.pop('agent_name', None)


# ── an ephemeral owns nothing ───────────────────────────────────────────────

def test_a_global_type_owns_no_bucket(env):
    """A global character is ephemeral by construction — it can work on any
    project precisely because it keeps nothing between calls. Giving it a
    bucket made its half-finished thought durable project working state."""
    mem, _ = env
    assert mem._session_owner(
        {'character': {'name': 'code-reviewer', 'agent_name': 'Fenn',
                       'scope': 'global'}}) is None
    assert mem._session_owner(
        {'character': {'name': 'dave', 'agent_name': 'Dave',
                       'scope': 'project'}}) == 'Dave'


def test_none_is_not_the_shared_bucket(env):
    """`owner=None` must mean "writes nothing", never "writes to ''". The
    shared bucket is read by EVERY agent, so falling through to it is the worst
    of the three outcomes, not a safe default."""
    mem, _ = env
    assert mem._cont_owner_key(None) == ''      # the coercion that would bite
    src = (Path(mem.__file__).read_text(encoding='utf-8'))
    assert "snap.get('owner') is not None" in src, \
        'the checkpoint no longer gates continuity on owner-is-None'


def test_helpers_passing_through_cannot_evict_the_projects_own_agent(env):
    """`_CONT_MAX_OWNERS` is 4 on least-recently-written eviction, so three
    ephemerals writing buckets would push the project's own agent out of its
    own record."""
    mem, _ = env
    mem.write_continuity(P, threads=['dave is mid-refactor'], owner='Dave')
    for who in ('Fenn', 'Quill', 'Marlow', 'Scout'):
        owner = mem._session_owner({'character': {'name': who.lower(),
                                                  'agent_name': who,
                                                  'scope': 'global'}})
        assert owner is None, f'{who} claimed a bucket'
    assert mem.read_continuity(P, owner='Dave')['threads'] == ['dave is mid-refactor']


def test_an_ephemeral_still_READS_the_record(env):
    """It must see what the project is part-way through — that is how it avoids
    duplicating the work. Only the write side is closed."""
    mem, _ = env
    mem.write_continuity(P, threads=['dave is mid-refactor'], owner='Dave')
    out = mem.render_continuity(P, owner='Fenn')
    assert 'dave is mid-refactor' in out
