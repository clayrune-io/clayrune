"""The memory effectiveness/durability monitor (MC-971, tools/memory-eval/
fact_canary_probe.py). Pins the pure logic without needing the vault corpus:
age bucketing, the live delivery-depth formula, filename-vs-needle rank
matching, and the injected-boilerplate false positive the first real run hit
(a "STANDING POSITIONS" reminder block saying "we already ARE that vault
shape" scored as a miss on Ron's behalf before this filter existed).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _probe():
    """Load the tool by path — `tools/memory-eval` is not a package."""
    path = PROJECT_ROOT / 'tools' / 'memory-eval' / 'fact_canary_probe.py'
    spec = importlib.util.spec_from_file_location('fact_canary_probe', path)
    assert spec and spec.loader, f'could not load {path}'
    sys.path.insert(0, str(path.parent))  # so its own `import _harness` resolves
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def p():
    return _probe()


# ── age bucketing ────────────────────────────────────────────────────────

def test_age_bucket_boundaries(p):
    assert p.age_bucket(0) == '<7d'
    assert p.age_bucket(6) == '<7d'
    assert p.age_bucket(7) == '7-30d'
    assert p.age_bucket(29) == '7-30d'
    assert p.age_bucket(30) == '30-90d'
    assert p.age_bucket(89) == '30-90d'
    assert p.age_bucket(90) == '>90d'


# ── the live per-turn delivery depth ────────────────────────────────────

def test_deliver_depth_matches_the_per_turn_ask():
    """codex_miss_repro.py's own comment: 'per-turn asks for
    max(topk*2, topk+4)'. This probe's pass/fail line must use the same
    formula or it is measuring a system nobody runs."""
    p = _probe()
    assert p.deliver_depth(6) == 12   # today's live topk
    assert p.deliver_depth(3) == 7    # small topk: +4 dominates
    assert p.deliver_depth(10) == 20  # large topk: *2 dominates


# ── rank matching: file (slug-drift tolerant) vs needle ────────────────

class _FakeMem:
    """Stands in for mc.memory just enough for find_rank's _mem_link_key call."""
    def _mem_link_key(self, s):
        from mc.memory import _mem_link_key
        return _mem_link_key(s)


def test_find_rank_file_match_tolerates_slug_drift(p):
    """The vault's slugs drifted (kebab links vs snake_case filenames) —
    delivery_review.py already had to solve this; the canary matcher must
    not regress it into false negatives."""
    m = _FakeMem()
    hits = [{'file': 'feedback_grep_memory_dir.md'}]
    assert p.find_rank(m, hits, 'file', 'feedback-grep-memory-dir.md') == 1


def test_find_rank_file_match_no_hit(p):
    m = _FakeMem()
    hits = [{'file': 'unrelated.md'}]
    assert p.find_rank(m, hits, 'file', 'feedback_grep_memory_dir.md') is None


def test_find_rank_needle_match_is_case_insensitive_and_ranked(p):
    m = _FakeMem()
    hits = [{'file': 'MEMORY_ARCHIVE.md', 'snippet': 'nothing here'},
            {'file': 'MEMORY_ARCHIVE.md', 'snippet': 'access cleared AFTER ADDING Codex credits'}]
    assert p.find_rank(m, hits, 'needle', 'after adding codex credits') == 2


# ── the false positive this probe's first run actually produced ────────

def test_injected_reminder_boilerplate_is_not_a_miss(p):
    """Real transcript text (2026-09-23): a `--- STANDING POSITIONS ---`
    block quoting a DECLINED position whose own reasoning says "because we
    already ARE that vault shape". First cut of this probe flagged that as
    a live miss of "we already" — a false positive on boilerplate, not on
    anything Ron said. This is the regression guard."""
    text = (
        '--- STANDING POSITIONS (already decided) ---\n'
        '  • DECLINED: Obsidian as the memory substrate — because we already '
        'ARE that vault shape — markdown + frontmatter + [[wikilinks]]\n'
        '\nCurrent task: fix the login bug\n'
    )
    stripped = p._strip_injected_context(text)
    assert 'we already' not in stripped.lower()
    assert 'fix the login bug' in stripped


def test_a_real_user_complaint_survives_stripping(p):
    """The actual MC-964 miss message must still match after stripping, or
    the filter is throwing out real signal along with the boilerplate."""
    text = (
        '<system-reminder>unrelated harness note</system-reminder>\n\n'
        "I'm bit worried since I told you earlier already about the codex "
        'allowance. The fact that you did not remember it is worrying'
    )
    stripped = p._strip_injected_context(text)
    assert 'i told you' in stripped.lower()


def test_scribe_and_distiller_calls_are_not_ron_messages(p):
    """Scribe checkpoint / Distiller extraction calls are logged role=user
    too (they're a model call, not a person typing), and their own prompt
    text can legitimately contain phrases like "already". Excluded by
    marker, not by phrase-filtering their prose."""
    text = 'You are a project-memory scribe. We already covered this above.'
    assert any(marker in text.lower() for marker in p._TOOL_PROMPT_MARKERS)
