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

import pytest


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
# §16 step 4 (MC-944): entries + wm markers live in SESSION_LOG.md now, not
# inline in MEMORY.md — MEMORY.md is curated-only after this write path runs.

def test_commit_managed_entry_preserves_sentinel_and_watermark(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "memproj"}  # no project_path → MEMORY_DIR/<id>.md (tmp-isolated)
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text("# Idx\n\n## Notes\n- [k](k.md)", encoding="utf-8")
    # Seed SESSION_LOG.md with one live-session watermark marker + an entry.
    wm = ['<!-- clayrune:wm:sidLIVE {"session_id":"sidLIVE",'
          '"running_summary":"in flight"} -->']
    m._write_session_log(p, ["- [2026-06-10] **seed** — pre-existing"], wm)

    # Append a new managed entry; the watermark for the OTHER live session must
    # be carried through untouched (we don't remove sidLIVE here).
    m._commit_managed_entry(
        p, mem_entry="- [2026-06-10] **fresh** — appended this turn")

    # MEMORY.md carries no managed region at all after the split.
    out = mp.read_text(encoding="utf-8")
    assert m._MEM_BEGIN not in out and m._MEM_END not in out
    assert out.rstrip() == "# Idx\n\n## Notes\n- [k](k.md)"

    # SESSION_LOG.md holds the entries + watermark, sentinels intact.
    log_text = m._get_session_log_path(p).read_text(encoding="utf-8")
    assert log_text.count(m._MEM_BEGIN) == 1 and log_text.count(m._MEM_END) == 1
    ents, gotwm = m._session_log_read(p)
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
    mp.write_text("# Idx", encoding="utf-8")
    wm = ['<!-- clayrune:wm:sidGONE {"session_id":"sidGONE",'
          '"running_summary":"x"} -->']
    m._write_session_log(p, [], wm)
    # Terminal write removes this session's wm marker (clean teardown).
    m._commit_managed_entry(
        p, mem_entry="- [2026-06-10] **done** — finished",
        wm_remove_sid="sidGONE")
    ents, gotwm = m._session_log_read(p)
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


# ── _scribe_call skips cleanly on a non-Claude install ───────────────────────
# Scribe/condense/the Distiller all choke through this ONE function regardless
# of the session's own provider (see its docstring) — a Codex/Gemini-only
# install must not spawn a doomed `claude -p` subprocess on every call.

def test_scribe_call_skips_when_claude_unavailable(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    monkeypatch.setattr(m._agent_runtime, "claude_oneshot_available", lambda: False)
    called = []
    monkeypatch.setattr(
        m._agent_runtime, "get_runtime",
        lambda name: called.append(name) or (_ for _ in ()).throw(
            AssertionError("oneshot must not be reached when claude is unavailable")))
    with pytest.raises(RuntimeError, match="claude unavailable"):
        m._scribe_call("haiku", "summarize", "body text")
    assert called == []  # get_runtime('claude') was never reached


def test_scribe_call_proceeds_when_claude_available(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    monkeypatch.setattr(m._agent_runtime, "claude_oneshot_available", lambda: True)

    calls = []

    def transform(provider, **kwargs):
        calls.append((provider, kwargs))
        return "a summary"

    # _scribe_call goes through the authorized transform seam, never a raw
    # runtime.oneshot() (Fenn #1: zero bypass sites).
    monkeypatch.setattr(m._agent_runtime, "run_text_transform", transform)
    assert m._scribe_call("haiku", "summarize", "body text") == "a summary"
    assert calls[0][0] == "claude"
    assert calls[0][1]["stdin_text"] == "body text"


def test_scribe_call_folds_seam_timeout_into_runtime_error(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    monkeypatch.setattr(m._agent_runtime, "claude_oneshot_available", lambda: True)

    def transform(provider, **kwargs):
        raise TimeoutError("timeout after 180s")

    monkeypatch.setattr(m._agent_runtime, "run_text_transform", transform)
    with pytest.raises(RuntimeError, match="timeout after 180s"):
        m._scribe_call("haiku", "summarize", "body text")


# ── _session_too_large: must find dot/worktree-flattened transcripts ─────────
# Regression: _session_transcript_path() used to delegate to
# ClaudeRuntime._build_transcript_path(), which only builds the PRIMARY
# encoded variant (no existence check). The CLI itself also flattens `_`
# and `.` to `-` (see agent_runtime._encoded_dir_candidates), and worktree
# sessions live under <project>/.clayrune/agents/<sid> — a dot-bearing path
# on every isolated agent. So on any install whose project path contains
# `_` or `.` (this one: "...\_claude\..."), the primary-only lookup always
# missed and auto-fresh silently never fired. Fixed by delegating to
# ClaudeRuntime.transcript_path() instead, which checks every encoded
# variant plus the worktree glob.

def test_session_too_large_finds_dot_flattened_worktree_transcript(tmp_data_dir, monkeypatch):
    m = _mem(tmp_data_dir)
    from mc.agent_runtime import ClaudeRuntime

    rt = ClaudeRuntime()
    fake_home = tmp_data_dir / 'claude_home' / 'projects'
    monkeypatch.setattr(m._agent_runtime, '_CLAUDE_HOME', fake_home)
    m._SESSION_SIZE_LIMIT = 100  # bytes — small so a short fixture body trips it

    # Real shape: a per-agent worktree under a dot-prefixed directory, exactly
    # what per-agent worktree isolation (b264200a) actually runs sessions from.
    worktree = str(tmp_data_dir / 'proj' / '.clayrune' / 'agents' / '0f7687efce3f')
    session_id = '41ee10c4-7594-4e17-b92b-6308102c1750'

    encoded = rt._encode_project_path(worktree)
    assert encoded and '.' in encoded, 'expected the dot to survive the base encoding'

    # Create ONLY the dot-flattened dir — what the CLI actually writes on disk.
    cli_dir = fake_home / encoded.replace('.', '-')
    cli_dir.mkdir(parents=True)
    jsonl_file = cli_dir / f'{session_id}.jsonl'
    jsonl_file.write_text('{"type":"user"}' * 20)  # > 100 bytes
    assert jsonl_file.stat().st_size > m._SESSION_SIZE_LIMIT

    assert not (fake_home / encoded).exists(), 'base-encoded (unflattened) dir must NOT exist'

    too_large, size = m._session_too_large(worktree, session_id)
    assert too_large is True
    assert size == jsonl_file.stat().st_size


# ── _session_too_large / _transcript_image_bytes: base64 images must not
# count toward the byte cap ──────────────────────────────────────────────────
# Regression (2026-09-18): clayrune_website auto-freshed twice as "session
# too large" at 6.4 MB and 9.9 MB — real context was only 123k/85k tokens.
# 2.8 MB / 4.7 MB of each transcript was base64 image data (14 blobs each,
# product-image work read back through a tool). _session_too_large counted
# raw transcript bytes, so images tripped a rollover tokens never justified.
# Fixture below reproduces the shape verified against the real transcript:
# a tool_result content block nesting a type='image' block with a big
# base64 `source.data` string, alongside a small amount of real text.

def _fixture_transcript_with_images(path, *, num_images, image_b64_len, text_body):
    import json as _json
    lines = [_json.dumps({
        'type': 'user',
        'message': {'role': 'user', 'content': [{'type': 'text', 'text': text_body}]},
    })]
    for i in range(num_images):
        lines.append(_json.dumps({
            'type': 'user',
            'message': {
                'role': 'user',
                'content': [{
                    'type': 'tool_result',
                    'tool_use_id': f'tool_{i}',
                    'content': [{
                        'type': 'image',
                        'source': {'type': 'base64', 'media_type': 'image/png',
                                   'data': 'A' * image_b64_len},
                    }],
                }],
            },
        }))
    path.write_text('\n'.join(lines), encoding='utf-8')


def test_session_too_large_excludes_base64_image_bytes(tmp_data_dir, monkeypatch):
    """A transcript that is only over the byte cap because of base64 image
    payloads must NOT be reported as too-large — the image bytes are
    subtracted before comparing against _SESSION_SIZE_LIMIT."""
    m = _mem(tmp_data_dir)
    from mc.agent_runtime import ClaudeRuntime

    rt = ClaudeRuntime()
    fake_home = tmp_data_dir / 'claude_home' / 'projects'
    monkeypatch.setattr(m._agent_runtime, '_CLAUDE_HOME', fake_home)
    m._SESSION_SIZE_LIMIT = 5 * 1024 * 1024  # real 5 MB default

    project_path = str(tmp_data_dir / 'proj_images')
    session_id = '8e5ff4a0-64b4-45d0-b0c9-99647758c04e'
    encoded = rt._encode_project_path(project_path)
    cli_dir = fake_home / encoded
    cli_dir.mkdir(parents=True)
    jsonl_file = cli_dir / f'{session_id}.jsonl'

    # 14 images of ~400 KB base64 each (~5.5 MB) + a small amount of real
    # text — mirrors the measured 9.9 MB / 4.7 MB image / 85k-token transcript,
    # scaled up slightly so raw size clears the 5 MB limit in this fixture too.
    _fixture_transcript_with_images(
        jsonl_file, num_images=14, image_b64_len=400_000, text_body='x' * 2000)

    raw_size = jsonl_file.stat().st_size
    assert raw_size > m._SESSION_SIZE_LIMIT, 'fixture must reproduce the raw-bytes false positive'

    img_bytes = m._transcript_image_bytes(jsonl_file)
    assert img_bytes == 14 * 400_000

    too_large, size = m._session_too_large(project_path, session_id)
    assert too_large is False, \
        f'image bytes must be excluded: raw={raw_size} net={size} limit={m._SESSION_SIZE_LIMIT}'
    assert size == raw_size - img_bytes


def test_session_too_large_still_trips_on_real_text_bloat(tmp_data_dir, monkeypatch):
    """Control: a transcript over the cap on genuine (non-image) text must
    still trip — image-exclusion isn't a blanket size increase."""
    m = _mem(tmp_data_dir)
    from mc.agent_runtime import ClaudeRuntime

    rt = ClaudeRuntime()
    fake_home = tmp_data_dir / 'claude_home' / 'projects'
    monkeypatch.setattr(m._agent_runtime, '_CLAUDE_HOME', fake_home)
    m._SESSION_SIZE_LIMIT = 100  # bytes — small so a short fixture trips it

    project_path = str(tmp_data_dir / 'proj_text')
    session_id = 'aaaaaaaa-64b4-45d0-b0c9-99647758c04e'
    encoded = rt._encode_project_path(project_path)
    cli_dir = fake_home / encoded
    cli_dir.mkdir(parents=True)
    jsonl_file = cli_dir / f'{session_id}.jsonl'
    _fixture_transcript_with_images(
        jsonl_file, num_images=0, image_b64_len=0, text_body='x' * 500)

    too_large, size = m._session_too_large(project_path, session_id)
    assert too_large is True
    assert size == jsonl_file.stat().st_size
