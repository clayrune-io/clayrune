"""`[[wikilink]]` graph + one-hop read-floor expansion (2026-08-09).

The vault has carried ~97 hand-authored wikilinks for months and nothing parsed
them — a past session's explicit "the companion note is over there" was invisible
to retrieval, and 6 of the links had rotted to nothing without anything going
red. These tests pin both halves of the fix: tolerant slug matching (so a link
written kebab still reaches a snake_case filename) and expansion that is strictly
ADDITIVE, so switching it on can never push a real lexical match out of the floor.
"""
import importlib


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


def _corpus(m, p):
    """The corpus loader keyed on THIS project's real index/archive names."""
    mp = m._get_memory_path(p)
    return m._mem_corpus(mp.parent, mp.name, m._get_archive_path(p).name)


def _seed(m, p, files):
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(m._mem_compose("# Index", [], []), encoding="utf-8")
    for name, text in files.items():
        (mp.parent / name).write_text(text, encoding="utf-8")
    return mp


# ── slug matching: one note identity, many spellings ─────────────────────────

def test_link_key_ignores_separators_and_case():
    import mc.memory as m
    k = m._mem_link_key
    assert k("arch-mobile-ui") == k("arch_mobile_ui") == k("Arch Mobile UI")
    assert k("") == k(None) == ""
    assert k("decision_step7") != k("decision_step8")


def test_link_targets_strip_alias_and_anchor():
    import mc.memory as m
    got = m._mem_link_targets(
        "see [[arch-overview]] and [[arch_mobile_ui|the mobile note]] "
        "plus [[feature_incognito#gotchas]]; `[[` alone is not a link")
    assert got == ["arch-overview", "arch_mobile_ui", "feature_incognito"]
    assert m._mem_link_targets("") == []
    assert m._mem_link_targets(None) == []


def test_link_targets_ignore_code_spans_and_fences():
    """A note that DOCUMENTS the syntax must not register itself as linking —
    this vault's own architecture note does exactly that, and without the rule
    it reported `[[wikilinks]]` dangling from three files."""
    import mc.memory as m
    assert m._mem_link_targets("the `[[wikilinks]]` syntax") == []
    assert m._mem_link_targets("```\nsee [[fenced_note]]\n```") == []
    assert m._mem_link_targets(
        "`[[syntax]]` but [[real_note]] counts") == ["real_note"]


# ── the graph ────────────────────────────────────────────────────────────────

def test_graph_resolves_kebab_link_to_snake_case_file(tmp_data_dir):
    """The drift that made 5 of 11 links look broken: links written kebab,
    filenames written snake_case."""
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj1"}
    _seed(m, p, {"arch_mobile_ui.md": "mobile notes, see [[arch-overview]]",
                 "arch_overview.md": "overview notes"})
    g = m._mem_link_graph(_corpus(m, p))
    assert g["arch_mobile_ui.md"]["out"] == ["arch_overview.md"]
    assert g["arch_overview.md"]["in"] == ["arch_mobile_ui.md"]


def test_graph_drops_dangling_and_self_links(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj2"}
    _seed(m, p, {"a.md": "widgets [[nothing-here]] [[a]] [[b]]",
                 "b.md": "sprockets"})
    g = m._mem_link_graph(_corpus(m, p))
    assert g["a.md"]["out"] == ["b.md"]          # dangling + self dropped
    assert set(g) == {"a.md", "b.md"}


def test_graph_ignores_archive_and_managed_units(tmp_data_dir):
    """An archive line is a line out of a container file, not a note — there is
    no single owning note to hop back to, so it must not become a graph node."""
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj3"}
    _seed(m, p, {"a.md": "widgets"})
    m._get_archive_path(p).write_text(
        "## Archived\n- [2026-08-09] **t** — see [[a]] widgets", encoding="utf-8")
    g = m._mem_link_graph(_corpus(m, p))
    assert set(g) == {"a.md"}
    assert g["a.md"]["in"] == []


# ── expansion: additive, one hop, capped ─────────────────────────────────────

def test_expansion_appends_linked_note_that_bm25_never_matched(tmp_data_dir):
    """The point of the feature. `sprockets.md` shares no vocabulary with the
    query, so BM25 cannot reach it; a past session linked it from the note that
    DOES match, and that edge is the retrieval signal."""
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj4"}
    _seed(m, p, {"widgets.md": "widget calibration offset. see [[sprockets]]",
                 "sprockets.md": "entirely different vocabulary here"})
    plain = m._memory_search(p, "widget calibration offset", topk=6)
    assert [h["file"] for h in plain] == ["widgets.md"]
    hits = m._memory_search(p, "widget calibration offset", topk=6, expand=2)
    assert [h["file"] for h in hits] == ["widgets.md", "sprockets.md"]
    nbr = hits[1]
    assert nbr["via"] == "widgets.md" and nbr["link"] == "out"
    assert nbr["snippet"] and nbr["score"] < hits[0]["score"]


def test_expansion_is_additive_never_displaces_a_lexical_hit(tmp_data_dir):
    """Guard on the whole design: expansion appends. Whatever topk returned
    without it must still be there, in the same order, with it."""
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj5"}
    files = {f"w{i}.md": f"widget note {i} " * (5 - i) for i in range(5)}
    files["w0.md"] += " see [[linked_only]]"
    files["linked_only.md"] = "unrelated vocabulary"
    _seed(m, p, files)
    base = m._memory_search(p, "widget note", topk=3)
    grown = m._memory_search(p, "widget note", topk=3, expand=2)
    assert [h["file"] for h in grown[:3]] == [h["file"] for h in base]
    assert len(grown) > 3


def test_expansion_respects_cap_and_skips_notes_already_in_hits(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj6"}
    _seed(m, p, {
        "hub.md": "widget hub. [[n1]] [[n2]] [[n3]] [[alsowidget]]",
        "alsowidget.md": "widget mention here too",
        "n1.md": "alpha", "n2.md": "beta", "n3.md": "gamma",
    })
    hits = m._memory_search(p, "widget", topk=2, expand=2)
    files = [h["file"] for h in hits]
    assert files[:2] == ["hub.md", "alsowidget.md"]
    assert len(hits) == 4                       # cap honoured
    # alsowidget is already a lexical hit — it must not reappear as a neighbour
    assert len(set(files)) == len(files)
    for h in hits[2:]:
        assert h["via"] == "hub.md"


def test_out_links_rank_above_back_links(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj7"}
    _seed(m, p, {"hit.md": "widget calibration. [[forward]]",
                 "forward.md": "alpha vocabulary",
                 "backward.md": "beta vocabulary, see [[hit]]"})
    hits = m._memory_search(p, "widget calibration", topk=1, expand=2)
    assert [h["file"] for h in hits] == ["hit.md", "forward.md", "backward.md"]
    assert hits[1]["link"] == "out" and hits[2]["link"] == "in"


def test_expand_off_leaves_the_result_shape_untouched(tmp_data_dir):
    """Read-floor callers unpack these dicts; with expansion off there must be
    no `via`/`link` key at all (and the default must be off)."""
    m = _mem(tmp_data_dir)
    p = {"id": "lgproj8"}
    _seed(m, p, {"a.md": "widget [[b]]", "b.md": "other"})
    for hits in (m._memory_search(p, "widget", topk=2),
                 m._memory_search(p, "widget", topk=2, expand=0),
                 m._memory_search(p, "widget", topk=2, expand=None)):
        assert [h["file"] for h in hits] == ["a.md"]
        assert set(hits[0]) == {"file", "score", "snippet"}
