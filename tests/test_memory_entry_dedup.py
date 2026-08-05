"""MEMORY.md managed-region repetition control (2026-08-05, backlog dae8d6e7).

Two independent leaks were filling the managed half of the auto-loaded index
with information it already had, so the byte floor evicted real multi-day
history to make room for it. Measured on this repo: 16 managed entries, ALL
same-day steward `_(live)_` checkpoints, 6.2KB of a 6.9KB region.

1. Titles were the raw task prompt (`task[:80]`), which for a harness-generated
   steward cycle is ~85 bytes of boilerplate carrying zero information.
2. Step-6 checkpointing folds each delta into a CUMULATIVE running summary but
   appended a fresh entry every time, so one session emitted N entries where
   only the newest was needed.

These guard the fixes. The load-bearing invariants they must not break: the
curated region is byte-preserved, live watermarks survive, and the archive is
append-only (superseded checkpoints are the one exception — the surviving
entry already contains their text, so archiving them would re-add the bloat).
"""
import importlib


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


# ── the marker is pinned to steward.fence (mc.memory can't import it) ────────

def test_steward_marker_pinned_to_fence():
    import mc.memory as m
    from steward import fence
    assert m._STEWARD_TASK_MARKER == fence.STEWARD_MARKER


# ── _entry_label: collapse boilerplate, keep real task text verbatim ─────────

def test_entry_label_collapses_steward_prompt():
    import mc.memory as m
    task = ("[Steward cycle] You are the autonomous STEWARD of this project — "
            "run ONE cycle now, following the mc-steward skill exactly.")
    assert m._entry_label(task) == "Steward cycle"


def test_entry_label_keeps_ordinary_task_text():
    import mc.memory as m
    assert m._entry_label("Fix the browser pane paste bug") == \
        "Fix the browser pane paste bug"
    assert len(m._entry_label("x" * 500)) == 80


def test_entry_label_ignores_marker_deep_in_the_text():
    """Only a LEADING marker is harness boilerplate; a task that merely
    mentions steward cycles keeps its own title."""
    import mc.memory as m
    t = "Investigate why the log is full of noise " * 2 + "[Steward cycle]"
    assert m._entry_label(t) != "Steward cycle"


# ── _entry_group_key / _collapse_duplicate_entries ───────────────────────────

def _e(date, label, body="body"):
    return f"- [{date}] **{label}** — {body}"


def test_collapse_keeps_newest_per_group_and_returns_overflow():
    import mc.memory as m
    ents = [_e("2026-08-05", "Steward cycle", f"n{i}") for i in range(6)]
    kept, over = m._collapse_duplicate_entries(ents, keep=3)
    assert kept == ents[3:]          # newest 3, original order
    assert over == ents[:3]          # oldest 3 demoted, original order
    assert kept + over != []         # nothing vanished
    assert len(kept) + len(over) == len(ents)


def test_collapse_never_touches_distinct_work():
    import mc.memory as m
    ents = [_e("2026-08-05", "task A"), _e("2026-08-05", "task B"),
            _e("2026-08-05", "task C"), _e("2026-08-05", "task D")]
    kept, over = m._collapse_duplicate_entries(ents, keep=1)
    assert kept == ents and over == []


def test_collapse_is_per_date_not_global():
    import mc.memory as m
    ents = ([_e("2026-08-04", "Steward cycle", f"a{i}") for i in range(2)]
            + [_e("2026-08-05", "Steward cycle", f"b{i}") for i in range(2)])
    kept, over = m._collapse_duplicate_entries(ents, keep=2)
    assert kept == ents and over == []


def test_collapse_folds_legacy_boilerplate_title_into_same_group():
    """Pre-fix entries carry the long prompt as their title; they must group
    with post-fix short-label entries, not form a second group."""
    import mc.memory as m
    legacy = _e("2026-08-05",
                "[Steward cycle] You are the autonomous STEWARD of this proj")
    ents = [legacy, legacy.replace("body", "b2"),
            _e("2026-08-05", "Steward cycle", "b3")]
    kept, over = m._collapse_duplicate_entries(ents, keep=1)
    assert kept == [_e("2026-08-05", "Steward cycle", "b3")]
    assert len(over) == 2


def test_collapse_never_drops_unparseable_lines():
    import mc.memory as m
    ents = ["- [malformed entry with no bold title",
            _e("2026-08-05", "Steward cycle", "a"),
            _e("2026-08-05", "Steward cycle", "b")]
    kept, over = m._collapse_duplicate_entries(ents, keep=1)
    assert ents[0] in kept
    assert over == [ents[1]]


# ── end-to-end through the real leaf-locked atomic write ─────────────────────

def test_commit_collapses_duplicates_and_archives_them(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "dedupproj"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    seed = [_e("2026-08-05", "Steward cycle", f"n{i}") for i in range(5)]
    mp.write_text(m._mem_compose("# Curated\n- [k](k.md)", seed, []),
                  encoding="utf-8")

    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "Steward cycle", "new"))

    cur, ents, _wm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert cur == "# Curated\n- [k](k.md)"          # curated byte-preserved
    assert len(ents) == m._MANAGED_DUP_KEEP
    assert ents[-1] == _e("2026-08-05", "Steward cycle", "new")
    # the demoted surplus is in the permanent archive, not deleted
    arch = m._get_archive_path(p).read_text(encoding="utf-8")
    for line in seed[:3]:
        assert line in arch


# ── supersede: one session, one entry ────────────────────────────────────────

def test_checkpoint_entry_supersedes_its_predecessor(tmp_data_dir):
    """Two checkpoints for the SAME session leave ONE entry — the newest —
    because the running summary is cumulative."""
    m = _mem(tmp_data_dir)
    p = {"id": "superproj"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Curated", [], []), encoding="utf-8")

    rec = {"session_id": "sidA", "byte_offset": 10, "running_summary": "a"}
    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "Steward cycle", "v1"),
                            wm_upsert=rec, supersede_sid="sidA")
    rec2 = {"session_id": "sidA", "byte_offset": 20, "running_summary": "a+b"}
    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "Steward cycle", "v2"),
                            wm_upsert=rec2, supersede_sid="sidA")

    _c, ents, wm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == [_e("2026-08-05", "Steward cycle", "v2")]
    r = m._wm_find(wm, "sidA")
    assert r and r.get("byte_offset") == 20


def test_supersede_leaves_other_sessions_entries_alone(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "superproj2"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    other = _e("2026-08-05", "someone else's task", "keep me")
    mp.write_text(m._mem_compose("# Curated", [other], []), encoding="utf-8")

    rec = {"session_id": "sidB", "running_summary": "a"}
    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "task B", "v1"),
                            wm_upsert=rec, supersede_sid="sidB")
    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "task B", "v2"),
                            wm_upsert=dict(rec), supersede_sid="sidB")

    _c, ents, _w = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert other in ents
    assert _e("2026-08-05", "task B", "v1") not in ents
    assert _e("2026-08-05", "task B", "v2") in ents


def test_thin_delta_carries_supersede_pointer_forward(tmp_data_dir):
    """A refused/thin delta writes no entry but still upserts the watermark.
    If that drops last_entry_hash, the NEXT real checkpoint can't supersede
    and the pile-up returns."""
    m = _mem(tmp_data_dir)
    p = {"id": "superproj3"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Curated", [], []), encoding="utf-8")

    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "task C", "v1"),
                            wm_upsert={"session_id": "sidC", "byte_offset": 1},
                            supersede_sid="sidC")
    # thin delta: offset advances, no entry
    m._commit_managed_entry(p, wm_upsert={"session_id": "sidC",
                                          "byte_offset": 2})
    _c, _e2, wm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    r = m._wm_find(wm, "sidC")
    assert r and r.get("last_entry_hash")

    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "task C", "v2"),
                            wm_upsert={"session_id": "sidC", "byte_offset": 3},
                            supersede_sid="sidC")
    _c, ents, _w = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == [_e("2026-08-05", "task C", "v2")]


def test_terminal_entry_supersedes_last_live_checkpoint(tmp_data_dir):
    """Session end must not leave the final `_(live)_` breadcrumb sitting next
    to the authoritative completed entry saying the same thing."""
    m = _mem(tmp_data_dir)
    p = {"id": "superproj4"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Curated", [], []), encoding="utf-8")

    live = "- [2026-08-05] **task D** _(live)_ — partial"
    m._commit_managed_entry(p, mem_entry=live,
                            wm_upsert={"session_id": "sidD", "byte_offset": 1},
                            supersede_sid="sidD")
    m._commit_managed_entry(p, mem_entry=_e("2026-08-05", "task D", "final"),
                            wm_remove_sid="sidD", supersede_sid="sidD")

    _c, ents, wm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == [_e("2026-08-05", "task D", "final")]
    assert wm == []  # clean teardown still drops the marker
