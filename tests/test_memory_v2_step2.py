"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 2 — the record and the
stamps: §4.2's position schema superset, §4.3's server-stamped provenance
helpers (prerequisites for mint, step 7 — not yet called by any write path),
and §4.5's holds_while grammar, evaluated at corpus build and cached on the
corpus signature. Position frontmatter is validated at write and at build,
REPORT ONLY (Condition 48) — nothing here refuses a write or a build.
"""
import sys
import time
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


# ── §4.2 schema superset — pure additive, zero migration ────────────────────

def test_pre_v2_position_file_still_parses(env):
    """The 46 existing position files carry none of the new fields and must
    remain valid records unchanged (Condition 7)."""
    mem, tmp = env
    mem.write_position(P, subject='Obsidian as the substrate', verdict='declined',
                        reason='already have the vault shape',
                        expires_when='if our graph machinery stops resolving links')
    rec = mem.list_positions(P)[0]
    assert rec['claim'] == ''
    assert rec['evidence'] == []
    assert rec['durability'] == ''
    assert rec['holds_while'] == ''
    assert rec['supersedes'] == ''
    assert rec['pin'] is False
    # expires_when is a permanent read alias for revisit_if.
    assert rec['expires_when'] == 'if our graph machinery stops resolving links'
    assert rec['revisit_if'] == 'if our graph machinery stops resolving links'


def test_new_fields_round_trip(env):
    mem, tmp = env
    mem.write_position(
        P, subject='cross-encoder rerank', verdict='declined',
        reason='measured worse locally', slug='rerank',
        claim='adopt a cross-encoder rerank stage',
        evidence=['tools/memory-eval/scorer_ab.py run 2026-09-01', '46 -> 41'],
        durability='measured', holds_while='corpus_units > 500')
    rec = mem.list_positions(P)[0]
    assert rec['claim'] == 'adopt a cross-encoder rerank stage'
    assert rec['evidence'] == [
        'tools/memory-eval/scorer_ab.py run 2026-09-01', '46 -> 41']
    assert rec['durability'] == 'measured'
    assert rec['holds_while'] == 'corpus_units > 500'


def test_revisit_if_is_the_canonical_write_name_expires_when_still_works(env):
    """Passing `revisit_if` writes the new key; passing `expires_when` (the
    existing 46 files' shape) keeps writing the old one. Either way the
    normalized `revisit_if` read comes back populated (Condition 7)."""
    mem, tmp = env
    mem.write_position(P, subject='A', verdict='declined', reason='r',
                        slug='a', revisit_if='when X happens')
    mem.write_position(P, subject='B', verdict='declined', reason='r',
                        slug='b', expires_when='when Y happens')
    recs = {r['subject']: r for r in mem.list_positions(P)}
    assert recs['A']['revisit_if'] == 'when X happens'
    assert 'revisit_if:' in (tmp / 'position_a.md').read_text(encoding='utf-8')
    assert recs['B']['revisit_if'] == 'when Y happens'
    assert 'expires_when:' in (tmp / 'position_b.md').read_text(encoding='utf-8')
    assert 'revisit_if:' not in (tmp / 'position_b.md').read_text(encoding='utf-8'), \
        'an existing-shape write must not be force-migrated to the new key'


def test_pin_round_trips_as_a_real_boolean(env):
    mem, tmp = env
    mem.write_position(P, subject='pinned one', verdict='declined', reason='r',
                        slug='pinned', pin=True)
    mem.write_position(P, subject='not pinned', verdict='declined', reason='r',
                        slug='unpinned', pin=False)
    recs = {r['subject']: r for r in mem.list_positions(P)}
    assert recs['pinned one']['pin'] is True
    assert recs['not pinned']['pin'] is False


def test_evidence_accepts_a_preformatted_string_too(env):
    mem, tmp = env
    mem.write_position(P, subject='C', verdict='declined', reason='r', slug='c',
                        evidence='line one\nline two')
    rec = mem.list_positions(P)[0]
    assert rec['evidence'] == ['line one', 'line two']


# ── validation — report-only, never raises, never blocks the write ─────────

def test_holds_while_forbidden_with_durability_principle_is_reported_not_refused(env, caplog):
    mem, tmp = env
    # Must not raise — Condition 48 / G4: report-only until measured.
    fn = mem.write_position(
        P, subject='authority guard', verdict='declined',
        reason='learning may change how, never what', slug='authority',
        durability='principle', holds_while='corpus_units > 10')
    assert fn == 'position_authority.md'
    rec = mem.list_positions(P)[0]
    # The write is NOT auto-repaired — both fields land on disk as given; only
    # a warning is logged. Repairing behind the author's back would silently
    # change what they wrote.
    assert rec['durability'] == 'principle'
    assert rec['holds_while'] == 'corpus_units > 10'


def test_unparseable_holds_while_is_reported_not_refused(env):
    mem, tmp = env
    fn = mem.write_position(P, subject='D', verdict='declined', reason='r',
                             slug='d', holds_while='not a valid predicate')
    assert fn == 'position_d.md'
    rec = mem.list_positions(P)[0]
    assert rec['holds_while'] == 'not a valid predicate'


def test_unknown_durability_value_is_reported_not_refused(env):
    mem, tmp = env
    fn = mem.write_position(P, subject='E', verdict='declined', reason='r',
                             slug='e', durability='vibes')
    assert fn == 'position_e.md'


# ── §4.5 holds_while grammar ─────────────────────────────────────────────────

def test_grammar_parses_a_single_clause():
    from mc import memory as mem
    join, clauses = mem._parse_holds_while('index_bytes > 24576')
    assert join == 'and'
    assert clauses == [('index_bytes', '', '>', 24576.0)]


def test_grammar_parses_delivered_with_unit_arg():
    from mc import memory as mem
    join, clauses = mem._parse_holds_while('delivered(archive) < 0.15')
    assert clauses == [('delivered', 'archive', '<', 0.15)]


def test_grammar_joins_with_and_or_or():
    from mc import memory as mem
    join, clauses = mem._parse_holds_while(
        'topic_notes < 1000 and broken_links > 50')
    assert join == 'and' and len(clauses) == 2
    join, clauses = mem._parse_holds_while(
        'topic_notes < 1000 or broken_links > 50')
    assert join == 'or' and len(clauses) == 2


def test_grammar_absent_is_never_refused():
    from mc import memory as mem
    assert mem._parse_holds_while('') == (None, [])
    assert mem._parse_holds_while(None) == (None, [])


def test_grammar_rejects_mixed_and_or():
    from mc import memory as mem
    with pytest.raises(mem.HoldsWhileError):
        mem._parse_holds_while('a > 1 and b < 2 or c > 3')


def test_grammar_rejects_unknown_metric():
    from mc import memory as mem
    with pytest.raises(mem.HoldsWhileError):
        mem._parse_holds_while('made_up_metric > 5')


def test_grammar_error_names_registry_and_revisit_if():
    from mc import memory as mem
    with pytest.raises(mem.HoldsWhileError) as ei:
        mem._parse_holds_while('nonsense')
    msg = str(ei.value)
    assert 'revisit_if' in msg
    assert 'index_bytes' in msg  # a real registry member is named


def test_eval_returns_none_for_unparseable_or_unevaluable():
    from mc import memory as mem
    assert mem._eval_holds_while('nonsense', {}) is None
    assert mem._eval_holds_while('', {}) is None
    assert mem._eval_holds_while('delivered(archive) < 0.5', {}) is None


def test_eval_true_and_false():
    from mc import memory as mem
    metrics = {'index_bytes': 25000.0, 'topic_notes': 10.0}
    assert mem._eval_holds_while('index_bytes > 24576', metrics) is True
    assert mem._eval_holds_while('index_bytes < 24576', metrics) is False
    assert mem._eval_holds_while(
        'index_bytes > 24576 and topic_notes > 5', metrics) is True
    assert mem._eval_holds_while(
        'index_bytes < 24576 or topic_notes > 5', metrics) is True


# ── evaluate_positions_holds_while — live-corpus, cached on signature ──────

def test_evaluate_positions_holds_while_reflects_the_811_example(env):
    """The §4.5 live example: a position's own re-open clause, expressed as a
    metric, is satisfied and nothing noticed. Reproduce it with a real
    corpus-build-time evaluation instead of prose."""
    mem, tmp = env
    mem.write_position(
        P, subject='promote/demote mover for memory', verdict='declined',
        reason='no demotion lever exists yet', slug='promote-demote-mover',
        holds_while='index_bytes > 24576')
    # MEMORY.md is currently tiny (well under budget) — the clause must not trip.
    result = mem.evaluate_positions_holds_while(P)
    assert result['position_promote-demote-mover.md']['trips'] is False

    # Grow MEMORY.md past the default cap and re-evaluate.
    (tmp / 'MEMORY.md').write_text('# index\n' + ('x' * 30000), encoding='utf-8')
    result2 = mem.evaluate_positions_holds_while(P)
    assert result2['position_promote-demote-mover.md']['trips'] is True


def test_evaluate_positions_holds_while_skips_positions_without_one(env):
    mem, tmp = env
    mem.write_position(P, subject='no predicate', verdict='declined', reason='r',
                        slug='no-predicate')
    result = mem.evaluate_positions_holds_while(P)
    assert 'position_no-predicate.md' not in result


def test_global_metrics_cache_invalidates_on_change(env):
    mem, tmp = env
    m1 = mem._holds_while_global_metrics(P)
    m1_again = mem._holds_while_global_metrics(P)
    assert m1 is m1_again, 'unchanged corpus must return the cached dict'

    time.sleep(0.01)
    (tmp / 'MEMORY.md').write_text('# index\nmore content now\n', encoding='utf-8')
    m2 = mem._holds_while_global_metrics(P)
    assert m2 is not m1
    assert m2['index_bytes'] != m1['index_bytes']


def test_days_since_decided_is_computed_per_position(env):
    mem, tmp = env
    mem.write_position(P, subject='old ruling', verdict='declined', reason='r',
                        slug='old', decided='2020-01-01',
                        holds_while='days_since_decided > 100')
    result = mem.evaluate_positions_holds_while(P)
    assert result['position_old.md']['trips'] is True


def test_broken_links_metric_counts_only_class_d(env):
    mem, tmp = env
    (tmp / 'a.md').write_text('see [[missing_note]] and [[b.md]]', encoding='utf-8')
    (tmp / 'b.md').write_text('no links here', encoding='utf-8')
    metrics = mem._holds_while_global_metrics(P)
    # [[b.md]] resolves via R2 (class A, fixed); [[missing_note]] does not
    # (class D) — exactly one broken link.
    assert metrics['broken_links'] == 1.0


# ── §4.3 provenance stamp helpers — built, not yet wired to any write path ─

def test_stamp_origin_interactive_only_when_manual_and_unmarked():
    from mc import memory as mem
    assert mem._stamp_origin('fix the bug', 'manual') == 'interactive'
    assert mem._stamp_origin('fix the bug', 'schedule') == 'unattended'
    assert mem._stamp_origin('fix the bug', None) == 'unattended'
    assert mem._stamp_origin('you run unattended tonight', 'manual') == 'unattended'


def test_stamp_generated_shape():
    from mc import memory as mem
    g = mem._stamp_generated('scribe:haiku')
    assert g['by'] == 'scribe:haiku'
    assert g['at']


def test_derive_verified_never_fabricates():
    from mc import memory as mem
    v = mem._derive_verified('manual', True)
    assert v == [{'by': 'human', 'at': v[0]['at']}]
    assert mem._derive_verified('manual', False) == []
    assert mem._derive_verified('schedule', True) == []
    assert mem._derive_verified(None, True) == []


def test_derive_verified_carries_actor():
    from mc import memory as mem
    v = mem._derive_verified('manual', True, actor='sess-123')
    assert v == [{'by': 'human:sess-123', 'at': v[0]['at']}]
