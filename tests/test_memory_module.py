"""mc/memory.py — the mop-up engine extraction (no behavior change).

Guards the PURE MOVE: an import smoke, the MEMORY.md Leg-0 format round-trip
(_mem_split / _mem_compose / _mem_migrate idempotence + sentinel/watermark
preservation), and a real _commit_managed_entry write under an isolated tmp
project proving the managed-region sentinels AND the Step-6 wm marker survive
the leaf-locked atomic write (the load-bearing discipline from CLAUDE.md).

The engine is wired by server.py; _server() reloads it so memory.wire() binds
against tmp_data_dir, then we drive mc.memory directly.
"""
import importlib
import importlib.util


def _mem(tmp_data_dir):
    """Reload server (runs memory.wire() against the isolated tmp data dir),
    return the mc.memory engine module."""
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


# ── import smoke ─────────────────────────────────────────────────────────────

def test_import_smoke():
    import mc.memory as m
    # the engine surface is present
    for name in ("_mem_split", "_mem_split_full", "_mem_compose", "_mem_migrate",
                 "_commit_managed_entry", "_write_session_memory", "_scribe_call",
                 "_dispatch_condense", "_should_condense", "_memory_search",
                 "_maybe_checkpoint", "_condense_apply", "_get_memory_path",
                 "wire"):
        assert hasattr(m, name), name
    # NO import cycle: mc.memory must never pull in server or a blueprint.
    src = importlib.util.find_spec("mc.memory").origin
    text = open(src, encoding="utf-8").read()
    assert "import server" not in text
    assert "from server" not in text
    assert "mc.blueprints" not in text


# ── Leg-0 MEMORY.md format round-trip ────────────────────────────────────────

def test_mem_split_compose_roundtrip(tmp_data_dir):
    m = _mem(tmp_data_dir)
    curated = "# Index\n\n## Topic\n- [a](a.md) — hook"
    entries = ["- [2026-06-10] **task one** — did a thing",
               "- [2026-06-10] **task two** — did another"]
    composed = m._mem_compose(curated, entries)
    cur, ents = m._mem_split(composed)
    assert cur == curated
    assert ents == entries
    # canonical form: exactly one sentinel-delimited managed region
    assert composed.count(m._MEM_BEGIN) == 1
    assert composed.count(m._MEM_END) == 1
    assert m._MEM_LOG_HEADER in composed


def test_mem_migrate_idempotent(tmp_data_dir):
    m = _mem(tmp_data_dir)
    curated = "# Curated\n\n## A\n- [x](x.md)"
    entries = ["- [2026-06-10] **e** — z"]
    wm = ['<!-- clayrune:wm:sidA {"session_id":"sidA",'
          '"running_summary":"live work"} -->']
    composed = m._mem_compose(curated, entries, wm)
    once = m._mem_migrate(composed)
    twice = m._mem_migrate(once)
    # already-canonical content round-trips byte-identically
    assert once == twice
    # wm marker survives split_full
    c, e, gotwm = m._mem_split_full(once)
    assert c == curated
    assert e == entries
    assert gotwm == wm


def test_mem_migrate_wraps_legacy_bare_header(tmp_data_dir):
    m = _mem(tmp_data_dir)
    # Legacy file: bare '## Session Log' with no sentinels.
    legacy = ("# Curated index\n\n## Session Log\n"
              "- [2026-06-01] **old** — legacy entry")
    migrated = m._mem_migrate(legacy)
    assert m._MEM_BEGIN in migrated and m._MEM_END in migrated
    cur, ents = m._mem_split(migrated)
    assert cur == "# Curated index"
    assert ents == ["- [2026-06-01] **old** — legacy entry"]
    # idempotent thereafter
    assert m._mem_migrate(migrated) == migrated


# ── _commit_managed_entry: leaf-locked atomic write preserves sentinels + wm ──

def test_commit_managed_entry_preserves_sentinel_and_watermark(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "memproj"}  # no project_path → MEMORY_DIR/<id>.md (tmp-isolated)
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    # Seed a curated index + one live-session watermark marker.
    wm = ['<!-- clayrune:wm:sidLIVE {"session_id":"sidLIVE",'
          '"running_summary":"in flight"} -->']
    mp.write_text(m._mem_compose("# Idx\n\n## Notes\n- [k](k.md)",
                                 ["- [2026-06-10] **seed** — pre-existing"], wm),
                  encoding="utf-8")

    # Append a new managed entry; the watermark for the OTHER live session must
    # be carried through untouched (we don't remove sidLIVE here).
    m._commit_managed_entry(
        p, mem_entry="- [2026-06-10] **fresh** — appended this turn")

    out = mp.read_text(encoding="utf-8")
    # sentinels intact
    assert out.count(m._MEM_BEGIN) == 1 and out.count(m._MEM_END) == 1
    # curated region byte-preserved
    cur, ents, gotwm = m._mem_split_full(out)
    assert cur == "# Idx\n\n## Notes\n- [k](k.md)"
    # both the seed and the fresh entry are present, in order
    assert ents == ["- [2026-06-10] **seed** — pre-existing",
                    "- [2026-06-10] **fresh** — appended this turn"]
    # the live watermark survived the atomic write (load-bearing)
    assert gotwm == wm
    rec = m._wm_find(gotwm, "sidLIVE")
    assert rec and rec.get("running_summary") == "in flight"


def test_commit_managed_entry_wm_remove_on_teardown(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "memproj2"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    wm = ['<!-- clayrune:wm:sidGONE {"session_id":"sidGONE",'
          '"running_summary":"x"} -->']
    mp.write_text(m._mem_compose("# Idx", [], wm), encoding="utf-8")
    # Terminal write removes this session's wm marker (clean teardown).
    m._commit_managed_entry(
        p, mem_entry="- [2026-06-10] **done** — finished",
        wm_remove_sid="sidGONE")
    _c, ents, gotwm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == ["- [2026-06-10] **done** — finished"]
    assert gotwm == []  # marker dropped on teardown


# ── _gc_stale_watermarks: the leak _wm_remove can't reach ────────────────────
# Hard MC kills skip teardown, so those markers stay forever and the index grows
# past the index byte budget (67 of them / 37.8KB by 2026-07-11). The sweep drops
# markers for dead sessions ONLY — a live session's marker is load-bearing.

def _seed_wm(sid, summary="x"):
    return ('<!-- clayrune:wm:%s {"session_id":"%s","byte_offset":42,'
            '"running_summary":"%s"} -->' % (sid, sid, summary))


def test_gc_stale_watermarks_prunes_dead_keeps_live(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "gcproj"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    curated = "# Idx\n\n## Notes\n- [k](k.md) — hook"
    entries = ["- [2026-07-11] **kept** — a managed entry"]
    wm = [_seed_wm("sidLIVE", "in flight"), _seed_wm("sidDEAD1"),
          _seed_wm("sidDEAD2")]
    mp.write_text(m._mem_compose(curated, entries, wm), encoding="utf-8")

    m.agent_sessions.clear()
    m.agent_sessions["sidLIVE"] = {"session_id": "sidLIVE", "status": "running"}
    try:
        assert m._gc_stale_watermarks([p]) == 2
    finally:
        m.agent_sessions.clear()

    cur, ents, gotwm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert gotwm == [_seed_wm("sidLIVE", "in flight")]  # live marker survives
    assert cur == curated and ents == entries  # nothing else touched


def test_gc_stale_watermarks_noop_when_all_live(tmp_data_dir):
    """No live-marker collateral, and an unchanged file is not rewritten."""
    m = _mem(tmp_data_dir)
    p = {"id": "gcproj2"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Idx", [], [_seed_wm("sidA")]),
                  encoding="utf-8")
    before = mp.read_text(encoding="utf-8")

    m.agent_sessions.clear()
    m.agent_sessions["sidA"] = {"session_id": "sidA", "status": "idle"}
    try:
        assert m._gc_stale_watermarks([p]) == 0
    finally:
        m.agent_sessions.clear()
    assert mp.read_text(encoding="utf-8") == before


# ── MC-917: index-cap hard refusal (_index_overflow / _enforce_index_cap) ────
# A budget nothing enforces silently drifts — the watermark-GC leak (67
# markers, 37.8KB) blew the ~24KB index_byte_budget with no error anywhere.
# These lock in the ONE enforcement point every write path routes through.

def test_index_overflow_none_under_budget(tmp_data_dir):
    m = _mem(tmp_data_dir)
    m.state.CONFIG["index_byte_budget"] = 100
    assert m._index_overflow("x" * 50) is None
    assert m._index_overflow("x" * 100) is None  # exactly at cap fits


def test_index_overflow_reports_exact_numbers(tmp_data_dir):
    m = _mem(tmp_data_dir)
    m.state.CONFIG["index_byte_budget"] = 100
    got = m._index_overflow("x" * 130)
    assert got == (130, 100, 30)


def test_enforce_index_cap_raises_with_numbers(tmp_data_dir):
    m = _mem(tmp_data_dir)
    m.state.CONFIG["index_byte_budget"] = 100
    try:
        m._enforce_index_cap("x" * 150)
        assert False, "expected MemoryCapExceeded"
    except m.MemoryCapExceeded as ex:
        assert (ex.current_bytes, ex.budget_bytes, ex.overflow_bytes) == (
            150, 100, 50)


def test_enforce_index_cap_noop_under_budget(tmp_data_dir):
    m = _mem(tmp_data_dir)
    m.state.CONFIG["index_byte_budget"] = 100
    m._enforce_index_cap("x" * 90)  # must not raise


def test_condense_apply_fold_rolls_back_when_over_cap(tmp_data_dir):
    """Fold is the only way _condense_apply grows curated, and curated has no
    mechanical drain. If the pointer insert would push MEMORY.md over the
    hard budget even after evicting every evictable managed entry, the
    insert must be rolled back (not silently left over-budget) — the fact
    stays safe in the archive either way, so this must never raise."""
    m = _mem(tmp_data_dir)
    m.state.CONFIG["index_byte_budget"] = 200
    curated = "# Index\n\n## Topic\n- existing pointer"
    e = "- [2026-08-31] **fact** — server.py:4902 matters"
    p = {"id": "capproj"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    pointer = "- [insight](insight.md) — " + ("x" * 200)  # alone blows the cap
    mp.write_text(m._mem_compose(curated, [e], []), encoding="utf-8")

    payload = {"entry_decisions": [
        {"id": m._sha8(e), "action": "fold", "fold_into": "## Topic",
         "pointer_line": pointer},
    ], "curated_rewrite": None}
    st = m._condense_apply(p, payload)

    # Downgraded, not folded — the pointer insert was rolled back.
    assert st["folded"] == 0 and st["fold_downgraded"] == 1
    cur, ents, _wm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert pointer not in cur              # never left in curated
    assert "- existing pointer" in cur     # prior curated content untouched
    assert ents == []                      # raw entry demoted, not kept
    # The fact is NOT lost: it's in the archive, verbatim.
    assert e in m._get_archive_path(p).read_text(encoding="utf-8")
    # File on disk is back under budget (roll back actually helped).
    final = mp.read_text(encoding="utf-8")
    assert len(final.encode("utf-8")) <= 200


def test_condense_apply_fold_keeps_pointer_under_budget(tmp_data_dir):
    """Regression guard: a fold that fits the budget is unaffected by the
    MC-917 rollback path."""
    m = _mem(tmp_data_dir)
    m.state.CONFIG["index_byte_budget"] = 24 * 1024  # default-sized budget
    curated = "# Index\n\n## Topic\n- existing pointer"
    e = "- [2026-08-31] **fact** — server.py:4902 matters"
    p = {"id": "capproj2"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose(curated, [e], []), encoding="utf-8")
    pointer = "- [insight](insight.md) — server.py:4902 matters"

    payload = {"entry_decisions": [
        {"id": m._sha8(e), "action": "fold", "fold_into": "## Topic",
         "pointer_line": pointer},
    ], "curated_rewrite": None}
    st = m._condense_apply(p, payload)

    assert st["folded"] == 1 and st["fold_downgraded"] == 0
    cur, _ents, _wm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert pointer in cur


# ── Scribe "why" leg (causal diagnosis alongside the event) ──────────────────
# docs/RESEARCH_HYPERAGENTS.md — the scribe records WHAT; this records the CAUSE.

def test_scribe_split_why_parses_two_parts(tmp_data_dir):
    m = _mem(tmp_data_dir)
    what, why = m._scribe_split_why(
        "Fixed the mobile toast.\n---\nThe toast named a button the <=960px "
        "breakpoint hides; check breakpoint parity before trusting UI copy.")
    assert what == "Fixed the mobile toast."
    assert why.startswith("The toast named a button")


def test_scribe_split_why_absent_separator_is_all_what(tmp_data_dir):
    """A model that ignores the suffix must degrade to the old behaviour."""
    m = _mem(tmp_data_dir)
    what, why = m._scribe_split_why("Just did a thing, no separator here.")
    assert what == "Just did a thing, no separator here."
    assert why == ""


def test_scribe_split_why_none_and_stubs_are_dropped(tmp_data_dir):
    """Most sessions have no diagnosis — NONE and stubs must not be stored."""
    m = _mem(tmp_data_dir)
    for tail in ("NONE", "none.", "N/A", "unclear", "why: NONE", "short"):
        what, why = m._scribe_split_why(f"Routine work.\n---\n{tail}")
        assert what == "Routine work."
        assert why == "", tail


def test_scribe_split_why_strips_label_and_caps(tmp_data_dir):
    m = _mem(tmp_data_dir)
    _what, why = m._scribe_split_why("Did it.\n---\nWHY: " + ("x" * 400))
    assert not why.lower().startswith("why:")
    assert len(why) == m._SCRIBE_WHY_CAP


def test_scribe_summarize_appends_why_marker(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    monkeypatch.setattr(
        m, "_scribe_call",
        lambda model, instr, body: "Did the thing.\n---\nBroke because Z was stale.")
    out, reason = m._scribe_summarize_text("ACTION x\nRESULT: y", "haiku",
                                           want_why=True)
    assert reason == "extracted"
    assert m._SCRIBE_WHY_MARKER in out
    assert out.startswith("Did the thing.")
    assert "Broke because Z was stale." in out


def test_scribe_summarize_default_has_no_why(tmp_data_dir, monkeypatch):
    """Checkpoint path (want_why=False) stays byte-identical to before."""
    m = _mem(tmp_data_dir)
    monkeypatch.setattr(m, "_scribe_call",
                        lambda model, instr, body: "Did the thing.")
    out, reason = m._scribe_summarize_text("ACTION x\nRESULT: y", "haiku")
    assert reason == "extracted"
    assert m._SCRIBE_WHY_MARKER not in out
    assert out == "Did the thing."


def test_scribe_why_suffix_only_sent_when_wanted(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    seen = []
    monkeypatch.setattr(m, "_scribe_call",
                        lambda model, instr, body: seen.append(instr) or "ok line")
    m._scribe_summarize_text("ACTION x", "haiku", want_why=False)
    m._scribe_summarize_text("ACTION x", "haiku", want_why=True)
    assert m._SCRIBE_WHY_SUFFIX not in seen[0]
    assert m._SCRIBE_WHY_SUFFIX in seen[1]


def test_scribe_why_respects_kill_switch(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    monkeypatch.setitem(m.state.CONFIG, "scribe_why_enabled", False)
    monkeypatch.setattr(
        m, "_scribe_call",
        lambda model, instr, body: "Did the thing.\n---\nA cause worth noting.")
    out, _ = m._scribe_summarize_text("ACTION x", "haiku", want_why=True)
    assert m._SCRIBE_WHY_MARKER not in out


def test_scribe_refusal_still_wins_over_why(tmp_data_dir, monkeypatch):
    """A refused body must not smuggle a why into memory alongside it."""
    m = _mem(tmp_data_dir)
    monkeypatch.setattr(
        m, "_scribe_call",
        lambda model, instr, body: "I don't see a transcript.\n---\nCause: nope.")
    out, reason = m._scribe_summarize_text("ACTION x", "haiku", want_why=True)
    assert reason == "model_refused"
    assert out is None
