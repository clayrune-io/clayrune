"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 4 — the split.

SESSION_LOG.md (§9.1/§9.2): the managed region (entries + `clayrune:wm:<sid>`
watermarks) moves out of MEMORY.md into its own sibling file, not auto-loaded
and not injected by `_build_agent_context`. The 20-entry ring (§9.2 B2)
replaces the old byte/line floor for that file, since it is never in the
prompt. The demoter (§9.1 Condition 36/37) can shrink the CURATED region —
refusing a line with no resolvable target — but is not wired to fire
automatically in this step (Condition 37: the terminal 8,192 B cap is step
10). Migration (§11) is lossless and re-runnable.

`tests/test_checkpoint_supersede.py`, `test_condense_structured.py`,
`test_memory_entry_dedup.py` and `test_memory_module.py` already pin the
rewritten write paths end to end; this file covers what's new and not
already exercised elsewhere: the migration function, the demoter, and the
corpus/GC cross-file wiring in isolation.
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
    return mem, tmp_path


P = {'id': 'p1'}


# ── SESSION_LOG.md — path, read/write round-trip ────────────────────────────

def test_session_log_path_is_a_sibling_of_memory_md(env):
    mem, tmp = env
    assert mem._get_session_log_path(P) == tmp / 'SESSION_LOG.md'


def test_session_log_read_absent_file_returns_empty(env):
    mem, tmp = env
    assert mem._session_log_read(P) == ([], [])
    assert mem._session_log_entries(P) == []


def test_session_log_write_read_round_trip(env):
    mem, tmp = env
    entries = ['- [2026-09-14] **a** — one', '- [2026-09-14] **b** — two']
    wm = [mem._wm_line({'session_id': 's1', 'running_summary': 'x'})]
    mem._write_session_log(P, entries, wm)
    got_entries, got_wm = mem._session_log_read(P)
    assert got_entries == entries
    assert got_wm == wm
    # Same sentinel/header format as MEMORY.md's old managed block.
    text = (tmp / 'SESSION_LOG.md').read_text(encoding='utf-8')
    assert text.count(mem._MEM_BEGIN) == 1 and text.count(mem._MEM_END) == 1
    assert mem._MEM_LOG_HEADER in text


def test_session_log_ring_default_and_config(env, monkeypatch):
    mem, tmp = env
    assert mem._session_log_ring() == 20
    monkeypatch.setitem(mem.state.CONFIG, 'session_log_ring', 5)
    assert mem._session_log_ring() == 5
    monkeypatch.setitem(mem.state.CONFIG, 'session_log_ring', -3)
    assert mem._session_log_ring() == 1  # floor, never 0 or negative


# ── the ring rotation log line (Condition, §9.2 B2) ─────────────────────────

def test_ring_rotation_logs_the_required_line(env, monkeypatch):
    """Condition, §9.2 B2: the ordinary ring rotation is the bound that used
    to emit nothing at all under the old byte/line floor — it must log now."""
    mem, tmp = env
    mem.state.CONFIG['session_log_ring'] = 3
    mp = tmp / 'MEMORY.md'
    mp.write_text('# Index', encoding='utf-8')
    logged = []
    monkeypatch.setattr(mem, '_log', lambda msg: logged.append(msg))
    for i in range(5):
        mem._commit_managed_entry(P, mem_entry=f'- [2026-09-{i+1:02d}] **e{i}** — x')
    ents, _wm = mem._session_log_read(P)
    assert len(ents) == 3
    assert any('[mem-log]' in m and 'rotated' in m
              and 'ring cap 3, oldest first' in m for m in logged)


# ── migrate_session_log_split — idempotent, lossless, re-runnable (§11) ────

def test_migration_moves_legacy_managed_content_out(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    legacy_entries = ['- [2026-09-01] **old1** — x', '- [2026-09-02] **old2** — y']
    legacy_wm = [mem._wm_line({'session_id': 'sOld', 'running_summary': 'z'})]
    mp.write_text(mem._mem_compose('# Curated\n- [k](k.md)', legacy_entries,
                                   legacy_wm), encoding='utf-8')

    stats = mem.migrate_session_log_split(P)

    assert stats['moved_entries'] == 2
    assert stats['moved_wm_markers'] == 1
    assert stats['session_log_entries'] == 2
    assert stats['session_log_wm_markers'] == 1

    # MEMORY.md: curated only, no sentinel, no entries, no markers.
    after = mp.read_text(encoding='utf-8')
    assert mem._MEM_BEGIN not in after and mem._MEM_END not in after
    assert after.rstrip() == '# Curated\n- [k](k.md)'

    # SESSION_LOG.md: everything moved, verbatim.
    ents, wm = mem._session_log_read(P)
    assert ents == legacy_entries
    assert wm == legacy_wm


def test_migration_is_idempotent_second_run_moves_nothing(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    legacy_entries = ['- [2026-09-01] **old1** — x']
    mp.write_text(mem._mem_compose('# Curated', legacy_entries, []),
                  encoding='utf-8')

    first = mem.migrate_session_log_split(P)
    assert first['moved_entries'] == 1

    before_log = (tmp / 'SESSION_LOG.md').read_text(encoding='utf-8')
    before_mem = mp.read_text(encoding='utf-8')

    second = mem.migrate_session_log_split(P)
    assert second['moved_entries'] == 0
    assert second['moved_wm_markers'] == 0
    assert second['session_log_entries'] == 1  # unchanged, not duplicated

    assert (tmp / 'SESSION_LOG.md').read_text(encoding='utf-8') == before_log
    assert mp.read_text(encoding='utf-8') == before_mem


def test_migration_on_an_already_split_project_is_a_no_op(env):
    """A project with no sentinel block at all (fresh install, or already
    migrated) round-trips its curated text unchanged."""
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    mp.write_text('# Curated\n\n## Topic\n- [a](a.md) — hook', encoding='utf-8')
    stats = mem.migrate_session_log_split(P)
    assert stats['moved_entries'] == 0
    assert stats['moved_wm_markers'] == 0
    assert mp.read_text(encoding='utf-8').rstrip() == \
        '# Curated\n\n## Topic\n- [a](a.md) — hook'


def test_migration_never_loses_a_watermark_across_merge_with_existing_log(env):
    """Existing SESSION_LOG.md content (from a prior _commit_managed_entry
    call) merges with what MEMORY.md still carries inline — nothing shadows
    or overwrites the other, per session_id."""
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    mem._write_session_log(
        P, ['- [2026-09-10] **already-migrated** — a'],
        [mem._wm_line({'session_id': 'sNew', 'running_summary': 'n'})])
    mp.write_text(mem._mem_compose(
        '# Curated', ['- [2026-09-01] **still-legacy** — b'],
        [mem._wm_line({'session_id': 'sOld', 'running_summary': 'o'})]),
        encoding='utf-8')

    stats = mem.migrate_session_log_split(P)
    assert stats['moved_entries'] == 1
    assert stats['moved_wm_markers'] == 1

    ents, wm = mem._session_log_read(P)
    assert set(ents) == {'- [2026-09-10] **already-migrated** — a',
                         '- [2026-09-01] **still-legacy** — b'}
    sids = {(mem._wm_parse(ln) or {}).get('session_id') for ln in wm}
    assert sids == {'sNew', 'sOld'}


# ── the demoter (§9.1 Condition 36/37) ──────────────────────────────────────

def test_demote_candidates_splits_resolvable_from_targetless(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    (tmp / 'real_note.md').write_text('some content here', encoding='utf-8')
    mp.write_text(
        '# Index\n'
        '- [Real pointer](real_note.md) — resolvable\n'
        '- [Dangling pointer](nowhere.md) — not resolvable\n'
        '- prose line with no link at all\n',
        encoding='utf-8')
    cand = mem.demote_candidates(P)
    demotable_targets = {c['target'] for c in cand['demotable']}
    refused_targets = {c['target'] for c in cand['refused']}
    assert demotable_targets == {'real_note'}
    assert 'nowhere' in refused_targets
    assert None in refused_targets  # the prose line with no link


def test_demote_line_removes_a_resolvable_pointer(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    (tmp / 'real_note.md').write_text('some content here', encoding='utf-8')
    mp.write_text(
        '# Index\n'
        '- [Real pointer](real_note.md) — resolvable\n'
        '- [Other](real_note.md) — also there\n',
        encoding='utf-8')
    cand = mem.demote_candidates(P)
    line_idx = cand['demotable'][0]['line']
    removed = mem.demote_line(P, line_idx)
    assert 'Real pointer' in removed
    after = mp.read_text(encoding='utf-8')
    assert 'Real pointer' not in after
    assert 'Other' in after           # the OTHER pointer to the same note survives
    # The note itself was never touched.
    assert (tmp / 'real_note.md').read_text(encoding='utf-8') == 'some content here'


def test_demote_line_refuses_a_targetless_line(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    mp.write_text('# Index\n- [Dangling](nowhere.md) — gone\n', encoding='utf-8')
    cand = mem.demote_candidates(P)
    line_idx = cand['refused'][0]['line']
    with pytest.raises(mem.DemotionRefused):
        mem.demote_line(P, line_idx)
    # Untouched.
    assert 'Dangling' in mp.read_text(encoding='utf-8')


def test_demote_line_logs_the_condition_36_format(env, monkeypatch):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    (tmp / 'real_note.md').write_text('some content here', encoding='utf-8')
    mp.write_text('# Index\n- [Real pointer](real_note.md) — hook\n',
                  encoding='utf-8')
    logged = []
    monkeypatch.setattr(mem, '_log', lambda msg: logged.append(msg))
    cand = mem.demote_candidates(P)
    mem.demote_line(P, cand['demotable'][0]['line'])
    assert any('[mem-index]' in m and 'demoted pointer' in m
              and 'real_note' in m and 'note remains searchable' in m
              for m in logged)


# ── corpus + GC cross-file wiring ────────────────────────────────────────────

def test_corpus_reads_session_log_managed_entries(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    mp.write_text('# Index', encoding='utf-8')
    mem._write_session_log(P, ['- [2026-09-14] **from log** — findable'], [])
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    managed = [u for u in units if u['cls'] == 'managed']
    assert any('from log' in u['text'] for u in managed)
    assert any(u['file'] == 'SESSION_LOG.md#managed' for u in managed)


def test_gc_stale_watermarks_sweeps_session_log(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    mp.write_text('# Index', encoding='utf-8')
    live_wm = mem._wm_line({'session_id': 'sLIVE', 'running_summary': 'x'})
    dead_wm = mem._wm_line({'session_id': 'sDEAD', 'running_summary': 'y'})
    mem._write_session_log(P, ['- [2026-09-14] **e** — x'], [live_wm, dead_wm])

    mem.agent_sessions.clear()
    mem.agent_sessions['sLIVE'] = {'session_id': 'sLIVE', 'status': 'running'}
    try:
        assert mem._gc_stale_watermarks([P]) == 1
    finally:
        mem.agent_sessions.clear()

    ents, wm = mem._session_log_read(P)
    assert ents == ['- [2026-09-14] **e** — x']       # entries untouched
    assert wm == [live_wm]                             # only the dead one pruned


def test_gc_stale_watermarks_still_sweeps_legacy_memory_md(env):
    """§10.4 both-formats-coexist: a not-yet-migrated project still gets its
    inline markers GC'd."""
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    live_wm = mem._wm_line({'session_id': 'sLIVE', 'running_summary': 'x'})
    dead_wm = mem._wm_line({'session_id': 'sDEAD', 'running_summary': 'y'})
    mp.write_text(mem._mem_compose('# Idx', ['- [2026-09-14] **e** — x'],
                                   [live_wm, dead_wm]), encoding='utf-8')

    mem.agent_sessions.clear()
    mem.agent_sessions['sLIVE'] = {'session_id': 'sLIVE', 'status': 'running'}
    try:
        assert mem._gc_stale_watermarks([P]) == 1
    finally:
        mem.agent_sessions.clear()

    _c, ents, wm = mem._mem_split_full(mp.read_text(encoding='utf-8'))
    assert ents == ['- [2026-09-14] **e** — x']
    assert wm == [live_wm]


# ── condense_combined_bytes includes SESSION_LOG.md ─────────────────────────

def test_condense_combined_bytes_includes_session_log(env):
    mem, tmp = env
    mp = tmp / 'MEMORY.md'
    mp.write_text('# Index', encoding='utf-8')
    before = mem._condense_combined_bytes(P)
    mem._write_session_log(P, ['- [2026-09-14] **e** — ' + 'x' * 500], [])
    after = mem._condense_combined_bytes(P)
    assert after > before + 400
