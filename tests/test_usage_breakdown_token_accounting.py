"""MC-998 / backlog 4668eafc follow-up 6 -- token accounting fixes from
Kestrel's review (docs/_journal/4668eafc-usage-token-accounting-review.md).

  FIX 1  estimate range uses ONE workload definition (processed input +
         output) for numerator and calibration denominator.
  FIX 2  totals carry the cache-write TTL split (5m / 1h / unknown) that
         the report renders under "Input tokens".
  FIX 3  cache-write TTL is captured: nested `cache_creation` is SUMMED
         across turns, the transcript extractor sums it per message, and it
         rides through session_fact / session_checkpoint (schema v7) with
         NULL = unknown, never invented.
  FIX 4  window_scope is honoured by the totals, sessions whose model is
         unknown are counted in a hint, and the model dimension splits a
         mixed session across its models (per-model category totals).
"""
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_aggregate import (  # noqa: E402
    build_breakdown, compute_rankings, compute_segmented_bar, compute_totals,
    filter_facts_in_range,
)
from mc.usage_breakdown_sampler import (  # noqa: E402
    baseline_checkpoint_fields, completion_checkpoint_fields,
    sample_tick_checkpoint_fields, session_fact_from_entry,
    turn_start_checkpoint_fields,
)
from mc.usage_breakdown_store import SCHEMA_VERSION, UsageBreakdownStore  # noqa: E402

T0, T1, T2 = '2026-09-28T10:00:00+00:00', '2026-09-28T10:05:00+00:00', '2026-09-28T10:10:00+00:00'
WIN = dict(range_start='2026-09-28T09:00:00+00:00', range_end='2026-09-28T11:00:00+00:00')


# ── FIX 1 -- estimate range ──────────────────────────────────────────────

def test_fix1_range_uses_the_same_workload_in_numerator_and_denominator():
    """Kestrel section 3: the low end divided fresh+output (6M of 1.26B) by a
    denominator calibrated on ALL input+output -> 0.1%-41%. Both ends must
    use processed-input+output."""
    totals = {'tokens': {'input_fresh': 20, 'input_processed_total': 1000, 'output_tokens': 100}}
    calibration = {'status': 'ok', 'workload_per_point': 50.0,
                   'workload_per_point_lo': 40.0, 'workload_per_point_hi': 60.0}
    seg = compute_segmented_bar({'status': 'ok', 'delta_pp': 30.0}, totals, calibration)
    low, high = seg['range_pp']
    assert low == 1100 / 60.0
    assert high == 1100 / 40.0
    assert low < seg['estimated_pp'] < high


# ── FIX 3 -- capture ─────────────────────────────────────────────────────

def test_fix3_accumulate_session_usage_sums_nested_cache_creation_ttl():
    from mc.blueprints import agent_routes as ar
    s = {}
    ar._accumulate_session_usage(s, {
        'input_tokens': 1, 'output_tokens': 2, 'cache_creation_input_tokens': 100,
        'cache_read_input_tokens': 5,
        'cache_creation': {'ephemeral_5m_input_tokens': 30, 'ephemeral_1h_input_tokens': 70}})
    ar._accumulate_session_usage(s, {
        'input_tokens': 1, 'output_tokens': 2, 'cache_creation_input_tokens': 50,
        'cache_read_input_tokens': 5,
        'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 50}})
    cc = s['usage']['cache_creation']
    assert cc == {'ephemeral_5m_input_tokens': 30, 'ephemeral_1h_input_tokens': 120}
    assert s['usage']['cache_creation_input_tokens'] == 150  # TTL is a split of it, never added to it


def test_fix3_transcript_extractor_sums_ttl_and_per_model_categories(tmp_path):
    from mc.memory import _extract_transcript_telemetry
    def line(mid, model, **u):
        return json.dumps({'message': {'id': mid, 'model': model, 'usage': u}})
    cc = lambda a, b: {'ephemeral_5m_input_tokens': a, 'ephemeral_1h_input_tokens': b}
    lines = [
        line('m1', 'claude-opus-5-5', input_tokens=2, output_tokens=10,
             cache_read_input_tokens=1000, cache_creation_input_tokens=100, cache_creation=cc(40, 60)),
        line('m1', 'claude-opus-5-5', input_tokens=2, output_tokens=10,   # repeated content block
             cache_read_input_tokens=1000, cache_creation_input_tokens=100, cache_creation=cc(40, 60)),
        line('m2', 'claude-sonnet-5-5', input_tokens=3, output_tokens=20,
             cache_read_input_tokens=2000, cache_creation_input_tokens=200, cache_creation=cc(0, 200)),
    ]
    p = tmp_path / 't.jsonl'
    p.write_text('\n'.join(lines), encoding='utf-8')
    tel = _extract_transcript_telemetry(str(p))
    assert tel['cache_write_tokens'] == 300
    assert tel['cache_write_5m_tokens'] == 40 and tel['cache_write_1h_tokens'] == 260
    assert tel['model_usage'] == {
        'claude-opus-5-5': {'input_fresh': 2, 'input_cache_write': 100,
                            'input_cache_read': 1000, 'output_tokens': 10},
        'claude-sonnet-5-5': {'input_fresh': 3, 'input_cache_write': 200,
                              'input_cache_read': 2000, 'output_tokens': 20},
    }


def test_fix3_transcript_without_ttl_dict_reports_unknown_not_zero(tmp_path):
    from mc.memory import _extract_transcript_telemetry
    p = tmp_path / 't.jsonl'
    p.write_text(json.dumps({'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': {
        'input_tokens': 1, 'output_tokens': 1, 'cache_creation_input_tokens': 100}}}), encoding='utf-8')
    tel = _extract_transcript_telemetry(str(p))
    assert tel['cache_write_tokens'] == 100
    assert tel['cache_write_5m_tokens'] is None and tel['cache_write_1h_tokens'] is None


def test_fix3_fact_and_checkpoints_carry_ttl_and_model_usage():
    mu = {'claude-opus-5-5': {'input_fresh': 2, 'input_cache_write': 100,
                              'input_cache_read': 1000, 'output_tokens': 10}}
    entry = {'provider': 'claude', 'input_tokens': 2, 'output_tokens': 10, 'cache_read_tokens': 1000,
             'cache_write_tokens': 100, 'cache_write_5m_tokens': 40, 'cache_write_1h_tokens': 60,
             'model_usage': mu, 'status': 'completed', 'ts': T1}
    fact = session_fact_from_entry(entry, project_id='p')
    assert (fact['cache_write_5m'], fact['cache_write_1h']) == (40, 60)
    assert fact['model_usage'] == mu
    ck = completion_checkpoint_fields(fact, session_id='s', observed_at=T1)
    assert (ck['cache_write_5m'], ck['cache_write_1h'], ck['model_usage']) == (40, 60, mu)
    tick = sample_tick_checkpoint_fields('s', provider='claude', observed_at=T1, telemetry=fact)
    assert (tick['cache_write_5m'], tick['cache_write_1h'], tick['model_usage']) == (40, 60, mu)
    ts = turn_start_checkpoint_fields('s', provider='claude', observed_at=T2, fact=fact)
    assert (ts['cache_write_5m'], ts['cache_write_1h'], ts['model_usage']) == (40, 60, mu)
    # older entry with no TTL evidence -> NULL, never a fabricated 0
    old = session_fact_from_entry({'provider': 'claude', 'input_tokens': 2, 'output_tokens': 10,
                                   'cache_write_tokens': 100, 'status': 'completed'}, project_id='p')
    assert old['cache_write_5m'] is None and old['cache_write_1h'] is None and old['model_usage'] is None
    # baseline is a confirmed zero for claude, unknown for codex
    base = baseline_checkpoint_fields('s', provider='claude', observed_at=T0)
    assert (base['cache_write_5m'], base['cache_write_1h'], base['model_usage']) == (0, 0, {})
    cx = baseline_checkpoint_fields('s', provider='codex', observed_at=T0)
    assert cx['cache_write_5m'] is None and cx['model_usage'] is None


def test_fix3_codex_fact_leaves_ttl_null():
    fact = session_fact_from_entry({'provider': 'codex', 'status': 'completed',
                                    'usage': {'input_tokens': 100, 'output_tokens': 5,
                                              'cached_input_tokens': 60}}, project_id='p')
    assert fact['cache_write_5m'] is None and fact['cache_write_1h'] is None
    assert fact['model_usage'] is None


def test_fix3_store_v7_roundtrip_and_v6_migration(tmp_path):
    assert SCHEMA_VERSION == 7
    store = UsageBreakdownStore(tmp_path / 'ub.sqlite')
    mu = {'m': {'input_fresh': 1, 'input_cache_write': 2, 'input_cache_read': 3, 'output_tokens': 4}}
    store.upsert_session_fact('s1', {'provider': 'claude', 'status': 'completed', 'token_coverage': 'complete',
                                     'input_cache_write': 9, 'cache_write_5m': 4, 'cache_write_1h': 5,
                                     'model_usage': mu})
    f = store.get_session_fact('s1')
    assert (f['cache_write_5m'], f['cache_write_1h'], f['model_usage']) == (4, 5, mu)
    store.record_session_checkpoint(session_id='s1', provider='claude', checkpoint_type='completion',
                                    observed_at=T1, token_coverage='complete', input_cache_write=9,
                                    cache_write_5m=4, cache_write_1h=5, model_usage=mu)
    store.record_session_checkpoint(session_id='s1', provider='claude', checkpoint_type='sample_tick',
                                    observed_at=T2, token_coverage='complete')
    rows = {r['checkpoint_type']: r for r in store.list_session_checkpoints()}
    assert rows['completion']['model_usage'] == mu and rows['completion']['cache_write_5m'] == 4
    assert rows['sample_tick']['cache_write_5m'] is None and rows['sample_tick']['model_usage'] is None

    # a real v6 file (no new columns) upgrades in place, keeps rows, new cols NULL
    path = tmp_path / 'v6.sqlite'
    v6 = UsageBreakdownStore(path)
    v6.upsert_session_fact('old', {'provider': 'claude', 'status': 'completed', 'token_coverage': 'complete'})
    with sqlite3.connect(path) as db:
        for t in ('session_fact', 'session_checkpoint'):
            for c in ('cache_write_5m', 'cache_write_1h', 'model_usage'):
                db.execute(f'ALTER TABLE {t} DROP COLUMN {c}')
        db.execute('PRAGMA user_version=6')
    migrated = UsageBreakdownStore(path).get_session_fact('old')
    assert migrated['cache_write_5m'] is None and migrated['model_usage'] is None
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 7


# ── checkpoint fixtures for the aggregate tests ──────────────────────────

def _row(sid, ctype, at, *, fresh=0, write=0, read=0, out=0, t5=None, t1=None, mu=None, cov='complete'):
    return {'session_id': sid, 'provider': 'claude', 'checkpoint_type': ctype, 'observed_at': at,
            'input_fresh': fresh, 'input_cache_write': write, 'input_cache_read': read,
            'input_processed_total': fresh + write + read, 'output_tokens': out, 'output_reasoning': 0,
            'token_coverage': cov, 'cache_write_5m': t5, 'cache_write_1h': t1, 'model_usage': mu}


def _fact(sid, model='claude-opus-5-5', **kw):
    f = {'session_id': sid, 'provider': 'claude', 'project_id': 'p', 'character': 'C',
         'trigger_type': 'manual', 'observed_model': model, 'status': 'completed',
         'started_at': T0, 'ended_at': T1, 'token_coverage': 'complete',
         'input_fresh': 0, 'input_cache_write': 0, 'input_cache_read': 0,
         'input_processed_total': 0, 'output_tokens': 0}
    f.update(kw)
    return f


def _session(sid, *, model='claude-opus-5-5', base=None, end=None):
    ck = {'baseline': base or _row(sid, 'baseline', T0, t5=0, t1=0, mu={}),
          'turn_starts': [], 'completions': [end], 'sample_ticks': []}
    # the fact mirrors the last completion's counters (a fact that disagrees
    # with its own checkpoint history reads as an untrustworthy final turn)
    tot = {k: end[k] for k in ('input_fresh', 'input_cache_write', 'input_cache_read',
                                'input_processed_total', 'output_tokens')}
    return _fact(sid, model, **tot), ck


def test_fix3_ttl_split_reaches_totals_and_is_never_added_to_the_combined_write():
    fact, ck = _session('s1', end=_row('s1', 'completion', T1, fresh=10, write=100, read=900, out=5,
                                       t5=40, t1=60))
    rows, _ = filter_facts_in_range([fact], {'s1': ck}, provider='claude', **WIN)
    tok = compute_totals(rows, {})['tokens']
    assert tok['input_cache_write'] == 100
    assert (tok['cache_write_5m'], tok['cache_write_1h'], tok['cache_write_ttl_unknown']) == (40, 60, 0)
    assert tok['input_processed_total'] == 1010            # TTL subtotals not added again


def test_fix3_session_without_ttl_is_unknown_and_totals_stay_null_not_zero():
    fact, ck = _session('s1', base=_row('s1', 'baseline', T0),  # old baseline: TTL NULL
                        end=_row('s1', 'completion', T1, fresh=10, write=100, read=900, out=5))
    rows, _ = filter_facts_in_range([fact], {'s1': ck}, provider='claude', **WIN)
    tok = compute_totals(rows, {})['tokens']
    assert tok['cache_write_5m'] is None and tok['cache_write_1h'] is None
    assert tok['cache_write_ttl_unknown'] == 100


def test_fix3_partly_known_session_splits_the_write_between_known_and_unknown():
    fact = _fact('s1')
    ck = {'baseline': _row('s1', 'baseline', T0),       # NULL TTL -> first segment unknown
          'turn_starts': [], 'completions': [_row('s1', 'completion', T2, fresh=10, write=300, read=900,
                                                   out=5, t5=100, t1=200)],
          'sample_ticks': [_row('s1', 'sample_tick', T1, fresh=5, write=100, read=400, out=2)]}
    # both segments have a NULL start row or NULL tick TTL -> all unknown
    rows, _ = filter_facts_in_range([fact], {'s1': ck}, provider='claude', **WIN)
    tok = compute_totals(rows, {})['tokens']
    assert tok['input_cache_write'] == 300
    assert tok['cache_write_ttl_unknown'] == 300 and tok['cache_write_5m'] is None
    # with a TTL-carrying tick the second segment becomes known
    ck['sample_ticks'] = [_row('s1', 'sample_tick', T1, fresh=5, write=100, read=400, out=2, t5=20, t1=80)]
    rows, _ = filter_facts_in_range([fact], {'s1': ck}, provider='claude', **WIN)
    tok = compute_totals(rows, {})['tokens']
    # baseline->tick (100 written) stays unknown; tick->completion (200) is known
    assert (tok['cache_write_5m'], tok['cache_write_1h'], tok['cache_write_ttl_unknown']) == (80, 120, 100)
    assert tok['cache_write_5m'] + tok['cache_write_1h'] + tok['cache_write_ttl_unknown'] == tok['input_cache_write']


# ── FIX 4 -- scope + per-model ───────────────────────────────────────────

def test_fix4a_scope_is_honoured_by_totals_and_unknown_model_is_counted():
    opus, ck_o = _session('so', model='claude-opus-5-5',
                          end=_row('so', 'completion', T1, fresh=10, write=0, read=90, out=5))
    son, ck_s = _session('ss', model='claude-sonnet-5-5',
                         end=_row('ss', 'completion', T1, fresh=20, write=0, read=180, out=7))
    unk, ck_u = _session('su', model='',
                         end=_row('su', 'completion', T1, fresh=30, write=0, read=270, out=9))
    facts, cks = [opus, son, unk], {'so': ck_o, 'ss': ck_s, 'su': ck_u}
    kw = dict(provider='claude', **WIN)
    all_rows, _ = filter_facts_in_range(facts, cks, window_scope='all', **kw)
    assert compute_totals(all_rows, {})['tokens']['input_processed_total'] == 600
    rows, inc = filter_facts_in_range(facts, cks, window_scope='opus', **kw)
    assert compute_totals(rows, {})['tokens']['input_processed_total'] == 100
    rows, inc = filter_facts_in_range(facts, cks, window_scope='sonnet', **kw)
    assert compute_totals(rows, {})['tokens']['input_processed_total'] == 200
    out = build_breakdown(provider='claude', window_kind='7d', window_scope='opus', dimension='project',
                          sort_by='input', range_samples=[], calibration_samples=[], session_facts=facts,
                          checkpoints=cks, code_deltas={}, coverage_begins=None, **WIN)
    assert out['totals']['tokens']['input_processed_total'] == 100
    assert out['totals']['model_unknown_session_count'] == 1
    out_all = build_breakdown(provider='claude', window_kind='7d', window_scope='all', dimension='project',
                              sort_by='input', range_samples=[], calibration_samples=[], session_facts=facts,
                              checkpoints=cks, code_deltas={}, coverage_begins=None, **WIN)
    assert out_all['totals']['model_unknown_session_count'] == 0


MIXED_MU = {'claude-opus-5-5': {'input_fresh': 10, 'input_cache_write': 0, 'input_cache_read': 90, 'output_tokens': 5},
            'claude-sonnet-5-5': {'input_fresh': 20, 'input_cache_write': 0, 'input_cache_read': 180, 'output_tokens': 7}}


def _mixed():
    return _session('mx', model='claude-opus-5-5',
                    end=_row('mx', 'completion', T1, fresh=30, write=0, read=270, out=12, mu=MIXED_MU))


def test_fix4b_scope_takes_only_the_matching_models_of_a_mixed_session():
    fact, ck = _mixed()
    rows, _ = filter_facts_in_range([fact], {'mx': ck}, provider='claude', window_scope='opus', **WIN)
    tok = compute_totals(rows, {})['tokens']
    assert (tok['input_processed_total'], tok['output_tokens']) == (100, 5)
    rows, _ = filter_facts_in_range([fact], {'mx': ck}, provider='claude', window_scope='sonnet', **WIN)
    tok = compute_totals(rows, {})['tokens']
    assert (tok['input_processed_total'], tok['output_tokens']) == (200, 7)


def test_fix4b_model_dimension_splits_a_mixed_session_across_its_models():
    fact, ck = _mixed()
    rows, _ = filter_facts_in_range([fact], {'mx': ck}, provider='claude', **WIN)
    rk = compute_rankings(rows, {}, dimension='model')
    by = {r['label']: r for r in rk['rows']}
    assert by['claude-opus-5-5']['input_processed_total'] == 100 and by['claude-opus-5-5']['output_tokens'] == 5
    assert by['claude-sonnet-5-5']['input_processed_total'] == 200 and by['claude-sonnet-5-5']['output_tokens'] == 7
    assert sum(r['input_processed_total'] for r in rk['rows']) == 300      # nothing double counted
    assert rk['mixed_session_count'] == 1


def test_fix4b_session_without_model_usage_keeps_its_label_and_is_counted_as_whole_session():
    fact, ck = _session('old', model='claude-opus-5-5',
                        base=_row('old', 'baseline', T0),
                        end=_row('old', 'completion', T1, fresh=10, write=0, read=90, out=5))
    rows, _ = filter_facts_in_range([fact], {'old': ck}, provider='claude', **WIN)
    rk = compute_rankings(rows, {}, dimension='model')
    assert [r['label'] for r in rk['rows']] == ['claude-opus-5-5']
    assert rk['whole_session_attribution_count'] == 1


def test_fix2_ranking_rows_carry_the_input_categories():
    fact, ck = _session('s1', end=_row('s1', 'completion', T1, fresh=10, write=100, read=900, out=5,
                                       t5=40, t1=60))
    rows, _ = filter_facts_in_range([fact], {'s1': ck}, provider='claude', **WIN)
    r = compute_rankings(rows, {}, dimension='project')['rows'][0]
    assert (r['input_fresh'], r['input_cache_write'], r['input_cache_read']) == (10, 100, 900)
