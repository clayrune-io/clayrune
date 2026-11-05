"""MC-1152 P1: channel-impact store.

Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md section 5 and section 12 row P1. Each
test names the P1 requirement it pins.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import threading
from pathlib import Path

import pytest

from mc import atomic_json
from mc import desk_impact_schema as S
from mc import desk_impact_store as store_mod
from mc.desk_impact_sources import base as B
from mc.desk_impact_sources.registry import AdapterRegistry
from mc.desk_impact_store import (
    ImpactStore, LinkConflict, NotFound, OwnershipError, RevisionConflict,
    StoreCorrupt,
)
from tests.desk_impact_fakes import (
    CAMPAIGNS, CONNECTIONS, FAKE_SOURCE, FakeSource, link_record, observation, spec,
)

ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6'


@pytest.fixture
def campaigns():
    return copy.deepcopy(CAMPAIGNS)


@pytest.fixture
def logs(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(store_mod, '_log',
                        lambda *a, **k: lines.append(' '.join(str(x) for x in a)))
    return lines


@pytest.fixture
def clock():
    state = {'n': 0}

    def tick() -> str:
        state['n'] += 1
        return f'2026-11-04T12:00:{state["n"] % 60:02d}.{state["n"]:06d}Z'
    return tick


@pytest.fixture
def adapter():
    return FakeSource()


@pytest.fixture
def st(tmp_path, campaigns, logs, adapter, clock):
    reg = AdapterRegistry()
    reg.register(adapter)
    return ImpactStore(tmp_path / 'data' / 'desk_impact',
                       campaign_resolver=lambda cid: copy.deepcopy(campaigns.get(cid)),
                       connection_resolver=lambda c: copy.deepcopy(CONNECTIONS.get(c)),
                       registry=reg, data_dir=tmp_path / 'data' / 'projects', clock=clock)


def _dir(tmp_path):
    return tmp_path / 'data' / 'desk_impact' / 'proj1'


# -- identity, ownership, traversal --------------------------------------------------------

@pytest.mark.parametrize('bad', ['..', '../proj1', 'a/b', 'a\\b', 'C:', 'C:\\x',
                                 '.hidden', '', ' ', 'p\x00', 'x' * 65, '-lead', None, 7])
def test_traversal_and_malformed_ids_refused_before_any_path(st, tmp_path, bad):
    with pytest.raises(S.SchemaError):
        st.get_definitions(bad, 'camp-a')
    with pytest.raises(S.SchemaError):
        st.get_definitions('proj1', bad)
    with pytest.raises(S.SchemaError):
        st.read_snapshots(bad)
    with pytest.raises(S.SchemaError):
        st.append_snapshot(bad, observation())
    with pytest.raises(S.SchemaError):
        st.record_attempt(bad, FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    assert not (tmp_path / 'data' / 'desk_impact').exists()


def test_wrong_project_campaign_reads_like_an_unknown_one(st):
    with pytest.raises(OwnershipError) as wrong:
        st.get_definitions('proj1', 'camp-other')
    with pytest.raises(NotFound) as unknown:
        st.get_definitions('proj1', 'camp-missing')
    assert str(wrong.value) == str(unknown.value)
    with pytest.raises(NotFound):
        st.get_definitions('proj1', 'camp-orphan')
    with pytest.raises(NotFound):
        st.set_definition('proj1', 'camp-other', spec(), expected_revision=0)
    with pytest.raises(NotFound):
        st.add_link('proj1', link_record(campaign_id='camp-other'))


def test_ownership_is_rechecked_on_every_read(st, campaigns):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    campaigns['camp-a']['project_id'] = 'proj2'              # campaign moved away
    for call in (lambda: st.get_definitions('proj1', 'camp-a'),
                 lambda: st.list_links('proj1', 'camp-a'),
                 lambda: st.get_campaign('proj1', 'camp-a')):
        with pytest.raises(OwnershipError):
            call()


def test_wrong_project_connection_refused_everywhere(st):
    with pytest.raises(OwnershipError):
        st.set_definition('proj1', 'camp-a', spec(connection_id='conn-foreign'),
                          expected_revision=0)
    with pytest.raises(OwnershipError):
        st.append_snapshot('proj1', observation(connection_id='conn-foreign'))
    with pytest.raises(OwnershipError):
        st.record_attempt('proj1', FAKE_SOURCE, 'conn-foreign', {'repo': 'a'}, ok=True)
    with pytest.raises(NotFound):
        st.set_definition('proj1', 'camp-a', spec(connection_id='conn-missing'),
                          expected_revision=0)


def test_default_connection_resolver_refuses_everything(tmp_path, campaigns, adapter):
    reg = AdapterRegistry()
    reg.register(adapter)
    bare = ImpactStore(tmp_path / 'data' / 'desk_impact', registry=reg,
                       campaign_resolver=lambda cid: campaigns.get(cid))
    with pytest.raises(NotFound):
        bare.set_definition('proj1', 'camp-a', spec(), expected_revision=0)


# -- single active definition, revisions, series isolation -----------------------------------

def test_no_definition_is_valid(st):
    out = st.get_definitions('proj1', 'camp-a')
    assert out == {'definition_id': None, 'head_revision': 0, 'active': None, 'revisions': []}


def test_first_definition_is_revision_one_and_active(st):
    rec = st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    assert rec['revision'] == 1 and rec['retired_at'] is None
    assert rec['effective_at'] == rec['created_at']
    got = st.get_definitions('proj1', 'camp-a')
    assert got['active'] == rec and got['head_revision'] == 1


def test_exactly_one_active_revision_and_history_is_kept(st):
    r1 = st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    r2 = st.set_definition('proj1', 'camp-a',
                           spec(metric='downloads', unit='downloads'), expected_revision=1)
    got = st.get_definitions('proj1', 'camp-a')
    assert [r['revision'] for r in got['revisions']] == [1, 2]
    assert got['active']['revision'] == 2 and r2['id'] == r1['id']      # one definition id
    assert got['revisions'][0]['retired_at'] is not None
    assert [r['retired_at'] is None for r in got['revisions']] == [False, True]
    assert got['revisions'][0]['metric'] == 'stars'          # prior revision inspectable


def test_identical_save_is_idempotent_and_starts_no_new_series(st):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    again = st.set_definition('proj1', 'camp-a', spec(), expected_revision=1)
    assert again['revision'] == 1
    assert st.get_definitions('proj1', 'camp-a')['head_revision'] == 1


def test_stale_revision_is_a_conflict_not_a_lost_write(st):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    with pytest.raises(RevisionConflict) as e:
        st.set_definition('proj1', 'camp-a', spec(metric='downloads', unit='downloads'),
                          expected_revision=0)
    assert e.value.current == 1
    assert st.get_definitions('proj1', 'camp-a')['active']['metric'] == 'stars'
    for bad in (None, '1', True, 1.0):
        with pytest.raises(S.SchemaError):
            st.set_definition('proj1', 'camp-a', spec(), expected_revision=bad)


def test_retire_leaves_no_active_definition_but_keeps_history(st):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    with pytest.raises(RevisionConflict):
        st.retire_definition('proj1', 'camp-a', expected_revision=0)
    st.retire_definition('proj1', 'camp-a', expected_revision=1)
    got = st.get_definitions('proj1', 'camp-a')
    assert got['active'] is None and len(got['revisions']) == 1
    with pytest.raises(NotFound):
        st.retire_definition('proj1', 'camp-a', expected_revision=1)
    again = st.set_definition('proj1', 'camp-a', spec(), expected_revision=1)
    assert again['revision'] == 2                         # numbering continues


def test_two_campaigns_have_independent_definitions(st):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    st.set_definition('proj1', 'camp-b', spec(label='B stars'), expected_revision=0)
    assert st.get_definitions('proj1', 'camp-a')['active']['label'] == 'GitHub stars'
    assert st.get_definitions('proj1', 'camp-b')['active']['label'] == 'B stars'
    assert (st.get_definitions('proj1', 'camp-a')['definition_id']
            != st.get_definitions('proj1', 'camp-b')['definition_id'])


def test_revision_series_are_isolated(st):
    r1 = st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    r2 = st.set_definition('proj1', 'camp-a',
                           spec(metric='downloads', unit='downloads'), expected_revision=1)
    st.append_snapshot('proj1', observation(value=10, definition_id=r1['id'],
                                            definition_revision=1))
    st.append_snapshot('proj1', observation(value=99, metric='downloads', unit='downloads',
                                            definition_id=r2['id'], definition_revision=2))
    s1 = st.effective_snapshots('proj1', definition_id=r1['id'], definition_revision=1)
    s2 = st.effective_snapshots('proj1', definition_id=r1['id'], definition_revision=2)
    assert [r['value'] for r in s1] == [10] and [r['value'] for r in s2] == [99]
    assert len(st.effective_snapshots('proj1', definition_id=r1['id'])) == 2   # not summed


# -- definition validation: adapter + store -----------------------------------------------------

def test_unsupported_metric_target_unit_and_source_refused(st, tmp_path):
    for bad in (spec(metric='forks', unit='forks'), spec(unit='downloads'),
                spec(target={'repo': 'a', 'x': 1}),
                spec(metric='stars+downloads'), spec(source_id='nope_source')):
        with pytest.raises((B.DefinitionRefused, S.SchemaError)):
            st.set_definition('proj1', 'camp-a', bad, expected_revision=0)
    assert st.get_definitions('proj1', 'camp-a')['active'] is None
    assert not (tmp_path / 'data' / 'desk_impact').exists()          # nothing was written


def test_unregistered_source_cannot_be_saved(tmp_path, campaigns):
    empty = ImpactStore(tmp_path / 'data' / 'desk_impact', registry=AdapterRegistry(),
                        campaign_resolver=lambda c: campaigns.get(c),
                        connection_resolver=lambda c: CONNECTIONS.get(c))
    with pytest.raises(B.DefinitionRefused, match='no adapter'):
        empty.set_definition('proj1', 'camp-a', spec(), expected_revision=0)


def test_store_enforces_capabilities_even_if_the_adapter_does_not(tmp_path, campaigns):
    class Lax(FakeSource):
        def validate_definition(self, definition, connection):
            return None                                       # accepts anything
    reg = AdapterRegistry()
    reg.register(Lax())
    lax = ImpactStore(tmp_path / 'data' / 'desk_impact', registry=reg,
                      campaign_resolver=lambda c: campaigns.get(c),
                      connection_resolver=lambda c: CONNECTIONS.get(c))
    with pytest.raises(B.DefinitionRefused):
        lax.set_definition('proj1', 'camp-a', spec(metric='forks', unit='forks'),
                           expected_revision=0)


def test_adapter_receives_the_validated_definition(st, adapter):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    assert adapter.validated and adapter.validated[0]['metric'] == 'stars'


# -- no secret persistence ---------------------------------------------------------------------------

def test_credentials_never_reach_disk(st, tmp_path):
    for call in (lambda: st.set_definition('proj1', 'camp-a', spec(label=FAKE_TOKEN),
                                           expected_revision=0),
                 lambda: st.set_definition('proj1', 'camp-a',
                                           spec(target={'repo': FAKE_TOKEN}),
                                           expected_revision=0),
                 lambda: st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'},
                                           ok=False, error_code='auth',
                                           error_detail=f'bad {FAKE_TOKEN}')):
        with pytest.raises(S.SchemaError):
            call()
    for f in (tmp_path / 'data').rglob('*'):
        if f.is_file():
            assert FAKE_TOKEN not in f.read_text(encoding='utf-8', errors='ignore')


# -- manual goal compatibility is exercised in test_desk_impact_binding.py ----------------------------


# -- link manifest ------------------------------------------------------------------------------------

def test_links_round_trip_and_are_frozen_once_issued(st):
    rec = link_record()
    row, created = st.add_link('proj1', rec)
    assert created and row == rec
    again, created2 = st.add_link('proj1', rec)               # a retry
    assert not created2 and again == rec
    changed = link_record()
    changed['final_url'] = changed['final_url'].replace('landing', 'other')
    changed['original_destination'] = changed['original_destination'].replace('landing', 'other')
    with pytest.raises(LinkConflict):
        st.add_link('proj1', changed)
    assert st.get_link('proj1', rec['link_id']) == rec


def test_multi_channel_post_has_separate_rows_per_destination_version(st):
    a = link_record(link_id='imlk-aaaaaaaaaaaaaaaa', account_id='acc-1', version_id='ver-1')
    b = link_record(link_id='imlk-bbbbbbbbbbbbbbbb', account_id='acc-2', version_id='ver-2',
                    source='linkedin')
    st.add_link('proj1', a)
    st.add_link('proj1', b)
    rows = st.list_links('proj1', 'camp-a')
    assert {r['link_id'] for r in rows} == {a['link_id'], b['link_id']}
    assert a['utm']['utm_content'] != b['utm']['utm_content']


def test_link_lookup_is_per_project(st, tmp_path):
    st.add_link('proj1', link_record())
    with pytest.raises(NotFound):
        st.get_link('proj2', link_record()['link_id'])
    with pytest.raises(S.SchemaError):
        st.get_link('proj1', 'ver-1')                         # not a link id


def test_invalid_link_saves_nothing(st, tmp_path):
    rec = link_record()
    rec['utm']['utm_campaign'] = 'camp-b'
    with pytest.raises(S.SchemaError):
        st.add_link('proj1', rec)
    assert not (tmp_path / 'data' / 'desk_impact').exists()


# -- snapshots: retry idempotency, correction, history ---------------------------------------------------

def test_retry_appends_nothing(st, tmp_path):
    row1, c1 = st.append_snapshot('proj1', observation(value=10))
    row2, c2 = st.append_snapshot('proj1', observation(value=10))
    assert c1 and not c2 and row2['snapshot_id'] == row1['snapshot_id']
    assert len(st.read_snapshots('proj1').records) == 1


def test_correction_replaces_the_effective_value_and_keeps_history(st):
    first, _ = st.append_snapshot('proj1', observation(value=10,
                                                       observed_at='2026-11-04T01:00:00Z'))
    second, created = st.append_snapshot('proj1', observation(value=12,
                                                              observed_at='2026-11-04T02:00:00Z'))
    assert created and second['key'] == first['key']
    eff = st.effective_snapshots('proj1')
    assert [r['value'] for r in eff] == [12]
    assert [r['value'] for r in st.snapshot_history('proj1', first['key'])] == [10, 12]


def test_an_older_observation_arriving_late_does_not_win(st):
    st.append_snapshot('proj1', observation(value=12, observed_at='2026-11-04T02:00:00Z'))
    st.append_snapshot('proj1', observation(value=10, observed_at='2026-11-04T01:00:00Z'))
    assert [r['value'] for r in st.effective_snapshots('proj1')] == [12]


def test_samples_at_different_instants_are_separate_keys(st):
    st.append_snapshot('proj1', observation(value=10, as_of='2026-11-04T00:00:00Z'))
    st.append_snapshot('proj1', observation(value=14, as_of='2026-11-05T00:00:00Z'))
    assert [r['value'] for r in st.effective_snapshots('proj1')] == [10, 14]


def test_zero_and_null_are_distinct_stored_states(st):
    st.append_snapshot('proj1', observation(value=0, as_of='2026-11-04T00:00:00Z'))
    st.append_snapshot('proj1', observation(value=None, as_of='2026-11-05T00:00:00Z'))
    eff = st.effective_snapshots('proj1')
    assert eff[0]['value'] == 0 and eff[0]['null_reason'] is None
    assert eff[1]['value'] is None and eff[1]['null_reason']


def test_snapshot_must_match_the_definition_revision_it_claims(st):
    r1 = st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    ok = observation(definition_id=r1['id'], definition_revision=1)
    st.append_snapshot('proj1', ok)
    with pytest.raises(S.SchemaError, match='target'):
        st.append_snapshot('proj1', observation(
            definition_id=r1['id'], definition_revision=1, target={'repo': 'someone/else'}))
    with pytest.raises(S.SchemaError, match='metric'):
        st.append_snapshot('proj1', observation(
            definition_id=r1['id'], definition_revision=1, metric='downloads',
            unit='stars'))
    with pytest.raises(NotFound):
        st.append_snapshot('proj1', observation(definition_id=r1['id'],
                                                definition_revision=2))
    with pytest.raises(NotFound):                          # another project's definition
        st.append_snapshot('proj1', observation(
            definition_id='imdf-ffffffffffffffff', definition_revision=1))


def test_oversized_snapshot_is_refused_whole(st, tmp_path):
    obs = observation()
    obs['coverage'] = {'complete': False, 'incomplete_pages': True,
                       'gaps': ['g' * 190] * 400}
    obs['value'], obs['null_reason'] = None, 'partial'
    with pytest.raises(S.SchemaError, match='limit'):
        st.append_snapshot('proj1', obs)
    assert st.read_snapshots('proj1').records == ()


def test_no_retention_cap_on_snapshots(st):
    for i in range(60):
        st.append_snapshot('proj1', observation(value=i, as_of=f'2026-11-{(i % 28) + 1:02d}T{i // 28:02d}:00:00Z'))
    assert len(st.read_snapshots('proj1').records) == 60


# -- crash recovery: truncated tail, atomic saves ----------------------------------------------------------

def _snap_path(tmp_path):
    return _dir(tmp_path) / 'snapshots.jsonl'


def test_truncated_final_line_is_reported_and_excluded(st, tmp_path, logs):
    st.append_snapshot('proj1', observation(value=10))
    path = _snap_path(tmp_path)
    with open(path, 'ab') as f:
        f.write(b'{"schema_version":1,"snapshot_id":"imsn-abc')    # crash mid-append
    read = st.read_snapshots('proj1')
    assert read.truncated_tail is True and len(read.records) == 1
    assert any('truncated final snapshot line' in l for l in logs)


def test_append_after_a_crash_does_not_concatenate_onto_the_partial_line(st, tmp_path):
    st.append_snapshot('proj1', observation(value=10, as_of='2026-11-04T00:00:00Z'))
    path = _snap_path(tmp_path)
    partial = b'{"schema_version":1,"snapshot_id":"imsn-abc'
    with open(path, 'ab') as f:
        f.write(partial)
    st.append_snapshot('proj1', observation(value=11, as_of='2026-11-05T00:00:00Z'))
    read = st.read_snapshots('proj1')
    assert read.truncated_tail is False
    assert [r['value'] for r in read.records] == [10, 11]
    aside = list(_dir(tmp_path).glob('snapshots.jsonl.truncated-*.part'))
    assert len(aside) == 1 and aside[0].read_bytes() == partial       # preserved


def test_a_complete_final_row_missing_only_its_newline_is_kept(st, tmp_path):
    st.append_snapshot('proj1', observation(value=10, as_of='2026-11-04T00:00:00Z'))
    path = _snap_path(tmp_path)
    path.write_bytes(path.read_bytes().rstrip(b'\n'))
    assert [r['value'] for r in st.read_snapshots('proj1').records] == [10]
    st.append_snapshot('proj1', observation(value=11, as_of='2026-11-05T00:00:00Z'))
    assert [r['value'] for r in st.read_snapshots('proj1').records] == [10, 11]


def test_corruption_inside_the_file_is_not_swallowed(st, tmp_path, logs):
    st.append_snapshot('proj1', observation(value=10, as_of='2026-11-04T00:00:00Z'))
    st.append_snapshot('proj1', observation(value=11, as_of='2026-11-05T00:00:00Z'))
    path = _snap_path(tmp_path)
    lines = path.read_bytes().split(b'\n')
    lines[0] = b'{not json'
    path.write_bytes(b'\n'.join(lines))
    with pytest.raises(StoreCorrupt):
        st.read_snapshots('proj1')
    with pytest.raises(StoreCorrupt):
        st.append_snapshot('proj1', observation(value=12, as_of='2026-11-06T00:00:00Z'))
    assert any('corrupt snapshot row' in l for l in logs)


def test_a_row_with_a_tampered_key_is_corruption(st, tmp_path):
    st.append_snapshot('proj1', observation(value=10))
    path = _snap_path(tmp_path)
    row = json.loads(path.read_bytes().split(b'\n')[0])
    row['metric'] = 'downloads'
    path.write_bytes(json.dumps(row).encode() + b'\n')
    with pytest.raises(StoreCorrupt):
        st.read_snapshots('proj1')


def test_definitions_saved_via_atomic_writer_and_corrupt_file_is_left_alone(st, tmp_path, logs):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    path = _dir(tmp_path) / 'definitions.json'
    assert not list(path.parent.glob('.definitions.json.*.tmp*'))      # no stranded temp
    path.write_text('{"half": ', encoding='utf-8')                     # simulated torn file
    with pytest.raises(StoreCorrupt):
        st.get_definitions('proj1', 'camp-a')
    with pytest.raises(StoreCorrupt):
        st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    assert path.read_text(encoding='utf-8') == '{"half": '             # never replaced
    assert any('corrupt record' in l for l in logs)


def test_schema_invalid_record_is_corrupt_not_recreated(st, tmp_path):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    path = _dir(tmp_path) / 'definitions.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['campaigns']['camp-a']['revisions'][0]['revision'] = 5
    path.write_text(json.dumps(doc), encoding='utf-8')
    before = path.read_text(encoding='utf-8')
    with pytest.raises(StoreCorrupt):
        st.get_definitions('proj1', 'camp-a')
    assert path.read_text(encoding='utf-8') == before


def test_failed_write_keeps_the_previous_definitions(st, tmp_path, monkeypatch):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    path = _dir(tmp_path) / 'definitions.json'
    before = path.read_text(encoding='utf-8')

    def boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(store_mod, 'write_json_atomic', boom)
    with pytest.raises(store_mod.StoreIOError):
        st.set_definition('proj1', 'camp-a', spec(metric='downloads', unit='downloads'),
                          expected_revision=1)
    assert path.read_text(encoding='utf-8') == before


def test_writes_go_through_the_atomic_writer(st, monkeypatch):
    calls = []
    real = atomic_json.write_json_atomic

    def spy(path, obj, *a, **k):
        calls.append(Path(path).name)
        return real(path, obj, *a, **k)
    monkeypatch.setattr(store_mod, 'write_json_atomic', spy)
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    st.add_link('proj1', link_record())
    st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    assert calls == ['definitions.json', 'links.json', 'coverage.json']


def test_concurrent_snapshot_appends_lose_nothing(st):
    errors = []

    def work(i):
        try:
            st.append_snapshot('proj1', observation(
                value=i, as_of=f'2026-11-{i + 1:02d}T00:00:00Z'))
        except Exception as e:                                # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert sorted(r['value'] for r in st.read_snapshots('proj1').records) == list(range(12))


# -- coverage / read-attempt status --------------------------------------------------------------------------

def test_failure_never_overwrites_the_last_good_observation_or_its_time(st):
    st.append_snapshot('proj1', observation(value=10))
    ok = st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    bad = st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=False,
                            error_code='rate_limited', error_detail='slow down',
                            retry_after='2026-11-04T13:00:00Z')
    assert bad['status'] == 'error' and bad['error_code'] == 'rate_limited'
    assert bad['last_success_at'] == ok['last_success_at']              # not relabelled fresh
    assert bad['last_attempt_at'] > ok['last_attempt_at']
    assert [r['value'] for r in st.effective_snapshots('proj1')] == [10]  # snapshot untouched
    healed = st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    assert healed['status'] == 'ok' and healed['error_code'] is None
    assert healed['last_success_at'] == healed['last_attempt_at']


def test_a_failure_with_no_prior_success_has_no_success_time(st):
    bad = st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=False,
                            error_code='permission', error_detail='403')
    assert bad['last_success_at'] is None


def test_failure_needs_a_known_error_code(st):
    with pytest.raises(S.SchemaError):
        st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=False,
                          error_code='whoops')
    with pytest.raises(S.SchemaError):
        st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=False)


def test_gaps_accumulate_and_are_never_dropped(st):
    g1 = {'start': '2026-11-01T00:00:00Z', 'end': '2026-11-03T00:00:00Z',
          'reason': 'machine was off'}
    g2 = {'start': '2026-11-05T00:00:00Z', 'end': '2026-11-06T00:00:00Z',
          'reason': 'api window'}
    st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True, gaps=[g1])
    st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True, gaps=[g1, g2],
                      incomplete_pages=True)
    e = st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    assert e['gaps'] == [g1, g2]
    assert st.get_coverage('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}) == e


def test_coverage_is_keyed_by_source_connection_and_target(st):
    st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'b'}, ok=False,
                      error_code='not_found')
    assert st.get_coverage('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'})['status'] == 'ok'
    assert st.get_coverage('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'b'})['status'] == 'error'
    assert st.get_coverage('proj1', FAKE_SOURCE, 'conn-2', {'repo': 'a'}) is None


# -- project discovery unaffected; runtime dir ignored and unbundled --------------------------------------------

def test_store_root_may_not_overlap_data_dir(tmp_path):
    data_dir = tmp_path / 'data' / 'projects'
    for bad in (data_dir, data_dir / 'desk_impact', tmp_path / 'data'):
        with pytest.raises(ValueError):
            ImpactStore(bad, campaign_resolver=lambda c: None, data_dir=data_dir)
    assert store_mod.default_root(tmp_path) == tmp_path / 'data' / 'desk_impact'
    assert not store_mod.default_root(tmp_path).is_relative_to(data_dir)


def test_project_discovery_unaffected(st, tmp_path, monkeypatch):
    from mc.blueprints import project_routes
    data_dir = tmp_path / 'data' / 'projects'
    data_dir.mkdir(parents=True)
    (data_dir / 'proj1.json').write_text(json.dumps({'id': 'proj1', 'name': 'P1'}),
                                         encoding='utf-8')
    monkeypatch.setattr(project_routes, 'DATA_DIR', data_dir)
    before = [p['id'] for p in project_routes.load_projects()]
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    st.add_link('proj1', link_record())
    st.append_snapshot('proj1', observation())
    st.record_attempt('proj1', FAKE_SOURCE, 'conn-1', {'repo': 'a'}, ok=True)
    after = [p['id'] for p in project_routes.load_projects()]
    assert before == after == ['proj1']
    assert sorted(p.name for p in data_dir.iterdir()) == ['proj1.json']


def test_runtime_dir_is_gitignored():
    for probe in ('data/desk_impact/proj1/definitions.json',
                  'data/desk_impact/proj1/snapshots.jsonl'):
        out = subprocess.run(['git', 'check-ignore', '-q', probe], cwd=ROOT,
                             capture_output=True, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, f'{probe} must be gitignored'


def test_runtime_dir_absent_from_build_bundle():
    spec_text = (ROOT / 'installer' / 'build-macos.spec').read_text(encoding='utf-8')
    assert 'desk_impact' not in spec_text
    assert not re.search(r"R\(\s*'data'\s*\)\s*,", spec_text)


def test_p1_is_dark_nothing_imports_the_impact_modules():
    """No route, UI, scheduler or server wiring imports them, and no module
    under mc/ or static/ opens a socket for this feature."""
    names = ('desk_impact_store', 'desk_impact_schema', 'desk_impact_binding',
             'desk_impact_sources')
    hits = []
    for base in ('server.py', 'mc', 'static'):
        p = ROOT / base
        files = [p] if p.is_file() else [f for f in p.rglob('*')
                                         if f.suffix in ('.py', '.js', '.html')]
        for f in files:
            if f.name.startswith('desk_impact') or 'desk_impact_sources' in f.parts:
                continue
            text = f.read_text(encoding='utf-8', errors='ignore')
            if any(n in text for n in names):
                hits.append(str(f.relative_to(ROOT)))
    assert hits == []


def test_impact_modules_make_no_network_calls():
    banned = re.compile(r'\b(requests|urllib\.request|http\.client|httpx|socket|'
                        r'urlopen|aiohttp)\b')
    files = [ROOT / 'mc' / n for n in ('desk_impact_schema.py', 'desk_impact_store.py',
                                       'desk_impact_binding.py')]
    files += sorted((ROOT / 'mc' / 'desk_impact_sources').glob('*.py'))
    offenders = [f.name for f in files
                 if banned.search(re.sub(r'(?m)^\s*#.*$|"""[\s\S]*?"""', '',
                                         f.read_text(encoding='utf-8')))]
    assert offenders == []


def test_store_has_no_delete_or_purge_method():
    names = [n for n in dir(ImpactStore) if not n.startswith('_')]
    assert not [n for n in names if re.search(r'delete|purge|prune|truncate|drop', n)]
