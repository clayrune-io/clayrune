"""Keep the documented override example executable."""

import json
import re
from pathlib import Path

from mc.rollover_threshold import threshold_with_reason


def test_documented_override_example_has_expected_precedence():
    docs = Path(__file__).resolve().parents[1] / 'docs' / 'ROLLOVER_THRESHOLDS.md'
    example = re.search(r'```json\n(.*?)\n```', docs.read_text(encoding='utf-8'), re.S)
    assert example is not None, 'Missing the runnable rollover config example'
    config = json.loads(example[1])

    sonnet = {'model': 'sonnet', 'effective_effort': 'high', '_mt_fresh_floor': 89_000}
    assert threshold_with_reason(sonnet, config) == (220_000, 'override:model+effort')
    assert threshold_with_reason(dict(sonnet, character='global:builder'), config) == (
        210_000, 'override:model+character+effort')
    assert threshold_with_reason({'model': 'unseen'}, config) == (200_000, 'base')


def _doc_text():
    docs = Path(__file__).resolve().parents[1] / 'docs' / 'ROLLOVER_THRESHOLDS.md'
    return ' '.join(docs.read_text(encoding='utf-8').split())


def test_doc_states_which_model_names_an_override_accepts():
    """2026-11-25 review F10: `{"model": "fable"}` matched nothing and the doc
    did not say why."""
    assert 'full model ids or the aliases haiku/sonnet/opus' in _doc_text()
    session = {'model': 'claude-fable-5-2', '_mt_fresh_floor': 80_000}
    by_word = {'context_rollover_overrides': [{'model': 'fable', 'tokens': 300_000}]}
    by_id = {'context_rollover_overrides': [{'model': 'claude-fable-5-2', 'tokens': 300_000}]}
    assert threshold_with_reason(session, by_word)[1] == 'computed:model'
    assert threshold_with_reason(session, by_id) == (300_000, 'override:model')


def test_doc_states_the_two_decided_meanings():
    """2026-11-25 review F11, decided: the global is the base fallback only,
    and a character pin is a target, not a cap. The numbers the doc quotes
    are the resolver's own."""
    text = _doc_text()
    assert 'base fallback only' in text
    assert 'a target, not a cap' in text
    dave = {'character': 'global:dave', 'model': 'opus', 'effective_effort': 'high',
            '_mt_fresh_floor': 89_000}
    for base in (120_000, 200_000, 400_000):
        assert threshold_with_reason(dave, {'context_rollover_tokens': base}) == (
            200_000, 'computed:model_effort')
    assert threshold_with_reason(dave, {
        'context_rollover_by_character': {'global:dave': 400_000},
    }) == (400_000, 'override:character')
