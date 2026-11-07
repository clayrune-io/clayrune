"""Real launcher/socket attribution and pytest sidecars, on an isolated API."""
from __future__ import annotations

import ast
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from flask import request

from test_test_run_launcher import api, family, invoke

adapter = importlib.import_module('test_run_pytest')
launcher = importlib.import_module('test_run_launcher')
ROOT = Path(__file__).resolve().parents[1]


def project(api, source='def test_a(): pass\ndef test_b(): assert False', conftest=''):
    (api.tmp / 'test_case.py').write_text(source, encoding='utf-8')
    if conftest:
        (api.tmp / 'conftest.py').write_text(conftest, encoding='utf-8')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    return env


def test_actual_reporter_counts_and_parent_script_units(api):
    done = invoke(api, '--', sys.executable, '-m', 'pytest', '-q', env=project(api))
    assert done.returncode == 1, done.stderr.decode()
    assert done.stderr == b'', done.stderr
    parent, children = family(api, 1)
    child = children[0]
    assert parent['unit'] == 'script' and parent['selected_total'] == parent['completed'] == 1
    assert child['unit'] == 'test' and child['runner'] == 'pytest'
    assert child['selected_total'] == child['completed'] == 2
    assert child['counts']['pass'] == child['counts']['fail'] == 1
    assert child['latest_failures'][0]['name'] == 'test_case.py::test_b'
    assert child['capture'] == 'complete' and child['state'] == 'failed'
    # Capabilities belong to the launcher, never the target environment.
    assert all(p[0].endswith(('/enroll', '/submit', '/finalize', '/revoke', '/register')) for p in api.seen)


def test_late_cleanup_failure_uses_wait_not_reporter_verdict(api):
    env = project(api, 'def test_ok(): pass',
                  'def pytest_unconfigure(config): raise RuntimeError("late cleanup failure")')
    done = invoke(api, '--', sys.executable, '-m', 'pytest', '-q', env=env)
    assert done.returncode != 0
    _, children = family(api, 1)
    child = children[0]
    assert child['reporter_state'] == 'passed' and child['state'] == 'failed'
    assert child['exit_code'] == done.returncode


@pytest.mark.parametrize('source,arguments,code,reason', [
    ('def test_abrupt():\n import os\n os._exit(7)', [], 7, 'pytest_reporter_finish_missing_progress_incomplete'),
    ('def test_ok(): pass', ['--unknown-pytest-option'], 4, 'pytest_exit_usage_error'),
])
def test_abrupt_or_usage_failure_is_explicit_without_fake_completed_tests(api, source, arguments, code, reason):
    done = invoke(api, '--', sys.executable, '-m', 'pytest', '-q', *arguments, env=project(api, source))
    assert done.returncode == code
    _, children = family(api, 1)
    assert children[0]['completed'] == 0 and children[0]['state'] == 'failed'
    assert reason in {d['code'] for d in children[0]['diagnostics']}


def test_actual_pytest_console_executable(api):
    executable = Path(sys.executable).with_name('pytest.exe' if os.name == 'nt' else 'pytest')
    if not executable.is_file():
        pytest.skip('pytest console executable unavailable in this interpreter')
    done = invoke(api, '--', str(executable), '-q', env=project(api, 'def test_ok(): pass'))
    assert done.returncode == 0 and done.stderr == b'', done.stdout + done.stderr
    _, children = family(api, 1)
    assert children[0]['counts']['pass'] == children[0]['completed'] == 1


def test_no_tests_finish_cannot_clear_launcher_capture_warning(api):
    @api.app.before_request
    def failed_registration():
        if request.path.endswith('/register'):
            return {'error': 'fixture registration failure'}, 503
    done = invoke(api, '--', sys.executable, '-m', 'pytest', '-q', env=project(api, ''))
    assert done.returncode == 5
    _, children = family(api, 1)
    assert children[0]['reporter_state'] == 'no_tests'
    assert children[0]['capture'] == 'partial'
    assert 'process_registration_failed' in {d['code'] for d in children[0]['diagnostics']}


def test_failure_visible_while_teardown_and_process_are_still_running(api):
    env = project(api, '''
import pytest, time
from pathlib import Path
@pytest.fixture
def delayed_teardown():
    yield
    deadline = time.monotonic() + 15
    while not Path('release').exists() and time.monotonic() < deadline:
        time.sleep(.02)
def test_fail(delayed_teardown): assert False
''')
    process = subprocess.Popen([sys.executable, str(ROOT / 'tools' / 'test-run.py'), '--project',
        'fixture', '--port', str(api.port), '--', sys.executable, '-m', 'pytest', '-q'],
        cwd=api.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    seen = None
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and process.poll() is None:
            children = [r for r in api.store.list_runs('fixture') if r['parent_run_id']]
            if children and children[0]['failure_total']:
                seen = children[0]
                break
            time.sleep(.02)
        assert seen is not None
        assert seen['state'] == 'running' and seen['completed'] == 0
        assert seen['latest_failures'][0]['name'] == 'test_case.py::test_fail'
        assert process.poll() is None
    finally:
        (api.tmp / 'release').touch()
        stdout, stderr = process.communicate(timeout=25)
    assert process.returncode == 1 and stderr == b'', stdout + stderr
    _, children = family(api, 1)
    assert children[0]['completed'] == children[0]['counts']['fail'] == 1


@pytest.mark.parametrize('restricted', ['config', 'incognito', 'sandboxed'])
def test_off_or_refused_enrollment_keeps_target_environment_clean(api, restricted):
    env = project(api, '''
import os
def test_clean():
    assert 'CLAYRUNE_PYTEST_PROGRESS_PATH' not in os.environ
''')
    env['CLAYRUNE_PYTEST_PROGRESS_PATH'] = str(api.tmp / 'outer-run-events.jsonl')  # inherited from an outer run
    if restricted == 'config':
        api.controls['config'] = False
    else:
        api.sessions['owner']['incognito' if restricted == 'incognito' else '_codex_unattended_sandbox'] = True
    done = invoke(api, '--', sys.executable, '-m', 'pytest', '-q', env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not api.store.list_runs('fixture')


def bundled_assets():
    tree = ast.parse((ROOT / 'installer' / 'build-macos.spec').read_text(encoding='utf-8-sig'))
    datas = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'datas' for t in node.targets))
    return [(Path(*[arg.value for arg in item.elts[0].args]), item.elts[1].value)
            for item in datas.elts if isinstance(item.elts[0], ast.Call)
            and all(isinstance(arg, ast.Constant) for arg in item.elts[0].args)]


def test_packaged_source_layout_runs_from_unrelated_target(api):
    install = api.tmp / 'installation with spaces'
    wanted = {'tools/test-run.py', 'tools/test_run_launcher.py', 'tools/test_run_transport.py',
              'tools/test_run_pytest.py', 'tools/test_run_node.py',
              'data/test_reporters/clayrune_pytest_progress.py'}
    assets = [(path, target) for path, target in bundled_assets() if path.as_posix() in wanted]
    assert {path.as_posix() for path, _ in assets} == wanted
    for path, target in assets:
        destination = install / target / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    target = api.tmp / 'target project'
    target.mkdir()
    (target / 'test_case.py').write_text('def test_ok(): pass', encoding='utf-8')
    env = project(api, 'def test_unused(): assert False')
    env['PYTHONPATH'] = str(target)
    done = subprocess.run([sys.executable, str(install / 'tools' / 'test-run.py'), '--project', 'fixture',
                           '--port', str(api.port), '--cwd', str(target), '--', sys.executable,
                           '-m', 'pytest', '-q'], cwd=target, env=env, capture_output=True, timeout=40)
    assert done.returncode == 0 and done.stderr == b'', done.stdout + done.stderr
    _, children = family(api, 1)
    assert children[0]['completed'] == children[0]['selected_total'] == 1
    assert children[0]['capture'] == 'complete'


@pytest.mark.parametrize('argv,position', [
    (['pytest', '-q'], 1), (['py.test.exe', 'test.py'], 1),
    (['/some/project/python', '-m', 'pytest', 'test.py'], 3),
    (['python', '-I', '-m', 'pytest'], None), (['npm', 'test'], None),
    ('pytest -q && echo done', None),
])
def test_exact_direct_argv_recognition_only(argv, position):
    assert adapter.pytest_position(argv) == position


class Telemetry:
    def __init__(self):
        self.messages = []
    def send(self, action, key, payload):
        self.messages.append((action, key, payload))
    def enrolled(self, key, timeout):
        return True


def test_missing_reporter_runs_original_once(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'ASSET', tmp_path / 'missing.py')
    spec = launcher.Unit(['python', '-m', 'pytest', '-k', 'exact'], str(tmp_path), {'KEEP': 'yes'}, None)
    telemetry, warnings, calls = Telemetry(), [], []
    progress = adapter.PytestProgress(spec, telemetry, 'child', warnings.append)
    observed = progress.execute(lambda prepared: calls.append(prepared) or {'exit_code': 7})
    assert observed['exit_code'] == 7 and calls == [spec]
    assert warnings == ['pytest_reporter_asset_unavailable']


@pytest.mark.parametrize('raw', [b'{"schema":1}\n', b'x' * 16385, b'{"schema":1,',
                               b'{"schema":1,"seq":2,"type":"run_started","payload":{}}\n'])
def test_bad_or_partial_sidecars_cannot_mint_process_evidence(tmp_path, raw):
    import io
    spec = launcher.Unit(['python', '-m', 'pytest'], str(tmp_path), {}, None)
    telemetry, warnings = Telemetry(), []
    progress = adapter.PytestProgress(spec, telemetry, 'child', warnings.append)
    progress.read(io.BytesIO(raw))
    assert progress.broken or progress.partial
    assert not progress.finished
    assert not any(action == 'finalize' for action, _, _ in telemetry.messages)
