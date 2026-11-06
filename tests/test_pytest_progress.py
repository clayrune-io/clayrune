"""Real unrelated pytest projects: no mc import or target-project edits."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mc.test_run_store import TestRunStore

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / 'data' / 'test_reporters' / 'clayrune_pytest_progress.py'
spec = importlib.util.spec_from_file_location('progress_asset', ASSET)
assert spec and spec.loader
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def records(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def snapshot(tmp_path, events):
    store = TestRunStore(tmp_path / 'store')
    run = store.create('fixture', observed_at=1, source='launcher', runner='pytest', unit='test')
    for record in events:
        run = store.apply('fixture', run['run_id'], dict(
            schema=1, run_id=run['run_id'], producer_id='fixture', producer_seq=record['seq'],
            observed_at=record['seq'] + 1, type=record['type'], payload=record['payload']))
    return run


def run_project(tmp_path, source='', conftest='', args=(), env_changes=None, executable=sys.executable):
    folder = tmp_path / 'unrelated project'
    folder.mkdir()
    if source:
        (folder / 'test_case.py').write_text(source, encoding='utf-8')
    if conftest:
        (folder / 'conftest.py').write_text(conftest, encoding='utf-8')
    sidecar = tmp_path / 'events.jsonl'
    sidecar.touch()
    env = dict(os.environ, PYTHONPATH=str(ASSET.parent), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               CLAYRUNE_PYTEST_PROGRESS_PATH=str(sidecar))
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    env.update(env_changes or {})
    done = subprocess.run([executable, '-m', 'pytest', '-p', 'clayrune_pytest_progress',
                           '-q', *args], cwd=folder, env=env, capture_output=True, timeout=20)
    events = records(sidecar)
    assert len(sidecar.read_bytes()) <= plugin.MAX_SPOOL_BYTES
    assert all(len(line) + 1 <= plugin.MAX_RECORD_BYTES for line in sidecar.read_bytes().splitlines())
    return done, events, snapshot(tmp_path, events)


def test_serial_all_outcomes_and_phase_precedence(tmp_path):
    done, events, run = run_project(tmp_path, '''
import pytest
def test_pass(): pass
def test_fail(): assert False
@pytest.mark.skip(reason='skip detail stays out of telemetry')
def test_skip(): pass
@pytest.fixture
def broken(): raise RuntimeError('private setup text')
def test_setup(broken): pass
@pytest.fixture
def teardown():
    yield
    raise RuntimeError('private teardown text')
def test_teardown(teardown): pass
@pytest.mark.xfail
def test_xfail(): assert False
@pytest.mark.xfail
def test_xpass(): pass
@pytest.mark.xfail(strict=True)
def test_strict_xpass(): pass
''')
    assert done.returncode == 1, done.stderr
    assert run['selected_total'] == run['completed'] == 8
    assert run['counts'] == {'pass': 1, 'fail': 2, 'skip': 1, 'error': 2, 'xfail': 1, 'xpass': 1}
    assert run['capture'] == 'complete'
    assert run['total_recalculated'] is False
    assert run['state'] == 'running' and run['reporter_state'] == 'failed'
    assert run['failure_total'] == 4
    assert 'private' not in json.dumps(events)
    call = next(e for e in events if e['type'] == 'unit_report' and
                e['payload']['unit_id'].endswith('::test_fail') and e['payload']['phase'] == 'call')
    assert call['payload']['final'] is False and call['payload']['outcome'] == 'fail'
    before_teardown = snapshot(tmp_path / 'before', events[:events.index(call) + 1])
    assert before_teardown['completed'] == 1 and before_teardown['failure_total'] == 1


@pytest.mark.parametrize('source,args,code,total,completed,errors,state,reason', [
    ('', (), 5, 0, 0, 0, 'no_tests', 'pytest_exit_no_tests'),
    ('raise RuntimeError("collection detail")', (), 2, 0, 0, 1, 'cancelled', 'pytest_exit_interrupted'),
    ('def test_interrupt(): raise KeyboardInterrupt()\ndef test_later(): pass', (),
     2, 2, 0, 0, 'cancelled', 'pytest_exit_interrupted'),
    ('def test_a(): assert False\ndef test_b(): pass\ndef test_c(): pass', ('-x',),
     1, 3, 1, 0, 'failed', None),
])
def test_exit_reasons_and_incomplete_runs(tmp_path, source, args, code, total, completed, errors, state, reason):
    done, events, run = run_project(tmp_path, source, args=args)
    assert done.returncode == code, done.stderr
    assert (run['selected_total'], run['completed'], run['collection_errors']) == (total, completed, errors)
    assert run['reporter_state'] == state and run['state'] == 'running'
    if reason:
        assert events[-1]['payload']['exit_code'] == code
    if code == 5:
        assert run['capture'] == 'complete'
    assert events[-1]['payload'].get('not_run') == total - completed


def test_deselection_and_collection_skip_are_not_completed_tests(tmp_path):
    done, _, run = run_project(tmp_path,
        'def test_selected(): pass\ndef test_other(): pass', args=('-k', 'selected'))
    assert done.returncode == 0
    assert run['selected_total'] == run['completed'] == run['deselected'] == 1


def test_collection_skip_is_separate(tmp_path):
    done, _, run = run_project(tmp_path, 'import pytest\npytest.skip("module skip", allow_module_level=True)')
    assert done.returncode == 5
    assert run['completed'] == run['collection_errors'] == 0
    assert not any(run['counts'].values())
    assert 'pytest_collection_skipped' in {d['code'] for d in run['diagnostics']}


def test_total_revision_after_runtime_item_removal(tmp_path):
    done, events, run = run_project(tmp_path,
        'def test_a(): pass\ndef test_b(): pass', conftest='''
def pytest_runtest_setup(item):
    if item.name == 'test_a': item.session.items.pop()
''')
    assert done.returncode == 0, done.stderr
    assert run['total_recalculated'] and run['selected_total'] == run['completed'] == 1
    assert [e['payload']['selected_total'] for e in events if e['type'] == 'collection'] == [2, 1]


def test_reporter_write_failure_keeps_real_pytest_result(tmp_path):
    done, events, _ = run_project(tmp_path, 'def test_ok(): pass', env_changes={
        'CLAYRUNE_PYTEST_PROGRESS_PATH': str(tmp_path / 'missing' / 'no-file')})
    assert done.returncode == 0 and not events
    assert b'pytest_reporter_write_failed_progress_incomplete' in done.stderr
    assert not (tmp_path / 'missing').exists()


def test_late_cleanup_error_does_not_rewrite_reporter_success(tmp_path):
    done, _, run = run_project(tmp_path, 'def test_ok(): pass', conftest='''
def pytest_unconfigure(config): raise RuntimeError('late cleanup failure')
''')
    assert done.returncode != 0
    assert run['reporter_state'] == 'passed' and run['state'] == 'running'


def test_oversized_name_is_explicitly_aliased_without_silent_truncation(tmp_path):
    done, _, run = run_project(tmp_path, 'import pytest\n@pytest.mark.parametrize("value", [1], ids=["x"*2000])\ndef test_long(value): assert False')
    assert done.returncode == 1 and run['completed'] == 1
    assert run['latest_failures'][0]['name'].startswith('oversized-nodeid:')
    assert 'pytest_name_oversized_detail_unavailable' in {d['code'] for d in run['diagnostics']}


@pytest.mark.parametrize('options,code', [
    (dict(numprocesses=2), 'pytest_xdist_not_measured'),
    (dict(reruns=1), 'pytest_retries_not_measured'),
])
def test_unproved_modes_withhold_counts_but_retain_failure_phases(tmp_path, options, code):
    path = tmp_path / 'events'
    path.touch()
    progress = plugin.Progress(SimpleNamespace(option=SimpleNamespace(**options)), str(path))
    session = SimpleNamespace(items=[1, 2])
    progress.pytest_sessionstart(session)
    progress.pytest_collection_finish(session)
    progress.pytest_runtest_logstart('test_case.py::test_fail')
    for phase, failed in [('setup', False), ('call', True), ('teardown', False)]:
        progress.pytest_runtest_logreport(SimpleNamespace(nodeid='test_case.py::test_fail',
            when=phase, outcome='failed' if failed else 'passed', failed=failed,
            skipped=False, passed=not failed))
    progress.pytest_sessionfinish(session, 1)
    progress.pytest_unconfigure()
    run = snapshot(tmp_path, records(path))
    assert run['selected_total'] is None and run['completed'] == 0
    assert run['capture'] == 'partial' and run['failure_total'] == 1
    assert code in {d['code'] for d in run['diagnostics']}


def test_inherited_xdist_worker_never_opens_shared_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv(plugin.ENV_PATH, str(tmp_path / 'absent'))
    calls = []
    config = SimpleNamespace(workerinput={}, pluginmanager=SimpleNamespace(register=lambda *a: calls.append(a)))
    plugin.pytest_configure(config)
    assert calls == [] and not (tmp_path / 'absent').exists()


def test_plugin_is_stdlib_only():
    import ast
    tree = ast.parse(ASSET.read_text(encoding='utf-8'))
    imports = {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports <= sys.stdlib_module_names | {'__future__'}


def test_separate_target_interpreter_with_only_pytest_dependencies(tmp_path):
    # A genuine second venv, with no mc installation, global site packages or
    # network dependency install. Reuse only files from already installed
    # pytest/dependency distributions, including their package metadata.
    import importlib.metadata
    import venv
    from packaging.requirements import Requirement
    target = tmp_path / 'isolated interpreter'
    venv.EnvBuilder(with_pip=False).create(target)
    packages = target / ('Lib/site-packages' if os.name == 'nt' else
                         f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
    packages.mkdir(parents=True, exist_ok=True)
    pending, copied = ['pytest'], set()
    while pending:
        name = pending.pop()
        if name in copied:
            continue
        copied.add(name)
        dist = importlib.metadata.distribution(name)
        for dependency in dist.requires or []:
            requirement = Requirement(dependency)
            if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
                pending.append(requirement.name)
        for entry in dist.files or []:
            if '..' in entry.parts or entry.is_absolute():
                continue
            source, destination = Path(dist.locate_file(entry)), packages / entry
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
    executable = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    done, _, run = run_project(tmp_path, f'''
import sys, importlib.util
def test_external():
    assert sys.prefix == {str(target)!r}
    assert importlib.util.find_spec('mc') is None
''', executable=str(executable))
    assert done.returncode == 0, done.stdout + done.stderr
    assert run['selected_total'] == run['completed'] == run['counts']['pass'] == 1
