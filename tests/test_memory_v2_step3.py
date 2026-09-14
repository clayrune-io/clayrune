"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 3 — discovery, zero
authoring: D0 (default `triggers:` from name + description, phrase exemption,
per-class df) and D1 (the retrievability gate run as a full-vault sweep — its
failures are the work list for later authoring steps, not fixed here).
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, tmp_path


P = {'id': 'p1'}


# ── D0 — default triggers ────────────────────────────────────────────────────

def test_default_triggers_strip_stopwords():
    from mc import memory as mem
    singles, _phrases = mem._note_default_triggers(
        'arch_mobile_ui.md', 'The mobile CSS layout and the FAB tab bar')
    assert 'the' not in singles and 'and' not in singles
    assert {'arch', 'mobile', 'css', 'layout', 'fab', 'tab', 'bar'} <= singles


def test_default_triggers_include_name_and_description():
    from mc import memory as mem
    singles, _phrases = mem._note_default_triggers(
        'decision_step7_semantic_search_deferral', 'defer bge-m3 semantic search')
    assert 'step7' in singles or 'step' in singles or 'semantic' in singles
    assert 'defer' in singles


def test_phrase_bigrams_are_adjacent_and_ordered():
    from mc import memory as mem
    _singles, phrases = mem._note_default_triggers(
        'project_memory_system_redesign',
        'server-side Scribe plus a read-floor and a memory layer')
    assert 'memory layer' in phrases


def test_phrase_bigrams_respect_the_config_toggle(env, monkeypatch):
    mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'trigger_phrase_bigrams', False)
    _singles, phrases = mem._note_default_triggers(
        'project_memory_system_redesign', 'our memory layer design')
    assert phrases == []


# ── per-class df (Condition 29) ─────────────────────────────────────────────

def test_df_is_computed_per_class_not_corpus_wide(env):
    """Archive lines echoing 'memory' must not inflate the topic-class df
    used to gate topic-note default triggers."""
    mem, tmp = env
    (tmp / 'MEMORY_ARCHIVE.md').write_text(
        '\n'.join(f'- [2026-01-0{i}] **task {i}** — did memory stuff'
                  for i in range(1, 8)),
        encoding='utf-8')
    (tmp / 'note_a.md').write_text('a note about memory systems', encoding='utf-8')
    (tmp / 'note_b.md').write_text('an unrelated note about scheduling',
                                    encoding='utf-8')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    df, n = mem._class_df(units, 'topic')
    assert n == 2  # only the two topic files, not the 7 archive lines
    assert df.get('memory', 0) == 1  # counted once, not once per archive echo


def test_high_df_single_term_is_gated_but_phrase_and_explicit_survive(env):
    """Reproduces §7.3's own example: `memory` at high df loses as a bare
    single term but a phrase containing it, or an explicit trigger, does not."""
    mem, tmp = env
    for i in range(10):
        (tmp / f'note_{i}.md').write_text('memory system notes here',
                                           encoding='utf-8')
    target = tmp / 'note_target.md'
    target.write_text('---\nname: note_target\n'
                       'description: our memory layer design\n---\n'
                       'body text', encoding='utf-8')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    terms, phrases = mem.note_triggers(
        P, 'note_target', 'our memory layer design', '', units=units)
    assert 'memory' not in terms, 'high-df single term must be gated out'
    assert 'memory layer' in phrases, 'the phrase survives regardless of df'


def test_explicit_triggers_always_survive_the_df_gate(env):
    mem, tmp = env
    for i in range(10):
        (tmp / f'note_{i}.md').write_text('memory system notes here',
                                           encoding='utf-8')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    terms, _phrases = mem.note_triggers(
        P, 'note_target', 'irrelevant description', 'memory', units=units)
    assert 'memory' in terms


# ── _note_frontmatter — read-only, does not touch the live tokenizer ───────

def test_note_frontmatter_reads_description_and_triggers():
    from mc import memory as mem
    text = ('---\nname: arch_overview\n'
            'description: SPA + Flask shape\n'
            'triggers: layout, modal\n---\nbody')
    meta = mem._note_frontmatter(text)
    assert meta['name'] == 'arch_overview'
    assert meta['description'] == 'SPA + Flask shape'
    assert meta['triggers'] == 'layout, modal'


def test_note_frontmatter_absent_returns_empty_dict():
    from mc import memory as mem
    assert mem._note_frontmatter('no frontmatter here') == {}


def test_topic_tokenizer_still_ignores_frontmatter(env):
    """§10.4 point 1 must hold: this step does not change what the live
    ranker sees for a topic file — frontmatter stays unparsed there."""
    mem, tmp = env
    (tmp / 'note_a.md').write_text(
        '---\nname: note_a\ndescription: zzzzunique marker\n---\nbody text',
        encoding='utf-8')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    u = next(x for x in units if x['file'] == 'note_a.md')
    # The frontmatter block's raw tokens end up in the document text (it is
    # tokenized as plain text, not parsed) — this just pins that nothing
    # SPECIAL happens to description/triggers weighting yet.
    assert 'zzzzunique' in u['tf'] or 'zzzzunique' in ' '.join(u['tf'])


# ── D1 — the retrievability gate, checked not asserted (§7.4) ──────────────

def test_retrievable_note_passes_the_gate(env):
    mem, tmp = env
    (tmp / 'arch_mobile_ui.md').write_text(
        '---\nname: arch_mobile_ui\n'
        'description: mobile CSS layout, FAB tab bar, Galaxy Z Fold gotcha\n'
        '---\nCSS details about the mobile layout and tab bar.',
        encoding='utf-8')
    ok, hits = mem._note_retrievable(
        P, 'arch_mobile_ui.md',
        {'arch', 'mobile', 'css', 'layout', 'fab', 'tab', 'bar'}, [])
    assert ok is True
    assert 'arch_mobile_ui.md' in hits


def test_unretrievable_note_reports_the_actual_miss(env, monkeypatch):
    """When the note is not among the search hits, the gate reports False
    AND the actual top hits, so a caller can report the specific miss rather
    than a bare failure (§7.4: "flagged with the specific miss")."""
    mem, tmp = env
    monkeypatch.setattr(
        mem, '_memory_search',
        lambda project, query, topk=3, expand=0, record=None: [
            {'file': 'other_note.md', 'score': 5.0, 'snippet': ''}])
    ok, hits = mem._note_retrievable(P, 'note_target.md', {'memory'}, [], topk=3)
    assert ok is False
    assert hits == ['other_note.md']


def test_empty_trigger_set_never_retrieves(env):
    """A note whose df-gated singles AND phrases both come up empty (the
    structural failure §7.3 describes: default triggers collapse to nothing
    distinguishing) must fail the gate rather than search on an empty query."""
    mem, tmp = env
    ok, hits = mem._note_retrievable(P, 'note_target.md', set(), [], topk=3)
    assert ok is False
    assert hits == []


def test_df_gate_can_strip_every_single_term_leaving_only_phrases(env):
    """Reproduces §7.3's own worked example: a subject built entirely from
    vault-dominant words keeps only its phrase(s), never zero vocabulary
    outright, because bigrams are exempt from the df gate."""
    mem, tmp = env
    for i in range(10):
        (tmp / f'note_{i}.md').write_text('memory project system notes',
                                           encoding='utf-8')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    terms, phrases = mem.note_triggers(
        P, 'project_memory_system_redesign', 'project memory system redesign',
        '', units=units)
    assert 'memory' not in terms and 'project' not in terms and 'system' not in terms
    assert 'redesign' in terms  # rare in this corpus, survives the gate
    assert phrases  # the exempt phrases still carry the common words


def test_sweep_covers_every_topic_note_and_reports_per_note_result(env):
    mem, tmp = env
    (tmp / 'note_a.md').write_text(
        '---\nname: note_a\ndescription: alpha unique topic\n---\nbody',
        encoding='utf-8')
    (tmp / 'note_b.md').write_text(
        '---\nname: note_b\ndescription: beta unique topic\n---\nbody',
        encoding='utf-8')
    results = mem.retrievability_sweep(P)
    files = {r['file'] for r in results}
    assert files == {'note_a.md', 'note_b.md'}
    for r in results:
        assert set(r) == {'file', 'terms', 'phrases', 'retrievable', 'top_hits'}


def test_sweep_excludes_non_topic_units(env):
    mem, tmp = env
    (tmp / 'note_a.md').write_text(
        '---\nname: note_a\ndescription: alpha\n---\nbody', encoding='utf-8')
    mem.write_position(P, subject='a position', verdict='declined', reason='r',
                        slug='pos-one')
    results = mem.retrievability_sweep(P)
    assert {r['file'] for r in results} == {'note_a.md'}
