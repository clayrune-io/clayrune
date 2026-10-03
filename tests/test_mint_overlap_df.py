"""MC-964 pre-step-10 fix A2 — the M4 overlap test must not count pure-numeric
tokens or words that sit in more than a set share of the vault's notes.

Fix A removed the words minting bakes in. What was left on the 2026-10-03
snapshot (861 mint-mint pairs) was house vocabulary: a year from dates in
backlog text, an operator's name. The stoplist is derived from the corpus, so
no test here names a year or a person: the "house word" is an invented token
whose document frequency the test controls.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import memory_overlap_df as df  # noqa: E402

P = {'id': 'p1'}
HOUSE = 'zorblat'   # invented; not in any stopword list


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setitem(mem.state.CONFIG, 'memory_mint_triggers_enabled', True)
    return mem, tmp_path


def _hand(tmp, i, extra=''):
    (tmp / f'hand_note_{i}.md').write_text(
        f'---\nname: hand-note-{i}\ndescription: "Subject alpha{i} beta{i} {extra}"\n'
        f'metadata:\n  type: project\n---\nbody\n', encoding='utf-8')


def _vault(tmp, n_notes=30, house_in=0):
    """`n_notes` hand notes with unique vocabulary; the first `house_in` carry HOUSE."""
    for i in range(n_notes):
        _hand(tmp, i, HOUSE if i < house_in else '')


def _mint(mem, subject, key):
    return mem.mint_topic_node(P, trigger_kind='backlog_done', subject=subject,
                               artifact_path=f'backlog:{key}')


def _flagged(mem, fa, fb):
    return any({p['a'], p['b']} == {fa, fb} for p in mem.mint_overlap_report(P))


# ── the pure module ────────────────────────────────────────────────────────

def _sets(n, with_term, term='common'):
    return [{term, f'u{i}'} if i < with_term else {f'u{i}'} for i in range(n)]


def test_stoplist_is_derived_from_the_corpus_not_a_constant():
    assert 'common' in df.df_stoplist(_sets(40, 10))       # 25% of 40
    assert 'common' not in df.df_stoplist(_sets(40, 2))    # 5% of 40
    # Same term, same code, different corpus, different verdict.


def test_stoplist_threshold_is_strictly_more_than_share():
    assert 'common' not in df.df_stoplist(_sets(40, 4), share=0.10)   # exactly 10%
    assert 'common' in df.df_stoplist(_sets(40, 5), share=0.10)


def test_stoplist_counts_a_term_once_per_note():
    sets = [{'common'} for _ in range(2)] + [{f'u{i}'} for i in range(38)]
    assert df.df_stoplist(sets, share=0.10) == frozenset()            # 2 of 40, not many


def test_stoplist_is_empty_for_a_small_corpus():
    # 2 of 3 notes is 67%, but three notes say nothing about house vocabulary.
    assert df.df_stoplist([{'a'}, {'a'}, {'b'}]) == frozenset()
    assert df.df_stoplist(_sets(df.MINT_OVERLAP_DF_MIN_NOTES - 1, 20)) == frozenset()
    assert 'common' in df.df_stoplist(_sets(df.MINT_OVERLAP_DF_MIN_NOTES, 20))


def test_drop_numeric_keeps_alphanumeric_tokens():
    assert df.drop_numeric({'2026', '1024', 'v2', '4668eafc', 'desk'}) == \
        {'v2', '4668eafc', 'desk'}


# ── numeric tokens: mints only ─────────────────────────────────────────────

def test_mint_terms_drop_pure_numeric_tokens(env):
    mem, tmp = env
    fa = _mint(mem, 'STATE 2026-09-29 (Dave): shipped 4096 bytes', 'a')
    fm = mem._note_frontmatter((tmp / fa).read_text(encoding='utf-8'))
    terms = mem._mint_overlap_terms(fa.rsplit('.', 1)[0], fm['description'], '')
    assert 'shipped' in terms and 'state' in terms
    assert not any(t.isdigit() for t in terms)


def test_numeric_only_overlap_does_not_flag_two_mints(env):
    """No DF here (corpus < min notes): the numeric drop alone must hold."""
    mem, tmp = env
    fa = _mint(mem, 'Apples grown 2026 1999', 'a')
    fb = _mint(mem, 'Quarterly invoices 2026 1999', 'b')
    assert fa and fb
    assert not _flagged(mem, *sorted([fa, fb]))


def test_hand_note_keeps_its_digits(env):
    mem, _tmp = env
    terms = mem._mint_overlap_terms('arch_notes', 'Release 2026 build 1999', '')
    assert {'2026', '1999'} <= terms


# ── DF stoplist through the M4 report and the inline WRITE check ───────────

def test_house_word_alone_does_not_flag_two_unrelated_mints(env):
    mem, tmp = env
    _vault(tmp, house_in=6)    # HOUSE in 6 of 32 notes once the mints land: >10%
    fa = _mint(mem, f'{HOUSE} crankshaft tolerances', 'a')
    fb = _mint(mem, f'{HOUSE} marmalade recipe', 'b')
    assert fa and fb
    assert not _flagged(mem, *sorted([fa, fb]))


def test_same_house_word_in_a_small_share_of_notes_still_counts(env):
    """Derived, not constant: the identical pair IS flagged when the word is
    rare in this vault."""
    mem, tmp = env
    _vault(tmp, house_in=0)
    fa = _mint(mem, f'{HOUSE} crankshaft tolerances', 'a')
    fb = _mint(mem, f'{HOUSE} marmalade recipe', 'b')
    assert not _flagged(mem, *sorted([fa, fb]))   # one shared word: below the floor
    fc = _mint(mem, f'{HOUSE} crankshaft tolerances revisited', 'c')
    assert _flagged(mem, *sorted([fa, fc]))       # house word + real subject words


def test_same_subject_mints_still_overlap_in_a_house_word_vault(env):
    mem, tmp = env
    _vault(tmp, house_in=6)
    fa = _mint(mem, f'{HOUSE} crankshaft tolerances drift on the lathe', 'a')
    fb = _mint(mem, f'{HOUSE} crankshaft tolerances drift after the rebuild', 'b')
    assert _flagged(mem, *sorted([fa, fb]))


def test_unrelated_mints_sharing_house_word_plus_one_word_are_not_flagged(env):
    mem, tmp = env
    _vault(tmp, house_in=6)
    fa = _mint(mem, f'{HOUSE} crankshaft tolerances', 'a')
    fb = _mint(mem, f'{HOUSE} crankshaft recipe', 'b')    # house + ONE real word
    assert not _flagged(mem, *sorted([fa, fb]))


def test_hand_hand_pairs_are_unchanged_by_the_stoplist(env):
    mem, tmp = env
    _vault(tmp, house_in=6)    # hand_note_0..5 each carry HOUSE
    # hand notes 0 and 1 share HOUSE only via description; give them one more
    # shared word so the plain intersection is 2.
    for i in (0, 1):
        _hand(tmp, i, f'{HOUSE} sharedword')
    pairs = {(p['a'], p['b']) for p in mem.mint_overlap_report(P)}
    assert ('hand_note_0.md', 'hand_note_1.md') in pairs


def test_inline_write_check_applies_the_stoplist(env):
    """detect_mint_overlap is the per-mint WRITE check: a new mint sharing a
    house word + one more with an existing mint gets no candidate."""
    mem, tmp = env
    _vault(tmp, house_in=6)
    fa = _mint(mem, f'{HOUSE} crankshaft tolerances', 'a')
    fb = _mint(mem, f'{HOUSE} crankshaft recipe', 'b')
    fm = mem._note_frontmatter((tmp / fb).read_text(encoding='utf-8'))
    assert fa
    assert fm['supersedes'] == ''
    assert fm['mint_candidates'] == ''
