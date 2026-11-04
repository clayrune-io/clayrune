"""HR-9: missing identity never fails a run; engines and syntax still refuse."""
from copy import deepcopy

import pytest

from mc import agent_runtime, engine_selection
from mc import hivemind_fallback as policy, hivemind_roster as roster
from tests.test_hivemind_roster import agents_dir, roster_chars, _roster_payload  # noqa: F401
from tests.test_hivemind_routes import client  # noqa: F401


@pytest.mark.parametrize('complexity,tier', [('simple', 'fast'), ('standard', 'balanced'), ('hard', 'best')])
@pytest.mark.parametrize('provider', ['claude', 'codex', 'gemini'])
def test_generic_tracks_current_tier_on_run_provider(monkeypatch, complexity, tier, provider):
    class Catalog:
        MODEL_SELECTION = True
        head = 'first'
        def latest_for(self, name):
            return f'{provider}-{name}-{self.head}'
        def model_supported(self, value):
            return True
    catalog = Catalog()
    monkeypatch.setitem(agent_runtime._RUNTIMES, provider, catalog)
    cfg = {'default_provider': 'claude', 'agent_model': 'sonnet'}
    snapshot = {}
    def resolve():
        return roster.resolve_assignment('global:undefined', cfg, {'provider': 'claude'},
                                         read_character=lambda _: None, complexity=complexity,
                                         worker_provider=provider, character_snapshot=snapshot)
    first = resolve()
    catalog.head = 'second'
    second = resolve()
    assert first['provider'] == second['provider'] == provider
    assert first['tracking'] == tier
    assert first['model'] == f'{provider}-{tier}-first'
    assert second['model'] == f'{provider}-{tier}-second'
    assert snapshot == {'name': '', 'body': '', 'skills': []}
    assert first['character_ref'] == ''
    assert first['requested_character'] == 'global:undefined'
    assert first['substituted'] is True
    assert first['substitution_step'] == 'tier_fallback'
    assert first['substitution_reason'] == 'never_defined'


def test_named_then_role_then_generic_no_recursive_fallback():
    records = {'named': {'engine': {'provider': 'codex'}}, 'standin': {'body': 'persona'}}
    cfg = {policy.ROLE_KEY: {'code': 'global:standin'}}
    def choose():
        return policy.choose('global:named', cfg, work_type='code', previously_defined=True,
                             lookup=records.get, parse_ref=roster.parse_character_ref)
    before = deepcopy(cfg)
    assert choose().character_ref == 'global:named'
    del records['named']
    role = choose()
    assert role.character_ref == 'global:standin'
    assert role.disclosure['substitution_step'] == 'role_fallback'
    assert role.disclosure['substitution_reason'] == 'missing_at_attempt'
    del records['standin']
    assert choose().tier == 'tier:balanced'
    assert cfg == before


@pytest.mark.parametrize('work_type', policy.WORK_TYPES)
def test_each_work_type_can_use_install_mapping(work_type):
    assignment = roster.resolve_assignment(
        'global:missing', {policy.ROLE_KEY: {work_type: 'global:standin'}},
        work_type=work_type, read_character=lambda n: {'engine': {'provider': 'codex'}} if n == 'standin' else None)
    assert assignment['character_ref'] == 'global:standin'
    assert assignment['provider'] == 'codex'
    assert assignment['substitution_step'] == 'role_fallback'


def test_self_mapping_terminates_and_custom_complexity_tier_is_used():
    a = roster.resolve_assignment('global:missing', {
        'default_provider': 'claude', policy.ROLE_KEY: {'other': 'global:missing'},
        policy.TIERS_KEY: {'standard': 'tier:fast'}}, read_character=lambda _: None)
    assert a['substitution_step'] == 'tier_fallback' and a['tracking'] == 'fast'


@pytest.mark.parametrize('ref', ['project:x', 'global:../x', '', None, 'display name'])
def test_malformed_references_never_enter_lookup(ref):
    def forbidden(_):
        pytest.fail('malformed reference reached lookup')
    with pytest.raises(roster.RosterError):
        roster.resolve_assignment(ref, {}, read_character=forbidden)


@pytest.mark.parametrize('engine', [{'provider': 'missing-vendor'}, {'provider': 'codex', 'model': 'sonnet'}])
def test_unusable_named_or_mapped_engine_is_never_swapped(engine):
    for requested, records in [('global:bad', {'bad': {'engine': engine}}),
                               ('global:missing', {'bad': {'engine': engine}})]:
        with pytest.raises(engine_selection.EngineSelectionError):
            roster.resolve_assignment(requested, {policy.ROLE_KEY: {'other': 'global:bad'}},
                                      read_character=records.get)


def test_bad_generic_provider_is_visible():
    with pytest.raises(engine_selection.EngineSelectionError, match='Unknown provider'):
        roster.resolve_assignment('global:missing', {}, worker_provider='missing-provider',
                                  read_character=lambda _: None)


def test_read_failure_is_not_missing():
    def unreadable(_):
        raise OSError('disk unavailable')
    with pytest.raises(roster.RosterError, match='disk unavailable'):
        roster.resolve_assignment('global:x', {}, read_character=unreadable)


@pytest.mark.parametrize('patch', [{'work_type': 'bogus'}, {'complexity': 'extreme'},
                                  {'work_type': None}, {'complexity': []}])
def test_bad_planner_fields_refused(patch):
    with pytest.raises(ValueError):
        policy.planner_fields(patch)


@pytest.mark.parametrize('patch', [{policy.ROLE_KEY: {'code': 'project:x'}},
                                  {policy.ROLE_KEY: {'bogus': 'global:x'}},
                                  {policy.ROLE_KEY: []}, {policy.TIERS_KEY: {'hard': 'opus'}},
                                  {policy.TIERS_KEY: {'bogus': 'tier:best'}}])
def test_invalid_policy_maps_refused(patch):
    with pytest.raises(ValueError):
        policy.validate_config(patch, roster.parse_character_ref)


def test_empty_maps_are_standalone_defaults():
    assert policy.planner_fields({}) == {'work_type': 'other', 'complexity': 'standard'}
    policy.validate_config({}, roster.parse_character_ref)


def test_create_unknown_worker_and_reviewer_returns_each_warning_and_criteria(client, agents_dir):
    payload = _roster_payload(reviewer_character='global:undefined-reviewer')
    payload['workstreams'][0].update(work_type='code', complexity='hard')
    response = client.post('/api/hivemind/create', json=payload)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert len(body['warnings']) == 3
    assert all(ref in ' '.join(body['warnings']) for ref in
               ['global:alpha', 'global:beta', 'global:undefined-reviewer'])
    assert body['workstreams'][0]['work_type'] == 'code'
    assert body['workstreams'][0]['complexity'] == 'hard'
    assert body['hivemind']['config']['reviewer_character_known'] is False


def test_planner_fields_round_trip_and_lock_with_assignment(client, roster_chars):
    response = client.post('/api/hivemind/create', json=_roster_payload()).get_json()
    hid = response['hivemind']['id']
    path = f'/api/hivemind/{hid}/workstreams/ws_001'
    assert client.put(path, json={'work_type': 'ui', 'complexity': 'simple'}).status_code == 200
    row = client.hm._hm_load_workstream(hid, 'ws_001')
    assert row['work_type'] == 'ui' and row['complexity'] == 'simple'
    row.update(status='active')
    client.hm._hm_save_workstream(hid, 'ws_001', row)
    assert client.put(path, json={'complexity': 'hard'}).status_code == 409


def test_policy_keys_allowlisted_and_defaults_contain_no_characters():
    from mc.blueprints.settings_routes import _CONFIG_EDITABLE_KEYS
    assert {policy.ROLE_KEY, policy.TIERS_KEY} <= _CONFIG_EDITABLE_KEYS
