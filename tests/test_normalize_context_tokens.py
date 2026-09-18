"""mc.agent_runtime.normalize_context_tokens — the ONE place per-turn usage
dicts (Claude, Gemini, and whatever Qwen/Codex/etc. turn out to emit) get
reconciled into a single 'context tokens' figure, per docs/CONTEXT_ECONOMY_SPEC.md
§1. Before this function existed, no such figure was ever computed at all
(`session['usage']` was stored verbatim, never summarized) -- these tests
fail on that ImportError/AttributeError absence and pass once the function
is implemented and handles each vendor shape + the 'unknown stays unknown'
None-not-zero contract.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.agent_runtime import normalize_context_tokens  # noqa: E402


class TestClaudeShape:
    def test_sums_input_cache_read_cache_creation(self):
        usage = {'input_tokens': 100, 'output_tokens': 50,
                 'cache_read_input_tokens': 40000, 'cache_creation_input_tokens': 2000}
        assert normalize_context_tokens(usage) == 100 + 40000 + 2000

    def test_missing_cache_fields_treated_as_zero(self):
        usage = {'input_tokens': 500, 'output_tokens': 12}
        assert normalize_context_tokens(usage) == 500


class TestGeminiShape:
    def test_gemini_stats_resolve_to_input_tokens(self):
        # Live-confirmed shape (tests/test_gemini_usage_wiring.py): no cache
        # fields at all -- Gemini has no prompt cache.
        usage = {'total_tokens': 34302, 'input_tokens': 34295, 'output_tokens': 7}
        assert normalize_context_tokens(usage) == 34295

    def test_falls_back_to_total_tokens_when_no_input_tokens_field(self):
        usage = {'total_tokens': 5000}
        assert normalize_context_tokens(usage) == 5000


class TestOpenAIShapedFallback:
    def test_prompt_tokens_used_when_nothing_else_matches(self):
        usage = {'prompt_tokens': 8000, 'completion_tokens': 200}
        assert normalize_context_tokens(usage) == 8000


class TestUnknownStaysUnknown:
    def test_none_usage_returns_none_not_zero(self):
        assert normalize_context_tokens(None) is None

    def test_empty_dict_returns_none(self):
        assert normalize_context_tokens({}) is None

    def test_non_dict_returns_none(self):
        assert normalize_context_tokens('not a dict') is None  # type: ignore[arg-type]

    def test_unrecognized_shape_returns_none(self):
        assert normalize_context_tokens({'weird_field': 123}) is None

    def test_all_zero_claude_fields_falls_through_not_zero(self):
        # A turn CAN legitimately report 0 for every Claude field (e.g. a
        # cache-only turn that's malformed) -- must not silently claim 0
        # tokens of context, which would never trip the rollover threshold
        # but also never fall back to the byte check either.
        usage = {'input_tokens': 0, 'cache_read_input_tokens': 0,
                 'cache_creation_input_tokens': 0}
        assert normalize_context_tokens(usage) is None
