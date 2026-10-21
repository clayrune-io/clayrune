"""MC-998: actual route writer + native-shaped synthetic rollouts + real store.

No real prompts, accounts, vendor calls, live sampling or operator DB access.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mc import usage_breakdown_sampler as sampler
from mc.agent_runtime import get_runtime
from mc.blueprints import agent_routes as ar
from mc.usage_breakdown_aggregate import _session_evidence, compute_calibration
from mc.usage_breakdown_store import UsageBreakdownStore

NATIVE_ID = '00000000-0000-7000-8000-000000000001'
START = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def ts(minutes=0, seconds=0):
    return (START + timedelta(minutes=minutes, seconds=seconds)).isoformat()


def usage(inp=1000, cached=700, out=100, reasoning=20):
    return {'input_tokens': inp, 'cached_input_tokens': cached,
            'cache_write_input_tokens': 0, 'output_tokens': out,
            'reasoning_output_tokens': reasoning, 'total_tokens': inp + out}


def event(kind, at, **payload):
    return {'timestamp': at, 'type': 'event_msg', 'payload': {'type': kind, **payload}}


def token(at, total=None, last=None):
    total = usage() if total is None else total
    return event('token_count', at, info={
        'total_token_usage': total, 'last_token_usage': last or total,
        'model_context_window': 258400},
        rate_limits={'primary': {'used_percent': 10, 'window_minutes': 300,
                                 'resets_at': 1792598400}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = UsageBreakdownStore(tmp_path / 'usage_breakdown.sqlite')
    monkeypatch.setattr(ar, 'DATA_DIR', str(tmp_path / 'projects'))
    monkeypatch.setattr(ar, 'load_project', lambda _: {'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, '_session_cumulative_transcript_telemetry', lambda *_: {})
    logs = []
    monkeypatch.setattr(ar, '_log', logs.append)
    path = tmp_path / f'rollout-fixture-{NATIVE_ID}.jsonl'
    meta = {'timestamp': ts(seconds=1), 'type': 'session_meta',
            'payload': {'id': NATIVE_ID, 'cwd': str(tmp_path), 'cli_version': '0.163.0'}}
    records = [meta, event('task_started', ts(seconds=2), turn_id='fixture-turn-1')]
    def write():
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    write()
    resolved_paths = []
    def resolve(project_path, native_id):
        resolved_paths.append((project_path, native_id))
        return path
    monkeypatch.setattr(get_runtime('codex'), 'transcript_path', resolve)
    session = {'session_id': 'fixture-mc-session', 'project_id': 'fixture-project',
               'provider': 'codex', 'provider_session_id': NATIVE_ID,
               'status': 'running', 'started_at': ts(),
               # Deliberately stale/different: this must NEVER supply a tick.
               'usage': usage(9999, 9000, 999, 50)}
    store.record_session_checkpoint(**sampler.baseline_checkpoint_fields(
        session['session_id'], provider='codex', observed_at=ts()))
    result = SimpleNamespace(store=store, path=path, records=records, session=session,
                             write=write, logs=logs, resolved_paths=resolved_paths)
    def tick(at=ts(2)):
        ar._write_usage_breakdown_sample_tick(result.session, at)
        return store.get_session_checkpoints(result.session['session_id'])['sample_ticks'][-1]
    result.tick = tick
    return result


def assert_unknown(row):
    assert row['token_coverage'] == 'unavailable'
    for key in ('input_fresh', 'input_cache_read', 'input_processed_total', 'output_tokens'):
        assert row[key] is None


def test_actual_writer_reads_midturn_native_totals_once_including_cached_subset(env):
    env.records.append(token(ts(2)))
    env.write()
    row = env.tick()
    assert row['token_coverage'] == 'complete'
    assert row['observed_at'] == ts(2)
    assert row['input_processed_total'] == 1000
    assert row['input_cache_read'] == 700
    assert row['input_fresh'] == 300
    assert row['output_tokens'] == 100
    assert row['output_reasoning'] == 20
    assert row['input_cache_write'] is None
    assert row['cache_write_5m'] is None and row['model_usage'] is None
    assert env.session['usage']['input_tokens'] == 9999


def test_long_running_turn_yields_boundary_segments_and_an_unmeasured_open_tail(env):
    for n in (2, 7, 12, 17):
        env.records.append(token(ts(n), usage(1000*n, 700*n, 100*n, 20*n),
                                 usage(1000*(n if n == 2 else 5),
                                       700*(n if n == 2 else 5),
                                       100*(n if n == 2 else 5), 20*(n if n == 2 else 5))))
        env.write()
        assert env.tick(ts(n))['token_coverage'] == 'complete'
    ck = env.store.get_session_checkpoints(env.session['session_id'])
    segments, open_spans = _session_evidence(ck, None)
    assert [r['input_processed_total'] for r in segments] == [2000, 5000, 5000, 5000]
    assert [r['input_cache_read'] for r in segments] == [1400, 3500, 3500, 3500]
    assert open_spans == [(START + timedelta(minutes=17), None)]


def test_actual_native_writer_boundaries_reach_calibration_without_a_completed_turn(env):
    samples = []
    for n in range(1, 8):
        env.records.append(token(ts(n), usage(1000*n, 700*n, 100*n, 20*n), usage()))
        env.write()
        env.tick(ts(n))
        env.store.record_allowance_sample(
            provider='codex', window_kind='5h', window_scope='all',
            raw_utilization=float(n), resets_at=ts(300), source_observed_at=ts(n),
            source_version='rollout_rate_limits_v1')
        samples.append({'raw_utilization': float(n), 'resets_at': ts(300),
                        'source_observed_at': ts(n), 'quality': 'ok'})
    sid = env.session['session_id']
    cal = compute_calibration(samples, {sid: env.store.get_session_checkpoints(sid)}, {},
                              provider='codex', window_scope='all')
    assert cal['eligible_interval_count'] == 6
    assert cal['total_delta_pp'] == 6
    assert all(iv['input_processed_total'] == 1000 and iv['output_tokens'] == 100
               for iv in cal['all_intervals'])
    # One native session supplies real intervals; it cannot bypass the existing
    # independent-session calibration gate just by running for a long time.
    assert cal['status'] == 'insufficient_samples'
    assert cal['distinct_session_count'] == 1


def test_repeated_same_observation_deduplicates_without_new_zero_segment(env):
    env.records.extend([token(ts(2)), token(ts(2))])
    env.write()
    env.tick()
    env.tick()
    ck = env.store.get_session_checkpoints(env.session['session_id'])
    assert len(ck['sample_ticks']) == 1
    assert ck['sample_ticks'][0]['input_processed_total'] == 1000


def test_same_thread_resume_and_completion_use_one_cumulative_scope(env, monkeypatch):
    env.records.append(token(ts(2)))
    env.write()
    first = env.tick()
    env.session['usage'] = usage()
    monkeypatch.setattr(ar, 'now_iso', lambda: ts(3))
    ar._write_usage_breakdown_turn_checkpoint(env.session)
    completed_fact = env.store.get_session_fact(env.session['session_id'])
    env.records.extend([event('task_complete', ts(3), turn_id='fixture-turn-1'),
                        event('task_started', ts(12), turn_id='fixture-turn-2'),
                        token(ts(13), usage(1600, 1100, 160, 30), usage(600, 400, 60, 10))])
    env.write()
    # A respawn/server adoption does not depend on in-memory sampler history.
    env.session = dict(env.session)
    env.session['started_at'] = ts(12)
    monkeypatch.setattr(ar, 'now_iso', lambda: ts(12))
    ar._write_usage_breakdown_turn_start_checkpoint(env.session)
    second = env.tick(ts(13))
    assert first['input_processed_total'] == completed_fact['input_processed_total'] == 1000
    assert second['input_processed_total'] == 1600
    segments, _ = _session_evidence(env.store.get_session_checkpoints(env.session['session_id']),
                                   {'status': 'running'})
    assert sum(s['input_processed_total'] or 0 for s in segments) == 1600
    assert segments[-1]['input_processed_total'] == 600


@pytest.mark.parametrize('case', ['no_record', 'stale', 'future', 'no_info', 'no_timestamp',
                                  'naive_time', 'malformed_info', 'last_only', 'total_only',
                                  'wrong_type', 'missing_meta', 'wrong_identity', 'inherited',
                                  'truncated', 'reset', 'counter_order', 'conflict'])
def test_missing_stale_malformed_or_reset_native_evidence_stays_unknown(env, case):
    record = token(ts(2))
    env.records.append(record)
    if case == 'no_record':
        env.records.pop()
    elif case == 'stale':
        record['timestamp'] = ts(1)
    elif case == 'future':
        record['timestamp'] = ts(3)
    elif case == 'no_info':
        record['payload']['info'] = None
    elif case == 'no_timestamp':
        record.pop('timestamp')
    elif case == 'naive_time':
        record['timestamp'] = '2026-10-21T12:02:00'
    elif case == 'malformed_info':
        record['payload']['info'] = []
    elif case in ('last_only', 'total_only'):
        record['payload']['info'].pop('total_token_usage' if case == 'last_only' else 'last_token_usage')
    elif case == 'wrong_type':
        record['type'] = 'response_item'
    elif case == 'missing_meta':
        env.records.pop(0)
    elif case == 'wrong_identity':
        env.records[0]['payload']['id'] = 'wrong-native-id'
    elif case == 'inherited':
        record['payload']['info']['last_token_usage'] = usage(300, 200, 30, 10)
    elif case == 'reset':
        env.records.insert(2, token(ts(1), usage(2000, 1400, 200, 40)))
    elif case == 'counter_order':
        env.records.insert(2, token(ts(1), usage(900, 200, 90, 10)))
    elif case == 'conflict':
        env.records.append(token(ts(2), usage(1100, 750, 110, 20)))
    env.write()
    if case == 'truncated':
        with env.path.open('a', encoding='utf-8') as stream:
            stream.write('{"type":"event_msg","payload":{"type":"token_count"')
    assert_unknown(env.tick())
    assert env.logs and 'unavailable' in env.logs[-1]


@pytest.mark.parametrize('field,value', [
    ('input_tokens', None), ('input_tokens', -1), ('input_tokens', True),
    ('input_tokens', '1000'), ('output_tokens', 1.5), ('cached_input_tokens', None),
    ('cached_input_tokens', 1001), ('reasoning_output_tokens', 101),
    ('cache_write_input_tokens', 1), ('total_tokens', 999999),
    ('cache_write_input_tokens', False)])
def test_bad_native_numbers_are_not_promoted_to_coverage(env, field, value):
    counters = usage()
    counters[field] = value
    env.records.append(token(ts(2), counters))
    env.write()
    assert_unknown(env.tick())


def test_explicit_native_zero_is_observed_but_absent_categories_stay_null(env):
    env.records.append(token(ts(2), usage(0, 0, 0, 0)))
    env.write()
    row = env.tick()
    assert row['token_coverage'] == 'complete'
    assert row['input_processed_total'] == row['output_tokens'] == 0
    assert row['input_cache_write'] is None


def test_external_resume_history_before_mc_baseline_is_not_charged_to_first_segment(env):
    env.records[0]['timestamp'] = ts(-60)
    env.records.append(token(ts(2)))
    env.write()
    assert_unknown(env.tick())
    assert 'predates' in env.logs[-1]


def test_rolled_thread_carry_is_not_trusted_as_live_source_evidence(env):
    env.session['_codex_usage_carry'] = usage(10000, 7000, 1000, 100)
    env.records.append(token(ts(2)))
    env.write()
    assert_unknown(env.tick())
    assert 'carry' in env.logs[-1]


def test_native_replacement_cannot_hide_a_reset_by_overtaking_old_counters(env):
    env.records.append(token(ts(2)))
    env.write()
    env.tick()
    env.records[0]['timestamp'] = ts(3)
    env.records[1]['timestamp'] = ts(3, 1)
    env.records[2] = token(ts(4), usage(9000, 5000, 900, 90))
    env.write()
    assert_unknown(env.tick(ts(4)))
    assert 'changed' in env.logs[-1]


def test_missing_rollout_and_unreadable_source_remain_unknown(env, monkeypatch):
    monkeypatch.setattr(get_runtime('codex'), 'transcript_path', lambda *_: None)
    assert_unknown(env.tick())


def test_read_error_is_visible_and_does_not_escape_the_actual_writer(env):
    env.path.unlink()  # owned synthetic fixture only
    assert_unknown(env.tick())
    assert 'FileNotFoundError' in env.logs[-1]


def test_later_native_records_do_not_leak_back_into_an_older_boundary(env):
    env.records.extend([token(ts(2)), token(ts(3), usage(2000, 1400, 200, 40))])
    env.write()
    assert env.tick()['input_processed_total'] == 1000


def test_missing_native_id_does_not_read_other_sessions_rollouts(env):
    env.session.pop('provider_session_id')
    assert_unknown(env.tick())
    assert env.resolved_paths == []


def test_no_new_source_at_a_later_boundary_is_not_a_confirmed_zero(env):
    env.records.append(token(ts(2)))
    env.write()
    assert env.tick()['token_coverage'] == 'complete'
    assert_unknown(env.tick(ts(3)))
    ck = env.store.get_session_checkpoints(env.session['session_id'])
    segments, _ = _session_evidence(ck, None)
    assert segments[-1]['token_coverage'] == 'unavailable'
    assert segments[-1]['input_processed_total'] is None


def test_real_runtime_resolves_native_rollout_and_its_worktree_identity(env, monkeypatch):
    from mc import agent_runtime
    from mc.agent_runtime import CodexRuntime
    native_home = env.path.parent / 'native-home'
    dated = native_home / '2026' / '10' / '21'
    dated.mkdir(parents=True)
    path = dated / f'rollout-2026-10-21T12-00-01-{NATIVE_ID}.jsonl'
    env.records.append(token(ts(2)))
    path.write_text(''.join(json.dumps(r) + '\n' for r in env.records), encoding='utf-8')
    monkeypatch.setattr(agent_runtime, '_CODEX_HOME', native_home)
    monkeypatch.setattr(get_runtime('codex'), 'transcript_path',
                        CodexRuntime.transcript_path.__get__(get_runtime('codex')))
    assert env.tick()['input_processed_total'] == 1000


def test_missing_durable_baseline_does_not_invent_a_session_origin(env):
    env.session['session_id'] = 'no-durable-baseline'
    env.records.append(token(ts(2)))
    env.write()
    assert_unknown(env.tick())


def test_unknown_reason_is_logged_once_until_evidence_changes(env):
    env.tick()
    env.tick()
    assert len(env.logs) == 1
    env.records.append(token(ts(3)))
    env.write()
    assert env.tick(ts(3))['token_coverage'] == 'complete'
    env.tick(ts(4))
    assert len(env.logs) == 2


def test_incognito_writes_and_reads_nothing(env):
    env.session['incognito'] = True
    ar._write_usage_breakdown_sample_tick(env.session, ts(2))
    assert env.resolved_paths == []
    assert env.store.get_session_checkpoints(env.session['session_id'])['sample_ticks'] == []


def test_agent_worktree_cwd_is_used_for_rollout_resolution(env):
    env.session['_agent_cwd'] = 'fixture-agent-worktree'
    env.records.append(token(ts(2)))
    env.write()
    assert env.tick()['token_coverage'] == 'complete'
    assert env.resolved_paths == [('fixture-agent-worktree', NATIVE_ID)]


def test_claude_real_writer_retains_transcript_totals(env, monkeypatch):
    env.session['provider'] = 'claude'
    monkeypatch.setattr(ar, '_session_cumulative_transcript_telemetry', lambda *_: {
        'input_tokens': 10, 'cache_read_tokens': 700, 'cache_write_tokens': 90,
        'output_tokens': 100})
    row = env.tick()
    assert row['provider'] == 'claude' and row['token_coverage'] == 'complete'
    assert row['input_processed_total'] == 800
    assert row['input_fresh'] == 10
    assert env.resolved_paths == []
