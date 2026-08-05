"""BM25 ranking for the memory read floor (2026-08-05, backlog dae8d6e7).

The old scorer was `sum(text.count(term))` with no document-length
normalization, so long files beat relevant ones. Replaying the read floor over
142 real dispatched tasks from this repo's transcript history:

    OLD (tf)   reachable 17/75 topic files | top-3 units held 93% of slots
    NEW BM25   reachable 42/75 topic files | top-3 units held 52% of slots

These tests pin the properties that produced that, so a future tweak can't
quietly reintroduce the length bias.
"""
import importlib


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


def _seed(m, p, files):
    """Write {name: text} into the project's memory dir, plus a minimal
    MEMORY.md so the corpus loader has its sentinels."""
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Index", [], []), encoding="utf-8")
    for name, text in files.items():
        (mp.parent / name).write_text(text, encoding="utf-8")
    return mp


# ── tokenizer: query and document must agree ─────────────────────────────────

def test_tokenizer_splits_snake_case_and_paths():
    import mc.memory as m
    assert m._mem_tokens("project_memory_system_redesign") == \
        ["project", "memory", "system", "redesign"]
    assert m._mem_tokens("mc/memory.py") == ["memory"]  # 'mc' and 'py' < 3 chars
    assert m._mem_tokens("BM25 Scorer") == ["bm25", "scorer"]


def test_tokenizer_drops_short_tokens_and_handles_empty():
    import mc.memory as m
    assert m._mem_tokens("a an of") == []   # <3 chars dropped
    assert m._mem_tokens("a the of") == ["the"]  # length is the only filter
    assert m._mem_tokens("") == []
    assert m._mem_tokens(None) == []


# ── the actual bug: length must not beat relevance ───────────────────────────

def test_short_relevant_file_beats_long_incidental_one(tmp_data_dir):
    """THE regression test. The long file mentions the query terms more times
    in absolute count purely because it is long; the short file is about them.
    The old scorer ranked the long one first."""
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj"}
    _seed(m, p, {
        "short_about_widgets.md": "widget calibration. the widget offset is 3.",
        "long_about_everything.md": (
            "deployment notes. " * 400) + ("widget " * 6) + ("logging notes. " * 400),
    })
    hits = m._memory_search(p, "widget calibration offset", topk=2)
    assert hits, "expected matches"
    assert hits[0]["file"] == "short_about_widgets.md"


def test_class_avgdl_keeps_topic_files_and_archive_lines_separate(tmp_data_dir):
    """Textbook BM25 with one global avgdl would be set by the ~30x more
    numerous one-line archive entries and would score every topic file as
    pathologically long — the mirror image of the bug being fixed."""
    m = _mem(tmp_data_dir)
    units = [{"cls": "topic", "len": 900}, {"cls": "topic", "len": 1100},
             {"cls": "archive", "len": 20}, {"cls": "archive", "len": 40},
             {"cls": "archive", "len": 30}]
    avg = m._mem_class_avgdl(units)
    assert avg["topic"] == 1000
    assert avg["archive"] == 30


def test_long_topic_file_not_buried_by_short_archive_lines(tmp_data_dir):
    """The other direction of the same adaptation: a topic file that is
    genuinely about the query still wins against many short archive lines that
    merely mention it."""
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj2"}
    mp = _seed(m, p, {
        "topic_hivemind.md": "hivemind orchestrator design. " * 60,
    })
    arch = m._get_archive_path(p)
    arch.write_text("## Archived Session Log\n" + "\n".join(
        f"- [2026-08-0{i%9+1}] **task {i}** — touched hivemind briefly"
        for i in range(120)), encoding="utf-8")
    hits = m._memory_search(p, "hivemind orchestrator design", topk=3)
    assert hits[0]["file"] == "topic_hivemind.md"


# ── IDF: rare tokens are the ones that carry signal here ─────────────────────

def test_rare_term_outranks_ubiquitous_one(tmp_data_dir):
    """This corpus is function names, file paths and SHAs. A note matching the
    rare token must beat one matching only the term every note contains."""
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj3"}
    files = {f"common_{i}.md": "clayrune project notes about the server"
             for i in range(12)}
    files["has_rare.md"] = "clayrune project notes. the frobnicator hook fires late."
    _seed(m, p, files)
    hits = m._memory_search(p, "clayrune frobnicator", topk=1)
    assert hits[0]["file"] == "has_rare.md"


def test_filename_match_is_a_signal(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj4"}
    _seed(m, p, {
        "incognito.md": "the feature keeps sessions off the record.",
        "other.md": "the feature keeps sessions off the record. incognito.",
    })
    hits = m._memory_search(p, "incognito", topk=2)
    assert hits[0]["file"] == "incognito.md"


# ── contract the read floor depends on ───────────────────────────────────────

def test_returns_empty_for_unmatchable_query(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj5"}
    _seed(m, p, {"a.md": "widgets and sprockets"})
    assert m._memory_search(p, "ok") == []          # all tokens < 3 chars
    assert m._memory_search(p, "") == []
    assert m._memory_search(p, "zzzznomatch") == []


def test_respects_topk_and_sorts_descending(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj6"}
    _seed(m, p, {f"f{i}.md": "widget " * (i + 1) for i in range(6)})
    hits = m._memory_search(p, "widget", topk=3)
    assert len(hits) == 3
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    for h in hits:
        assert set(h) == {"file", "score", "snippet"}
        assert h["score"] > 0


def test_curated_index_is_excluded_managed_entries_are_not(tmp_data_dir):
    """MEMORY.md's curated half is auto-loaded already; searching it would
    return the agent its own context. Managed entries ARE searchable."""
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj7"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose(
        "# Index\n- curatedsentinelword lives here",
        ["- [2026-08-05] **t** — managedsentinelword lives here"], []),
        encoding="utf-8")
    assert m._memory_search(p, "curatedsentinelword") == []
    hits = m._memory_search(p, "managedsentinelword")
    assert hits and hits[0]["file"].endswith("#managed")


# ── corpus cache must not serve stale results ────────────────────────────────

def test_cache_invalidates_when_a_note_changes(tmp_data_dir):
    """The parsed corpus is cached on (name, mtime, size) to avoid re-tokenizing
    ~1.7MB per dispatch. A note written mid-session must still be findable."""
    m = _mem(tmp_data_dir)
    p = {"id": "bm25proj8"}
    mp = _seed(m, p, {"a.md": "widgets"})
    assert m._memory_search(p, "sprockets") == []
    (mp.parent / "b.md").write_text("sprockets sprockets", encoding="utf-8")
    hits = m._memory_search(p, "sprockets")
    assert hits and hits[0]["file"] == "b.md"
