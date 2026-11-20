"""MC-1179 DCW-C1: fenced blocks are found in linear time, and are the same blocks.

The pattern `parameter_parsers` used rescanned to the end of the text from every unclosed
opening fence: 80 KB of hostile README held the interpreter lock for 8 seconds, and the
2 MB cap would have been over an hour. `fence_scan` replaces it. The old pattern is kept
here, and only here, as the reference the scanner is compared against.
"""
from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc.desk_connect import fence_scan, parameter_parsers as pp  # noqa: E402

FENCE = chr(96) * 3
OLD_FENCE = re.compile(FENCE + r'[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)\r?\n[ \t]*' + FENCE, re.DOTALL)
OLD_COMMENT = re.compile(r'(?m)^\s*//.*$')

CASES = [
    '',
    'no fence at all',
    FENCE + 'json\n{"a": 1}\n' + FENCE,
    FENCE + 'json  \r\n{"a": 1}\r\n  ' + FENCE + ' tail',
    FENCE + '\n' + FENCE,                                  # one newline: not a closed block
    FENCE + '\n\n' + FENCE,                                # empty block
    'x' + FENCE + 'js\nmid-line opener\n' + FENCE,
    FENCE + 'not an opener ' + FENCE + '\nbody\n' + FENCE,
    chr(96) * 4 + '\nfour ticks\n' + chr(96) * 4,
    FENCE + 'a\none\n' + FENCE + '\ntext\n' + FENCE + 'b\ntwo\n\t' + FENCE,
    FENCE + 'a\nnever closed',
    FENCE + 'a\nclosed\n' + FENCE + '\n' + FENCE + 'b\nnever closed',
    FENCE + 'c++\n\r\n' + FENCE,
]


@pytest.mark.parametrize('text', CASES)
def test_same_blocks_as_the_pattern_it_replaces(text):
    assert fence_scan.fenced_blocks(text) == [m.group(1) for m in OLD_FENCE.finditer(text)]


def test_same_blocks_on_random_fence_soup():
    rng = random.Random(1179)
    alphabet = [chr(96), chr(96), FENCE, '\n', '\n', '\r\n', ' ', '\t', 'a', 'js', '{"url": 1}', '+']
    for _ in range(4000):
        text = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert fence_scan.fenced_blocks(text) == [m.group(1) for m in OLD_FENCE.finditer(text)], repr(text)


def test_comment_lines_are_dropped_the_same_way_json_sees_it():
    rng = random.Random(1179)
    alphabet = ['//', '/', '\n', '\n', ' ', '\t', '\r', '"k": 1,', '{', '}', '"//not a comment"']
    for _ in range(4000):
        block = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        new, old = fence_scan.drop_comment_lines(block), OLD_COMMENT.sub('', block)
        # Whitespace between tokens is the only permitted difference.
        assert new.split() == old.split(), repr(block)
        assert pp.load_json(new) == pp.load_json(old), repr(block)


HOSTILE = {
    'unclosed openers': ('a' + FENCE + '\n') * 16_000,                       # 80 KB: was 8 s
    'blank lines in a block': FENCE + '\n"command"' + '\n' * 60_000 + 'x\n' + FENCE,
    'two megabytes': ('a' + FENCE + 'json \n') * 200_000,
}


@pytest.mark.parametrize('label', sorted(HOSTILE))
def test_hostile_text_is_scanned_in_linear_time(label):
    text = HOSTILE[label]
    began = time.perf_counter()
    blocks = pp._candidate_blocks(text)
    took = time.perf_counter() - began
    assert blocks == []
    assert took < 2.0, f'{label}: {took:.2f}s for {len(text)} characters'


def test_config_blocks_are_still_read_from_a_readme():
    readme = ('# Server\n\nInstall:\n\n' + FENCE + 'json\n{\n  // the server\n'
              '  "mcpServers": {"demo": {"command": "npx", "args": ["-y", "demo-mcp@1.2.3"]}}\n}\n'
              + FENCE + '\n\nand\n\n' + FENCE + 'sh\nnpm test\n' + FENCE + '\n')
    assert pp._candidate_blocks(readme) == [
        {'mcpServers': {'demo': {'command': 'npx', 'args': ['-y', 'demo-mcp@1.2.3']}}}]
