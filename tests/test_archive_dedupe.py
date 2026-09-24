"""Read-time archive de-duplication for the memory read floor (mc/memory.py).

The Step-6 checkpointer appends a fresh session-log line every time it runs, so
one long conversation leaves a trail of near-identical entries that supersede
each other. Measured on mission_control 2026-08-23:

- 1,684 of 2,222 archive lines (**76%**) superseded, worst group 47 copies
- 1,561 of 2,222 were `_(live)_` — mid-session checkpoints, not finished runs
- archive outnumbered topic notes ~30:1 and took 34% of read-floor slots
- **17 of 137 real tasks (12%) got SIX archive lines and zero topic notes**

That measurement justified `_dedupe_archive_lines_legacy`'s "keep only the
LAST line per (day, task)" rule — the tests below pin ITS behavior, and it is
still shipped (`archive_dedupe_legacy_enabled`), just no longer the default.

RETIRED as the default 2026-09-24 (MC-964 Step A, RC1,
docs/MEMORY_OVERHAUL_PLAN.md #6): "same (day, task)" over-generalised from
"near-identical entries that supersede each other" to "same chat title" — one
87-line chat on 2026-09-17 silently deleted a fact (a Codex top-up) that had
nothing to do with the unrelated line that happened to close the chat. The
default is now content-containment dedupe
(`_dedupe_archive_lines_containment` / the bare `_dedupe_archive_lines` entry
point) — see tests/test_memory_archive_dedupe.py for its behavior, including
the frozen real /goal case this file's docstring refers to.

Dedupe is READ-TIME ONLY, in both rules. The archive file is append-only cold
storage and is never truncated; dedupe changes what retrieval sees, not what
is stored.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def dd():
    """Pinned to the LEGACY rule on purpose — see module docstring. The
    current default's behavior lives in tests/test_memory_archive_dedupe.py.
    """
    import server  # noqa: F401  (wires module paths)
    from mc.memory import _dedupe_archive_lines_legacy
    return _dedupe_archive_lines_legacy


def _line(day, task, tail):
    return f'- [{day}] **{task}** _(live)_ — {tail}'


def test_the_last_entry_in_a_group_wins(dd):
    """The specific failure: the FIRST checkpoint said the feature did not
    exist, the LAST said it was verified working."""
    out = dd([
        _line('2026-07-29', '/goal command?', 'found no /goal command'),
        _line('2026-07-29', '/goal command?', 'exists but MC-blocked'),
        _line('2026-07-29', '/goal command?', 'verified working in MC'),
    ])
    assert len(out) == 1
    assert 'verified working' in out[0]


def test_the_same_task_on_a_different_day_is_a_separate_occasion(dd):
    """57 tasks recur across days. Those are genuinely distinct and must
    survive — only within-day checkpoints supersede each other."""
    out = dd([
        _line('2026-07-29', 'restart the server', 'first time'),
        _line('2026-07-30', 'restart the server', 'second time'),
    ])
    assert len(out) == 2


def test_different_tasks_on_one_day_both_survive(dd):
    out = dd([
        _line('2026-07-29', 'task A', 'a'),
        _line('2026-07-29', 'task B', 'b'),
    ])
    assert len(out) == 2


def test_interleaved_groups_are_handled(dd):
    """Concurrent sessions interleave in the archive, so a naive
    consecutive-run collapse would miss them."""
    out = dd([
        _line('2026-07-29', 'A', 'a1'),
        _line('2026-07-29', 'B', 'b1'),
        _line('2026-07-29', 'A', 'a2'),
        _line('2026-07-29', 'B', 'b2'),
    ])
    assert len(out) == 2
    assert 'a2' in out[0] and 'b2' in out[1]


def test_surviving_lines_keep_archive_order(dd):
    """Rank is computed later, but a stable order keeps the corpus (and its
    cache signature) deterministic."""
    out = dd([
        _line('2026-07-29', 'A', 'a1'),
        _line('2026-07-29', 'B', 'b1'),
        _line('2026-07-29', 'A', 'a2'),
    ])
    assert [o.split('— ')[-1] for o in out] == ['b1', 'a2']


def test_a_long_task_is_keyed_on_its_first_120_chars(dd):
    """Steward prompts are near-identical for hundreds of characters; keying on
    the whole string would treat them as distinct and defeat the dedupe."""
    base = 'x' * 130
    out = dd([_line('2026-07-29', base + 'AAA', 'first'),
              _line('2026-07-29', base + 'BBB', 'second')])
    assert len(out) == 1 and 'second' in out[0]


def test_unparseable_lines_are_kept_verbatim(dd):
    """A line that does not match the `- [date] **task**` shape has no group;
    dropping it would silently lose archive content."""
    out = dd(['- [malformed entry with no bold task', '- [2026-07-29] **A** — a'])
    assert len(out) == 2


def test_identical_unparseable_lines_collapse(dd):
    out = dd(['- [junk', '- [junk'])
    assert out == ['- [junk']


def test_empty_input(dd):
    assert dd([]) == []


def test_dedupe_is_idempotent(dd):
    lines = [_line('2026-07-29', 'A', 'a1'), _line('2026-07-29', 'A', 'a2')]
    assert dd(dd(lines)) == dd(lines)


def test_the_corpus_actually_applies_the_default_containment_rule(tmp_path):
    """Guard the wiring, not just the helper — the helper being right is no
    use if _mem_corpus stops calling it. Uses the DEFAULT entry point
    (content-containment, MC-964 Step A), not the `dd` fixture above."""
    from mc import memory as mem
    (tmp_path / 'MEMORY.md').write_text('# idx\n', encoding='utf-8')
    (tmp_path / 'MEMORY_ARCHIVE.md').write_text(
        '\n'.join([
            _line('2026-07-29', 'the widget question', 'the answer is blue'),
            _line('2026-07-29', 'the widget question',
                  'the answer is blue and now confirmed correct'),
        ]), encoding='utf-8')
    units = mem._mem_corpus(tmp_path, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    arch = [u for u in units if u['cls'] == 'archive']
    assert len(arch) == 1
    assert 'confirmed correct' in arch[0]['text']


def test_the_corpus_keeps_distinct_facts_under_one_task(tmp_path):
    """The RC1 regression, exercised through the real corpus builder: two
    facts that do not restate each other must both survive, even under the
    same (day, task) key — unlike the legacy last-wins rule."""
    from mc import memory as mem
    (tmp_path / 'MEMORY.md').write_text('# idx\n', encoding='utf-8')
    (tmp_path / 'MEMORY_ARCHIVE.md').write_text(
        '\n'.join([
            _line('2026-09-17', 'Where do we stand?',
                  'the access problem cleared after adding Codex credits'),
            _line('2026-09-17', 'Where do we stand?',
                  'vendor-decoupling review found writes bypassing protection'),
        ]), encoding='utf-8')
    units = mem._mem_corpus(tmp_path, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    arch = [u for u in units if u['cls'] == 'archive']
    assert len(arch) == 2
