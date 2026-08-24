"""The checkpoint pile-up's ROOT cause (mc/memory.py `_commit_managed_entry`).

Step-6 checkpointing folds each transcript delta into a *cumulative* running
summary, so every `_(live)_` entry it writes is a strict superset of the one
before. `supersede_sid` was built to drop the previous entry in the same atomic
write, keyed on `last_entry_hash` stashed on the session's watermark.

It worked — right up until the entry left MEMORY.md. The floor evicts
oldest-first into `MEMORY_ARCHIVE.md`, which is append-only cold storage and is
never truncated. Once a live session's entry had been relocated there, the
supersede-by-hash lookup found nothing and every later checkpoint APPENDED.

Measured 2026-08-23 before the fix: **1,684 of 2,222 archive lines superseded**,
worst single group **47 copies** of one conversation, 1,561 of the 2,222 still
tagged `_(live)_`. The read floor then served those stale first drafts back —
one task in eight got six archive lines and no topic notes at all.

The guard: the floor may not evict a line a live watermark still points at.
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


def _proj():
    return {'id': 'p1', 'name': 'P1'}


def _arch_lines(tmp_path):
    ap = tmp_path / 'MEMORY_ARCHIVE.md'
    if not ap.exists():
        return []
    return [ln for ln in ap.read_text(encoding='utf-8').splitlines()
            if ln.startswith('- [')]


def _managed(mem, tmp_path):
    txt = (tmp_path / 'MEMORY.md').read_text(encoding='utf-8')
    return _split_entries(mem, txt)


def _split_entries(mem, txt):
    return mem._mem_split_full(txt)[1]


# ── the guard itself ─────────────────────────────────────────────────────────

def test_supersedable_hashes_reads_live_watermarks(env):
    mem, _ = env
    wm = [mem._wm_line({'session_id': 's1', 'last_entry_hash': 'aaa11111'}),
          mem._wm_line({'session_id': 's2', 'last_entry_hash': 'bbb22222'}),
          mem._wm_line({'session_id': 's3'})]          # no entry yet
    assert mem._supersedable_hashes(wm) == {'aaa11111', 'bbb22222'}


def test_supersedable_hashes_tolerates_junk(env):
    mem, _ = env
    assert mem._supersedable_hashes(['not a marker', '', None]) == set()
    assert mem._supersedable_hashes([]) == set()
    assert mem._supersedable_hashes(None) == set()


# ── end-to-end: a long session under floor pressure ──────────────────────────

def test_a_live_entry_is_not_evicted_under_floor_pressure(env, monkeypatch):
    """The exact failure: budget pressure archives the line the next checkpoint
    was going to replace, and supersession is silently lost from then on."""
    mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'index_line_hard_floor', 3)
    p = _proj()

    # Unrelated history, enough to sit at the floor.
    for i in range(6):
        mem._commit_managed_entry(p, mem_entry=f'- [2026-08-01] **old {i}** — x')

    # A long session checkpoints repeatedly.
    for n in range(8):
        entry = f'- [2026-08-23] **the long task** _(live)_ — summary v{n}'
        mem._commit_managed_entry(
            p, mem_entry=entry,
            wm_upsert={'session_id': 'sLONG', 'byte_offset': n},
            supersede_sid='sLONG')

    live = [ln for ln in _managed(mem, tmp) if 'the long task' in ln]
    archived = [ln for ln in _arch_lines(tmp) if 'the long task' in ln]

    # ONE surviving entry, the newest, and NOTHING from this session archived.
    assert len(live) == 1, f'expected 1 live entry, got {len(live)}'
    assert 'summary v7' in live[0]
    assert archived == [], f'live session leaked {len(archived)} lines to the archive'


def test_unrelated_history_still_overflows(env, monkeypatch):
    """The guard must protect the live line WITHOUT stopping the floor from
    doing its job on everything else."""
    mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'index_line_hard_floor', 3)
    p = _proj()
    for i in range(10):
        mem._commit_managed_entry(p, mem_entry=f'- [2026-08-01] **old {i}** — x')
    mem._commit_managed_entry(
        p, mem_entry='- [2026-08-23] **live one** _(live)_ — v0',
        wm_upsert={'session_id': 'sL'}, supersede_sid='sL')
    assert any('old 0' in ln for ln in _arch_lines(tmp)), 'floor stopped working'


def test_the_entry_archives_normally_once_the_session_ends(env, monkeypatch):
    """Protection is tied to a LIVE watermark. Teardown removes it, and the
    final entry becomes ordinary evictable history — nothing is pinned."""
    mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'index_line_hard_floor', 3)
    p = _proj()
    mem._commit_managed_entry(
        p, mem_entry='- [2026-08-23] **done task** _(live)_ — final',
        wm_upsert={'session_id': 'sEND'}, supersede_sid='sEND')
    mem._commit_managed_entry(p, wm_remove_sid='sEND')     # teardown
    for i in range(10):
        mem._commit_managed_entry(p, mem_entry=f'- [2026-08-24] **later {i}** — y')
    assert any('done task' in ln for ln in _arch_lines(tmp)), \
        'a finished session\'s entry must stop being protected'


def test_two_concurrent_sessions_each_keep_one_entry(env, monkeypatch):
    mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'index_line_hard_floor', 3)
    p = _proj()
    for n in range(5):
        for sid, label in (('sA', 'task A'), ('sB', 'task B')):
            mem._commit_managed_entry(
                p, mem_entry=f'- [2026-08-23] **{label}** _(live)_ — v{n}',
                wm_upsert={'session_id': sid}, supersede_sid=sid)
    managed = _managed(mem, tmp)
    assert len([ln for ln in managed if 'task A' in ln]) == 1
    assert len([ln for ln in managed if 'task B' in ln]) == 1
    assert [ln for ln in _arch_lines(tmp) if 'task A' in ln or 'task B' in ln] == []
