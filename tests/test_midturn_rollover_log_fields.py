"""MC-1178 prep: every midturn rollover row carries character, model, provider,
fresh_floor_tokens and working_room_tokens (log only — roll decisions are
pinned by tests/test_midturn_rollover.py and are unchanged)."""
import json

import pytest

from mc import midturn_rollover as mt
from mc import state
from mc.rollover_decision import capture
from mc.rollover_growth_snapshot import GROWTH_ROWS

FIELDS = ('character', 'model', 'provider', 'fresh_floor_tokens',
          'working_room_tokens', 'requested_model', 'canonical_model', 'effective_effort',
          'opening_prompt_unanswered', 'transcript_bytes')


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


def _record(s, tokens, *, begin_roll=False):
    decision = capture(s, 'midturn', 'tokens', tokens)
    if begin_roll:
        mt.begin_roll(s)
    mt.record_roll(s, decision)


def test_row_carries_all_fields(log):
    s = _session(character={'name': 'dave', 'scope': 'global'}, provider='claude',
                 model='claude-opus-4', observed_model='claude-opus-4-20250101')
    s['_mt_fresh_floor'] = 30_000
    _record(s, 250_000)
    row = log()[0]
    assert row['character'] == 'global:dave'
    assert row['model'] == 'claude-opus-4-20250101'    # observed beats requested
    assert row['provider'] == 'claude'
    assert row['fresh_floor_tokens'] == 30_000
    assert row['working_room_tokens'] == 170_000       # 200k default - 30k
    assert row['context_tokens'] == 250_000            # existing fields intact


def test_requested_model_used_when_nothing_observed(log):
    s = _session(model='opus')
    _record(s, 1)
    assert log()[0]['model'] == 'opus'
    s2 = _session(agent_model='sonnet')
    _record(s2, 1)
    assert log()[1]['model'] == 'sonnet'


def test_unknown_requested_alias_has_no_canonical_model(log):
    _record(_session(model='fable'), 200_000)
    row = log()[0]
    assert row['requested_model'] == 'fable'
    assert row['canonical_model'] is None


def test_absent_values_are_null_not_missing(log):
    _record(_session(), 250_000)
    row = log()[0]
    for k in FIELDS:
        assert k in row, k
        assert row[k] is None, (k, row[k])


def test_empty_native_default_model_and_bare_character_are_null(log):
    _record(_session(model='', character=None), 1)
    row = log()[0]
    assert row['model'] is None and row['character'] is None


def test_working_room_uses_the_character_threshold(log, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'context_rollover_by_character',
                        {'global:dave': 120_000})
    s = _session(character={'name': 'dave', 'scope': 'global'})
    s['_mt_fresh_floor'] = 45_000
    _record(s, 130_000)
    row = log()[0]
    assert row['threshold'] == 120_000 and row['working_room_tokens'] == 75_000


def test_call_tokens_never_replace_verified_floor(log):
    s = _session(_mt_fresh_floor=40_000)
    mt.note_call_tokens(s, 90_000)
    mt.note_call_tokens(s, 150_000)
    assert s['_mt_fresh_floor'] == 40_000
    _record(s, 150_000)
    assert log()[0]['fresh_floor_tokens'] == 40_000


def test_unverified_readings_never_set_the_floor(log):
    s = _session()
    mt.note_call_tokens(s, 5_000, parent_tool_use_id='toolu_x')
    assert s.get('_mt_fresh_floor') is None
    mt.note_call_tokens(s, 40_000)
    assert s.get('_mt_fresh_floor') is None


def test_roll_row_keeps_outgoing_floor(log):
    s = _session(_mt_fresh_floor=40_000)
    mt.note_call_tokens(s, 210_000)
    # agent_interrupt calls begin_roll BEFORE _maybe_midturn_roll calls record_roll.
    _record(s, 210_000, begin_roll=True)
    assert s['_mt_fresh_floor'] is None             # reset for the new segment
    assert log()[0]['fresh_floor_tokens'] == 40_000  # the segment that rolled
    s['_mt_fresh_floor'] = 55_000                   # verified in the successor
    mt.note_call_tokens(s, 120_000)
    assert s['_mt_fresh_floor'] == 55_000
    _record(s, 230_000, begin_roll=True)
    row = log()[1]
    assert row['fresh_floor_tokens'] == 55_000 and row['roll_number'] == 2
    assert row['working_room_tokens'] == 145_000
    assert s['_mt_fresh_floor'] is None


def test_roll_with_no_reading_logs_null_floor_and_room(log):
    s = _session()
    _record(s, 250_000, begin_roll=True)
    row = log()[0]
    assert row['fresh_floor_tokens'] is None and row['working_room_tokens'] is None


def test_gave_up_row_carries_the_current_segments_floor(log):
    s = _session(character={'name': 'dave', 'scope': 'global'})
    s['_mt_fresh_floor'] = 35_000
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



def test_model_threshold_reason_uses_outgoing_floor_after_reset(log, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'context_rollover_by_character', {'global:dave': 100_000})
    s = _session(model='claude-fable-5-2', character='global:dave')
    s['_mt_fresh_floor'] = 90_000
    _record(s, 300_000, begin_roll=True)
    row = log()[0]
    assert row['threshold'] == 290_000
    assert row['threshold_reason'] == 'computed:model'
    assert row['fresh_floor_tokens'] == 90_000
    assert row['working_room_tokens'] == 200_000
    assert row['growth_source'] == 'model'
    assert row['mean_growth_tokens'] == GROWTH_ROWS[None, 'claude-fable-5-2', None].mean_growth
    assert s['_mt_fresh_floor'] is None


def test_gave_up_logs_model_threshold_for_current_floor(log):
    s = _session(agent_model='claude-fable-5-1')
    s['_mt_fresh_floor'] = 90_000
    s['_mt_roll_failures'] = mt.MAX_ROLL_FAILURES
    s['_mt_attempt_tokens'] = 310_000
    mt._observe_failures(s)
    row = log()[0]
    assert row['threshold'] == 300_000 and row['threshold_reason'] == 'computed:reviewed_family'
    assert row['growth_source'] == 'reviewed_family'
