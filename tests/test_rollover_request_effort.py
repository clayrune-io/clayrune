"""The effort of the triggering call, when its transcript row is not on disk
yet (mc/rollover_request.py).

2026-11-25 review, F9: effort is read from the transcript row of the call
that triggered the check. stdout runs ahead of the transcript, so mid-turn
that row is often not flushed yet; effort was then None and the resolver used
the pooled growth row. Measured, opus over an 89k floor: 220,000 without
effort, 200,000 with `high` — the same chat got a different limit depending
on which path asked and when.
"""
import json

import pytest

from mc import agent_runtime
from mc import rollover_decision
from mc import rollover_request as rr
from mc import state

NATIVE = 'native-effort-1'
MODEL = 'claude-opus-5-5'


def _event(call):
    return {'type': 'assistant', 'session_id': NATIVE,
            'message': {'id': call, 'model': MODEL}}


def _rows(call, effort):
    request = {'type': 'api-request', 'sessionId': NATIVE, 'id': f'req-{call}',
               'params': {'output_config': {'effort': effort}} if effort else {}}
    assistant = dict(_event(call), sessionId=NATIVE, requestRef=f'req-{call}')
    return [request, assistant]


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    path = tmp_path / f'{NATIVE}.jsonl'
    path.write_text('', encoding='utf-8')
    monkeypatch.setattr(agent_runtime, '_find_claude_transcript', lambda cwd, native: path)

    def append(call, effort):
        with path.open('a', encoding='utf-8') as stream:
            for row in _rows(call, effort):
                stream.write(json.dumps(row) + '\n')
    return append


def _session(**extra):
    return dict({'claude_session_id': NATIVE, 'character': 'global:dave',
                 'model': 'opus', 'provider': 'claude', '_mt_fresh_floor': 89_000}, **extra)


def test_row_not_on_disk_yet_uses_the_last_known_effort(transcript):
    session = _session()
    transcript('m1', 'high')
    rr.note_call(session, _event('m1'), None, 150_000)
    assert rr.call_facts(session, 150_000) == (MODEL, 'high')

    rr.note_call(session, _event('m2'), None, 199_000)     # stdout is ahead of the file
    assert rr.call_facts(session, 199_000) == (MODEL, 'high')

    transcript('m2', 'medium')                             # the row lands: it is the answer
    assert rr.call_facts(session, 199_000) == (MODEL, 'medium')


def test_last_known_effort_survives_calls_that_were_never_looked_up(transcript):
    session = _session()
    transcript('m1', 'high')
    rr.note_call(session, _event('m1'), None, 150_000)
    rr.call_facts(session, 150_000)
    for i, tokens in enumerate((160_000, 170_000, 180_000)):
        rr.note_call(session, _event(f'quiet-{i}'), None, tokens)   # no decision asked
    assert rr.call_facts(session, 180_000) == (MODEL, 'high')


def test_a_row_on_disk_without_an_effort_is_not_overridden(transcript):
    session = _session()
    transcript('m1', 'high')
    rr.note_call(session, _event('m1'), None, 150_000)
    rr.call_facts(session, 150_000)
    transcript('m2', None)
    rr.note_call(session, _event('m2'), None, 199_000)
    assert rr.call_facts(session, 199_000) == (MODEL, None)


def test_no_effort_ever_seen_stays_unknown(transcript):
    session = _session()
    rr.note_call(session, _event('m1'), None, 150_000)
    assert rr.call_facts(session, 150_000) == (MODEL, None)


def test_the_limit_does_not_move_a_grid_step_while_the_row_is_flushing(transcript, monkeypatch):
    monkeypatch.setattr(state, 'CONFIG', {})
    session = _session()
    transcript('m1', 'high')
    rr.note_call(session, _event('m1'), None, 150_000)
    on_disk = rollover_decision.capture(session, 'midturn', 'tokens', 150_000).fields

    rr.note_call(session, _event('m2'), None, 199_000)
    flushing = rollover_decision.capture(session, 'midturn', 'tokens', 199_000).fields

    assert on_disk['effective_effort'] == flushing['effective_effort'] == 'high'
    assert on_disk['threshold'] == flushing['threshold'] == 200_000
    assert flushing['threshold_reason'] == on_disk['threshold_reason']
