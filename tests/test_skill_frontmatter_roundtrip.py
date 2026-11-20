"""Frontmatter written by `dump_skill_md` reads back as what was written.

MC-1179 pass 3, ML34. The dump escapes a quote and a backslash when it
double-quotes a value; the parser removed the outer quotes and nothing else.
A description containing `"hi"` read back as `\\"hi\\"` after one round trip
and doubled its backslashes on every later one. Every read-then-write path
does a round trip: the skill PUT route, and `write_position`, which re-reads
the position on every supersession.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import skills  # noqa: E402

BS = chr(92)
Q = chr(34)

VALUES = [
    'say: ' + Q + 'hi' + Q + ' to them',            # colon forces quoting
    'path: C:' + BS + 'Users' + BS + 'name',        # lone backslashes
    'a trailing backslash: ' + BS,
    'both: ' + BS + Q + ' in one value',
    'plain value with no specials',
    "single 'quotes': kept as they are",
    ' leading space is quoted too',
]


@pytest.mark.parametrize('value', VALUES)
def test_a_value_survives_five_round_trips_unchanged(value):
    text = skills.dump_skill_md({'name': 'x', 'description': value}, 'body\n')
    first = text
    for _ in range(5):
        meta, body = skills.parse_skill_md(text)
        assert meta['description'] == value
        assert body == 'body\n'
        text = skills.dump_skill_md(meta, body)
    assert text == first        # stable: nothing grows


def test_a_hand_written_single_quoted_value_is_not_unescaped():
    text = "---\nname: x\ndescription: 'keeps " + BS + BS + " and " + BS + Q + "'\n---\nb\n"
    meta, _ = skills.parse_skill_md(text)
    assert meta['description'] == 'keeps ' + BS + BS + ' and ' + BS + Q


def test_an_unquoted_value_is_not_unescaped():
    text = '---\nname: x\ndescription: keeps ' + BS + BS + ' as typed\n---\nb\n'
    meta, _ = skills.parse_skill_md(text)
    assert meta['description'] == 'keeps ' + BS + BS + ' as typed'
