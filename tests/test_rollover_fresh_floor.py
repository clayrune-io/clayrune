"""The fresh floor comes from the native transcript's own first call
(mc/rollover_fresh_floor.py), whichever call the reader happened to see first.

2026-11-25 review, F1 and F2: a reader that attaches late (server restart,
revive, adopted process) used to leave the floor unknown, and before that it
took the late reading itself as the floor, so every restart moved the limit.
"""
import json

import pytest

from mc import agent_runtime
from mc.rollover_fresh_floor import FloorProbe
from mc.rollover_threshold import resolve

NATIVE = 'native-1'
PIN = {'context_rollover_by_character': {'global:dave': 100_000}}
_NORMALIZE = agent_runtime.normalize_context_tokens


def _usage(tokens):
    return {'input_tokens': 10, 'cache_read_input_tokens': tokens - 10,
            'cache_creation_input_tokens': 0, 'output_tokens': 5}


def _event(call, tokens, native=NATIVE):
    return {'type': 'assistant', 'session_id': native, 'message': {
        'id': call, 'model': 'claude-opus-5-5', 'usage': _usage(tokens)}}


def _row(call, tokens, native=NATIVE, parent='prompt'):
    return dict(_event(call, tokens, native), sessionId=native, parentUuid=parent)


def _prompt(native=NATIVE, parent=None):
    return {'type': 'user', 'sessionId': native, 'parentUuid': parent, 'uuid': 'prompt',
            'message': {'role': 'user', 'content': 'opening prompt'}}


def _write(path, *rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


def _attach(path):
    proc = object()
    session = {'character': 'global:dave', 'model': 'opus', 'provider': 'claude',
               'claude_session_id': NATIVE, 'proc': proc}
    return session, FloorProbe(proc), (lambda cwd, native: path)


def test_reader_that_saw_the_first_call_gets_it_as_the_floor(tmp_path):
    path = tmp_path / 'native.jsonl'
    _write(path, _prompt(), _row('first', 89_000))
    session, probe, find = _attach(path)
    probe.observe(session, _event('first', 89_000), find, _NORMALIZE)
    assert session['_mt_fresh_floor'] == 89_000


@pytest.mark.parametrize('late', [116_754, 180_000, 230_000, 300_000])
def test_late_reader_takes_the_transcripts_first_call_not_its_own(tmp_path, late):
    """F2: the limit must be the same before and after a restart."""
    path = tmp_path / 'native.jsonl'
    _write(path, _prompt(), _row('first', 89_000), _row('mid', 150_000, parent='first'),
           _row('late', late, parent='mid'))
    before = resolve({'character': 'global:dave', 'model': 'opus',
                      '_mt_fresh_floor': 89_000}, PIN)

    session, probe, find = _attach(path)
    assert resolve(session, PIN)[:2] == (200_000, 'base')    # restarted, nothing read yet
    probe.observe(session, _event('late', late), find, _NORMALIZE)

    assert session['_mt_fresh_floor'] == 89_000
    after = resolve(session, PIN)
    assert after == before and after.tokens == 220_000
    assert after.tokens != 100_000


def test_late_reader_on_a_transcript_without_its_own_start_stays_unknown(tmp_path):
    # The native segment's rows continue an earlier one: no root, no opening
    # prompt, so its first call is not a fresh start.
    path = tmp_path / 'native.jsonl'
    _write(path, _prompt(parent='earlier'), _row('first', 240_000))
    session, probe, find = _attach(path)
    probe.observe(session, _event('late', 300_000), find, _NORMALIZE)
    assert session.get('_mt_fresh_floor') is None
    assert resolve(session, PIN)[:2] == (200_000, 'base')


def test_subagent_and_other_native_rows_are_not_the_start(tmp_path):
    path = tmp_path / 'native.jsonl'
    side = dict(_row('side', 5_000), isSidechain=True)
    _write(path, _prompt('other'), _row('other-first', 40_000, native='other'),
           _prompt(), side, _row('first', 89_000), _row('late', 180_000, parent='first'))
    session, probe, find = _attach(path)
    probe.observe(session, dict(_event('sub', 7_000), parent_tool_use_id='toolu_1'),
                  find, _NORMALIZE)
    assert session.get('_mt_fresh_floor') is None
    probe.observe(session, _event('late', 180_000), find, _NORMALIZE)
    assert session['_mt_fresh_floor'] == 89_000
