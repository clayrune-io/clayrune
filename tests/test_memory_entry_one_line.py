"""ML15 (MC-1179): a managed entry is ONE physical line.

`_entry_label` returned `task[:80]` with its newlines kept, and the entry was
built as `- [date] **{label}** — {brief}` with only `brief` flattened. A task
whose first 80 characters held a newline therefore wrote an entry of two or
more physical lines. SESSION_LOG.md is read back line by line and only lines
starting `- [` are entries, so the NEXT commit kept the first line (the cut
label) and dropped the rest: the closing `**` and the whole Scribe summary, the
part a model call was paid for. Measured on the reviewed install: 1,226 of
7,727 archive entries in that state.

Second effect: `last_entry_hash` was the hash of the multi-line string, which
never matched a stored line again, so every later checkpoint of that session
appended instead of superseding.

These pin both ends: the label is one line, and the write boundary itself
refuses to store an entry that is not (the startup reconciler builds its own
label and never went through `_entry_label`).
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

    monkeypatch.setattr(mem, '_get_memory_path',
                        lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, '_should_condense', lambda *a, **k: False)
    monkeypatch.setattr(mem, '_scribe_stat', lambda *a, **k: None)
    return mem, tmp_path


P = {'id': 'p1', 'name': 'P1'}

MULTILINE_TASK = "Fix the login bug\n\nSteps:\n1. open the page\n2. click sign in"


# ── the label ────────────────────────────────────────────────────────────────

def test_entry_label_has_no_newline():
    import mc.memory as m
    label = m._entry_label(MULTILINE_TASK)
    assert '\n' not in label and '\r' not in label
    assert label == "Fix the login bug Steps: 1. open the page 2. click sign in"


def test_entry_label_still_caps_at_80_after_flattening():
    import mc.memory as m
    label = m._entry_label("word\n" * 200)
    assert len(label) <= 80
    assert '\n' not in label


def test_entry_label_single_line_task_is_unchanged():
    import mc.memory as m
    assert m._entry_label("Fix the browser pane paste bug") == \
        "Fix the browser pane paste bug"


# ── the summary survives the NEXT commit ─────────────────────────────────────

def test_multiline_task_keeps_its_summary_across_the_next_commit(env):
    mem, _tmp = env
    entry = (f"- [2026-11-16] **{mem._entry_label(MULTILINE_TASK)}** — "
             "moved the redirect guard above the early return")
    mem._commit_managed_entry(P, mem_entry=entry)
    # The next commit re-reads SESSION_LOG.md line by line.
    mem._commit_managed_entry(P, mem_entry="- [2026-11-16] **other task** — x")

    entries = mem._session_log_read(P)[0]
    kept = [e for e in entries if 'login bug' in e]
    assert len(kept) == 1
    assert kept[0].count('**') == 2, "the label's closing ** was dropped"
    assert 'moved the redirect guard above the early return' in kept[0]


# ── the write boundary: no caller can store a multi-line entry ───────────────

def test_commit_flattens_an_entry_that_arrives_with_a_newline(env):
    """The startup reconciler (server.py) builds `**{task[:80]}**` itself and
    never calls `_entry_label`; the writer is the one place every path meets."""
    mem, _tmp = env
    raw = "- [2026-11-16] **Fix the login bug\n\nSteps** _(reconciled)_ — the summary"
    mem._commit_managed_entry(P, mem_entry=raw)
    mem._commit_managed_entry(P, mem_entry="- [2026-11-16] **other task** — x")

    entries = mem._session_log_read(P)[0]
    kept = [e for e in entries if 'login bug' in e]
    assert kept == ["- [2026-11-16] **Fix the login bug Steps** _(reconciled)_ — the summary"]


def test_checkpoint_of_a_multiline_task_supersedes_instead_of_piling_up(env):
    """`last_entry_hash` must be the hash of the line AS STORED, or the next
    checkpoint of the same session never finds the entry it replaces."""
    mem, _tmp = env
    sid = 'sess-ml15'
    for n in (1, 2, 3):
        entry = f"- [2026-11-16] **Fix the login bug\nSteps** _(live)_ — summary v{n}"
        mem._commit_managed_entry(
            P, mem_entry=entry, wm_upsert={'session_id': sid, 'offset': n},
            supersede_sid=sid)

    entries = mem._session_log_read(P)[0]
    assert len(entries) == 1, entries
    assert entries[0].endswith('summary v3')
