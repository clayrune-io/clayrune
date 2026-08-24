"""Delivery telemetry — DAVE_DESIGN §9 phase 4.

The measurement that MC-892's eviction attempt lacked: *which* memory actually
reaches a prompt. These tests pin the four properties the residency decision
will rest on, so a later tweak cannot quietly turn the counters into noise.
"""
import importlib


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


def _seed(m, p, files, archive_lines=None):
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Index", [], []), encoding="utf-8")
    for name, text in files.items():
        (mp.parent / name).write_text(text, encoding="utf-8")
    if archive_lines:
        (mp.parent / m._get_archive_path(p).name).write_text(
            "\n".join(archive_lines) + "\n", encoding="utf-8")
    return mp


# ── identity: a filename is not an identity for a line class ────────────────

def test_archive_lines_get_distinct_uids_under_one_filename(tmp_data_dir):
    """The core claim. ~2.5k archive lines share one label; keying telemetry on
    the label would credit every line with its neighbours' hits."""
    m = _mem(tmp_data_dir)
    p = {"id": "delivuid"}
    _seed(m, p, {}, archive_lines=[
        "- [2026-08-01] **a** — kestrelword one",
        "- [2026-08-02] **b** — kestrelword two",
    ])
    mp = m._get_memory_path(p)
    units = m._mem_corpus(mp.parent, mp.name, m._get_archive_path(p).name)
    arch = [u for u in units if u["cls"] == "archive"]
    assert len(arch) == 2
    assert arch[0]["file"] == arch[1]["file"]          # same container
    assert arch[0]["uid"] != arch[1]["uid"]            # different claims
    assert all(u["uid"].startswith(u["file"] + "#") for u in arch)


def test_topic_note_uid_survives_an_edit(tmp_data_dir):
    """A note keeps its history when its body changes — it is the same note.
    A LINE does not, because an edited line is a different claim."""
    m = _mem(tmp_data_dir)
    assert m._unit_uid("arch_thing.md", "one", "topic") == \
        m._unit_uid("arch_thing.md", "two", "topic")
    assert m._unit_uid("MEMORY_ARCHIVE.md", "one", "archive") != \
        m._unit_uid("MEMORY_ARCHIVE.md", "two", "archive")


# ── the counters ────────────────────────────────────────────────────────────

def test_read_floor_delivery_is_counted_human_search_is_not(tmp_data_dir):
    """Opt-in per call site. The memory-search box calls the same function, and
    a human looking a note up is not evidence that it earns its residency —
    otherwise anyone can inflate a note's score by searching for it."""
    m = _mem(tmp_data_dir)
    import mc.memory_delivery as d
    p = {"id": "delivcount"}
    _seed(m, p, {"arch_kestrel.md": "kestrelword is the subject of this note"})

    m._memory_search(p, "kestrelword")                      # human search
    assert d.read_stats(p) == {}

    m._memory_search(p, "kestrelword", record="read_floor")  # a real delivery
    st = d.read_stats(p)
    assert st["tasks"] == 1
    assert st["contexts"] == {"read_floor": 1}
    rec = st["units"]["arch_kestrel.md"]
    assert rec["n"] == 1 and rec["cls"] == "topic"
    assert "kestrelword" in rec["head"]

    m._memory_search(p, "kestrelword", record="read_floor")
    st = d.read_stats(p)
    assert st["tasks"] == 2
    assert st["units"]["arch_kestrel.md"]["n"] == 2


def test_task_counter_advances_even_when_nothing_matched(tmp_data_dir):
    """The denominator is what makes a zero readable: never-delivered over
    three tasks is noise, never over three hundred is a demotion."""
    m = _mem(tmp_data_dir)
    import mc.memory_delivery as d
    p = {"id": "delivdenom"}
    _seed(m, p, {"arch_kestrel.md": "kestrelword is the subject"})
    m._memory_search(p, "somethingelseentirely", record="read_floor")
    st = d.read_stats(p)
    assert st["tasks"] == 1
    assert st.get("units") == {}


def test_summary_reports_what_never_arrived(tmp_data_dir):
    """The never-delivered set is the half that matters for demotion, and the
    counters cannot produce it — they only know what did arrive."""
    m = _mem(tmp_data_dir)
    import mc.memory_delivery as d
    p = {"id": "delivnever"}
    _seed(m, p, {"arch_kestrel.md": "kestrelword subject",
                 "arch_dormant.md": "dormantword subject"})
    m._memory_search(p, "kestrelword", record="read_floor")
    s = d.summary(p, m.corpus_uids(p))
    assert s["tasks"] == 1
    assert [r["uid"] for r in s["delivered"]] == ["arch_kestrel.md"]
    assert "arch_dormant.md" in [r["uid"] for r in s["never"]]


# ── never load-bearing ──────────────────────────────────────────────────────

def test_telemetry_can_be_switched_off(tmp_data_dir):
    m = _mem(tmp_data_dir)
    import mc.memory_delivery as d
    from mc import state
    p = {"id": "delivoff"}
    _seed(m, p, {"arch_kestrel.md": "kestrelword subject"})
    state.CONFIG["delivery_telemetry_enabled"] = False
    try:
        m._memory_search(p, "kestrelword", record="read_floor")
        assert d.read_stats(p) == {}
    finally:
        state.CONFIG.pop("delivery_telemetry_enabled", None)


def test_a_telemetry_failure_never_costs_the_read_floor(tmp_data_dir, monkeypatch):
    """The floor is the only retrieval channel that actually runs. A counter
    must never be able to take it down."""
    m = _mem(tmp_data_dir)
    import mc.memory_delivery as d
    p = {"id": "delivboom"}
    _seed(m, p, {"arch_kestrel.md": "kestrelword subject"})

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(d, "_write", boom)
    hits = m._memory_search(p, "kestrelword", record="read_floor")
    assert [h["file"] for h in hits] == ["arch_kestrel.md"]


def test_internal_keys_never_leak_into_the_public_result(tmp_data_dir):
    """`uid`/`head`/`cls` are bookkeeping. Read-floor callers unpack these."""
    m = _mem(tmp_data_dir)
    p = {"id": "delivshape"}
    _seed(m, p, {"arch_kestrel.md": "kestrelword subject"})
    for h in m._memory_search(p, "kestrelword", record="read_floor"):
        assert set(h) <= {"file", "score", "snippet", "via", "link"}
