"""The recurring delivery check (tools/memory-eval/delivery_review.py).

Phase 4's real output. The screen and the automatic mover were both dropped —
the backfill's analysis produced two promotions and one bug, which is not enough
volume to justify either. What IS worth keeping is the watch: the position that
had been reaching 57% of tasks was wrong from the day positions shipped, and
nothing in the code, the tests or the UI could have revealed it. Only a count
against real tasks did.

These tests pin the four things that make such a watch worth reading rather than
worth muting.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

P = {'id': 'p1'}


def _review():
    """Load the tool by path — `tools/memory-eval` is not a package."""
    path = PROJECT_ROOT / 'tools' / 'memory-eval' / 'delivery_review.py'
    spec = importlib.util.spec_from_file_location('delivery_review', path)
    assert spec and spec.loader, f'could not load {path}'
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    from mc import memory_delivery as deliv
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text(
        mem._mem_compose('# Index\n', [], []), encoding='utf-8')
    return mem, deliv, tmp_path


def _seed(mem, deliv, tmp, notes, tasks, hits_per_task):
    """`notes` = {name: text}; `hits_per_task` = the units delivered each task."""
    for name, text in notes.items():
        (tmp / name).write_text(text, encoding='utf-8')
    for _ in range(tasks):
        deliv.record(P, [{'uid': u, 'file': u, 'cls': 'topic', 'head': u}
                         for u in hits_per_task])


def _index(mem, tmp, body):
    (tmp / 'MEMORY.md').write_text(
        mem._mem_compose('# Index\n' + body, [], []), encoding='utf-8')


# ── the denominator ─────────────────────────────────────────────────────────

def test_it_refuses_to_judge_on_too_few_tasks(env):
    """A rate over five tasks is noise. Reporting one would train the reader to
    ignore the channel — and the whole value here is that it gets read."""
    mem, deliv, tmp = env
    r = _review()
    _seed(mem, deliv, tmp, {'a.md': 'alpha'}, 5, ['a.md'])
    tasks, found = r.collect(mem, P)
    assert tasks == 5
    assert [f['kind'] for f in found] == ['not-enough-data']


# ── the promotion gap, and the comparison that must not be naive ────────────

def test_a_note_fetched_constantly_with_no_index_line_is_flagged(env):
    mem, deliv, tmp = env
    r = _review()
    _seed(mem, deliv, tmp, {'hot.md': 'hot note'}, 100, ['hot.md'])
    _, found = r.collect(mem, P)
    promote = [f for f in found if f['kind'] == 'promote']
    assert [f['subject'] for f in promote] == ['hot.md']


def test_slug_drift_is_not_a_missing_index_line(env):
    """`[[feedback-grep-memory-dir]]` and `feedback_grep_memory_dir.md` are the
    same note — `_mem_link_key` ignores non-alphanumerics. A naive comparison
    reports the note as unindexed AND the link as dangling: two false findings
    from one wrong match, on a vault whose slugs are known to have drifted."""
    mem, deliv, tmp = env
    r = _review()
    _index(mem, tmp, 'see [[feedback-grep-memory-dir]]\n')
    _seed(mem, deliv, tmp, {'feedback_grep_memory_dir.md': 'grep the dir'},
          100, ['feedback_grep_memory_dir.md'])
    _, found = r.collect(mem, P)
    assert not [f for f in found if f['kind'] == 'promote'], \
        'hyphen/underscore drift was read as a missing index line'


def test_a_markdown_link_counts_as_indexed_too(env):
    mem, deliv, tmp = env
    r = _review()
    _index(mem, tmp, '- [Hot](hot.md) — the hot one\n')
    _seed(mem, deliv, tmp, {'hot.md': 'hot note'}, 100, ['hot.md'])
    _, found = r.collect(mem, P)
    assert not [f for f in found if f['kind'] == 'promote']


# ── the failure that already happened ───────────────────────────────────────

def test_a_position_riding_most_tasks_is_flagged(env):
    """The MC-898 case: 108 of 188 tasks, because its subject said "agent"."""
    mem, deliv, tmp = env
    r = _review()
    _seed(mem, deliv, tmp, {}, 100, ['position_everywhere.md'])
    _, found = r.collect(mem, P)
    furniture = [f for f in found if f['kind'] == 'position-furniture']
    assert [f['subject'] for f in furniture] == ['position_everywhere.md']


def test_a_position_firing_on_its_own_subject_is_not_flagged(env):
    """Positions are SUPPOSED to fire. Only riding everything is the defect."""
    mem, deliv, tmp = env
    r = _review()
    for i in range(100):
        deliv.record(P, [{'uid': 'position_narrow.md', 'file': 'position_narrow.md',
                          'cls': 'position', 'head': 'p'}] if i < 10 else [])
    _, found = r.collect(mem, P)
    assert not [f for f in found if f['kind'] == 'position-furniture']


# ── a flag is raised once ───────────────────────────────────────────────────

def test_a_known_condition_goes_quiet_and_re_arms_when_it_changes(env):
    """`preference-5c17ba9d`: alerts on flat continuations train the reader to
    ignore them, and then you lose the week it mattered."""
    mem, deliv, tmp = env
    r = _review()
    _seed(mem, deliv, tmp, {'hot.md': 'hot note'}, 100, ['hot.md'])

    _, found = r.collect(mem, P)
    first = {f['hash'] for f in found}
    assert first
    r.write_state(mem, P, {'flagged': sorted(first)})

    _, again = r.collect(mem, P)
    seen = set(r.read_state(mem, P).get('flagged') or [])
    assert not [f for f in again if f['hash'] not in seen], \
        'the same finding would be reported a second week running'

    # The count moves -> the finding is materially different -> it re-arms.
    for _ in range(50):
        deliv.record(P, [{'uid': 'hot.md', 'file': 'hot.md', 'cls': 'topic',
                          'head': 'hot'}])
    _, third = r.collect(mem, P)
    assert [f for f in third if f['hash'] not in seen], \
        'the finding changed and stayed silent'


# ── it reports, it never edits ──────────────────────────────────────────────

def test_it_writes_only_its_own_sidecar(env):
    """Same rule as positions_review: an unattended process that rewrites what
    every prompt says is the authority guard wearing a different hat."""
    mem, deliv, tmp = env
    r = _review()
    _index(mem, tmp, '- [Hot](hot.md)\n')
    _seed(mem, deliv, tmp, {'hot.md': 'hot note'}, 100, ['hot.md'])
    before = {f.name: f.read_bytes() for f in tmp.glob('*.md')}

    r.collect(mem, P)
    r.write_state(mem, P, {'flagged': []})

    after = {f.name: f.read_bytes() for f in tmp.glob('*.md')}
    assert after == before, 'the review touched a note or the index'
    assert (tmp / r.REVIEW_STATE_FILE).is_file()
