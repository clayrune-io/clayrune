"""Real argv execution, suppression and authority boundaries on temp trees."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mc import agent_jobs, dev_harness_runner as runner
from mc.dev_harness_catalogue import load_catalogue
from mc.dev_harness_policy import lost_protections, resolve_policy
from mc.dev_harness_store import HarnessStore, StoreError
from test_dev_harness_diff import branch, git, repo  # fixture reuse


def step(code='pass', **changes):
    return {'id': 'unit', 'argv': [sys.executable, '-c', code], 'timeout_seconds': 10,
            'fail_codes': [1], 'configs': [], **changes}


def evaluate(repo, tmp_path, steps=None, **kwargs):
    return runner.evaluate(project={'id': 'fixture', 'project_path': str(repo)},
        steps=steps or [step()], logs=tmp_path / '.clayrune/logs', target='master', incoming='incoming', **kwargs)


def test_pass_suppresses_raw_output_everywhere(repo, tmp_path, capsys):
    branch(repo)
    secret = 'synthetic-value-never-in-log'
    registered, unregistered = [], []
    report = evaluate(repo, tmp_path, [step(f"import sys; print('{secret}'); print('{secret}', file=sys.stderr)")],
        register=lambda proc, name: registered.append(proc.pid), unregister=unregistered.append)
    assert report['status'] == 'pass', report
    assert report['trusted'] is report['authorizes_merge'] is False
    assert registered == unregistered and len(registered) == 1
    assert secret not in json.dumps(report)
    assert secret not in capsys.readouterr().out
    logs = list((tmp_path / '.clayrune/logs').glob('*.log'))
    assert logs and all(p.read_bytes() == b'' for p in logs)
    job = agent_jobs.public_job(report['results'][0]['job_id'])
    assert secret not in json.dumps(job)
    assert job['command'] == 'dev-harness:unit'
    assert 'tool_digest' in report['results'][0]


@pytest.mark.parametrize('code,status', [(1, 'fail'), (3, 'error')])
def test_exit_contract(repo, tmp_path, code, status):
    branch(repo)
    report = evaluate(repo, tmp_path, [step(f'raise SystemExit({code})')])
    assert report['status'] == status
    assert runner.exit_code(status) == (1 if status == 'fail' else 2)


def test_timeout_and_cancellation_reap_owned_process(repo, tmp_path):
    branch(repo)
    report = evaluate(repo, tmp_path, [step('import time; time.sleep(30)', timeout_seconds=0.1)])
    assert report['status'] == 'error'
    assert report['results'][0]['timed_out'] is True
    cancel = threading.Event()
    report = evaluate(repo, tmp_path, [step('import time; time.sleep(30)')], cancel=cancel,
        register=lambda proc, name: cancel.set())
    assert report['status'] == 'error'
    assert report['results'][0]['cancelled'] is True


@pytest.mark.parametrize('code', ["from pathlib import Path; Path('app.py').write_text('changed')",
                                 "from pathlib import Path; Path('unexpected.py').write_text('new')"])
def test_source_mutation_cannot_pass(repo, tmp_path, code):
    branch(repo)
    report = evaluate(repo, tmp_path, [step(code)])
    assert report['status'] == 'error' and report['code'] == 'source_mutated'
    assert (repo / 'app.py').read_text() == 'value = 1\n'
    assert not (repo / 'unexpected.py').exists()


def test_active_policy_change_invalidates_internal_receipt(repo, tmp_path):
    branch(repo)
    store = HarnessStore(tmp_path / '.clayrune/runtime')
    catalogue = load_catalogue()
    draft = {'profile': 'local-hobby'}
    policy = resolve_policy(draft, catalogue)
    first = store.activate('fixture', expected_revision=0, draft=draft, mode='preview', catalogue=catalogue,
        acknowledged_losses=lost_protections(None, policy, 'preview', catalogue))
    def mutate(proc, name):
        store.activate('fixture', expected_revision=1, draft=draft, mode='off', catalogue=catalogue,
            acknowledged_losses=lost_protections(first['policy'], policy, 'off', catalogue))
    receipt = runner.run_managed(store=store, project={'id': 'fixture', 'project_path': str(repo)},
        steps=[step()], target='master', incoming='incoming', register=mutate, unregister=lambda pid: None)
    assert receipt['status'] == 'error' and receipt['code'] == 'active_snapshot_changed'
    assert receipt['kind'] == 'runner_receipt' and receipt['trusted'] is True
    assert receipt['authorizes_merge'] is False
    assert receipt['policy_digest'] and receipt['active_revision'] == 1
    assert not (store.project_dir('fixture') / 'evidence').exists()
    path = store.project_dir('fixture') / 'runs' / (receipt['run_id'] + '.json')
    assert json.loads(path.read_text()) == receipt


def test_pre_cancel_does_not_spawn(repo, tmp_path):
    branch(repo)
    cancel = threading.Event()
    cancel.set()
    report = evaluate(repo, tmp_path, cancel=cancel)
    assert report['status'] == 'error' and report['code'] == 'cancelled'
    assert not report['results']


@pytest.mark.parametrize('change', [
    {'id': '<unsafe>'}, {'timeout_seconds': 0}, {'timeout_seconds': float('nan')},
    {'argv': 'echo unsafe'}, {'argv': []}, {'argv': ['bad\0program']},
    {'fail_codes': [0]}, {'fail_codes': [True]}, {'configs': ['../escape']},
    {'argv': ['this-tool-does-not-exist-xyz']}, {'unexpected': True},
])
def test_invalid_recipes_fail_before_execution(repo, tmp_path, change):
    branch(repo)
    report = evaluate(repo, tmp_path, [step(**change)])
    assert report['status'] == 'error' and report['results'] == []


def test_config_digest_changes_with_content(repo):
    before = runner.prepare_steps(repo, [step(configs=['app.py'])])
    (repo / 'app.py').write_text('different = True\n')
    after = runner.prepare_steps(repo, [step(configs=['app.py'])])
    assert before[0]['config_digest'] != after[0]['config_digest']


def test_headless_cannot_select_managed_receipt_or_store(repo, tmp_path, capsys):
    report_dir = tmp_path / 'untrusted-reports'
    with pytest.raises(SystemExit) as exc:
        runner.main(['--project', str(repo), '--trusted', '--data-root', str(report_dir)])
    assert exc.value.code == 2
    assert not report_dir.exists()
    assert runner.main(['--project', str(repo)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['kind'] == 'headless_report' and report['trusted'] is False


def test_real_cli_pass_and_no_receipt(repo, tmp_path):
    branch(repo)
    recipes = tmp_path / 'recipes.out'
    recipes.write_text(json.dumps([step()]), encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'tools/dev-harness-check.py'),
        '--project', str(repo), '--target', 'master', '--incoming', 'incoming', '--steps', str(recipes)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['kind'] == 'headless_report' and report['status'] == 'pass'
    assert not report['trusted'] and not report['authorizes_merge']
    assert not (repo / 'data/dev_harness').exists()


def test_dirty_preview_executes_untracked_bytes_without_merge_authority(repo, tmp_path):
    (repo / 'untracked.py').write_text('assert True\n')
    code = "from pathlib import Path; assert Path('untracked.py').read_text() == 'assert True\\n'"
    report = runner.evaluate(project={'id': 'fixture', 'project_path': str(repo)},
        steps=[step(code)], logs=tmp_path / '.clayrune/logs')
    assert report['status'] == 'pass', report
    assert report['source']['kind'] == 'preview'
    assert not report['trusted'] and not report['authorizes_merge']


def test_target_advances_while_running_is_error(repo, tmp_path):
    branch(repo)
    def advance(proc, name):
        git(repo, 'commit', '--allow-empty', '-qm', 'target moved')
    report = evaluate(repo, tmp_path, register=advance)
    assert report['status'] == 'error' and report['code'] == 'inputs_changed'


def test_tool_mutation_is_error(repo, tmp_path, monkeypatch):
    branch(repo)
    real = runner._file_digest
    count = 0
    def changed(path):
        nonlocal count
        if path == Path(sys.executable).resolve():
            count += 1
            if count > 1:
                return '0' * 64
        return real(path)
    monkeypatch.setattr(runner, '_file_digest', changed)
    report = evaluate(repo, tmp_path)
    assert report['status'] == 'error' and report['code'] == 'tool_mutated'


def test_binary_changed_source_is_not_partial_pass(repo, tmp_path):
    branch(repo)
    git(repo, 'checkout', '-q', 'incoming')
    (repo / 'new.bin').write_bytes(b'\0\xff')
    git(repo, 'add', 'new.bin')
    git(repo, 'commit', '-qm', 'binary')
    git(repo, 'checkout', '-q', 'master')
    report = evaluate(repo, tmp_path)
    assert report['status'] == 'error' and report['code'] == 'binary_source_requires_adapter'
    assert not report['results']


def test_registration_failure_prevents_command_pass(repo, tmp_path):
    branch(repo)
    def refuse(proc, name):
        raise RuntimeError('sensitive exception text never reported')
    report = evaluate(repo, tmp_path, register=refuse)
    assert report['status'] == 'error'
    assert 'sensitive exception' not in json.dumps(report)


def test_missing_worktree_exclusion_is_error_without_source_edits(repo, tmp_path):
    branch(repo)
    (repo / '.gitignore').write_text('*.out\n')
    git(repo, 'commit', '-qam', 'no lifecycle exclusion')
    before = (repo / '.gitignore').read_bytes()
    report = evaluate(repo, tmp_path)
    assert report['status'] == 'error'
    assert (repo / '.gitignore').read_bytes() == before


@pytest.mark.parametrize('status,expected', [('pass', 0), ('fail', 1), ('review_required', 1),
    ('error', 2), ('pending', 2), ('unsupported', 2), ('off', 2)])
def test_cli_exit_status_mapping(status, expected):
    assert runner.exit_code(status) == expected


def test_receipt_read_rejects_corruption_missing_and_stale_state(repo, tmp_path):
    branch(repo)
    store = HarnessStore(tmp_path / '.clayrune/runtime')
    catalogue = load_catalogue()
    draft = {'profile': 'local-hobby'}
    policy = resolve_policy(draft, catalogue)
    store.activate('fixture', expected_revision=0, draft=draft, mode='preview', catalogue=catalogue,
        acknowledged_losses=lost_protections(None, policy, 'preview', catalogue))
    receipt = runner.run_managed(store=store, project={'id': 'fixture', 'project_path': str(repo)},
        steps=[step()], target='master', incoming='incoming',
        register=lambda proc, name: None, unregister=lambda pid: None)
    assert receipt['status'] == 'pass'
    assert runner.read_receipt(store, 'fixture', receipt['run_id']) == receipt
    path = store.project_dir('fixture') / 'runs' / (receipt['run_id'] + '.json')
    original = path.read_bytes()
    path.write_text('{truncated', encoding='utf-8')
    with pytest.raises(StoreError):
        runner.read_receipt(store, 'fixture', receipt['run_id'])
    path.write_text(json.dumps({**receipt, 'status': 'fail'}), encoding='utf-8')
    with pytest.raises(StoreError, match='Invalid runner receipt'):
        runner.read_receipt(store, 'fixture', receipt['run_id'])
    path.unlink()
    with pytest.raises(StoreError):
        runner.read_receipt(store, 'fixture', receipt['run_id'])
    path.write_bytes(original)
    store.activate('fixture', expected_revision=1, draft=draft, mode='off', catalogue=catalogue,
        acknowledged_losses=lost_protections(policy, policy, 'off', catalogue))
    with pytest.raises(StoreError, match='Stale runner receipt'):
        runner.read_receipt(store, 'fixture', receipt['run_id'])


def test_arbitrary_freeze_error_text_is_never_exposed(repo, tmp_path):
    branch(repo)
    from mc.dev_harness_diff import FreezeError
    def refuse(proc, name):
        raise FreezeError('sensitive callback text')
    report = evaluate(repo, tmp_path, register=refuse)
    assert report['status'] == 'error' and report['code'] == 'evaluation_error'
    assert 'sensitive callback text' not in json.dumps(report)


def test_cli_escapes_rendering_markers_without_corrupting_json(repo, monkeypatch, capsys):
    marker = '[browser:https://example.invalid]<script>`'
    record = {'status': 'pass', 'kind': 'headless_report', 'trusted': False, 'paths': [marker]}
    monkeypatch.setattr(runner, 'evaluate', lambda **kwargs: record)
    assert runner.main(['--project', str(repo)]) == 0
    output = capsys.readouterr().out
    assert '[browser:' not in output and '<script>' not in output and '`' not in output
    assert json.loads(output) == record


def test_missing_project_is_error_not_an_empty_preview(tmp_path):
    report = runner.evaluate(project={'id': 'fixture', 'project_path': str(tmp_path / 'missing')},
        steps=[], logs=tmp_path / 'logs')
    assert report['status'] == 'error' and report['code'] == 'source_unavailable'


def test_keyboard_interrupt_reaps_child_and_returns_error(repo, tmp_path):
    import _thread
    branch(repo)
    processes, timers = [], []
    def interrupt(proc, name):
        processes.append(proc)
        timer = threading.Timer(0.2, _thread.interrupt_main)
        timers.append(timer)
        timer.start()
    try:
        report = evaluate(repo, tmp_path, [step('import time; time.sleep(30)')], register=interrupt)
    finally:
        for timer in timers:
            timer.join()
    assert report['status'] == 'error' and report['code'] == 'cancelled'
    assert processes and all(proc.poll() is not None for proc in processes)
