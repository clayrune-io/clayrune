"""MC-1152 P1: channel-impact schemas and the conversion-source interface.

Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md sections 4, 5, 7 and 12 row P1.
"""
from __future__ import annotations

import copy
import math

import pytest

from mc import desk_impact_schema as S
from mc.desk_impact_sources import base as B
from mc.desk_impact_sources.registry import AdapterRegistry, REGISTRY, UnknownSource
from tests.desk_impact_fakes import FAKE_SOURCE, FakeSource, link_record, observation, spec

FAKE_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6'


# -- ids and path safety -----------------------------------------------------------

@pytest.mark.parametrize('bad', ['..', 'a/b', 'a\\b', 'C:', '.hidden', '', ' ',
                                 'x\x00', 'x' * 65, '-lead', 'é', None, 7])
def test_malformed_ids_refused(bad):
    with pytest.raises(S.SchemaError):
        S.require_id('project_id', bad)


def test_own_ids_carry_their_kind():
    did = S.new_id('definition')
    assert S.require_own_id('definition', did) == did
    with pytest.raises(S.SchemaError):
        S.require_own_id('link', did)


# -- measurement semantics (spec section 4) -----------------------------------------------

def test_zero_with_complete_coverage_is_a_value():
    snap = S.validate_snapshot_input(observation(value=0))
    assert snap['value'] == 0 and snap['null_reason'] is None


def test_null_requires_a_reason():
    obs = observation(value=None)
    obs['null_reason'] = ''
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)
    assert S.validate_snapshot_input(observation(value=None))['value'] is None


def test_a_value_requires_complete_coverage():
    obs = observation(value=5)
    obs['coverage'] = {'complete': False, 'gaps': ['2026-11-02 missing'],
                       'incomplete_pages': True}
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)
    obs['value'], obs['null_reason'] = None, 'partial pagination'
    assert S.validate_snapshot_input(obs)['value'] is None


def test_complete_coverage_cannot_list_gaps():
    obs = observation()
    obs['coverage'] = {'complete': True, 'gaps': ['x'], 'incomplete_pages': False}
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)


@pytest.mark.parametrize('bad', [-1, -0.5, math.nan, math.inf, True, '5'])
def test_counts_must_be_finite_nonnegative_numbers(bad):
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(observation(value=bad))


def test_a_negative_delta_is_never_stored_as_a_value():
    # "never clamp a negative delta into a successful zero" (spec section 8)
    with pytest.raises(S.SchemaError, match='negative'):
        S.validate_snapshot_input(observation(value=-3))


def test_times_must_be_utc():
    for bad in ('2026-11-04T00:00:00', '2026-11-04T00:00:00-08:00', 'yesterday', 5):
        with pytest.raises(S.SchemaError):
            S.validate_snapshot_input(observation(observed_at=bad))
    S.validate_snapshot_input(observation(observed_at='2026-11-04T00:00:00+00:00'))


def test_window_is_half_open_and_ordered():
    obs = observation()
    obs['window'] = {'start': '2026-11-04T00:00:00Z', 'end': '2026-11-04T00:00:00Z',
                     'granularity': 'day', 'timezone': 'UTC'}
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)


def test_unknown_and_missing_snapshot_fields_refused():
    obs = observation()
    obs['campaign_credit'] = 5
    with pytest.raises(S.SchemaError, match='unknown'):
        S.validate_snapshot_input(obs)
    obs = observation()
    del obs['scope']
    with pytest.raises(S.SchemaError, match='missing'):
        S.validate_snapshot_input(obs)


def test_unsupported_attribution_cannot_carry_dimensions():
    obs = observation()
    obs['attribution'] = {'supported': False, 'dimensions': {'utm_campaign': 'camp-a'}}
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)


def test_definition_id_and_revision_go_together():
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(observation(definition_id=S.new_id('definition')))


def test_snapshot_key_ignores_value_and_time_but_not_window_or_dimensions():
    a = S.validate_snapshot_input(observation(value=1, observed_at='2026-11-04T00:00:00Z'))
    b = S.validate_snapshot_input(observation(value=2, observed_at='2026-11-04T05:00:00Z'))
    assert S.snapshot_key(a) == S.snapshot_key(b)
    assert S.snapshot_content_hash(a) != S.snapshot_content_hash(b)
    c = S.validate_snapshot_input(observation(as_of='2026-11-05T00:00:00Z'))
    assert S.snapshot_key(c) != S.snapshot_key(a)
    d = observation()
    d['attribution'] = {'supported': True, 'dimensions': {'utm_content': 'x'}}
    assert S.snapshot_key(S.validate_snapshot_input(d)) != S.snapshot_key(a)


# -- no secret persistence ----------------------------------------------------------------

def test_credentials_are_refused_in_every_free_text_field():
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(label=FAKE_TOKEN))
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(target={'repo': FAKE_TOKEN}))
    obs = observation(value=None)
    obs['null_reason'] = f'auth failed for {FAKE_TOKEN}'
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)
    obs = observation()
    obs['target'] = {'repo': 'a', 'token': FAKE_TOKEN}
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_input(obs)


def test_link_urls_with_secret_parameters_are_refused():
    rec = link_record()
    rec['original_destination'] = 'https://example.com/?access_token=abc'
    with pytest.raises(S.SchemaError):
        S.validate_link_record(rec)


def test_refusal_messages_never_echo_the_value():
    with pytest.raises(S.SchemaError) as e:
        S.validate_definition_spec(spec(label=FAKE_TOKEN))
    assert FAKE_TOKEN not in str(e.value)


# -- definition spec -----------------------------------------------------------------------

def test_definition_selects_exactly_one_metric():
    with pytest.raises(S.SchemaError, match='unknown field'):
        S.validate_definition_spec(spec(metrics=['stars', 'downloads']))
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(metric=['stars', 'downloads']))
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(metric='stars+downloads'))


def test_target_is_bounded():
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(target={}))
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(target={'repo': {'nested': 1}}))
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(target={'repo': 'x' * 201}))
    with pytest.raises(S.SchemaError):
        S.validate_definition_spec(spec(label='x' * 201))   # refused, not truncated


# -- link manifest ---------------------------------------------------------------------------

def test_link_record_binds_campaign_and_link_id_into_the_utm():
    S.validate_link_record(link_record())
    rec = link_record()
    rec['utm']['utm_campaign'] = 'camp-b'
    with pytest.raises(S.SchemaError, match='utm_campaign'):
        S.validate_link_record(rec)
    rec = link_record()
    rec['utm']['utm_content'] = 'imlk-ffffffffffffffff'
    with pytest.raises(S.SchemaError, match='utm_content'):
        S.validate_link_record(rec)


def test_link_final_url_must_carry_exactly_the_stored_map():
    rec = link_record()
    rec['final_url'] = rec['final_url'].replace('utm_source=x', 'utm_source=y')
    with pytest.raises(S.SchemaError, match='final_url'):
        S.validate_link_record(rec)
    rec = link_record()
    rec['final_url'] = rec['final_url'].replace('#top', '&utm_source=z#top')   # duplicated tag
    with pytest.raises(S.SchemaError, match='final_url'):
        S.validate_link_record(rec)


def test_link_medium_is_social_email_or_agent():
    for m in ('social', 'email', 'agent'):
        S.validate_link_record(link_record(medium=m))
    with pytest.raises(S.SchemaError):
        S.validate_link_record(link_record(medium='cpc'))


# -- conversion-source interface ----------------------------------------------------------------

def test_adapter_is_abstract_and_needs_all_four_operations():
    class Partial(B.SourceAdapter):
        source_id = 'partial'
        def capabilities(self, connection): ...
    with pytest.raises(TypeError):
        Partial()                                           # type: ignore[abstract]
    assert {'capabilities', 'validate_definition', 'collect', 'normalize'} <= set(
        B.SourceAdapter.__abstractmethods__)


def test_capabilities_are_metadata_only():
    caps = FakeSource().capabilities({'project_id': 'proj1'})
    assert caps.source_id == FAKE_SOURCE
    assert not (caps.attributes_campaign or caps.attributes_channel or caps.attributes_post)
    assert caps.metric_named('stars') and caps.metric_named('nope') is None


def test_check_against_capabilities_accepts_and_refuses():
    caps = FakeSource().capabilities({})
    clean = S.validate_definition_spec(spec())
    assert B.check_against_capabilities(clean, caps).metric == 'stars'
    for bad, why in (
            (spec(metric='forks'), 'metric'),
            (spec(unit='downloads'), 'unit'),
            (spec(target={'repo': 'a', 'extra': 'x'}), 'unsupported field'),
            (spec(metric='downloads', unit='downloads', target={'asset_id': 3}), 'target.repo'),
            (spec(metric='downloads', unit='downloads',
                  target={'repo': 'a', 'asset_id': 'not-int'}), 'asset_id'),
            (spec(source_id='other_source'), 'source_id')):
        with pytest.raises(B.DefinitionRefused, match=why):
            B.check_against_capabilities(S.validate_definition_spec(bad), caps)
    B.check_against_capabilities(S.validate_definition_spec(
        spec(metric='downloads', unit='downloads', target={'repo': 'a'})), caps)


def test_snapshot_input_takes_scope_and_counter_kind_from_the_capability():
    cap = FakeSource().capabilities({}).metric_named('stars')
    assert cap is not None
    req = B.CollectRequest(project_id='proj1', source_id=FAKE_SOURCE,
                           connection_ref='conn-1', target={'repo': 'example/repo'},
                           window=B.Window(None, '2026-11-04T00:00:00Z', 'instant'))
    snap = B.snapshot_input(req, cap, adapter=FAKE_SOURCE, value=3, null_reason=None,
                            observed_at='2026-11-04T00:00:00Z',
                            effective_at='2026-11-04T00:00:00Z',
                            coverage=B.CoverageReport(True), request_count=1)
    assert snap['scope'] == 'repository' and snap['counter_kind'] == 'cumulative'
    assert snap['attribution'] == {'supported': False, 'dimensions': {}}
    with pytest.raises(S.SchemaError):                      # partial -> number refused
        B.snapshot_input(req, cap, adapter=FAKE_SOURCE, value=3, null_reason=None,
                         observed_at='2026-11-04T00:00:00Z',
                         effective_at='2026-11-04T00:00:00Z',
                         coverage=B.CoverageReport(False, ('missing day',)),
                         request_count=1)


def test_transport_protocol_has_no_write_verb():
    assert [n for n in dir(B.Transport) if not n.startswith('_')] == ['get']


def test_source_error_is_not_a_zero_conversion():
    res = B.CollectResult(observations=(), coverage=B.CoverageReport(False),
                          errors=(B.SourceError('rate_limited', 'slow down',
                                                '2026-11-04T01:00:00Z'),),
                          request_count=1)
    assert not res.ok and res.observations == ()


# -- registry ------------------------------------------------------------------------------------

def test_registry_contains_dark_github_metadata_only():
    assert REGISTRY.source_ids() == ('github',)
    assert not REGISTRY.get('github').capabilities({}).attributes_channel
    with pytest.raises(UnknownSource):
        REGISTRY.get('not_registered')


def test_registry_registers_once():
    reg = AdapterRegistry()
    reg.register(FakeSource())
    assert reg.get(FAKE_SOURCE).source_id == FAKE_SOURCE
    with pytest.raises(ValueError):
        reg.register(FakeSource())
    nameless = FakeSource()
    nameless.source_id = ''
    with pytest.raises(ValueError):
        AdapterRegistry().register(nameless)


def test_validate_snapshot_record_detects_a_tampered_key():
    snap = S.validate_snapshot_input(observation())
    row = {'schema_version': 1, 'snapshot_id': S.new_id('snapshot'),
           'key': S.snapshot_key(snap), 'recorded_at': '2026-11-04T00:00:00Z', **snap}
    S.validate_snapshot_record(row)
    bad = copy.deepcopy(row)
    bad['value'] = 999            # key still matches: value is not part of identity
    S.validate_snapshot_record(bad)
    bad = copy.deepcopy(row)
    bad['metric'] = 'downloads'   # identity field changed, key now stale
    with pytest.raises(S.SchemaError):
        S.validate_snapshot_record(bad)
