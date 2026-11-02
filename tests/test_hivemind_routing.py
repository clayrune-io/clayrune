"""HR-6b: explicit decomposition, pure routing and existing HR-1 compatibility.

All roster records are in-memory fixtures; request compatibility uses the
existing isolated blueprint fixture with fake threads/runtimes, never :5199.
"""
from copy import deepcopy
import json
import subprocess

import pytest

from mc import hivemind_roster as roster
from mc.hivemind_routing import plan_routing
from tests.test_hivemind_routes import client  # noqa: F401


@pytest.fixture
def records():
    # Real global type identifiers; no operator-specific engine settings.
    return {
        'wren': {'engine': {'provider': 'codex'}},
        'builder': {'engine': {'provider': 'claude'}},
        'code-reviewer': {'engine': {'provider': 'gemini'}},
    }


def unit(index, **overrides):
    value = {
        'id': f'ws_{index}', 'title': f'Deliverable {index}',
        'character': 'global:wren' if index % 2 else 'global:builder',
        'owned_files': [f'mc/part_{index}.py'], 'depends_on': [],
        'description': f'Implement part {index}.',
        'checks': [f'python -m pytest tests/test_part_{index}.py -q'],
        'smokes': ['boot.mjs'],
    }
    value.update(overrides)
    return value


def decomposition(count=3, **overrides):
    value = {
        'project_id': 'thm', 'goal': 'Build distinct deliverables',
        'max_concurrent_workers': 1,
        'reviewer_character': 'global:code-reviewer',
        'units': [unit(i) for i in range(count)],
    }
    value.update(overrides)
    return value


def route(data, records, **kwargs):
    return plan_routing(data, app_config={'default_provider': 'claude'},
                        read_character=records.get, **kwargs)


def assert_invalid(result, code):
    assert result['route'] == 'invalid'
    assert result['independent_count'] == 0
    assert result['independent_units'] == result['ordinary_units'] == []
    assert result['payload'] is None
    assert code in {e['code'] for e in result['errors']}


@pytest.mark.parametrize('count', [0, 1, 2, 3, 4, 8])
def test_threshold_and_single_dispatch_destination(records, count):
    data = decomposition(count)
    result = route(data, records)
    assert result['errors'] == result['held_units'] == []
    assert result['independent_count'] == count
    assert result['independent_units'] == [u['id'] for u in data['units']]
    if count < 3:
        assert result['route'] == 'ordinary'
        assert result['ordinary_units'] == data['units']
        assert result['payload'] is None
    else:
        assert result['route'] == 'hivemind'
        assert result['ordinary_units'] == []  # never also standalone dispatch
        payload = result['payload']
        assert payload['max_concurrent_workers'] == 1
        assert len(payload['workstreams']) == count
        assert roster.plan_create(payload, app_config={}, read_character=records.get)
        assert payload['reviewer_character'] == 'global:code-reviewer'
        for source, ws in zip(data['units'], payload['workstreams']):
            assert ws['character'] == source['character']
            assert ws['dependencies'] == []
            assert ws['smokes'] == source['smokes']
            assert source['description'] in ws['description']
            assert json.dumps(source['owned_files']) in ws['description']
            assert json.dumps(source['checks']) in ws['description']
            assert not {'provider', 'model', 'effort'} & ws.keys()


@pytest.mark.parametrize('capacity', [1, 2, 3, 7])
def test_never_increases_or_truncates_authorized_capacity(records, capacity):
    result = route(decomposition(max_concurrent_workers=capacity), records)
    assert result['payload']['max_concurrent_workers'] == capacity


def test_reviewer_is_not_third_worker(records):
    result = route(decomposition(2), records)
    assert result['route'] == 'ordinary' and result['independent_count'] == 2
    data = decomposition(2)
    data['units'].append(unit(2, role='reviewer', character='global:code-reviewer'))
    assert_invalid(route(data, records), 'invalid_unit')


@pytest.mark.parametrize('roots', [1, 2, 3])
def test_dependent_chain_retained_as_full_q6_holds(records, roots):
    data = decomposition(roots)
    data['units'] += [unit(10, depends_on=['ws_0']), unit(11, depends_on=['ws_10'])]
    result = route(data, records)
    assert result['independent_count'] == roots
    assert result['route'] == ('hivemind' if roots >= 3 else 'ordinary')
    assert [u['id'] for u in result['held_units']] == ['ws_10', 'ws_11']
    for held, original in zip(result['held_units'], data['units'][-2:]):
        assert 'Q6' in held['hold_reason']
        assert 'reviewed integration' in held['hold_reason']
        assert {k: v for k, v in held.items() if k != 'hold_reason'} == original
    runnable = (result['payload']['workstreams'] if roots >= 3 else result['ordinary_units'])
    assert {u['id'] for u in runnable} == {f'ws_{i}' for i in range(roots)}


def test_declaration_not_prose_or_count_of_edits(records):
    data = decomposition(1)
    data['units'][0]['title'] = 'Three independent tasks, plus review'
    data['units'][0]['owned_files'] += ['mc/another.py', 'mc/third.py']
    result = route(data, records)
    assert result['route'] == 'ordinary' and result['independent_count'] == 1


@pytest.mark.parametrize('dependencies', [
    [['missing'], [], []],  # direct missing input
    [[], ['ws_0'], ['gone']],  # missing on a dependent, no dispatchable subset
    [['ws_0'], [], []],  # self cycle
    [['ws_1'], ['ws_0'], []],  # two-node cycle
    [['ws_1'], ['ws_2'], ['ws_0']],  # no roots
])
def test_bad_graph_never_becomes_independent_work(records, dependencies):
    data = decomposition()
    for row, deps in zip(data['units'], dependencies):
        row['depends_on'] = deps
    code = 'missing_dependency' if any(d in ('missing', 'gone') for ds in dependencies for d in ds) else 'dependency_cycle'
    assert_invalid(route(data, records), code)


@pytest.mark.parametrize('other_path', ['mc/part_0.py', 'MC/PART_0.PY',
                                        r'mc\part_0.py', './mc/part_0.py',
                                        'mc//part_0.py', 'mc/part_0.py/child'])
def test_ownership_aliases_and_ancestor_conflicts(records, other_path):
    data = decomposition()
    data['units'][1]['owned_files'] = [other_path]
    result = route(data, records)
    assert_invalid(result, 'ownership_overlap')
    assert result['errors'][0]['units'] == ['ws_0', 'ws_1']


def test_overlap_on_held_unit_is_reported(records):
    data = decomposition()
    data['units'].append(unit(5, depends_on=['ws_0'], owned_files=['mc/part_0.py']))
    assert_invalid(route(data, records), 'ownership_overlap')


def test_reports_multiple_graph_problems(records):
    data = decomposition()
    data['units'][0]['depends_on'] = ['ws_0', 'missing']
    data['units'][1]['owned_files'] = ['mc/part_0.py']
    result = route(data, records)
    assert {e['code'] for e in result['errors']} == {
        'missing_dependency', 'dependency_cycle', 'ownership_overlap'}
    assert result['payload'] is None


@pytest.mark.parametrize('path', ['/etc/foo', r'C:\repo\a.py', 'C:a.py',
                                  r'\\server\a', '../a.py', 'mc/../a.py',
                                  'mc/', '.', '**/*.py', 'mc/a?.py',
                                  'mc/[ab].py', 'mc/a\x00.py', 'mc/a./b.py'])
def test_ownership_must_be_concrete_relative_files(records, path):
    data = decomposition()
    data['units'][0]['owned_files'] = [path]
    assert_invalid(route(data, records), 'invalid_unit')


@pytest.mark.parametrize('patch', [
    {'id': '../bad'}, {'id': ''}, {'title': ''}, {'description': 1},
    {'owned_files': []}, {'owned_files': 'mc/a.py'}, {'owned_files': [None]},
    {'owned_files': ['mc/a.py', 'MC/A.PY']},
    {'depends_on': None}, {'depends_on': ['ws_1', 'ws_1']},
    {'checks': []}, {'checks': ['']}, {'checks': 'pytest'},
    {'smokes': ['../boot.mjs']}, {'smokes': ['python -m pytest']},
    {'smokes': ['boot.mjs', 'boot.mjs']}, {'smokes': [False]},
    {'dependencies': ['ws_1']}, {'provider': 'codex'}, {'model': 'custom'},
    {'effort': 'high'}, {'role': 'reviewer'},
    {'character': 'Sol_Tobin'}, {'character': 'project:wren'},
    {'character': 'global:missing'}, {'character': 'global:../wren'},
])
def test_invalid_unit_fields_are_never_silently_ignored(records, patch):
    data = decomposition()
    data['units'][0].update(patch)
    if patch.get('character') == 'global:missing':
        result = route(data, records)
        assert result['route'] == 'hivemind'
        assert any('global:missing' in w for w in result['warnings'])
        return
    assert_invalid(route(data, records), 'invalid_unit')


@pytest.mark.parametrize('field', ['id', 'title', 'owned_files', 'depends_on', 'checks', 'character'])
def test_required_unit_fields(records, field):
    data = decomposition()
    data['units'][0].pop(field)
    assert_invalid(route(data, records), 'invalid_unit')


@pytest.mark.parametrize('bad', [None, [], '', 1])
def test_nonobject_unit(records, bad):
    assert_invalid(route(decomposition(units=[bad]), records), 'invalid_unit')


def test_duplicate_ids(records):
    data = decomposition()
    data['units'][1]['id'] = 'ws_0'
    assert_invalid(route(data, records), 'duplicate_id')


@pytest.mark.parametrize('bad', [None, [], '', 1, True])
def test_nonobject_plan(records, bad):
    assert_invalid(route(bad, records), 'invalid_plan')


@pytest.mark.parametrize('patch', [
    {'project_id': ''}, {'goal': 1}, {'units': None}, {'units': {}},
    {'max_concurrent_workers': True}, {'max_concurrent_workers': 0},
    {'max_concurrent_workers': -1}, {'max_concurrent_workers': 1.5},
    {'max_concurrent_workers': '1'}, {'max_concurrent_workers': None},
    {'workstreams': []},
])
def test_bad_plan_shape_or_capacity(records, patch):
    assert_invalid(route(decomposition(**patch), records), 'invalid_plan')


@pytest.mark.parametrize('count', [0, 2, 3])
def test_bad_reviewer_is_reported_even_below_threshold(records, count):
    result = route(decomposition(count, reviewer_character='global:missing'), records)
    assert result['route'] == ('hivemind' if count >= 3 else 'ordinary')
    assert any('global:missing' in w for w in result['warnings'])
    assert_invalid(route(decomposition(count, reviewer_character='global:../missing'), records), 'invalid_reviewer')


def test_reviewer_optional_for_ordinary_required_for_roster(records):
    data = decomposition(2)
    data.pop('reviewer_character')
    assert route(data, records)['route'] == 'ordinary'
    data['units'].append(unit(2))
    assert_invalid(route(data, records), 'invalid_roster')


def test_unknown_engine_fails_visibly_no_substitution(records):
    records['wren']['engine'] = {'provider': 'unregistered-vendor'}
    result = route(decomposition(), records)
    assert_invalid(result, 'invalid_unit')
    assert 'Unknown provider' in result['errors'][0]['message']


def test_lookup_failure_is_reported(records):
    def broken(name):
        raise OSError('snapshot unavailable')
    result = plan_routing(decomposition(), app_config={}, read_character=broken)
    assert_invalid(result, 'invalid_unit')
    assert 'snapshot unavailable' in result['errors'][0]['message']


def test_canonical_refs_and_paths(records):
    data = decomposition()
    data['units'][0].update(character=' GLOBAL:wren ', owned_files=[r'.\mc\part_0.py'])
    result = route(data, records)
    assert result['payload']['workstreams'][0]['character'] == 'global:wren'
    assert '["mc/part_0.py"]' in result['payload']['workstreams'][0]['description']


def test_no_required_smokes_but_checks_remain_visible(records):
    data = decomposition()
    for row in data['units']:
        row.pop('description')
        row.pop('smokes')
    result = route(data, records)
    assert result['route'] == 'hivemind'
    for ws in result['payload']['workstreams']:
        assert ws['smokes'] == [] and 'Acceptance checks:' in ws['description']


def test_deterministic_detached_json_and_no_execution(records, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected I/O or execution')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(roster, '_default_read_character', forbidden)
    data = decomposition()
    data['units'].append(unit(9, depends_on=['ws_0']))
    original, snapshot = deepcopy(data), deepcopy(records)
    first, second = route(data, records), route(data, records)
    assert first == second == json.loads(json.dumps(first))
    assert data == original and records == snapshot
    first['payload']['workstreams'][0]['smokes'].append('changed.mjs')
    first['held_units'][0]['owned_files'].append('changed.py')
    assert data == original and route(data, records) == second


def test_payload_round_trip_through_existing_create(client, records, monkeypatch):
    monkeypatch.setattr(roster, '_default_read_character', records.get)
    data = decomposition()
    data['units'].append(unit(9, depends_on=['ws_0']))
    plan = route(data, records)
    response = client.post('/api/hivemind/create', json=plan['payload'])
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    cfg = body['hivemind']['config']
    assert cfg['roster_version'] == 1 and cfg['max_concurrent_workers'] == 1
    assert cfg['reviewer_character'] == 'global:code-reviewer'
    persisted = client.get(f"/api/hivemind/{body['hivemind']['id']}").get_json()
    rows = {ws['id']: ws for ws in persisted['workstreams']}
    assert set(rows) == {'ws_0', 'ws_1', 'ws_2'}
    for source in data['units'][:3]:
        ws = rows[source['id']]
        assert ws['character'] == source['character'] and ws['dependencies'] == []
        assert json.dumps(source['owned_files']) in ws['description']
        assert json.dumps(source['checks']) in ws['description']
        assert ws['smokes'] == ['boot.mjs']
    assert client.fake_rt.dispatch_calls == client.claude_rt.dispatch_calls == []
    assert [u['id'] for u in plan['held_units']] == ['ws_9']
