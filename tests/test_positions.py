"""Standing positions — what we decided NOT to do, and why (mc/memory.py).

Every capture path Clayrune has is downstream of an ARTIFACT: the checkpointer
summarises what happened, the Scribe extracts from outcomes, the Distiller
looks for recurrence. Deciding *not* to build something produces no commit, no
file, no diff — so all three are structurally blind to it, while re-proposing a
settled question costs a whole conversation.

Demonstrated 2026-08-23. Ron named two such decisions; one of them ("we
evaluated Obsidian and declined, because we already have the vault shape and
built the graph machinery ourselves") **was in the vault and in the
always-loaded index** — and the very next turn the agent proposed adopting
Obsidian and manufactured a justification for it.

So this is not a storage gap. The knowledge was stored as *history*, and
history does not fire when someone re-proposes the thing it settled. Hence:
a distinct unit class, a heavily-boosted `subject`, reserved top-k slots, and
its own block in the prompt.
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
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, tmp_path


P = {'id': 'p1'}


# ── writing ──────────────────────────────────────────────────────────────────

def test_a_position_round_trips(env):
    mem, tmp = env
    fn = mem.write_position(
        P, subject='Obsidian as the memory substrate', verdict='declined',
        reason='we already are that vault shape and built the graph machinery',
        expires_when='our own graph machinery stops resolving links')
    assert fn.startswith('position_')
    got = mem.list_positions(P)
    assert len(got) == 1
    assert got[0]['verdict'] == 'declined'
    assert 'graph machinery' in got[0]['reason']
    assert got[0]['expires_when']


def test_reason_is_required(env):
    """A bare verdict is dogma an agent can only obey. The reason is the part
    that makes a position re-openable rather than permanent."""
    mem, _ = env
    with pytest.raises(ValueError, match='reason is required'):
        mem.write_position(P, subject='x', verdict='declined', reason='')


def test_subject_is_required(env):
    mem, _ = env
    with pytest.raises(ValueError, match='subject is required'):
        mem.write_position(P, subject='', verdict='declined', reason='y')


def test_recording_the_same_subject_supersedes_in_place(env):
    """A reversal must read as ONE current ruling, not two contradictory ones."""
    mem, _ = env
    mem.write_position(P, subject='Obsidian', verdict='declined',
                       reason='we built the graph ourselves', decided='2026-08-01')
    mem.write_position(P, subject='Obsidian', verdict='adopted',
                       reason='the graph machinery rotted', decided='2026-11-01')
    got = mem.list_positions(P)
    assert len(got) == 1
    assert got[0]['verdict'] == 'adopted'


def test_the_superseded_reasoning_is_kept(env):
    """'declined in August, reversed in November because Y' is worth more than
    either half — a position that vanishes takes its reasoning with it."""
    mem, tmp = env
    mem.write_position(P, subject='Obsidian', verdict='declined',
                       reason='we built the graph ourselves', decided='2026-08-01')
    mem.write_position(P, subject='Obsidian', verdict='adopted',
                       reason='the graph rotted', decided='2026-11-01')
    body = mem.list_positions(P)[0]['body']
    assert 'Previously' in body and 'we built the graph ourselves' in body


def test_decided_defaults_to_today(env):
    mem, _ = env
    mem.write_position(P, subject='s', verdict='declined', reason='r')
    assert mem.list_positions(P)[0]['decided']


# ── retrieval — the half that actually failed ────────────────────────────────

def test_a_position_wins_its_own_subject(env):
    """The failure this class exists to prevent: the ruling was present and an
    ordinary note that merely MENTIONS the subject outranked it."""
    mem, tmp = env
    (tmp / 'arch_memory_link_layer.md').write_text(
        'Obsidian obsidian obsidian graph machinery and link resolution notes, '
        'mentioning obsidian repeatedly in passing.' * 8, encoding='utf-8')
    mem.write_position(P, subject='Obsidian as the memory substrate',
                       verdict='declined', reason='we built the graph ourselves')
    hits = mem._memory_search(P, 'should we adopt Obsidian?', topk=3)
    assert hits, 'nothing surfaced at all'
    assert hits[0]['file'].startswith('position_'), \
        f'position lost its own subject to {hits[0]["file"]}'


def test_a_matching_position_survives_a_busy_query(env):
    """Reserved slots. Being present and never surfacing is exactly how the
    Obsidian ruling failed to stop the agent."""
    mem, tmp = env
    for i in range(40):
        (tmp / f'topic_{i}.md').write_text(
            'obsidian memory vault graph notes ' * 30, encoding='utf-8')
    mem.write_position(P, subject='Obsidian', verdict='declined',
                       reason='we built the graph ourselves')
    hits = mem._memory_search(P, 'obsidian memory vault graph', topk=3)
    assert any(h['file'].startswith('position_') for h in hits)


def test_positions_do_not_hijack_unrelated_queries(env):
    """The boost must not turn every position into permanent prompt furniture.

    The first version of this test only asserted a position was not RANKED
    FIRST, which was far too weak: measured against the real vault, both
    recorded positions rode along on "fix the cloudflare tunnel quota alarm".
    A standing ruling that appears on every task stops being read. So the
    assertion is ABSENCE.
    """
    mem, tmp = env
    (tmp / 'arch_tunnel.md').write_text(
        'cloudflare tunnel quota and dns routes per account', encoding='utf-8')
    mem.write_position(P, subject='Obsidian as the memory substrate',
                       verdict='declined', reason='we built the graph ourselves')
    hits = mem._memory_search(P, 'cloudflare tunnel quota', topk=6)
    assert not any(h['file'].startswith('position_') for h in hits),         'a position fired on an unrelated query'


def test_explicit_triggers_narrow_when_the_subject_is_too_broad(env):
    """`triggers:` is the escape hatch. A subject phrased in common words
    would otherwise fire on half the vault; naming the trigger makes the
    behaviour a line you can read and edit, not a statistical accident."""
    mem, _ = env
    mem.write_position(P, subject='Obsidian as the memory substrate',
                       verdict='declined', reason='we built the graph ourselves',
                       triggers='obsidian')
    assert not any(h['file'].startswith('position_')
                   for h in mem._memory_search(P, 'memory substrate', topk=6))
    assert any(h['file'].startswith('position_')
               for h in mem._memory_search(P, 'try obsidian?', topk=6))


def test_stopwords_never_become_triggers(env):
    """Otherwise a subject containing 'the' fires on every task ever."""
    mem, _ = env
    assert mem._position_triggers({'subject': 'the use of the thing'}) == {'use', 'thing'}
    assert 'the' not in mem._position_triggers({'subject': 'the graph'})


def test_asking_about_the_subject_directly_fires(env):
    mem, _ = env
    mem.write_position(P, subject='Obsidian as the memory substrate',
                       verdict='declined', reason='we built the graph ourselves')
    hits = mem._memory_search(P, 'should we adopt obsidian as the substrate?',
                              topk=6)
    assert any(h['file'].startswith('position_') for h in hits)


def test_the_result_shape_stays_public_api(env):
    """Read-floor callers unpack these dicts; internal bookkeeping must not
    leak into them."""
    mem, tmp = env
    mem.write_position(P, subject='Obsidian', verdict='declined', reason='r')
    for h in mem._memory_search(P, 'obsidian', topk=3):
        assert set(h) <= {'file', 'score', 'snippet', 'via'},             'internal bookkeeping leaked into the public result shape'


def test_reserve_can_be_turned_off(env, monkeypatch):
    mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'read_floor_position_reserve', 0)
    assert mem._position_reserve() == 0


# ── the prompt block ─────────────────────────────────────────────────────────

def test_the_prompt_states_verdict_reason_and_reopen_condition(env):
    from mc.blueprints import agent_routes as ar
    mem, tmp = env
    mem.write_position(
        P, subject='Obsidian as the memory substrate', verdict='declined',
        reason='we already have the vault shape', expires_when='the graph rots')
    hit = {'file': mem.list_positions(P)[0]['file'], 'snippet': ''}
    assert ar._is_position_hit(hit)
    line = ar._render_position(P, hit)
    assert 'DECLINED' in line
    assert 'we already have the vault shape' in line
    assert 'REOPEN IF' in line


def test_an_ordinary_note_is_not_treated_as_a_position(env):
    from mc.blueprints import agent_routes as ar
    assert not ar._is_position_hit({'file': 'arch_memory_link_layer.md'})
    assert not ar._is_position_hit({'file': 'MEMORY_ARCHIVE.md'})
    assert not ar._is_position_hit({})


# ── capture: the instruction that gives the route a caller ───────────────────

def test_capture_block_names_the_project_and_the_route():
    """Storage, retrieval and a route shipped together; a caller did not. Two
    positions existed a day later, both hand-written. The route's own docstring
    argues capture must stay explicit rather than mined from transcripts — but
    explicit only works if the agent is told, so the telling is the feature."""
    from mc import memory as mem
    out = mem.render_position_capture({'id': 'mission_control'}, 5199)
    assert '/api/project/mission_control/memory/positions' in out
    assert 'localhost:5199' in out
    assert 'reason' in out and 'expires_when' in out


def test_capture_block_is_empty_without_a_project():
    from mc import memory as mem
    assert mem.render_position_capture({}, 5199) == ''
    assert mem.render_position_capture(None, 5199) == ''


# ── forgetting one ───────────────────────────────────────────────────────────

def test_forget_removes_it_from_the_corpus(env):
    """Notes are demoted, never deleted — a cold note costs nothing. A position
    is a RULING: it gets its own prompt block and outranks the notes around it
    on its subject, so a wrong one is not dead weight, it misdirects. There has
    to be a way to take one back."""
    mem, _ = env
    fn = mem.write_position(P, subject='Obsidian', verdict='declined',
                            reason='we built the graph machinery ourselves')
    assert len(mem.list_positions(P)) == 1
    assert mem.delete_position(P, fn) is True
    assert mem.list_positions(P) == []
    assert not any(h.get('cls') == 'position'
                   for h in mem._memory_search(P, 'Obsidian', 6))


def test_forget_is_idempotent(env):
    mem, _ = env
    assert mem.delete_position(P, 'position_never_existed.md') is False


def test_forget_refuses_anything_that_is_not_a_position(env):
    """The filename is UI-supplied and the memory dir also holds MEMORY.md and
    every topic note, so it is the one string here that reaches the filesystem."""
    mem, tmp = env
    (tmp / 'topic_keep.md').write_text('keep me\n', encoding='utf-8')
    for bad in ('MEMORY.md', 'topic_keep.md', '', '../MEMORY.md',
                'position_x.md/../../MEMORY.md', 'sub/position_x.md'):
        with pytest.raises(ValueError):
            mem.delete_position(P, bad)
    assert (tmp / 'topic_keep.md').exists()
    assert (tmp / 'MEMORY.md').exists()
