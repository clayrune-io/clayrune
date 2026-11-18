"""MC-1179 ML16: `condense_mode: 'agent'` (the shipped default until
2026-11-17; now what a config that says 'agent' gets, see
tests/test_condense_mode_default.py).

Two defects, both pinned here:

  * **The trigger could not clear.** It summed MEMORY.md + SESSION_LOG.md +
    the permanent archive (+ CLAUDE.md) against `condense_threshold_kb`. The
    archive is append-only and is where a condense run MOVES things, so a run
    left the sum where it was; past the threshold a project launched one
    tool-capable agent per cooldown for the rest of its life.
  * **The prompt described a layout that is gone.** It told the agent that
    MEMORY.md has a managed region (sentinels, `## Session Log`, watermark
    lines) and to keep that structure. Since the SESSION_LOG.md split
    MEMORY.md holds none of it.

No model is called: `subprocess.Popen` is replaced and the command captured.
"""
import importlib
import time

import pytest


@pytest.fixture
def s(tmp_data_dir, monkeypatch):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    import mc.state as state
    monkeypatch.setattr(m, "_native_memory_path", lambda pp: None)
    monkeypatch.setitem(state.CONFIG, "condense_mode", "agent")
    monkeypatch.setitem(state.CONFIG, "condense_enabled", True)
    monkeypatch.setitem(state.CONFIG, "condense_threshold_kb", 30)
    monkeypatch.setitem(state.CONFIG, "index_line_budget", 160)
    m._condense_triggered_at.clear()
    m._condensing_projects.clear()
    yield m
    # `_dispatch_condense` stamps the cooldown for this project id; other test
    # files reuse the id and would find their trigger refused.
    m._condense_triggered_at.clear()
    m._condensing_projects.clear()


def _project(s, tmp_path, *, memory_lines=10, archive_kb=0, claude_md_kb=0,
             session_entries=0, line="- [topic](topic.md): a pointer line"):
    """A migrated project: MEMORY.md is curated pointer lines only."""
    ppdir = tmp_path / "proj"
    ppdir.mkdir(exist_ok=True)
    p = {"id": "tproj", "project_path": str(ppdir)}
    mp = s._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text("# Index\n" + "\n".join([line] * memory_lines) + "\n",
                  encoding="utf-8")
    if archive_kb:
        s._get_archive_path(p).write_text(
            "## Archived Session Log\n" + "- [d] **old** x\n" * (archive_kb * 64),
            encoding="utf-8")
    if claude_md_kb:
        (ppdir / "CLAUDE.md").write_text("x" * (claude_md_kb * 1024),
                                         encoding="utf-8")
    if session_entries:
        s._get_session_log_path(p).write_text(
            s._mem_compose("", [f"- [d] **e{i}** x" for i in range(session_entries)], []),
            encoding="utf-8")
    return p


def _old_gate(s, p):
    """The size gate exactly as it was: every file summed against the
    threshold. The fixed trigger may only fire where this one did."""
    import mc.state as state
    from pathlib import Path
    total = 0
    for f in (s._get_memory_path(p), s._get_session_log_path(p),
              s._get_archive_path(p), Path(p["project_path"]) / "CLAUDE.md"):
        if f.exists():
            total += f.stat().st_size
    return total > state.CONFIG["condense_threshold_kb"] * 1024


# ── the trigger ──────────────────────────────────────────────────────────────

def test_a_large_archive_alone_does_not_fire(s, tmp_path):
    """The archive is permanent and only grows. With the index inside its
    budget there is nothing a run could do."""
    p = _project(s, tmp_path, memory_lines=10, archive_kb=200)
    assert _old_gate(s, p)
    assert s._should_condense(p, include_claude_md=True) is False


def test_a_large_claude_md_alone_does_not_fire(s, tmp_path):
    p = _project(s, tmp_path, memory_lines=10, claude_md_kb=200)
    assert _old_gate(s, p)
    assert s._should_condense(p, include_claude_md=True) is False


def test_the_trigger_clears_once_the_run_has_done_its_job(s, tmp_path):
    """A run moves lines from the index to the archive. The combined size does
    not change; the trigger must still clear."""
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40)
    assert s._should_condense(p, include_claude_md=True) is True

    mp, ap = s._get_memory_path(p), s._get_archive_path(p)
    lines = mp.read_text(encoding="utf-8").splitlines()
    before = mp.stat().st_size + ap.stat().st_size
    mp.write_text("\n".join(lines[:100]) + "\n", encoding="utf-8")
    with ap.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines[100:]) + "\n")
    assert mp.stat().st_size + ap.stat().st_size >= before - 2   # nothing shrank

    assert s._should_condense(p, include_claude_md=True) is False


def test_an_index_over_its_line_budget_fires(s, tmp_path):
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40)
    assert s._should_condense(p, include_claude_md=True) is True


def test_an_index_over_its_byte_budget_fires(s, tmp_path):
    p = _project(s, tmp_path, memory_lines=40, archive_kb=10,
                 line="- [topic](topic.md): " + "x" * 1000)      # ~40 KB, 41 lines
    assert s._get_memory_path(p).stat().st_size > s._index_byte_trigger()
    assert s._should_condense(p, include_claude_md=True) is True


def test_the_size_gate_still_holds(s, tmp_path):
    """`condense_threshold_kb` keeps its meaning: below it nothing fires, even
    with the index over its line budget. The fix adds no way to launch a run."""
    p = _project(s, tmp_path, memory_lines=400, line="- x")       # ~1.6 KB
    assert not _old_gate(s, p)
    assert s._should_condense(p, include_claude_md=True) is False


@pytest.mark.parametrize("memory_lines", [5, 150, 170, 400])
@pytest.mark.parametrize("archive_kb", [0, 20, 200])
@pytest.mark.parametrize("claude_md_kb", [0, 20, 60])
def test_fires_only_where_the_old_trigger_fired(s, tmp_path, memory_lines,
                                                archive_kb, claude_md_kb):
    p = _project(s, tmp_path, memory_lines=memory_lines, archive_kb=archive_kb,
                 claude_md_kb=claude_md_kb, session_entries=5)
    if s._should_condense(p, include_claude_md=True):
        assert _old_gate(s, p)
        assert memory_lines > 160


def test_a_missing_or_unreadable_index_does_not_fire_or_raise(s, tmp_path):
    p = _project(s, tmp_path, memory_lines=400, archive_kb=200)
    s._get_memory_path(p).unlink()
    assert s._should_condense(p, include_claude_md=True) is False


def test_disabled_cooldown_and_in_flight_still_refuse(s, tmp_path, monkeypatch):
    import mc.state as state
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40)
    assert s._should_condense(p, include_claude_md=True) is True
    s._condense_triggered_at[p["id"]] = time.time()
    assert s._should_condense(p, include_claude_md=True) is False
    s._condense_triggered_at.clear()
    s._condensing_projects.add(p["id"])
    assert s._should_condense(p, include_claude_md=True) is False
    s._condensing_projects.clear()
    monkeypatch.setitem(state.CONFIG, "condense_enabled", False)
    assert s._should_condense(p, include_claude_md=True) is False


# ── the prompt the agent is launched with ────────────────────────────────────

def _launch_and_capture(s, p, monkeypatch):
    """Run `_dispatch_condense` with Popen replaced; return the prompt."""
    captured = {}

    class _NoLaunch(Exception):
        pass

    def _fake_popen(cmd, *a, **k):
        captured["cmd"] = list(cmd)
        raise _NoLaunch("test: no process is started")

    monkeypatch.setattr(s.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(s, "_resolve_claude", lambda: "claude-test-double")
    monkeypatch.setattr(s, "_model_for_provider", lambda *a, **k: "")
    s._dispatch_condense(p)
    deadline = time.time() + 10
    while p["id"] in s._condensing_projects and time.time() < deadline:
        time.sleep(0.02)
    assert "cmd" in captured, "the dispatcher never reached the launch"
    cmd = captured["cmd"]
    return cmd[cmd.index("-p") + 1]


def test_split_layout_prompt_names_no_managed_region(s, tmp_path, monkeypatch):
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40, session_entries=5)
    prompt = _launch_and_capture(s, p, monkeypatch)

    assert "clayrune:managed:begin" not in prompt
    assert "clayrune:managed:end" not in prompt
    assert "MANAGED region" not in prompt
    assert "keep the sentinels" not in prompt
    assert str(s._get_memory_path(p)) in prompt
    assert str(s._get_archive_path(p)) in prompt


def test_split_layout_prompt_puts_the_session_log_off_limits(s, tmp_path,
                                                             monkeypatch):
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40, session_entries=5)
    prompt = _launch_and_capture(s, p, monkeypatch)

    log = str(s._get_session_log_path(p))
    assert log in prompt, "the agent is not told SESSION_LOG.md exists"
    after = prompt[prompt.index(log):]
    assert "do NOT modify" in after[:400]
    assert "`## Session Log`" in prompt and "Do not add" in prompt


def test_inline_layout_prompt_still_protects_the_managed_region(s, tmp_path,
                                                                monkeypatch):
    """A MEMORY.md that has not been migrated yet still carries the region
    inline; the agent must still be told to keep it and its watermarks."""
    p = _project(s, tmp_path, memory_lines=10, archive_kb=40)
    mp = s._get_memory_path(p)
    wm = "<!-- clayrune:wm:abc123 {} -->"
    mp.write_text(s._mem_compose(
        "# Index", [f"- [d] **e{i}** x" for i in range(300)], [wm]),
        encoding="utf-8")
    prompt = _launch_and_capture(s, p, monkeypatch)

    assert "clayrune:managed:begin" in prompt
    assert "clayrune:managed:end" in prompt
    assert "`<!-- clayrune:wm:`" in prompt and "PRESERVE IT" in prompt


@pytest.mark.parametrize("claude_md_kb, expected", [(0, False), (10, False),
                                                    (20, True)])
def test_claude_md_section_only_for_a_large_claude_md(s, tmp_path, monkeypatch,
                                                      claude_md_kb, expected):
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40,
                 claude_md_kb=claude_md_kb)
    prompt = _launch_and_capture(s, p, monkeypatch)
    assert ("CLAUDE.md condensation" in prompt) is expected


def test_the_prompt_states_the_budget_the_trigger_measures(s, tmp_path,
                                                           monkeypatch):
    p = _project(s, tmp_path, memory_lines=400, archive_kb=40)
    prompt = _launch_and_capture(s, p, monkeypatch)
    assert "under 160 LINES" in prompt
    assert f"under {s._index_byte_trigger() // 1024} KB" in prompt
