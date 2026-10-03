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
    # ...but its "where things stand" is NOT Dave's own status (3db65948).
    assert rec['understanding'] == ''
    assert rec['shared_understanding'] == 'where things stood'
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


# ── a dispatched helper owns nothing; a direct global chat owns a bucket ────

GLOBAL_DAVE = {'name': 'dave', 'agent_name': 'Dave', 'scope': 'global'}


def test_a_dispatched_global_type_owns_no_bucket(env):
    """A dispatched helper is ephemeral by construction — it can work on any
    project precisely because it keeps nothing between calls. Giving it a
    bucket made its half-finished thought durable project working state."""
    mem, _ = env
    fenn = {'name': 'code-reviewer', 'agent_name': 'Fenn', 'scope': 'global'}
    # spawner callback armed (notify_session) / workflow step / any trigger
    assert mem._session_owner({'character': fenn, 'trigger_type': 'dispatch',
                               '_notify_session': 'abc123'}) is None
    assert mem._session_owner({'character': fenn, 'trigger_type': 'manual',
                               '_notify_session': 'abc123'}) is None
    assert mem._session_owner({'character': fenn, 'trigger_type': 'manual',
                               '_notify_workflow': {'run_id': 'r', 'step': 's'}}
                              ) is None
    for trig in ('dispatch', 'schedule', 'workflow', 'hivemind_orchestrator',
                 'steward'):
        assert mem._session_owner({'character': fenn, 'trigger_type': trig}) is None, trig
    assert mem._session_owner(
        {'character': {'name': 'dave', 'agent_name': 'Dave', 'scope': 'project'},
         'trigger_type': 'dispatch'}) == 'Dave'


def test_a_direct_global_chat_owns_a_bucket_and_reads_it_back(env):
    """3db65948: Dave is a GLOBAL persona used as the project's main chat. The
    write side returned None for every global, so his continuity was never
    written while the read side asked for 'Dave' anyway."""
    mem, _ = env
    for sess in ({'character': GLOBAL_DAVE},
                 {'character': GLOBAL_DAVE, 'trigger_type': 'manual',
                  '_notify_session': '', '_notify_workflow': None}):
        assert mem._session_owner(sess) == 'Dave', sess
    owner = mem._session_owner({'character': GLOBAL_DAVE, 'trigger_type': 'manual'})
    mem.write_continuity(P, threads=['dave is mid-refactor'],
                         understanding='phase 2 landed', owner=owner)
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['threads'] == ['dave is mid-refactor']
    assert rec['understanding'] == 'phase 2 landed'
    out = mem.render_continuity(P, owner='Dave')
    assert 'IN FLIGHT — dave is mid-refactor' in out
    assert 'Where things stand: phase 2 landed' in out


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
                                                  'scope': 'global'},
                                    'trigger_type': 'dispatch',
                                    '_notify_session': 'abc123'})
        assert owner is None, f'{who} claimed a bucket'
    assert mem.read_continuity(P, owner='Dave')['threads'] == ['dave is mid-refactor']


def test_an_ephemeral_still_READS_the_record(env):
    """It must see what the project is part-way through — that is how it avoids
    duplicating the work. Only the write side is closed."""
    mem, _ = env
    mem.write_continuity(P, threads=['dave is mid-refactor'], owner='Dave')
    out = mem.render_continuity(P, owner='Fenn')
    assert 'dave is mid-refactor' in out


# ── the ownerless bucket's understanding is not a persona's own status ──────

def test_ownerless_understanding_is_not_shown_as_the_personas_status(env):
    """3db65948: Dave was shown a stale August `(project)` backfill line as HIS
    "Where things stand". It is returned apart and rendered as a labelled,
    dated project-wide line."""
    mem, _ = env
    mem.write_continuity(P, understanding='August backfill: phase 1 underway')
    mem.write_continuity(P, threads=['dave thread'], owner='Dave')
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['understanding'] == ''
    assert rec['shared_understanding'] == 'August backfill: phase 1 underway'
    out = mem.render_continuity(P, owner='Dave')
    assert 'Where things stand: August' not in out
    assert 'August backfill: phase 1 underway' in out
    # dated by its heading stamp, not left blank
    stamp = rec['shared_updated'][:10]
    assert stamp and f'Project-wide (unowned), {stamp}: ' in out


def test_undated_ownerless_understanding_says_so(env):
    mem, tmp = env
    (tmp / 'continuity.md').write_text(
        '---\nname: continuity\n---\n\n## Where things stand\nold line\n',
        encoding='utf-8')
    out = mem.render_continuity(P, owner='Dave')
    assert 'Project-wide (unowned), undated: old line' in out


def test_ownerless_understanding_alone_still_renders_for_a_named_reader(env):
    mem, _ = env
    mem.write_continuity(P, understanding='only a project note')
    assert 'only a project note' in mem.render_continuity(P, owner='Dave')


def test_a_personas_own_understanding_wins_and_both_are_shown_apart(env):
    mem, _ = env
    mem.write_continuity(P, understanding='project note')
    mem.write_continuity(P, understanding='dave status', owner='Dave')
    out = mem.render_continuity(P, owner='Dave')
    assert 'Where things stand: dave status' in out
    assert 'Project-wide (unowned)' in out and 'project note' in out


def test_the_merged_view_is_unchanged(env):
    """The human surface (owner=None) still shows the ownerless understanding
    in the ordinary slot — only a NAMED reader stops being handed it as its own."""
    mem, _ = env
    mem.write_continuity(P, understanding='project note')
    assert mem.read_continuity(P)['understanding'] == 'project note'


# ── a bucketless reader (dispatched helper) is an outsider ──────────────────

def test_a_bucketless_reader_is_not_shown_a_same_named_bucket_as_its_own(env):
    mem, _ = env
    mem.write_continuity(P, threads=['fenn old august thread'], owner='Fenn')
    own = mem.render_continuity(P, owner='Fenn')
    assert 'IN FLIGHT — fenn old august thread' in own
    out = mem.render_continuity(P, owner='Fenn', reader_owns=False)
    assert 'IN FLIGHT' not in out
    assert 'ANOTHER AGENT' in out and 'Fenn — fenn old august thread' in out


def test_the_prompt_builder_resolves_the_owner_through_the_session(env):
    """Read side = write side: a registered DIRECT global session reads its own
    bucket; a registered DISPATCHED one reads as an outsider."""
    from mc.blueprints import agent_routes as ar
    from mc import state
    mem, tmp = env
    mem.write_continuity(P, threads=['dave is mid-refactor'], owner='Dave')
    proj = {'id': 'p1', 'name': 'P1', 'project_path': str(tmp)}
    state.CONFIG['agent_name'] = 'Vector'
    ar.agent_sessions['sid-direct'] = {
        'session_id': 'sid-direct', 'character': GLOBAL_DAVE,
        'trigger_type': 'manual'}
    ar.agent_sessions['sid-helper'] = {
        'session_id': 'sid-helper', 'character': GLOBAL_DAVE,
        'trigger_type': 'dispatch', '_notify_session': 'abc123'}
    try:
        direct = ar._build_agent_context(proj, task='x', character_name='Dave',
                                         session_id='sid-direct')
        assert 'IN FLIGHT — dave is mid-refactor' in direct
        helper = ar._build_agent_context(proj, task='x', character_name='Dave',
                                         session_id='sid-helper')
        assert 'IN FLIGHT — dave is mid-refactor' not in helper
        assert 'Dave — dave is mid-refactor' in helper
    finally:
        ar.agent_sessions.pop('sid-direct', None)
        ar.agent_sessions.pop('sid-helper', None)
        state.CONFIG.pop('agent_name', None)
