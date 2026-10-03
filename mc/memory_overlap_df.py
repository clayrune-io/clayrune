"""Corpus document-frequency stoplist for the M4 mint-overlap test (MC-964
pre-step-10 fix A2).

A word that sits in more than a set share of the vault's notes is not
evidence that two notes share a subject: it is the vault's own house
vocabulary (an operator's name, a product name, a note-type prefix). Fix A
removed the words minting itself bakes in; what was left on the 2026-10-03
snapshot (861 mint-mint pairs) was exactly this class, so the stoplist is
DERIVED from the corpus at check time rather than listed by hand. A hand list
would put one operator's names in a public repo and be wrong on every other
install.

Pure functions, no imports from `mc.memory` — the caller supplies each note's
term set.
"""
from collections import Counter
from typing import Iterable

# Share of the vault's topic notes above which a term stops counting as shared
# subject. 0.10 is the highest round value below the DF of the residual noise
# on the 2026-10-03 snapshot (216 notes: `ron` 0.199, `dave` 0.116, `2026`
# 0.116) and it leaves the subject words that sit between 0.05 and 0.10
# (`desk`, `memory`, `session`, `review`, `project`) alone. Measurement in
# docs/_journal/b2d85e51-memory-overhaul.md.
MINT_OVERLAP_DF_SHARE = 0.10

# Below this many notes a document frequency says nothing (3 notes: a word in
# two of them is 67%). The stoplist is empty rather than noisy.
MINT_OVERLAP_DF_MIN_NOTES = 30


def drop_numeric(terms: Iterable[str]) -> set[str]:
    """Pure-numeric tokens (`2026` from a `2026-09-29` date, a size, a count)
    are never a subject. Alphanumeric tokens (`v2`, `4668eafc`) are kept."""
    return {t for t in terms if not t.isdigit()}


def df_stoplist(term_sets: Iterable[set[str]], *,
                share: float = MINT_OVERLAP_DF_SHARE,
                min_notes: int = MINT_OVERLAP_DF_MIN_NOTES) -> frozenset[str]:
    """Terms present in MORE than `share` of the given notes' term sets.

    `term_sets` is one set per note, over the same vocabulary the overlap test
    compares, so a term is counted once per note however often it repeats.
    """
    sets = list(term_sets)
    n = len(sets)
    if n < max(1, min_notes):
        return frozenset()
    df = Counter(t for s in sets for t in s)
    cutoff = share * n
    return frozenset(t for t, c in df.items() if c > cutoff)
