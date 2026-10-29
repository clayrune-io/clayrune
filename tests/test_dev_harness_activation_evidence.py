"""Trusted activation composition from synthetic M3/M4 receipts only; no runs."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mc import dev_harness_activation_evidence as owner
from mc import dev_harness_native_plan as plans
from mc import dev_harness_sources as sources
from mc.dev_harness_catalogue import digest, load_catalogue
from mc.dev_harness_policy import resolve_policy
from mc.dev_harness_store import StoreError
from test_dev_harness_diff import repo, git
from test_dev_harness_native_plan import inputs, inspect, approve


def install_fixture(inputs):
    store, project, draft, _, _ = inputs
    selected = sources.selected_sources(resolve_policy(draft, load_catalogue()), load_catalogue())
    # Reproduce M2's verified installation layout in disposable trusted state.
    staging = Path(project['project_path']) / '.clayrune/fixture-guidance'
    staging.mkdir(parents=True)
    (staging/'LICENSE').write_text('MIT fixture license\n')
    for source in selected:
        path = staging / source['subpath'] / 'SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text('Fixture guidance\n')
    import shutil
    lock = {'sources':selected, 'files':sources.resource_manifest(staging, selected)}
    source_digest = digest(lock)
    destination = Path(project['project_path'])/'.clayrune/dev-harness/guidance'/source_digest
    destination.parent.mkdir(parents=True)
    shutil.copytree(staging, destination)
    path = store.project_dir('one')/'installations'/(source_digest+'.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'status':'verified','project_id':'one','lock':lock}))
    draft['digests']['sources'] = source_digest
    sources.validate_activation(store, project, resolve_policy(draft, load_catalogue()), load_catalogue())


def receipt(inputs, proposal, snapshot, *, identity='a'*32, results=None, source=None):
    store, project, *_ = inputs
    prepared = plans.validate_plan(store, project, proposal['policy'], proposal['plan'])
    if results is None:
        results = []
        for step in prepared:
            native = step['native_prepared']
            results.append({'id':step['id'],'status':'pass','exit_code':0,'cancelled':False,'timed_out':False,
                'argv_digest':step['argv_digest'],'tool_digest':step['tool_digest'],
                'config_digest':step['config_digest'],'recipe_digest':native['recipe_digest'],
                'tool_closure_digest':native['closure_digest'],'environment_digest':native['environment_digest'],
                'tool_version':native['recipe_data']['tool_version'],
                'report_contract':native['recipe_data']['report_contract'], 'coverage':'native_command_only'})
    value = {'version':1,'kind':'runner_receipt','trusted':True,'authorizes_merge':False,
        'run_id':identity,'project_id':'one','active_revision':snapshot['revision'],
        'policy_digest':digest(snapshot['policy']),'catalogue_digest':snapshot['catalogue']['digest'],
        'digests':snapshot['policy']['digests'], 'native_plan_digest':proposal['native_plan_digest'],
        'checker_digest':proposal['plan']['payload']['checker_digest'], 'status':'pass','results':results,
        'steps_digest':digest([{k:v for k,v in step.items() if k != 'argv'} for step in prepared]),
        'source':source or proposal['plan']['payload']['baseline_identity']['source']}
    save_receipt(store, value)
    return value


def save_receipt(store, value):
    value['receipt_digest'] = digest({k:v for k,v in value.items() if k != 'receipt_digest'})
    path = store.project_dir('one')/'runs'/(value['run_id']+'.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def ready(inputs):
    install_fixture(inputs)
    proposal = inspect(inputs)
    snapshot = approve(inputs, proposal)
    measured = receipt(inputs, proposal, snapshot)
    return inputs, proposal, snapshot, measured


def compose(ready):
    inputs, _, _, measured = ready
    return owner.compose_evidence(store=inputs[0], project=inputs[1], receipt_ids=[measured['run_id']])


def test_native_only_enforce_requires_exact_human_preview_and_stored_measurements(ready):
    inputs, proposal, snapshot, measured = ready
    value = compose(ready)
    assert value['kind'] == 'measured_preview' and value['status'] == 'pass'
    assert value['active_revision'] == snapshot['revision']
    assert value['coverage_scope'] == 'named_native_checks'
    assert value['receipt_digests'] == [measured['receipt_digest']]
    assert value['native_checks']['unit']['status'] == 'pass'
    assert all(v['status']=='unsupported' for v in value['practices'].values())
    record = approve(inputs, proposal, mode='enforce', revision=1, evidence_id=value['evidence_id'])
    assert record['revision'] == 2 and record['mode'] == 'enforce'
    assert record['evidence'] == value
    assert record['audit'][-1]['native_plan_digest'] == proposal['native_plan_digest']
    assert inputs[0].read('one') == record


@pytest.mark.parametrize('mutation', ['source', 'revision', 'plan', 'policy', 'catalogue', 'status', 'code', 'kind',
    'trusted', 'authorizes_merge', 'checker', 'missing', 'duplicate', 'unexpected', 'cancelled', 'timed_out',
    'argv_digest', 'tool_digest', 'config_digest', 'recipe_digest', 'tool_closure_digest', 'environment_digest',
    'tool_version', 'report_contract', 'coverage', 'fail', 'review_required', 'exit_code', 'steps_digest'])
def test_bad_receipts_cannot_mint_activation_evidence(ready, mutation):
    inputs, _, _, measured = ready
    store = inputs[0]
    if mutation == 'source': measured['source']['head'] = 'b'*40
    elif mutation == 'revision': measured['active_revision'] += 1
    elif mutation == 'plan': measured['native_plan_digest'] = 'b'*64
    elif mutation == 'policy': measured['policy_digest'] = 'b'*64
    elif mutation == 'catalogue': measured['catalogue_digest'] = 'b'*64
    elif mutation == 'status': measured['status'] = 'error'
    elif mutation == 'code': measured['code'] = 'inputs_changed'
    elif mutation in ('kind', 'trusted', 'authorizes_merge'): measured[mutation] = 'wrong'
    elif mutation == 'checker': measured['checker_digest'] = 'b'*64
    elif mutation == 'steps_digest': measured['steps_digest'] = 'b'*64
    elif mutation == 'missing': measured['results'] = []
    elif mutation == 'duplicate': measured['results'] *= 2
    elif mutation == 'unexpected': measured['results'][0]['id'] = 'unapproved'
    elif mutation in ('cancelled', 'timed_out'): measured['results'][0][mutation] = True
    elif mutation in ('fail', 'review_required'): measured['results'][0]['status'] = mutation
    else: measured['results'][0][mutation] = 'wrong'
    save_receipt(store, measured)
    before = store._active_path('one').read_bytes()
    with pytest.raises((ValueError, StoreError)): compose(ready)
    assert store._active_path('one').read_bytes() == before
    assert not (store.project_dir('one')/'evidence').exists()


@pytest.mark.parametrize('mutation', ['revision', 'policy', 'plan', 'source', 'receipt', 'evidence_digest', 'native_checks'])
def test_stale_evidence_activation_preserves_snapshot(ready, mutation):
    inputs, proposal, snapshot, measured = ready
    store = inputs[0]
    value = compose(ready)
    if mutation == 'revision': value['active_revision'] = 0
    elif mutation == 'policy': value['policy_digest'] = 'f'*64
    elif mutation == 'plan': value['native_plan_digest'] = 'f'*64
    elif mutation == 'source': (Path(inputs[1]['project_path'])/'app.py').write_text('new source')
    elif mutation == 'receipt':
        measured['results'][0]['status'] = 'fail'
        save_receipt(store, measured)
    elif mutation == 'evidence_digest': value['evidence_digest'] = 'f'*64
    elif mutation == 'native_checks': value['native_checks']['unit']['status'] = 'fail'
    if mutation != 'evidence_digest':
        value['evidence_digest'] = digest({k:v for k,v in value.items() if k != 'evidence_digest'})
    path = store.project_dir('one')/'evidence'/(value['evidence_id']+'.json')
    path.write_text(json.dumps(value))
    before = store._active_path('one').read_bytes()
    with pytest.raises((ValueError, StoreError)):
        approve(inputs, proposal, mode='enforce', revision=1, evidence_id=value['evidence_id'])
    assert store._active_path('one').read_bytes() == before


@pytest.mark.parametrize('warning_status,code', [('fail',None),('error','invalid_native_report'),('error','empty_diff_coverage')])
def test_warning_findings_explicit_all_steps_measured(inputs,warning_status,code):
    install_fixture(inputs)
    inputs[2]['native_checks']['lint'] = {'enabled':True,'severity':'warning','practice_ids':['P01']}
    inputs[3].append({**copy.deepcopy(inputs[3][0]),'id':'lint','recipe_id':'ruff','targets':['app.py'],'command_args':None})
    inputs[4][0]['check_ids'].append('lint')
    proposal = inspect(inputs)
    snapshot = approve(inputs, proposal)
    measured = receipt(inputs, proposal, snapshot)
    measured['status'] = warning_status
    measured['results'][1]['status'] = warning_status
    measured['results'][1]['exit_code'] = 1
    if code: measured['results'][1]['code']=code
    save_receipt(inputs[0], measured)
    value = owner.compose_evidence(store=inputs[0], project=inputs[1], receipt_ids=[measured['run_id']])
    assert value['status'] == 'pass' and value['native_checks']['lint']['status'] == warning_status
    if code: assert value['native_checks']['lint']['code']==code
    assert approve(inputs, proposal, mode='enforce', revision=1, evidence_id=value['evidence_id'])['mode'] == 'enforce'


def test_warning_shared_integrity_error_is_never_activation_pass(inputs):
    install_fixture(inputs)
    inputs[2]['native_checks']['unit']['severity']='warning'
    proposal=inspect(inputs)
    snapshot=approve(inputs,proposal)
    measured=receipt(inputs,proposal,snapshot)
    measured['status']='error'
    measured['results'][0].update(status='error',code='tool_closure_changed')
    save_receipt(inputs[0],measured)
    with pytest.raises(ValueError,match='input mismatch'):
        owner.compose_evidence(store=inputs[0],project=inputs[1],receipt_ids=[measured['run_id']])


def test_native_and_full_protection_losses_remain_separate(ready):
    inputs, proposal, _, _ = ready
    value = compose(ready)
    approve(inputs, proposal, mode='enforce', revision=1, evidence_id=value['evidence_id'])
    with pytest.raises(ValueError, match='native lost protection'):
        approve(inputs, proposal, mode='preview', revision=2)
    result = approve(inputs, proposal, mode='preview', revision=2, losses=['unit'])
    assert result['acknowledged_native_losses'] == ['unit']
    assert result['audit'][-1]['acknowledged_native_losses'] == ['unit']


def test_no_evidence_from_unapproved_or_off_plan(inputs):
    install_fixture(inputs)
    proposal = inspect(inputs)
    with pytest.raises(ValueError, match='Approved native Preview'):
        owner.compose_evidence(store=inputs[0], project=inputs[1], receipt_ids=['a'*32])
    approve(inputs, proposal, mode='off')
    with pytest.raises(ValueError, match='Approved native Preview'):
        owner.compose_evidence(store=inputs[0], project=inputs[1], receipt_ids=['a'*32])


def test_guidance_reverified_and_persistence_failure_does_not_claim_evidence(ready, monkeypatch):
    inputs, _, _, _ = ready
    def fail(*a, **k): raise OSError('synthetic write failure')
    monkeypatch.setattr(owner, 'write_json_atomic', fail)
    with pytest.raises(OSError): compose(ready)
    assert not list((inputs[0].project_dir('one')/'evidence').glob('*.json'))
    root = Path(inputs[1]['project_path'])/'.clayrune/dev-harness/guidance'
    next(root.rglob('LICENSE')).write_text('changed guidance')
    with pytest.raises(StoreError): compose(ready)


def test_native_pass_cannot_substitute_for_required_full_practice(inputs):
    inputs[2]['practices']['P02']['severity']='required'
    install_fixture(inputs)
    proposal=inspect(inputs)
    snapshot=approve(inputs,proposal)
    measured=receipt(inputs,proposal,snapshot)
    evidence=owner.compose_evidence(store=inputs[0],project=inputs[1],receipt_ids=[measured['run_id']])
    before=inputs[0]._active_path('one').read_bytes()
    with pytest.raises(ValueError,match='Unsupported'):
        approve(inputs,proposal,mode='enforce',revision=1,evidence_id=evidence['evidence_id'])
    assert inputs[0]._active_path('one').read_bytes()==before


def test_warning_only_plan_cannot_mint_blocking_bar(inputs):
    inputs[2]['native_checks']['unit']['severity']='warning'
    install_fixture(inputs)
    proposal=inspect(inputs)
    snapshot=approve(inputs,proposal)
    measured=receipt(inputs,proposal,snapshot)
    with pytest.raises(ValueError,match='Required native check'):
        owner.compose_evidence(store=inputs[0],project=inputs[1],receipt_ids=[measured['run_id']])
