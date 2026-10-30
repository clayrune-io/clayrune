"""Real synthetic native processes on disposable repos, never live approval."""
import copy
import json
import sys
from pathlib import Path

import pytest

from mc import dev_harness_runner as runner
from mc import dev_harness_integration as owner
from mc import dev_harness_native_plan as plans
from mc.dev_harness_store import StoreError
from mc.landing_serialization import landing
from test_dev_harness_diff import repo, git, branch
from test_dev_harness_native_plan import inputs, inspect, approve
from test_dev_harness_activation_evidence import install_fixture

_EXECUTE = runner._execute


@pytest.fixture
def executable(inputs, monkeypatch):
    store, project, draft, steps, groups = inputs
    root = Path(project['project_path'])
    tool = root/'.clayrune/synthetic.py'
    tool.write_text('import sys\nif "--version" in sys.argv: print("pytest 8.4.2")\n'
                    'elif "fail" in sys.argv: raise SystemExit(1)\n'
                    'else: print("synthetic-output-must-not-escape")\n')
    steps[0].update(launcher=[sys.executable, str(tool)], tool_roots=[str(tool)], command_args=['pass'])
    monkeypatch.setattr(runner, '_execute', _EXECUTE)
    return inputs


def run(inputs, **kwargs):
    store, project, *_ = inputs
    registered, unregistered = [], []
    with landing(Path(project['project_path'])) as lock:
        result = owner.evaluate_locked(store=store, project=project, owner=lock,
            register=lambda proc, name: registered.append(proc.pid), unregister=unregistered.append, **kwargs)
        assert registered == unregistered
        return result


def test_real_preview_receipt_measurement_enforce_and_live_consume(executable):
    install_fixture(executable)
    proposal = inspect(executable)
    approve(executable, proposal)
    result = run(executable, measurement=True)
    assert result.record['status'] == 'pass', result.record
    assert result.record['measured_evidence_id']
    receipt = runner.read_receipt(executable[0], 'one', result.record['receipt_ids'][0])
    assert receipt['trusted'] and receipt['source']['kind'] == 'preview'
    assert receipt['native_plan_digest'] == proposal['native_plan_digest']
    assert 'synthetic-output' not in json.dumps(result.record)
    approve(executable, proposal, mode='enforce', revision=1, evidence_id=result.record['measured_evidence_id'])
    with pytest.raises(ValueError, match='landing_ownership_required'):
        owner.consume(executable[0], result)
    branch(Path(executable[1]['project_path']))
    with landing(Path(executable[1]['project_path'])) as lock:
        result = owner.evaluate_locked(store=executable[0], project=executable[1], owner=lock,
            target='master', incoming='incoming', register=lambda *a: None, unregister=lambda p: None)
        assert result.record['status'] == 'pass', result.record
        pins = owner.consume(executable[0], result)
        assert pins['H'] == git(Path(executable[1]['project_path']), 'rev-parse', 'incoming')
        (Path(executable[1]['project_path'])/'unexpected').write_text('mutation')
        assert owner.consume(executable[0], result)['run_id'] == result.record['run_id']
        (Path(executable[1]['project_path'])/'app.py').write_text('tracked mutation')
        with pytest.raises(ValueError):
            owner.consume(executable[0], result)


def test_warning_fail_does_not_skip_required(executable):
    store, project, draft, steps, groups = executable
    draft['native_checks']['warning'] = {'enabled': True, 'severity': 'warning', 'practice_ids': ['P02']}
    steps.insert(0, {**copy.deepcopy(steps[0]), 'id': 'warning', 'command_args': ['fail']})
    groups[0]['check_ids'].append('warning')
    proposal = inspect(executable)
    approve(executable, proposal)
    result = run(executable)
    assert result.record['status'] == 'pass', result.record
    assert len(result.record['receipt_ids']) == 2
    assert result.record['warnings']['warning']['status'] == 'fail'
    assert result.record['required']['unit']['status'] == 'pass'


def test_clean_coverage_warning_is_incomplete_and_required_still_runs(executable):
    store, project, draft, steps, groups = executable
    coverage = Path(project['project_path'])/'.clayrune/coverage-fixture.py'
    coverage.write_text('print("7.10.6")\n')
    draft['practices']['P02']['caps'] = {'changed_line_floor_percent':75}
    draft['native_checks']['coverage'] = {'enabled':True,'severity':'warning','practice_ids':['P02']}
    steps.insert(0, {'id':'coverage','recipe_id':'coverage-python','launcher':[sys.executable,str(coverage)],
        'targets':[],'configs':[],'tool_roots':[str(coverage)],'command_args':['run'],'timeout_seconds':10,
        'coverage':{'report':'.clayrune/report.xml','format':'coverage.py-xml-7.10.6','source_roots':['.'],
                    'floor_percent':75,'diff_launcher':[sys.executable,str(coverage)]}})
    groups[0]['check_ids'].append('coverage')
    proposal = inspect(executable)
    approve(executable, proposal)
    result = run(executable)
    assert result.record['required']['unit']['status'] == 'pass', result.record
    assert result.record['warnings']['coverage'] == {'status':'error','code':'empty_diff_coverage','severity':'warning'}
    assert result.record['status'] == 'pass' and result.record['standards_hold'] is False
    measured = run(executable, measurement=True)
    assert measured.record['status'] == 'error' and measured.record['code'] == 'measurement_unavailable'
    assert measured.record['measured_evidence_id'] is None


def test_candidate_and_pending_receipts_distinct(executable):
    from test_dev_harness_pending_merge import pending
    from mc.dev_harness_pending_merge import freeze_pending
    proposal = inspect(executable)
    approve(executable, proposal)
    root = Path(executable[1]['project_path'])
    pending(root)
    result = run(executable, pending=freeze_pending(root))
    assert result.record['status'] == 'pass', result.record
    receipt = runner.read_receipt(executable[0], 'one', result.record['receipt_ids'][0])
    assert receipt['source']['kind'] == 'pending_merge'
    assert receipt['source']['T'] == git(root, 'write-tree')


def test_candidate_plan_config_edit_refuses_before_native_spawn(executable):
    proposal = inspect(executable)
    approve(executable, proposal)
    root = Path(executable[1]['project_path'])
    git(root, 'checkout', '-qb', 'incoming')
    (root/'pyproject.toml').write_text('[tool.changed]\n')
    git(root, 'add', 'pyproject.toml')
    git(root, 'commit', '-qm', 'plan drift')
    git(root, 'checkout', '-q', 'master')
    result = run(executable, target='master', incoming='incoming')
    assert result.record['status'] == 'error'
    assert result.record['code'] == 'plan_inputs_changed'
    assert result.record['standards_hold'] is False  # intact Preview adds no hold


def test_registration_failure_and_safe_stale_read(executable):
    proposal = inspect(executable)
    approve(executable, proposal)
    with landing(Path(executable[1]['project_path'])) as lock:
        def refuse(*_):
            raise RuntimeError('sensitive-registration-detail')
        result = owner.evaluate_locked(store=executable[0], project=executable[1], owner=lock,
            register=refuse, unregister=lambda _: None)
    assert result.record['status'] == 'error'
    record = owner.read_run(executable[0], executable[1], result.record['run_id'])
    assert record['authorizes_merge'] is False and not record['stale']
    (Path(executable[1]['project_path'])/'app.py').write_text('changed')
    assert owner.read_run(executable[0], executable[1], result.record['run_id'])['stale']
    with pytest.raises(LookupError):
        owner.read_run(executable[0], executable[1], 'a'*32)
    path = executable[0].project_dir('one')/'integration_runs'/(result.record['run_id']+'.json')
    value = json.loads(path.read_text())
    value['status'] = 'pass'
    path.write_text(json.dumps(value))
    with pytest.raises(StoreError):
        owner.read_run(executable[0], executable[1], result.record['run_id'])


def test_preview_environment_integrity_error_remains_observation(executable, monkeypatch):
    proposal = inspect(executable)
    approve(executable, proposal)
    with landing(Path(executable[1]['project_path'])) as lock:
        result = owner.evaluate_locked(store=executable[0], project=executable[1], owner=lock,
            register=lambda *args: monkeypatch.setenv('DH_FIXTURE_CHANGED', '1'), unregister=lambda p: None)
        assert result.record['status'] == 'error' and result.record['standards_hold'] is False
        assert owner.consume(executable[0], result)['run_id'] == result.record['run_id']


def test_registered_plan_cannot_execute_in_another_repository(executable, tmp_path):
    proposal = inspect(executable)
    approve(executable, proposal)
    other = tmp_path/'.clayrune/other-repository'
    other.mkdir(parents=True)
    git(other, 'init', '-q')
    with landing(other) as lock, pytest.raises(ValueError, match='project_repository_mismatch'):
        owner.evaluate_locked(store=executable[0], project=executable[1], owner=lock,
            workspace={**executable[1],'project_path':str(other)},
            register=lambda *a: pytest.fail('wrong repository must not execute'), unregister=lambda p: None)
