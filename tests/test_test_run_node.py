"""Node script adapter with real launcher, OS attribution and isolated HTTP."""
from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from flask import request

from test_test_run_launcher import api, family, invoke
from test_test_run_pytest import bundled_assets, Telemetry

adapter = importlib.import_module('test_run_node')
launcher = importlib.import_module('test_run_launcher')
ROOT = Path(__file__).resolve().parents[1]
NODE_PATH = shutil.which('node')
NODE = NODE_PATH or 'node'
pytestmark = pytest.mark.skipif(NODE_PATH is None, reason='Node executable unavailable')


def script(api, source, name='external smoke.mjs'):
    path = api.tmp / name
    path.write_text(source, encoding='utf-8')
    return str(path)


@pytest.mark.parametrize('source,code', [('console.log("pass");', 0),
    ('process.exit(17);', 17), ('throw new Error("private exception");', 1),
    ('process.on("exit", () => { process.exitCode = 29; });', 29)])
def test_script_results_are_wait_results_not_assertions(api, source, code):
    path = script(api, source)
    done = invoke(api, '--', NODE, path)
    assert done.returncode == code, done.stderr
    parent, children = family(api, 1)
    child = children[0]
    assert parent['unit'] == child['unit'] == 'script'
    assert child['runner'] == 'node-script' and child['display_label'].startswith('Smoke script ')
    assert child['selected_total'] == child['completed'] == 1
    assert child['counts']['pass' if code == 0 else 'fail'] == 1
    assert child['counts']['skip'] == 0 and child['capture'] == 'complete'
    assert child['exit_code'] == parent['exit_code'] == code
    if code:
        assert child['latest_failures'][0]['name'] == 'external smoke.mjs'
    if code == 29:
        assert child['reporter_state'] == 'passed' and child['state'] == 'failed'
    persisted = ''.join(p.read_text(encoding='utf-8') for p in (api.tmp / 'data').rglob('*.json'))
    assert 'private exception' not in persisted


@pytest.mark.parametrize('stop,size,completed', [(True, 2, 2), (False, 3, 3)])
def test_manifest_order_fail_fast_remainder_is_not_run(api, stop, size, completed):
    units = [dict(argv=[NODE, script(api, f'console.log("script-{n}"); process.exit({code});', f'{n}.mjs')])
             for n, code in enumerate([0, 7, 11])]
    manifest = api.tmp / 'scripts.json'
    manifest.write_text(json.dumps(dict(schema=1, stop_on_failure=stop, units=units)), encoding='utf-8')
    done = invoke(api, '--manifest', str(manifest))
    assert done.returncode == 7 and done.stderr == b'', done.stderr
    assert done.stdout.decode().splitlines() == [f'script-{n}' for n in range(size)]
    parent, children = family(api, size)
    assert parent['selected_total'] == 3 and parent['completed'] == completed
    assert parent['counts']['skip'] == 0
    assert all(c['unit'] == 'script' and c['completed'] == 1 for c in children)
    finishes = [e['payload'] for path, body, _ in api.seen if path.endswith('/submit')
                for e in body['events'] if e['type'] == 'run_finished' and e['payload'].get('not_run') == 3 - completed]
    assert any(p['exit_code'] == 7 for p in finishes)


def test_exact_script_args_cwd_env_binary_output_and_no_mc_dependency(api):
    args = ['', 'space here', 'quote"literal', '$HOME;$(no)', 'snowman-\u2603']
    path = script(api, '''
import { createRequire } from 'node:module';
let absent = false;
try { createRequire(import.meta.url).resolve('mc'); } catch { absent = true; }
process.stdout.write(JSON.stringify([process.argv.slice(2), process.cwd(), process.env.KEEP, absent]));
process.stderr.write(Buffer.from([0,255,10,13]));
''')
    done = invoke(api, '--', NODE, path, *args, env=dict(os.environ, KEEP='untouched'))
    assert done.returncode == 0 and done.stderr == bytes([0, 255, 10, 13]), done.stderr
    assert json.loads(done.stdout) == [args, str(api.tmp), 'untouched', True]
    assert family(api, 1)[1][0]['capture'] == 'complete'


def test_commonjs_js_script_keeps_require_semantics(api):
    done = invoke(api, '--', NODE, script(api, 'console.log(typeof require("node:fs").readFileSync)', 'smoke.js'))
    assert done.returncode == 0 and done.stdout == b'function\n' and done.stderr == b'', done.stderr
    assert family(api, 1)[1][0]['completed'] == 1


@pytest.mark.parametrize('content', [None, '{}', '{"schema":1,"stop_on_failure":true,"units":[]}'])
def test_missing_or_wrong_launcher_manifest_runs_nothing(api, content):
    path = api.tmp / 'bad-manifest.json'
    if content is not None:
        path.write_text(content, encoding='utf-8')
    done = invoke(api, '--manifest', str(path))
    assert done.returncode == 2 and done.stdout == b''
    assert b'invalid_invocation_or_manifest' in done.stderr
    assert not api.store.list_runs('fixture')


def test_truncated_sidecar_preserves_script_outcome_and_cleans_up(tmp_path):
    telemetry, warnings = Telemetry(), []
    spec = launcher.Unit([NODE, 'a.mjs'], str(tmp_path), {}, None)
    paths = []
    def execute(prepared):
        path = Path(prepared.env[adapter.ENV_PATH])
        paths.append(path)
        path.write_bytes(b'{"schema":1,')
        return {'exit_code': 31}
    progress = adapter.NodeProgress(spec, telemetry, 'child', warnings.append)
    assert progress.execute(execute)['exit_code'] == 31
    assert warnings == ['node_reporter_finish_missing_progress_incomplete']
    assert all(not p.exists() for p in paths)
    report = telemetry.messages[-1][2]['events'][0]
    assert report['type'] == 'unit_report' and report['payload']['outcome'] == 'fail'


@pytest.mark.parametrize('restricted', ['config', 'incognito', 'sandboxed'])
def test_off_refused_capture_keeps_original_execution(api, restricted):
    path = script(api, '''
if (process.env.CLAYRUNE_SMOKE_PROGRESS_PATH || process.env.CLAYRUNE_SMOKE_PROGRESS_MANIFEST)
  throw new Error('reporter must not be injected');
console.log('original'); process.exit(9);
''')
    if restricted == 'config':
        api.controls['config'] = False
    else:
        api.sessions['owner']['incognito' if restricted == 'incognito' else '_codex_unattended_sandbox'] = True
    done = invoke(api, '--', NODE, path)
    assert done.returncode == 9 and done.stdout == b'original\n', done.stderr
    assert not api.store.list_runs('fixture')
    assert b'node_enrollment_unavailable_progress_incomplete' in done.stderr


def test_missing_script_has_visible_diagnostic_and_real_exit(api):
    done = invoke(api, '--', NODE, str(api.tmp / 'missing.mjs'))
    assert done.returncode == 1
    child = family(api, 1)[1][0]
    assert child['counts']['fail'] == 1 and child['capture'] == 'partial'
    assert 'node_reporter_finish_missing_progress_incomplete' in {d['code'] for d in child['diagnostics']}


def test_timeout_missing_finish_still_reports_actual_script_failure(api):
    path = script(api, 'setInterval(() => {}, 1000)')
    done = invoke(api, '--timeout-seconds', '.3', '--', NODE, path)
    assert done.returncode != 0
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'timeout'
    assert children[0]['counts']['fail'] == 1 and children[0]['capture'] == 'partial'


def test_copied_packaging_layout_runs_external_node_project(api):
    install = api.tmp / 'installation with spaces'
    wanted = {'tools/test-run.py', 'tools/test_run_launcher.py', 'tools/test_run_transport.py',
              'tools/test_run_pytest.py', 'tools/test_run_node.py', 'data/test_reporters/smoke-progress.mjs'}
    assets = [(path, target) for path, target in bundled_assets() if path.as_posix() in wanted]
    assert {path.as_posix() for path, _ in assets} == wanted
    for path, target in assets:
        destination = install / target / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    target = api.tmp / 'unrelated project'
    target.mkdir()
    (target / 'smoke.cjs').write_text('console.log("external");', encoding='utf-8')
    done = subprocess.run([sys.executable, str(install / 'tools' / 'test-run.py'), '--project', 'fixture',
        '--port', str(api.port), '--', NODE, 'smoke.cjs'], cwd=target, capture_output=True, timeout=40)
    assert done.returncode == 0 and done.stdout == b'external\n' and done.stderr == b'', done.stderr
    assert family(api, 1)[1][0]['capture'] == 'complete'


@pytest.mark.parametrize('argv,expected', [(['node', 'a.mjs', '--x'], 'a.mjs'),
    (['node.exe', 'a.cjs'], 'a.cjs'), (['node', '--test', 'a.js'], None),
    (['npm', 'test'], None), (['node', '-e', 'throw 1'], None), ('node a.mjs && node b.mjs', None)])
def test_conservative_node_recognition(argv, expected):
    assert adapter.node_script(argv) == expected


def test_missing_asset_falls_back_once_with_known_script_result(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'ASSET', tmp_path / 'missing.mjs')
    spec = launcher.Unit([NODE, 'exact.mjs', 'args'], str(tmp_path), {'KEEP': 'yes'}, None)
    telemetry, warnings, calls = Telemetry(), [], []
    progress = adapter.NodeProgress(spec, telemetry, 'child', warnings.append)
    observed = progress.execute(lambda prepared: calls.append(prepared) or {'exit_code': 7})
    assert observed['exit_code'] == 7 and calls == [spec]
    assert warnings == ['node_reporter_asset_unavailable']
    assert any(p['events'][0]['type'] == 'unit_report' for _, _, p in telemetry.messages)
    assert not any(a == 'finalize' for a, _, _ in telemetry.messages)


@pytest.mark.parametrize('enrolled', [False, True])
def test_observation_path_failure_cannot_block_original_execution(tmp_path, monkeypatch, enrolled):
    telemetry, warnings, calls = Telemetry(), [], []
    monkeypatch.setattr(telemetry, 'enrolled', lambda key, timeout: enrolled)
    def failed_resolution(self):
        if not enrolled:
            pytest.fail('refused enrollment must not resolve reporter paths')
        raise OSError('fixture path failure')
    monkeypatch.setattr(Path, 'resolve', failed_resolution)
    spec = launcher.Unit([NODE, 'a.mjs'], str(tmp_path), {}, None)
    progress = adapter.NodeProgress(spec, telemetry, 'child', warnings.append)
    assert progress.execute(lambda original: calls.append(original) or {'exit_code': 27})['exit_code'] == 27
    assert calls == [spec]
    assert warnings == ['node_sidecar_unavailable' if enrolled else 'node_enrollment_unavailable_progress_incomplete']


@pytest.mark.parametrize('raw', [b'{}\n', b'x' * 16385,
    b'{"schema":1,"seq":2,"type":"run_started","payload":{}}\n'])
def test_corrupt_sidecar_cannot_finalize_or_invent_assertions(tmp_path, raw):
    telemetry, warnings = Telemetry(), []
    progress = adapter.NodeProgress(launcher.Unit([NODE, 'a.mjs'], str(tmp_path), {}, None),
                                    telemetry, 'child', warnings.append)
    progress.read(io.BytesIO(raw))
    assert progress.broken and warnings == ['node_sidecar_invalid_progress_incomplete']
    assert not any(a == 'finalize' for a, _, _ in telemetry.messages)


@pytest.mark.parametrize('manifest', [None, '{}'])
def test_adapter_surfaces_reporter_manifest_loss_and_preserves_real_exit(tmp_path, manifest):
    path = tmp_path / 'a.mjs'
    path.write_text('process.exit(14);', encoding='utf-8')
    telemetry, warnings = Telemetry(), []
    spec = launcher.Unit([NODE, str(path)], str(tmp_path), dict(os.environ), None)
    def execute(prepared):
        env = dict(prepared.env)
        if manifest is None:
            env.pop(adapter.ENV_MANIFEST)
        else:
            env[adapter.ENV_MANIFEST] = manifest
        done = subprocess.run(prepared.argv, cwd=prepared.cwd, env=env, capture_output=True, timeout=15)
        assert b'node_reporter_manifest_invalid_progress_incomplete' in done.stderr
        return {'exit_code': done.returncode}
    observed = adapter.NodeProgress(spec, telemetry, 'child', warnings.append).execute(execute)
    assert observed['exit_code'] == 14
    assert warnings == ['node_reporter_finish_missing_progress_incomplete']


def test_registration_warning_survives_successful_reporter(api):
    @api.app.before_request
    def refused():
        if request.path.endswith('/register'):
            return {'error': 'fixture'}, 503
    done = invoke(api, '--', NODE, script(api, ''))
    assert done.returncode == 0
    child = family(api, 1)[1][0]
    assert child['reporter_state'] == 'passed' and child['capture'] == 'partial'
