"""Claude's context_tokens must come from one model call, not `result.usage`.

Numbers are from a real 2-Read haiku turn measured 2026-09-18: the calls held
30.2k and 32.4k of context; `result.usage` summed them to 62.5k.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mc.blueprints import agent_routes as ar  # noqa: E402

CALL_1 = {'input_tokens': 10, 'cache_read_input_tokens': 17640,
          'cache_creation_input_tokens': 12525}
CALL_2 = {'input_tokens': 8, 'cache_read_input_tokens': 30165,
          'cache_creation_input_tokens': 2224}
RESULT = {'input_tokens': 18, 'cache_read_input_tokens': 47805,
          'cache_creation_input_tokens': 14749, 'output_tokens': 50}


def test_last_call_sets_context_tokens():
    s = {}
    ar._note_call_context_tokens(s, {'usage': CALL_1})
    ar._note_call_context_tokens(s, {'usage': CALL_2})
    assert s['context_tokens'] == 8 + 30165 + 2224


def test_result_usage_does_not_overwrite_context_tokens():
    s = {}
    ar._note_call_context_tokens(s, {'usage': CALL_2})
    ar._accumulate_session_usage(s, RESULT)
    assert s['context_tokens'] == 32397      # not the 62572 turn sum
    assert s['usage']['cache_read_input_tokens'] == 47805  # totals still summed


def test_message_without_usage_leaves_value_alone():
    s = {'context_tokens': 1234}
    ar._note_call_context_tokens(s, {})
    assert s['context_tokens'] == 1234
