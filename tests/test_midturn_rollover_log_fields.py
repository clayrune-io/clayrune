"""MC-1178 prep: every midturn rollover row carries character, model, provider,
fresh_floor_tokens and working_room_tokens (log only — roll decisions are
pinned by tests/test_midturn_rollover.py and are unchanged)."""
import json

import pytest

from mc import midturn_rollover as mt
from mc import state

FIELDS = ('character', 'model', 'provider', 'fresh_floor_tokens',
          'working_room_tokens')


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(mt, '_log_dir', lambda: tmp_path)
    monkeypatch.setitem(state.CONFIG, 'midturn_rollover_enabled', True)
    monkeypatch.delitem(state.CONFIG, 'context_rollover_by_character', raising=False)
    monkeypatch.setitem(state.CONFIG, 'context_rollover_tokens', 200_000)

    def rows():
        f = tmp_path / 'p1.jsonl'
        return [json.loads(x) for x in f.read_text().splitlines()] if f.is_file() else []
    return rows


def _session(**extra):
    s: dict = {'session_id': 's1', 'project_id': 'p1', 'claude_session_id': 'c1'}
    s.update(extra)
    return s


def test_row_carries_all_fields(log):
    s = _session(character={'name': 'dave', 'scope': 'global'}, provider='claude',
                 model='claude-opus-4', observed_model='claude-opus-4-20250101')
    mt.note_call_tokens(s, 30_000)
    mt.record_roll(s, 250_000)
    row = log()[0]
    assert row['character'] == 'global:dave'
    assert row['model'] == 'claude-opus-4-20250101'    # observed beats requested
    assert row['provider'] == 'claude'
    assert row['fresh_floor_tokens'] == 30_000
    assert row['working_room_tokens'] == 170_000       # 200k default - 30k
    assert row['context_tokens'] == 250_000            # existing fields intact


def test_requested_model_used_when_nothing_observed(log):
    s = _session(model='opus')
    mt.record_roll(s, 1)
    assert log()[0]['model'] == 'opus'
    s2 = _session(agent_model='sonnet')
    mt.record_roll(s2, 1)
    assert log()[1]['model'] == 'sonnet'


def test_absent_values_are_null_not_missing(log):
    mt.record_roll(_session(), 250_000)
    row = log()[0]
    for k in FIELDS:
        assert k in row, k
        assert row[k] is None, (k, row[k])


def test_empty_native_default_model_and_bare_character_are_null(log):
    mt.record_roll(_session(model='', character=None), 1)
    row = log()[0]
    assert row['model'] is None and row['character'] is None


def test_working_room_uses_the_character_threshold(log, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'context_rollover_by_character',
                        {'global:dave': 120_000})
    s = _session(character={'name': 'dave', 'scope': 'global'})
    mt.note_call_tokens(s, 45_000)
    mt.record_roll(s, 130_000)
    row = log()[0]
    assert row['threshold'] == 120_000 and row['working_room_tokens'] == 75_000


def test_floor_is_the_first_reading_not_the_latest(log):
    s = _session()
    mt.note_call_tokens(s, 40_000)
    mt.note_call_tokens(s, 90_000)
    mt.note_call_tokens(s, 150_000)
    assert s['_mt_fresh_floor'] == 40_000
    mt.record_roll(s, 150_000)
    assert log()[0]['fresh_floor_tokens'] == 40_000


def test_subagent_readings_never_set_the_floor(log):
    s = _session()
    mt.note_call_tokens(s, 5_000, parent_tool_use_id='toolu_x')
    assert s.get('_mt_fresh_floor') is None
    mt.note_call_tokens(s, 40_000)
    assert s['_mt_fresh_floor'] == 40_000


def test_floor_is_first_post_roll_reading_and_roll_row_keeps_outgoing_floor(log):
    s = _session()
    mt.note_call_tokens(s, 40_000)
    mt.note_call_tokens(s, 210_000)
    # agent_interrupt calls begin_roll BEFORE _maybe_midturn_roll calls record_roll.
    mt.begin_roll(s)
    assert s['_mt_fresh_floor'] is None             # reset for the new segment
    mt.record_roll(s, 210_000)
    assert log()[0]['fresh_floor_tokens'] == 40_000  # the segment that rolled
    mt.note_call_tokens(s, 55_000)                  # first reading after the roll
    mt.note_call_tokens(s, 120_000)
    assert s['_mt_fresh_floor'] == 55_000
    mt.begin_roll(s)
    mt.record_roll(s, 230_000)
    row = log()[1]
    assert row['fresh_floor_tokens'] == 55_000 and row['roll_number'] == 2
    assert row['working_room_tokens'] == 145_000
    assert s['_mt_fresh_floor'] is None


def test_roll_with_no_reading_logs_null_floor_and_room(log):
    s = _session()
    mt.begin_roll(s)
    mt.record_roll(s, 250_000)
    row = log()[0]
    assert row['fresh_floor_tokens'] is None and row['working_room_tokens'] is None


def test_gave_up_row_carries_the_current_segments_floor(log):
    s = _session(character={'name': 'dave', 'scope': 'global'})
    mt.note_call_tokens(s, 35_000)
    s['_mt_roll_failures'] = mt.MAX_ROLL_FAILURES
    s['_mt_attempt_tokens'] = 210_000
    mt._observe_failures(s)
    row = log()[0]
    assert row['event'] == 'gave_up' and row['character'] == 'global:dave'
    assert row['fresh_floor_tokens'] == 35_000


def test_decisions_unchanged_by_the_floor(log):
    """should_roll must not read the floor: only the baseline gates growth."""
    s = _session(provider='claude')
    over = lambda tokens, sess: tokens is not None and tokens >= 200_000
    mt.note_call_tokens(s, 250_000)            # fresh floor already over the limit
    assert mt.should_roll(s, over) is True     # no baseline armed -> rolls
