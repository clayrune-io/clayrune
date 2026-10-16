"""Per-tool fixtures and actual M3 executor boundaries, never installed by tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mc import dev_harness_runner as runner
from mc.dev_harness_checks.execution import version_matches
from mc.dev_harness_checks.recipes import RECIPE_IDS, closure, load_recipe, make_step
from mc.dev_harness_checks.reports import validate
from mc.dev_harness_catalogue import load_catalogue
from mc.dev_harness_policy import lost_protections, resolve_policy
from mc.dev_harness_store import HarnessStore
from test_dev_harness_diff import branch, git, repo

FIXTURES = Path(__file__).parent / 'fixtures/dev_harness'
NATIVE_IDS = tuple(n for n in RECIPE_IDS if not n.startswith('coverage-'))


@pytest.mark.parametrize('identity', NATIVE_IDS)
@pytest.mark.parametrize('case', [0, 1, 2])
def test_per_tool_positive_finding_crash(identity, case):
    recipe = load_recipe(identity)
    row = json.loads((FIXTURES / (identity + '.json')).read_text())[case]
    result = validate(recipe['report_contract'], json.dumps(row['report']).encode(), row['exit_code'], recipe['fail_codes'])
    assert result['status'] == row['status']
    assert not any(isinstance(v, (list, dict)) for v in result.values())


@pytest.mark.parametrize('identity', NATIVE_IDS)
def test_unknown_exit_cannot_pass(identity):
    recipe = load_recipe(identity)
    assert validate(recipe['report_contract'], b'[]', 73, [1])['status'] == 'error'


@pytest.mark.parametrize('identity', [n for n in NATIVE_IDS if load_recipe(n)['report_contract'] != 'exit-only-v1'])
@pytest.mark.parametrize('raw', [b'', b'null', b'{}', b'{"results":[],"results":[]}', b'not JSON'])
def test_empty_unknown_corrupt_report_is_error(identity, raw):
    assert validate(load_recipe(identity)['report_contract'], raw, 0, [1])['status'] == 'error'


@pytest.mark.parametrize('identity', ['ruff','eslint','gitleaks','bandit','semgrep','pip-audit','npm-audit','osv-scanner'])
def test_zero_exit_findings_and_failure_without_findings_are_error(identity):
    recipe = load_recipe(identity)
    positive, finding, _ = json.loads((FIXTURES/(identity+'.json')).read_text())
    assert validate(recipe['report_contract'], json.dumps(finding['report']).encode(), 0, [1])['status'] == 'error'
    assert validate(recipe['report_contract'], json.dumps(positive['report']).encode(), 1, [1])['status'] == 'error'


def test_unknown_licence_requires_review():
    raw = b'{"schema_version":1,"packages":[{"decision":"unknown"}]}'
    assert validate('licence-policy-v1', raw, 0, [1])['status'] == 'review_required'
    assert validate('licence-policy-v1', raw, 1, [1])['status'] == 'review_required'


@pytest.mark.parametrize('value', ['8.28.01', '8.28.0-beta', '8.28.0+dev', '8.28.0 1.2.3', 'unknown'])
def test_version_is_exact(value):
    assert not version_matches(value.encode(), '8.28.0')
    assert version_matches(b'gitleaks version 8.28.0\n', '8.28.0')


def fake_step(tmp_path, identity='gitleaks', payload=None, code=0, version=None):
    recipe = load_recipe(identity)
    folder = tmp_path / '.clayrune/native-tools'
    folder.mkdir(parents=True, exist_ok=True)
    tool = folder / (identity + '.py')
    if payload is None:
        payload = json.loads((FIXTURES/(identity+'.json')).read_text())[0]['report']
    tool.write_text('import sys\n' +
        'if "--version" in sys.argv:\n print(' + repr(version or recipe['tool_version']) + '); sys.exit(0)\n' +
        'print("native-private-stderr", file=sys.stderr)\n' +
        'print(' + repr(json.dumps(payload)) + ')\nraise SystemExit(' + str(code) + ')\n', encoding='utf-8')
    configs = []
    if identity == 'semgrep':
        rules = tmp_path / 'rules.yml'
        rules.write_text('rules: []\n')
        configs = ['rules.yml']
    return make_step(identity, launcher=[sys.executable, str(tool)],
                     targets=['app.py'] if '{targets}' in recipe['args'] else [], configs=configs,
                     tool_roots=[str(tool)], command_args=['check'] if recipe['configured_command'] else None)


@pytest.mark.parametrize('identity', NATIVE_IDS)
@pytest.mark.parametrize('case', [0, 1, 2])
def test_real_frozen_native_execution_and_suppression(repo, tmp_path, identity, case, capsys):
    branch(repo)
    row = json.loads((FIXTURES/(identity+'.json')).read_text())[case]
    step = fake_step(tmp_path, identity=identity, payload=row['report'], code=row['exit_code'])
    if identity == 'semgrep':
        git(repo, 'add', 'rules.yml')
        git(repo, 'commit', '-qm', 'approved rules')
    pids, done = [], []
    report = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        logs=tmp_path/'.clayrune/logs', target='master', incoming='incoming',
        register=lambda proc, name:pids.append(proc.pid), unregister=done.append)
    assert report['status'] == row['status'], report
    assert len(pids) == 2 and pids == done
    assert all(p.read_bytes() == b'' for p in (tmp_path/'.clayrune/logs').glob('*.log'))
    captured = capsys.readouterr()
    assert 'native-private-stderr' not in captured.out + captured.err
    assert json.dumps(row['report']) not in captured.out + captured.err
    assert '_stdout' not in json.dumps(report)
    assert 'REDACTED' not in json.dumps(report)


def test_unredacted_gitleaks_is_error_and_never_persisted(repo, tmp_path):
    branch(repo)
    payload = json.loads((FIXTURES/'gitleaks.json').read_text())[1]['report']
    payload[0]['Secret'] = ''.join(('synthetic', '-sensitive-value'))
    step = fake_step(tmp_path, payload=payload, code=1)
    report = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        logs=tmp_path/'.clayrune/logs', target='master', incoming='incoming')
    assert report['status'] == 'error'
    assert payload[0]['Secret'] not in json.dumps(report)
    assert all(p.read_bytes() == b'' for p in (tmp_path/'.clayrune/logs').glob('*.log'))


def test_wrong_version_prevents_scan(repo, tmp_path):
    branch(repo)
    report = runner.evaluate(project={'id':'fixture','project_path':str(repo)},
        steps=[fake_step(tmp_path, version='99.1.1')], logs=tmp_path/'.clayrune/logs',
        target='master', incoming='incoming')
    assert report['status'] == 'error'
    assert report['results'][0]['code'] == 'unsupported_tool_version'
    assert len(list((tmp_path/'.clayrune/logs').glob('*.log'))) == 1


def test_tool_closure_addition_and_change_invalidate(tmp_path):
    root = tmp_path/'installed'
    root.mkdir()
    (root/'plugin.py').write_text('a=1')
    first = closure([str(root)])
    (root/'extra.py').write_text('a=2')
    assert closure([str(root)]) != first
    (root/'plugin.py').write_text('a=3')
    assert closure([str(root)]) != first


def test_no_implicit_installer_or_secret_report_file_in_data():
    for identity in RECIPE_IDS:
        recipe = load_recipe(identity)
        assert recipe['installation'] == 'already_installed_only'
        assert not {'npx', 'install', 'fix', '--output', '--report-path=report.json'} & set(recipe['args'])
    assert '--redact=100' in load_recipe('gitleaks')['args']
    assert '--report-path=-' in load_recipe('gitleaks')['args']


def test_native_missing_tool_is_error(repo, tmp_path):
    branch(repo)
    step = fake_step(tmp_path)
    step['argv'][0] = step['native']['launcher'][0] = 'tool-not-installed-dhm4'
    report = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        logs=tmp_path/'.clayrune/logs',target='master',incoming='incoming')
    assert report['status'] == 'error' and report['code'] == 'tool_unavailable'
    assert not report['results']


def test_native_overflow_cannot_pass_or_fill_job_log(repo, tmp_path):
    branch(repo)
    step = fake_step(tmp_path)
    path = Path(step['native']['launcher'][-1])
    path.write_text('import sys\nif "--version" in sys.argv:\n print("8.28.0"); sys.exit(0)\n'
                    'sys.stdout.write("x"*(8*1024*1024+1))\n')
    report = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        logs=tmp_path/'.clayrune/logs',target='master',incoming='incoming')
    assert report['status'] == 'error'
    assert all(p.read_bytes() == b'' for p in (tmp_path/'.clayrune/logs').glob('*.log'))


def test_managed_unknown_licence_remains_readable_review_required(repo, tmp_path):
    branch(repo)
    step = fake_step(tmp_path,'licence',payload={'schema_version':1,'packages':[{'decision':'unknown'}]})
    store = HarnessStore(tmp_path/'.clayrune/runtime')
    catalogue = load_catalogue()
    policy = resolve_policy({'profile':'local-hobby'}, catalogue)
    store.activate('fixture', expected_revision=0,draft={'profile':'local-hobby'}, mode='preview',catalogue=catalogue,
        acknowledged_losses=lost_protections(None,policy,'preview',catalogue))
    receipt = runner.run_managed(store=store, project={'id':'fixture','project_path':str(repo)}, steps=[step],
        target='master',incoming='incoming',register=lambda p,n:None,unregister=lambda pid:None)
    assert receipt['status'] == 'review_required'
    assert runner.read_receipt(store,'fixture',receipt['run_id']) == receipt
    assert not receipt['authorizes_merge']
    assert 'unknown' not in json.dumps(receipt)


def test_native_config_creation_after_scan_is_error(repo, tmp_path):
    (repo/'.gitignore').write_text('.clayrune/\nruff.toml\n')
    git(repo,'add','.gitignore')
    git(repo,'commit','-qm','ignored native configuration')
    branch(repo)
    step = fake_step(tmp_path,'ruff')
    tool = Path(step['native']['launcher'][-1])
    tool.write_text(tool.read_text()+'\n')
    # Modify only the ignored disposable configuration during the scan.
    text = tool.read_text().replace('print(\'[]\')', 'from pathlib import Path; Path("ruff.toml").write_text("line-length=80\\n"); print(\'[]\')')
    tool.write_text(text)
    report = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        logs=tmp_path/'.clayrune/logs',target='master',incoming='incoming')
    assert report['status'] == 'error' and report['code'] == 'native_inputs_changed'
