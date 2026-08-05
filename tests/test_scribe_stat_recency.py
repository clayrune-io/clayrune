"""Scribe/condense counters must be self-dating (2026-08-05).

Lifetime totals alone cannot answer "is this still bleeding, or is it an old
backlog?" On 2026-08-05 that ambiguity produced a live misdiagnosis: reading
`condense_rejected:model_error` 96 + `model_timeout` 58 against 46 successes
gives a 22% success rate and the conclusion "condense is badly broken" — when
91+58 of those failures predated the switch to the haiku default, the post-fix
record was 41 successes to 5 errors, and a direct `_condense_plan` call returned
'ok' in 58s.

`distiller._increment_counter` already stamped `counters_last` for exactly this
reason. The scribe half never did. These pin the fix, plus the locked+atomic
write that replaced a plain `write_text` (a torn stats file 500s /scribe-stats).
"""
import importlib
import json
import threading


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


def _stats(m, pid):
    fp = m.DATA_DIR / f"{pid}_scribe_stats.json"
    return json.loads(fp.read_text(encoding="utf-8"))


def test_failure_counters_are_stamped(tmp_data_dir):
    m = _mem(tmp_data_dir)
    for key in ("scribe_fell_back:model_error", "condense_rejected:model_timeout",
                "checkpoint_skipped:model_refused", "condense_rejected:duplicate_id"):
        m._scribe_stat("recproj", key)
    s = _stats(m, "recproj")
    assert set(s["counters_last"]) == {
        "scribe_fell_back:model_error", "condense_rejected:model_timeout",
        "checkpoint_skipped:model_refused", "condense_rejected:duplicate_id",
    }
    for v in s["counters_last"].values():
        assert v and v.endswith("Z")


def test_success_counters_are_not_stamped(tmp_data_dir):
    """Only failure classes self-date — stamping every success counter would
    double the file for no diagnostic value."""
    m = _mem(tmp_data_dir)
    m._scribe_stat("recproj2", "scribe_extracted")
    m._scribe_stat("recproj2", "condense_structured_ok")
    m._scribe_stat("recproj2", "condense_entries_demoted", 5)
    s = _stats(m, "recproj2")
    assert s["scribe_extracted"] == 1
    assert s["condense_entries_demoted"] == 5
    assert "counters_last" not in s


def test_stamp_advances_on_a_later_bump(tmp_data_dir):
    m = _mem(tmp_data_dir)
    m._scribe_stat("recproj3", "scribe_fell_back:model_error")
    first = _stats(m, "recproj3")["counters_last"]["scribe_fell_back:model_error"]
    m._scribe_stat("recproj3", "scribe_fell_back:model_error")
    s = _stats(m, "recproj3")
    assert s["scribe_fell_back:model_error"] == 2
    assert s["counters_last"]["scribe_fell_back:model_error"] >= first


def test_counters_survive_a_corrupt_stats_file(tmp_data_dir):
    """A torn file must not wedge telemetry forever — and must not raise,
    since every caller treats this as best-effort."""
    m = _mem(tmp_data_dir)
    fp = m.DATA_DIR / "recproj4_scribe_stats.json"
    fp.write_text("{not json", encoding="utf-8")
    m._scribe_stat("recproj4", "scribe_extracted")
    assert _stats(m, "recproj4")["scribe_extracted"] == 1


def test_non_positive_bump_does_not_touch_the_file(tmp_data_dir):
    m = _mem(tmp_data_dir)
    m._scribe_stat("recproj5", "scribe_extracted", 0)
    m._scribe_stat("recproj5", "scribe_extracted", -3)
    assert not (m.DATA_DIR / "recproj5_scribe_stats.json").exists()


def test_concurrent_bumps_do_not_lose_counts(tmp_data_dir):
    """The old plain read-modify-write could drop a concurrent bump. Scribe,
    the checkpoint worker and condense all bump from different threads."""
    m = _mem(tmp_data_dir)
    threads = [threading.Thread(target=lambda: [
        m._scribe_stat("recproj6", "scribe_extracted") for _ in range(20)])
        for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _stats(m, "recproj6")["scribe_extracted"] == 120


def test_stats_file_stays_excluded_from_project_loading(tmp_data_dir):
    """LOAD-BEARING (CLAUDE.md): load_projects() treats every *.json in
    DATA_DIR as a project. A stats sidecar that leaks in becomes a malformed
    project and 500s the restart endpoints."""
    m = _mem(tmp_data_dir)
    m._scribe_stat("recproj7", "scribe_fell_back:model_error")
    srv = importlib.import_module("server")
    ids = {p.get("id") for p in srv.load_projects()}
    assert "recproj7_scribe_stats" not in ids
