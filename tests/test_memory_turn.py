"""mc/memory_turn.py — per-turn memory delivery (MC-944, MEMORY_DESIGN_V2_SPEC.md
build-sequence step 5, §9.6).

THE ACCEPTANCE TEST (spec §2). Measured 2026-09-06: a session resumed on a task
string of "Hi Dave" and continued for five days retrieved nothing new for the
rest of its life — the read floor only ever ran once, at
`_build_agent_context()`, and a live Mode-B process never re-enters it. Replayed
live, 24 delivered read-floor slots surfaced zero mentions of a 346 KB design
this project had already finished. `test_a_live_followup_surfaces_what_the_stale_
dispatch_task_missed` below is that trace, reproduced against a synthetic vault:
dispatch happens on an unrelated task ("Hi Dave"); a LATER live turn is a
near-verbatim restatement of the design note's own subject — the exact shape of
the spec's pass condition (§2.3) and its own worked example query. On master,
`mc.memory_turn` does not exist, so this fails at collection; after the fix, the
live turn's block names the note dispatch never saw.

Conventions borrowed from tests/test_memory_search_bm25.py (`_mem`/`_seed`
reload-server fixture) and tests/test_memory_fts.py (`_write_transcript`/
`_wire_claude_home` for the cold tier).
"""
import importlib

from mc import agent_runtime


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


def _turn(m):
    import mc.memory_turn as mt
    return mt


def _seed(m, p, files):
    """Write {name: text} into the project's memory dir, plus a minimal
    MEMORY.md so the corpus loader has its sentinels."""
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Index", [], []), encoding="utf-8")
    for name, text in files.items():
        (mp.parent / name).write_text(text, encoding="utf-8")
    return mp


DESIGN_NOTE = "design_memory_redesign_2026_08.md"
DESIGN_TEXT = (
    "OKF Agent Memory versus Clayrune's own memory layer redesign. Compared the "
    "OKF SaveConcept format against our memory layer and rejected it as a "
    "replacement substrate. Rejected: a two-level lazy index, a vector database, "
    "cross-encoder rerank (measured worse, 46 -> 41). The wikilink layer measured "
    "best of the alternatives considered."
)
UNRELATED_NOTE = "unrelated_widget_calibration.md"
UNRELATED_TEXT = "widget calibration notes: the offset drifts by 3mm after reflow."


# ── the acceptance test (§2) ─────────────────────────────────────────────────

def test_a_live_followup_surfaces_what_the_stale_dispatch_task_missed(tmp_data_dir):
    """§2.3's own worked example: 'compare OKF agent memory to our memory
    layer' on a session dispatched on an unrelated task must surface the prior
    design — not at dispatch (which never saw it) but on the LATER live turn.
    """
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "acceptanceproj"}
    _seed(m, p, {DESIGN_NOTE: DESIGN_TEXT, UNRELATED_NOTE: UNRELATED_TEXT})

    session = {}
    # Dispatch: the task string that actually broke B5 in the trace.
    mt.seed_delivered(p, session, "Hi Dave")
    assert not session.get("_mem_turn_delivered"), (
        "an unrelated dispatch task must not seed the design note as delivered")

    # A later live turn: the exact restatement quoted in spec §2.3.
    result = mt.refresh_for_turn(
        p, session, "compare okf agent memory to our memory layer")
    assert DESIGN_NOTE in result["block"], (
        f"expected the prior design note in the per-turn block, got: {result['block']!r}")
    assert DESIGN_NOTE in result["delivered"]
    assert result["bytes"] > 0
    assert result["bytes"] <= mt.turn_budget_bytes()


def test_before_fix_a_direct_stdin_write_carried_no_memory_at_all():
    """Names the regression precisely: today's (pre-MC-944) direct-stdin-write
    sites build `{"role": "user", "content": claude_content}` straight from
    `_apply_mobile_brief(message, data)` — no memory of any kind. This asserts
    the module this fix adds actually exists and is callable; on master,
    `import mc.memory_turn` raises ModuleNotFoundError and every test in this
    file fails at collection.
    """
    import mc.memory_turn as mt
    assert callable(mt.refresh_for_turn)
    assert callable(mt.seed_delivered)


# ── delivered-set suppression ────────────────────────────────────────────────

def test_a_note_delivered_once_is_suppressed_on_the_next_turn(tmp_data_dir):
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "dedupeproj"}
    _seed(m, p, {DESIGN_NOTE: DESIGN_TEXT})
    session = {}

    first = mt.refresh_for_turn(p, session, "tell me about the memory redesign")
    assert DESIGN_NOTE in first["delivered"]
    assert DESIGN_NOTE in first["block"]

    second = mt.refresh_for_turn(p, session, "tell me about the memory redesign")
    assert DESIGN_NOTE in second["suppressed"], second
    assert DESIGN_NOTE not in second["delivered"]
    assert DESIGN_NOTE not in second["block"]
    # Nothing else exists in the corpus to backfill with, and the raw search
    # DID match (it was just suppressed) — no cold probe should fire either.
    assert second["cold_used"] is False
    assert second["block"] == ""


def test_seed_delivered_prevents_a_dispatch_hit_from_looking_new_later(tmp_data_dir):
    """The dispatch/revival half of the mechanism: whatever the system prompt
    already delivered must not be re-announced as 'new' on turn 2."""
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "seedproj"}
    _seed(m, p, {DESIGN_NOTE: DESIGN_TEXT})
    session = {}

    mt.seed_delivered(p, session, "tell me about the memory redesign")
    assert DESIGN_NOTE in session["_mem_turn_delivered"]

    followup = mt.refresh_for_turn(p, session, "tell me about the memory redesign")
    assert DESIGN_NOTE in followup["suppressed"]
    assert followup["block"] == ""


# ── positions are exempt from suppression ───────────────────────────────────

def test_a_position_re_surfaces_every_turn_unlike_a_note(tmp_data_dir):
    """A position is a standing 'do not re-propose this' warning, not a
    one-time fact — mc/memory.py §5.1's Kill 3 is that presence once in
    context did not stop a 2026-08-23 re-proposal. Suppressing it after one
    delivery would defeat the one thing it exists to do."""
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "posproj"}
    _seed(m, p, {})
    fname = m.write_position(
        p, subject="Adopt Obsidian as the memory substrate", verdict="declined",
        reason="already evaluated and rejected on portability grounds",
        triggers="obsidian")
    session = {}

    first = mt.refresh_for_turn(p, session, "should we use obsidian for this")
    assert fname in first["positions"]
    assert "DECLINED" in first["block"]

    second = mt.refresh_for_turn(p, session, "should we use obsidian for this")
    assert fname in second["positions"], (
        "a position must re-surface every turn it matches, never be suppressed")
    assert "DECLINED" in second["block"]


# ── cold probe on a true warm miss ──────────────────────────────────────────

def _write_transcript(path, session_id, turns):
    import json
    lines = []
    for role, text in turns:
        if role == 'user':
            lines.append(json.dumps(
                {'type': 'user', 'message': {'role': 'user', 'content': text},
                 'timestamp': '2026-08-16T00:00:00Z'}))
        else:
            lines.append(json.dumps(
                {'type': 'assistant',
                 'message': {'content': [{'type': 'text', 'text': text}]},
                 'timestamp': '2026-08-16T00:00:01Z'}))
    (path / f'{session_id}.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_cold_probe_fires_only_when_the_warm_floor_is_truly_empty(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    fake_home = tmp_path / '.claude' / 'projects'
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, 'CLAUDE_HOME', fake_home)
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)

    import mc.memory_fts as fts
    p = {"id": "coldproj", "project_path": str(tmp_path / 'proj')}
    _seed(m, p, {})  # empty curated corpus — the warm floor has nothing at all

    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    tdir.mkdir(parents=True, exist_ok=True)
    _write_transcript(tdir, 'sess-cold', [
        ('user', 'what did we decide about the two-level lazy index proposal'),
        ('assistant', 'Rejected: it moves 70% of the index behind an act that '
                       'happens in 5% of sessions, disproved by measurement.'),
    ])
    fts.build_index(p)

    session = {}
    result = mt.refresh_for_turn(p, session, "two-level lazy index proposal")
    assert result["cold_used"] is True
    assert "cold tier" in result["block"]
    assert "session:sess-cold" in result["block"]


def test_cold_probe_does_not_fire_when_the_note_was_merely_suppressed(tmp_data_dir):
    """A note that matched and was already delivered is not a miss — firing
    the cold tier there would just repeat the same answer via a second
    channel, which is not what §9.4's miss condition is for."""
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "nomisscoldproj"}
    _seed(m, p, {DESIGN_NOTE: DESIGN_TEXT})
    session = {}
    mt.refresh_for_turn(p, session, "tell me about the memory redesign")
    second = mt.refresh_for_turn(p, session, "tell me about the memory redesign")
    assert second["cold_used"] is False


# ── budget bound ─────────────────────────────────────────────────────────────

def test_the_per_turn_block_never_exceeds_the_configured_budget(tmp_data_dir):
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "budgetproj"}
    from mc import state
    state.CONFIG['memory_turn_budget_bytes'] = 300
    files = {}
    long_body = ("memory design redesign wikilink negation index prompt budget " * 30)
    for i in range(8):
        files[f"note_{i}.md"] = f"memory design note {i}. {long_body}"
    _seed(m, p, files)
    session = {}

    result = mt.refresh_for_turn(p, session, "memory design redesign prompt budget")
    assert result["bytes"] <= 300
    assert len(result["block"].encode("utf-8")) <= 300


def test_disabling_the_feature_is_a_full_bypass(tmp_data_dir):
    m = _mem(tmp_data_dir)
    mt = _turn(m)
    p = {"id": "offproj"}
    _seed(m, p, {DESIGN_NOTE: DESIGN_TEXT})
    from mc import state
    state.CONFIG['memory_turn_refresh_enabled'] = False
    session = {}
    result = mt.refresh_for_turn(p, session, "compare okf agent memory to our memory layer")
    assert result == {'block': '', 'delivered': [], 'suppressed': [],
                       'positions': [], 'cold_used': False, 'bytes': 0}
    mt.seed_delivered(p, session, "compare okf agent memory to our memory layer")
    assert not session.get('_mem_turn_delivered')
