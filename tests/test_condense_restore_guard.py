"""MC-1179 ML21: the agent-mode condense restore and heal.

Both used to be a plain `write_text` outside the per-project memory write
lock. A session commit that landed between the end of the agent run and the
repair was overwritten by the pre-image.

Now: MEMORY.md is read once when the run ends (`condense_restore.EndImage`),
the integrity check judges that text, and the repair takes the write lock,
re-reads, and writes only if the file still holds what the end image held.
Otherwise the file is left, the skip is logged and counted.

No model is called: `subprocess.Popen` and the stream reader are replaced.
"""
import importlib
import time

import pytest

import mc.condense_restore as cr

PRE = "# Index\n" + "- [topic](topic.md): a pointer line\n" * 40
LATER = PRE + "- [new](new.md): a commit that landed after the run ended\n"


# ── the module on its own ────────────────────────────────────────────────────

def test_restore_writes_the_pre_image_when_the_file_is_unchanged(tmp_path):
    mp = tmp_path / "MEMORY.md"
    mp.write_text("", encoding="utf-8")
    end = cr.EndImage(mp)
    assert cr.restore("p-unchanged", mp, PRE, end) is True
    assert mp.read_text(encoding="utf-8") == PRE


def test_restore_leaves_a_file_that_changed_after_the_run_ended(tmp_path):
    mp = tmp_path / "MEMORY.md"
    mp.write_text("", encoding="utf-8")
    end = cr.EndImage(mp)
    mp.write_text(LATER, encoding="utf-8")
    assert cr.restore("p-changed", mp, PRE, end) is False
    assert mp.read_text(encoding="utf-8") == LATER


def test_a_missing_file_is_restored_and_a_recreated_one_is_left(tmp_path):
    mp = tmp_path / "MEMORY.md"
    end = cr.EndImage(mp)
    assert end.replay() == ""
    assert cr.restore("p-missing", mp, PRE, end) is True
    assert mp.read_text(encoding="utf-8") == PRE

    mp2 = tmp_path / "MEMORY2.md"
    end2 = cr.EndImage(mp2)
    mp2.write_text(LATER, encoding="utf-8")
    assert cr.restore("p-missing", mp2, PRE, end2) is False
    assert mp2.read_text(encoding="utf-8") == LATER


def test_an_unreadable_end_image_replays_its_error_and_still_restores(tmp_path):
    mp = tmp_path / "MEMORY.md"
    mp.write_bytes(b"\xff\xfe\xff not utf-8 \xff")
    end = cr.EndImage(mp)
    with pytest.raises(UnicodeDecodeError):
        end.replay()
    assert cr.restore("p-unreadable", mp, PRE, end) is True
    assert mp.read_text(encoding="utf-8") == PRE


def test_restore_and_heal_write_under_the_project_write_lock(tmp_path, monkeypatch):
    held = []
    real = cr._atomic_write_text

    def _spy(path, text, *a, **k):
        held.append(cr._get_mem_write_lock("p-lock").locked())
        return real(path, text, *a, **k)

    monkeypatch.setattr(cr, "_atomic_write_text", _spy)
    mp = tmp_path / "MEMORY.md"
    mp.write_text("", encoding="utf-8")
    assert cr.restore("p-lock", mp, PRE, cr.EndImage(mp)) is True
    assert cr.heal("p-lock", mp, ["<!-- wm -->"], cr.EndImage(mp),
                   lambda t: (t, [], []),
                   lambda c, e, w: c + "".join(w)) is True
    assert held == [True, True]
    assert not cr._get_mem_write_lock("p-lock").locked()


def test_heal_reinjects_only_when_the_file_is_unchanged(tmp_path):
    import mc.memory as m
    wm = "<!-- clayrune:wm:abc123 -->"
    post = m._mem_compose("# Index\n- [a](a.md): a\n", ["- [d] **e** x"], [])
    mp = tmp_path / "MEMORY.md"
    mp.write_text(post, encoding="utf-8")
    end = cr.EndImage(mp)
    assert cr.heal("p-heal", mp, [wm], end, m._mem_split_full,
                   m._mem_compose) is True
    assert wm in m._mem_split_full(mp.read_text(encoding="utf-8"))[2]

    mp.write_text(post, encoding="utf-8")
    end = cr.EndImage(mp)
    changed = post + "\n- a later line\n"
    mp.write_text(changed, encoding="utf-8")
    assert cr.heal("p-heal", mp, [wm], end, m._mem_split_full,
                   m._mem_compose) is False
    assert mp.read_text(encoding="utf-8") == changed


# ── through the dispatcher ───────────────────────────────────────────────────

@pytest.fixture
def s(tmp_data_dir, monkeypatch):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    import mc.state as state
    monkeypatch.setattr(m, "_native_memory_path", lambda pp: None)
    monkeypatch.setitem(state.CONFIG, "condense_mode", "agent")
    monkeypatch.setitem(state.CONFIG, "condense_enabled", True)
    m._condense_triggered_at.clear()
    m._condensing_projects.clear()
    sessions_before = set(state.agent_sessions)
    yield m
    m._condense_triggered_at.clear()
    m._condensing_projects.clear()
    # The run registers a housekeeping session with status 'running', and the
    # real stream reader is what ends it. _run_condense stubs that reader, so
    # the record stayed 'running' with no trigger_type for the rest of the
    # pytest process, and mc.unattended then took every later Origin-less
    # request for an unattended agent's (26 Desk tests answered 403 in the
    # default order).
    for sid in set(state.agent_sessions) - sessions_before:
        left = state.agent_sessions.pop(sid, None) or {}
        m.get_manager(left.get("project_id")).session_ids.discard(sid)


class _Proc:
    pid = 0
    returncode = 1


def _run_condense(s, tmp_path, monkeypatch, *, after_check=None):
    """One agent-mode run whose "agent" empties MEMORY.md and exits 1.
    `after_check(mem_path)` runs between the integrity check and the repair,
    which is where a concurrent session commit lands."""
    ppdir = tmp_path / "proj"
    ppdir.mkdir(exist_ok=True)
    p = {"id": "tproj_ml21", "project_path": str(ppdir)}
    mp = s._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(PRE, encoding="utf-8")

    stats = []
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(s, "_resolve_claude", lambda: "claude-test-double")
    monkeypatch.setattr(s, "_model_for_provider", lambda *a, **k: "")
    monkeypatch.setattr(s, "_hide_windows_delayed", lambda pid: None)
    monkeypatch.setattr(s, "_register_process", lambda *a, **k: None)
    monkeypatch.setattr(s, "_read_agent_stream",
                        lambda proc, session: mp.write_text("", encoding="utf-8"))
    monkeypatch.setattr(s, "_scribe_stat",
                        lambda pid, key, n=1: stats.append((pid, key)))
    real_check = s._condense_integrity_check

    def _check(*a, **k):
        out = real_check(*a, **k)
        if after_check:
            after_check(mp)
        return out

    monkeypatch.setattr(s, "_condense_integrity_check", _check)
    s._dispatch_condense(p)
    deadline = time.time() + 10
    while p["id"] in s._condensing_projects and time.time() < deadline:
        time.sleep(0.02)
    assert p["id"] not in s._condensing_projects, "the run never finished"
    return p, mp, stats


def test_a_truncated_run_is_still_restored(s, tmp_path, monkeypatch):
    p, mp, stats = _run_condense(s, tmp_path, monkeypatch)
    assert mp.read_text(encoding="utf-8") == PRE
    assert stats == []
    assert "restored pre-image" in s._get_condense_status(p["id"])["error"]


def test_a_commit_after_the_run_ended_is_not_overwritten(s, tmp_path, monkeypatch):
    p, mp, stats = _run_condense(
        s, tmp_path, monkeypatch,
        after_check=lambda path: path.write_text(LATER, encoding="utf-8"))
    assert mp.read_text(encoding="utf-8") == LATER
    assert stats == [(p["id"], "condense_restore_skipped_changed")]
    st = s._get_condense_status(p["id"])
    assert st["state"] == "error"
    assert "not restored" in st["error"]
    assert "restored pre-image" not in st["error"]
