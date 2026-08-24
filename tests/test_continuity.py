"""The continuity record — what is in flight, and what we owe (mc/memory.py).

The third memory layer (DAVE_DESIGN §3). FACTS work: the read floor reaches
84% of turns that previously got nothing. EPISODIC is thin. CONTINUITY did not
exist at all — what a session was part-way through, and what it promised,
evaporated the moment that session ended. That absence is most of why an agent
reads as a stranger rather than a colleague.

The property that makes it safe to ship: **bounded by construction**. MEMORY.md
needs a remover because it is an open-ended curated list, and MC-892 proved the
remover is the hard part — the proposed eviction would have dropped 29-30 lines
with no surviving delivery channel, and the gate built to catch that returned
green. A fixed-slot record cannot have that problem: every write replaces the
whole record, caps are enforced in code rather than by the model, and nothing
accumulates. There is no eviction policy because there is no growth.
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


# ── the record ───────────────────────────────────────────────────────────────

def test_empty_when_nothing_has_been_written(env):
    mem, _ = env
    rec = mem.read_continuity(P)
    assert rec['threads'] == [] and rec['commitments'] == []
    assert rec['understanding'] == ''


def test_round_trips(env):
    mem, _ = env
    mem.write_continuity(P, threads=['finish the floor view'],
                         commitments=['re-measure the quota next week'],
                         understanding='positions shipped; continuity is next')
    rec = mem.read_continuity(P)
    assert rec['threads'] == ['finish the floor view']
    assert rec['commitments'] == ['re-measure the quota next week']
    assert 'positions shipped' in rec['understanding']
    assert rec['updated']


def test_writing_replaces_rather_than_appends(env):
    """The whole safety argument. Append would reintroduce unbounded growth and
    with it the remover problem that MC-892 could not solve."""
    mem, _ = env
    mem.write_continuity(P, threads=['a', 'b'])
    mem.write_continuity(P, threads=['c'])
    assert mem.read_continuity(P)['threads'] == ['c']


def test_an_empty_list_clears_a_slot(env):
    """Finished work leaves by being omitted — that IS the removal mechanism."""
    mem, _ = env
    mem.write_continuity(P, threads=['a'], commitments=['b'])
    mem.write_continuity(P, threads=[])
    rec = mem.read_continuity(P)
    assert rec['threads'] == []
    assert rec['commitments'] == ['b']      # untouched slot survives


def test_none_leaves_a_slot_untouched(env):
    """A caller that only learned about commitments must not blank the rest."""
    mem, _ = env
    mem.write_continuity(P, threads=['a'], understanding='u')
    mem.write_continuity(P, commitments=['c'])
    rec = mem.read_continuity(P)
    assert rec['threads'] == ['a'] and rec['understanding'] == 'u'
    assert rec['commitments'] == ['c']


def test_caps_are_enforced_in_code_not_by_the_model(env):
    """A model asked for 'max 5' will eventually return 9."""
    mem, _ = env
    mem.write_continuity(P, threads=[f't{i}' for i in range(20)],
                         commitments=[f'c{i}' for i in range(20)])
    rec = mem.read_continuity(P)
    assert len(rec['threads']) == mem._CONT_MAX_THREADS
    assert len(rec['commitments']) == mem._CONT_MAX_COMMITMENTS


def test_long_items_are_trimmed(env):
    mem, _ = env
    mem.write_continuity(P, threads=['x' * 500], understanding='y' * 2000)
    rec = mem.read_continuity(P)
    assert len(rec['threads'][0]) <= mem._CONT_MAX_ITEM_CHARS
    assert len(rec['understanding']) <= mem._CONT_MAX_UNDERSTANDING


def test_duplicates_collapse(env):
    mem, _ = env
    mem.write_continuity(P, threads=['same', 'same', 'other'])
    assert mem.read_continuity(P)['threads'] == ['same', 'other']


def test_the_record_stays_small(env):
    """The cost is per-turn and permanent, so it has to be a known number
    rather than one that happens to be small today."""
    mem, tmp = env
    mem.write_continuity(P, threads=['t' * 200] * 20,
                         commitments=['c' * 200] * 20,
                         understanding='u' * 2000)
    size = (tmp / mem.CONTINUITY_FILE).stat().st_size
    assert size < 3072, f'continuity record grew to {size} bytes'


# ── the prompt block ─────────────────────────────────────────────────────────

def test_renders_nothing_when_empty(env):
    """An empty block is prompt noise that teaches the agent to skip the
    heading — worse than absence."""
    mem, _ = env
    assert mem.render_continuity(P) == ''


def test_renders_threads_and_commitments_distinctly(env):
    mem, _ = env
    mem.write_continuity(P, threads=['finish the floor view'],
                         commitments=['re-measure the quota'],
                         understanding='mid-way through phase 1')
    out = mem.render_continuity(P)
    assert 'IN FLIGHT — finish the floor view' in out
    assert 'YOU SAID YOU WOULD — re-measure the quota' in out
    assert 'mid-way through phase 1' in out


def test_reaches_the_agent_prompt(env):
    from mc.blueprints import agent_routes as ar
    mem, tmp = env
    mem.write_continuity(P, commitments=['re-measure the archive quota'])
    ctx = ar._build_agent_context(
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp)}, task='anything')
    assert 'CONTINUITY' in ctx
    assert 're-measure the archive quota' in ctx


def test_incognito_sees_no_continuity(env):
    """Incognito skips memory and rules; leaking working state into it would
    defeat the point of the mode."""
    from mc.blueprints import agent_routes as ar
    mem, tmp = env
    mem.write_continuity(P, commitments=['something private'])
    ctx = ar._build_agent_context(
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp)},
        task='anything', incognito=True)
    assert 'something private' not in ctx


# ── extraction ───────────────────────────────────────────────────────────────

def test_extraction_parses_and_persists(env, monkeypatch):
    mem, _ = env
    monkeypatch.setattr(mem, '_scribe_call', lambda *a, **k: (
        '{"threads":["ship the floor"],"commitments":["email Ron"],'
        '"understanding":"phase 1 underway"}'))
    out = mem._extract_continuity(P, 'some transcript delta', 'haiku')
    assert out['threads'] == ['ship the floor']
    assert mem.read_continuity(P)['commitments'] == ['email Ron']


def test_extraction_survives_a_fenced_reply(env, monkeypatch):
    """Models wrap JSON in ``` far too often to treat it as an error."""
    mem, _ = env
    monkeypatch.setattr(mem, '_scribe_call', lambda *a, **k: (
        '```json\n{"threads":["a"],"commitments":[],"understanding":"u"}\n```'))
    assert mem._extract_continuity(P, 'delta', 'haiku')['threads'] == ['a']


def test_extraction_failure_leaves_the_record_alone(env, monkeypatch):
    """Best-effort: the checkpoint entry is already committed by this point, so
    a bad extraction must not corrupt working state."""
    mem, _ = env
    mem.write_continuity(P, threads=['keep me'])
    monkeypatch.setattr(mem, '_scribe_call', lambda *a, **k: 'sorry, I cannot')
    assert mem._extract_continuity(P, 'delta', 'haiku') is None
    assert mem.read_continuity(P)['threads'] == ['keep me']


def test_extraction_never_raises(env, monkeypatch):
    mem, _ = env
    def boom(*a, **k):
        raise RuntimeError('model down')
    monkeypatch.setattr(mem, '_scribe_call', boom)
    assert mem._extract_continuity(P, 'delta', 'haiku') is None
