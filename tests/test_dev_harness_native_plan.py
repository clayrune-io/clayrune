"""A2a proposal integrity without executing native tools or approving live state."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from mc import dev_harness_native_plan as plans
from mc.dev_harness_catalogue import digest, load_catalogue
from mc.dev_harness_checks.recipes import RECIPE_IDS, load_recipe
from mc.dev_harness_checks.reports import validate
from mc.dev_harness_checks.coverage import validate_diff
from mc.dev_harness_policy import lost_protections, resolve_policy
from mc.dev_harness_store import HarnessStore, StoreError
from test_dev_harness_diff import repo, git


def native_draft():
    catalogue = load_catalogue()
    return {'profile': 'local-hobby',
            'practices': {k: {'severity': 'warning'} for k in catalogue['practices']},
            'digests': {'sources': digest('synthetic-guidance')},
            'native_checks': {'unit': {'enabled': True, 'severity': 'required', 'practice_ids': ['P02']}}}


@pytest.fixture
def inputs(repo, tmp_path, monkeypatch):
    # File-only closure: no version probes and no subprocess tool execution.
    tool = repo / '.clayrune/installed-fixture.txt'
    tool.parent.mkdir(parents=True)
    tool.write_text('installed distribution fixture', encoding='utf-8')
    config = repo / 'pyproject.toml'
    config.write_text('[tool.fixture]\n', encoding='utf-8')
    git(repo, 'add', 'pyproject.toml')
    git(repo, 'commit', '-qm', 'config')
    item = {'id': 'unit', 'recipe_id': 'pytest', 'launcher': [sys.executable, '-m', 'pytest'],
            'targets': [], 'configs': ['pyproject.toml'], 'tool_roots': [str(tool)],
            'command_args': ['tests'], 'coverage': None, 'timeout_seconds': 30}
    groups = [{'id': 'all', 'include': ['*'], 'check_ids': ['unit'],
               'not_applicable_check_ids': [], 'reason_code': 'source_change'}]
    from mc import dev_harness_runner as runner
    monkeypatch.setattr(runner, '_execute', lambda *a, **k: pytest.fail('A2a must not execute native commands'))
    return HarnessStore(tmp_path / '.clayrune/state'), {'id': 'one', 'project_path': str(repo)}, native_draft(), [item], groups


def inspect(inputs):
    store, project, draft, steps, groups = inputs
    return plans.inspect_plan(store=store, project=project, draft=draft,
        catalogue=load_catalogue(), steps=steps, groups=groups)


def approve(inputs, proposal, mode='preview', revision=0, evidence_id=None, losses=None):
    store, project, _, _, _ = inputs
    policy = proposal['policy']
    current = store.read('one')
    return store.activate('one', expected_revision=revision, mode=mode,
        draft={k: policy[k] for k in ('profile', 'practices', 'digests', 'native_checks')},
        catalogue=load_catalogue(), native_plan_digest=proposal['native_plan_digest'],
        project=project, evidence_id=evidence_id, acknowledged_native_losses=losses,
        acknowledged_losses=lost_protections(current['policy'] if current else None, policy, mode, load_catalogue()))


def test_inspect_immutable_roundtrip_no_approval_or_command(inputs):
    store, project, _, _, _ = inputs
    value = inspect(inputs)
    assert store.read('one') is None
    assert value['policy']['version'] == 2
    assert value['native_plan_digest'] == digest(value['plan']['payload']) == value['policy']['digests']['native_rules']
    assert digest(value['plan']['payload']['baseline_identity']) == value['policy']['digests']['baseline']
    assert value['plan']['payload']['policy_inputs_digest'] == plans.policy_inputs_digest(value['policy'])
    assert plans.read_plan(store, 'one', value['native_plan_digest']) == value['plan']
    assert inspect(inputs) == value
    assert value['authorizes_merge'] is False
    assert all(v['deterministic'] == 'unsupported' for v in value['full_practice_coverage'].values())
    assert not (store.project_dir('one') / 'runs').exists()
    assert approve(inputs, value)['native_plan'] == value['plan']
    assert store.read('one')['native_plan'] == value['plan']


@pytest.mark.parametrize('raw', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', ' ' * (plans.MAX_PLAN_BYTES+1)],
                         ids=['duplicate', 'nan', 'infinity', 'overflow', 'size-limit'])
def test_strict_json(raw):
    with pytest.raises(ValueError):
        plans.strict_loads(raw)


@pytest.mark.parametrize('mutation', ['extra', 'duplicate', 'missing', 'traversal', 'absolute', 'nul',
    'false-timeout', 'large-timeout', 'path-duplicate', 'precompiled', 'tool-relative', 'secret',
    'fixed-args', 'traceability', 'disabled', 'unknown-recipe', 'group-partition', 'group-id', 'prefix'])
def test_invalid_proposal_never_writes_approval(inputs, mutation):
    store, project, draft, steps, groups = inputs
    if mutation == 'extra': steps[0]['recipe_digest'] = 'a'*64
    elif mutation == 'duplicate': steps.append(copy.deepcopy(steps[0]))
    elif mutation == 'missing': steps.clear()
    elif mutation == 'traversal': steps[0]['configs'] = ['../outside']
    elif mutation == 'absolute': steps[0]['targets'] = ['/etc/hosts']
    elif mutation == 'nul': steps[0]['launcher'].append('\0')
    elif mutation == 'false-timeout': steps[0]['timeout_seconds'] = True
    elif mutation == 'large-timeout': steps[0]['timeout_seconds'] = 7201
    elif mutation == 'path-duplicate': steps[0]['configs'] *= 2
    elif mutation == 'precompiled': steps[0]['argv'] = ['anything']
    elif mutation == 'tool-relative': steps[0]['tool_roots'] = ['relative']
    elif mutation == 'secret': steps[0]['command_args'] = ['--token=private-value']
    elif mutation == 'fixed-args': steps[0].update(recipe_id='ruff', targets=['app.py'])
    elif mutation == 'traceability': draft['native_checks']['unit']['practice_ids'] = ['P06']
    elif mutation == 'disabled': draft['native_checks']['unit']['enabled'] = False
    elif mutation == 'unknown-recipe': steps[0]['recipe_id'] = 'unknown'
    elif mutation == 'group-partition': groups[0]['not_applicable_check_ids'] = ['unit']
    elif mutation == 'group-id': groups.append(copy.deepcopy(groups[0]))
    elif mutation == 'prefix': groups[0]['include'] = ['**/*.py']
    with pytest.raises(ValueError): inspect(inputs)
    assert store.read('one') is None


@pytest.mark.parametrize('pin', ['baseline', 'native_rules'])
def test_compute_null_pins_and_reject_supplied_mismatch(inputs, pin):
    inputs[2]['digests'][pin] = None
    proposal = inspect(inputs)
    inputs[2]['digests'][pin] = 'b'*64
    with pytest.raises(ValueError): inspect(inputs)
    assert proposal['policy']['digests'][pin]


@pytest.mark.parametrize('mutation', ['config', 'new-config', 'tool', 'recipe', 'fixture', 'checker', 'project', 'common-dir', 'policy', 'store-missing', 'store-corrupt', 'embedded'])
def test_changed_inputs_refuse_without_activation(inputs, monkeypatch, mutation):
    value = inspect(inputs)
    store, project, _, steps, _ = inputs
    if mutation == 'config': (Path(project['project_path'])/'pyproject.toml').write_text('changed')
    elif mutation == 'new-config': (Path(project['project_path'])/'pytest.ini').write_text('[pytest]')
    elif mutation == 'tool': Path(steps[0]['tool_roots'][0]).write_text('changed')
    elif mutation == 'recipe': monkeypatch.setattr('mc.dev_harness_checks.recipes.load_recipe', lambda _: {**load_recipe('pytest'), 'tool_version': '0.0.0'})
    elif mutation == 'fixture': monkeypatch.setattr(plans, 'fixture_contract', lambda _: {'changed': True})
    elif mutation == 'checker': monkeypatch.setattr(plans, 'checker_digest', lambda: 'c'*64)
    elif mutation == 'project': project['project_path'] = str(Path(project['project_path'])/'.clayrune')
    elif mutation == 'common-dir': monkeypatch.setattr(plans, 'repository_identity', lambda _: {'git_common_dir': 'different'})
    elif mutation == 'policy': value['policy']['native_checks']['unit']['severity'] = 'warning'
    elif mutation == 'store-missing': plans._plan_path(store, 'one', value['native_plan_digest']).unlink()
    elif mutation == 'store-corrupt': plans._plan_path(store, 'one', value['native_plan_digest']).write_text('{}')
    elif mutation == 'embedded': value['plan']['payload']['groups'][0]['include'] = ['app.py']
    with pytest.raises((ValueError, StoreError)):
        plans.validate_plan(store, project, value['policy'], value['plan'])
    assert store.read('one') is None


def test_active_snapshot_store_copy_and_embedded_binding(inputs):
    value = inspect(inputs)
    record = approve(inputs, value)
    record['native_plan']['payload']['groups'][0]['include'] = ['app.py']
    record['snapshot_digest'] = digest({k:v for k,v in record.items() if k != 'snapshot_digest'})
    path = inputs[0]._active_path('one')
    path.write_text(json.dumps(record))
    with pytest.raises(StoreError): inputs[0].read('one')


def test_ordinary_source_change_does_not_invalidate_plan(inputs):
    value = inspect(inputs)
    record = approve(inputs, value)
    (Path(inputs[1]['project_path'])/'app.py').write_text('changed application')
    assert inputs[0].read('one') == record


def test_drift_holds_reads_but_human_can_inspect_and_approve_replacement(inputs):
    value = inspect(inputs)
    approve(inputs, value)
    path = Path(inputs[1]['project_path'])/'pyproject.toml'
    path.write_text('[tool.fixture]\nchanged=true\n')
    with pytest.raises(StoreError): inputs[0].read('one')
    replacement = inspect(inputs)
    assert replacement['native_losses'] == ['unit']
    policy = replacement['policy']
    record = inputs[0].activate('one',expected_revision=1, mode='preview', project=inputs[1],
        catalogue=load_catalogue(), native_plan_digest=replacement['native_plan_digest'],
        draft={k:policy[k] for k in ('profile','practices','digests','native_checks')},
        acknowledged_losses=replacement['protection_losses'], acknowledged_native_losses=['unit'])
    assert record['revision']==2 and inputs[0].read('one')==record


def test_version_one_snapshot_read_preserves_original_digest(inputs):
    store = inputs[0]
    policy = resolve_policy({}, load_catalogue())
    record = store.activate('one',expected_revision=0,draft={},mode='preview',catalogue=load_catalogue(),
        acknowledged_losses=lost_protections(None,policy,'preview',load_catalogue()))
    record['policy'].pop('native_checks')
    record['policy']['version']=1
    for key in ('native_plan','native_plan_digest','acknowledged_native_losses'):
        record.pop(key)
        record['audit'][0].pop(key,None)
    record['audit'][0]['policy_digest']=digest(record['policy'])
    record['snapshot_digest']=digest({k:v for k,v in record.items() if k!='snapshot_digest'})
    path = store._active_path('one')
    path.write_text(json.dumps(record))
    before=path.read_bytes()
    assert store.read('one')==record and path.read_bytes()==before


@pytest.mark.parametrize('mutation', ['removed','disabled','downgraded','argv','scope','config','cap','tool','mapping'])
def test_exact_native_loss_ids_include_each_prior_required_change(inputs, mutation):
    proposal=inspect(inputs)
    current=approve(inputs,proposal)
    policy=copy.deepcopy(proposal['policy'])
    envelope=copy.deepcopy(proposal['plan'])
    if mutation=='removed': policy['native_checks'].clear()
    elif mutation=='disabled': policy['native_checks']['unit']['enabled']=False
    elif mutation=='downgraded': policy['native_checks']['unit']['severity']='warning'
    elif mutation=='argv': envelope['payload']['steps'][0]['command_args']=['different']
    elif mutation=='scope': envelope['payload']['groups'][0]['include']=['app.py']
    elif mutation=='config': envelope['payload']['steps'][0]['config_digests']={}
    elif mutation=='cap': envelope['payload']['baseline_identity']['caps']['P02']={'changed_line_floor_percent':70}
    elif mutation=='tool': envelope['payload']['steps'][0]['tool_closure_digest']='b'*64
    elif mutation=='mapping': envelope['payload']['groups'][0]['reason_code']='docs_change'
    assert plans.native_losses(current,policy,'preview',envelope)==['unit']
    # A different measured application baseline alone is not a lost native bar.
    unchanged=copy.deepcopy(proposal['plan'])
    unchanged['payload']['baseline_identity']['source']['head']='b'*40
    assert plans.native_losses(current,proposal['policy'],'preview',unchanged)==[]


@pytest.mark.parametrize('native', [[], {'unit':{}}, {'bad.id':{'enabled':True,'severity':'required','practice_ids':['P02']}},
    {'unit':{'enabled':1,'severity':'required','practice_ids':['P02']}},
    {'unit':{'enabled':True,'severity':'required','practice_ids':['P02','P02']}},
    {'unit':{'enabled':True,'severity':'required','practice_ids':['P17','P01']}},
    {'unit':{'enabled':True,'severity':'required','practice_ids':['P23']}},
    {'unit':{'enabled':True,'severity':'required','practice_ids':[]}},
    {'unit':{'enabled':True,'severity':'required','practice_ids':[['P02']]}}])
def test_policy_v2_rejects_malformed_native_selections(native):
    with pytest.raises(ValueError): resolve_policy({'native_checks':native},load_catalogue())


def test_policy_v2_no_inferred_checks_and_enabled_guidance_required():
    assert resolve_policy({},load_catalogue())['native_checks']=={}
    draft=native_draft()
    draft['practices']['P02']['enabled']=False
    with pytest.raises(ValueError): resolve_policy(draft,load_catalogue())


def test_trusted_snapshot_envelope_is_not_limited_to_one_plan_input(inputs):
    store=inputs[0]
    policy=resolve_policy({},load_catalogue())
    record=store.activate('one',expected_revision=0,draft={},mode='preview',catalogue=load_catalogue(),
        acknowledged_losses=lost_protections(None,policy,'preview',load_catalogue()))
    # Legacy snapshots have no 4 MiB plan-input restriction. Audit/embedded
    # measurement growth must not strand an otherwise valid atomic snapshot.
    record['audit'][0]['fixture_padding']='x'*(plans.MAX_PLAN_BYTES+1)
    record['snapshot_digest']=digest({k:v for k,v in record.items() if k!='snapshot_digest'})
    store._active_path('one').write_text(json.dumps(record))
    assert store.read('one')==record


@pytest.mark.parametrize('identity', ['coverage-python', 'coverage-javascript'])
def test_coverage_required_rejected_warning_accepted(inputs, identity):
    _, project, draft, steps, _ = inputs
    steps[0].update(recipe_id=identity, command_args=['run', '-m', 'pytest'], coverage={
        'report': '.clayrune/coverage/report.xml', 'format': 'lcov-v1' if identity.endswith('javascript') else 'coverage.py-xml-7.10.6',
        'source_roots': ['.'], 'floor_percent': 75, 'diff_launcher': [sys.executable]})
    draft['practices']['P02']['caps'] = {'changed_line_floor_percent':75}
    with pytest.raises(ValueError, match='coverage_required_unmeasurable'): inspect(inputs)
    draft['native_checks']['unit']['severity'] = 'warning'
    value = inspect(inputs)
    assert value['plan']['payload']['steps'][0]['coverage']['floor_percent'] == 75


@pytest.mark.parametrize('identity', RECIPE_IDS)
def test_shipped_fixture_manifest_and_three_cases(identity):
    contract = plans.fixture_contract(identity)
    path = Path(contract['fixture_file'])
    assert plans.file_digest(path) == contract['fixture_file_digest']
    fixture = json.loads(path.read_bytes())
    recipe = load_recipe(identity)
    if not identity.startswith('coverage-'):
        assert [v['case'] for v in fixture] == contract['cases']
        for row in fixture:
            assert validate(recipe['report_contract'], json.dumps(row['report']).encode(),
                row['exit_code'], recipe['fail_codes'])['status'] == row['status']
    else:
        for case in ('positive', 'finding'):
            row = fixture[case]
            raw = json.dumps({'src_stats': {'src/app.py': {'covered_lines': [1] if row['uncovered'] else [1,2],
                'violation_lines': [2] if row['uncovered'] else []}}, 'total_num_lines': row['lines'],
                'total_num_violations':row['uncovered'], 'total_percent_covered':row['percent']}).encode()
            assert validate_diff(raw, {'src/app.py': {1,2}},75)['status'] == ('pass' if case == 'positive' else 'fail')
        assert fixture['crash']['exit_code'] == 2
        with pytest.raises((ValueError, KeyError)): validate_diff(b'{}', {'src/app.py':{1,2}},75)
