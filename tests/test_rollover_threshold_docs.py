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
