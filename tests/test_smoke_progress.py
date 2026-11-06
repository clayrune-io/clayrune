"""Standalone Node preload protocol, with no project/framework dependency."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / 'data' / 'test_reporters' / 'smoke-progress.mjs'
NODE_PATH = shutil.which('node')
NODE = NODE_PATH or 'node'
pytestmark = pytest.mark.skipif(NODE_PATH is None, reason='Node executable unavailable')


def run_script(tmp_path, source, *, manifest='valid', spool=True):
    script = tmp_path / 'external smoke.mjs'
    script.write_text(source, encoding='utf-8')
    path = tmp_path / 'events.jsonl'
    if spool:
        path.touch()
    env = dict(os.environ, CLAYRUNE_SMOKE_PROGRESS_PATH=str(path))
    env.pop('CLAYRUNE_SMOKE_PROGRESS_OWNER_PID', None)
    env.pop('CLAYRUNE_SMOKE_PROGRESS_MANIFEST', None)
    if manifest == 'valid':
        env['CLAYRUNE_SMOKE_PROGRESS_MANIFEST'] = json.dumps(dict(
            schema=1, script=str(script.resolve()), unit_id=script.name))
    elif manifest is not None:
        env['CLAYRUNE_SMOKE_PROGRESS_MANIFEST'] = manifest
    done = subprocess.run([NODE, '--import', ASSET.as_uri(), str(script)], cwd=tmp_path,
                          env=env, capture_output=True, timeout=15)
    records = [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []
    return done, records


@pytest.mark.parametrize('source,code', [
    ('console.log("ALL PASS is only stdout");', 0),
    ('process.exit(17);', 17),
    ('throw new Error("private exception detail");', 1),
    ('await Promise.reject(new Error("private rejection detail"));', 1),
])
def test_real_script_lifecycle_and_exits(tmp_path, source, code):
    done, records = run_script(tmp_path, source)
    assert done.returncode == code
    assert [r['seq'] for r in records] == [1, 2, 3, 4]
    assert [r['type'] for r in records] == ['run_started', 'collection', 'unit_started', 'run_finished']
    assert records[1]['payload'] == {'selected_total': 1, 'revision': 1}
    assert records[-1]['payload'] == dict(state='passed' if code == 0 else 'failed', exit_code=code, not_run=0)
    assert 'private' not in json.dumps(records) and 'ALL PASS' not in json.dumps(records)
    assert 'node_reporter_' not in done.stderr.decode()


@pytest.mark.parametrize('manifest', [None, '{}', '{', '{"schema":2}',
    '{"schema":1,"script":"wrong.mjs","unit_id":"wrong"}'])
def test_missing_or_wrong_manifest_is_visible_without_changing_script(tmp_path, manifest):
    done, records = run_script(tmp_path, 'console.log("original ran"); process.exit(13);', manifest=manifest)
    assert done.returncode == 13 and done.stdout == b'original ran\n'
    assert b'node_reporter_manifest_invalid_progress_incomplete' in done.stderr
    assert records == []


def test_missing_sidecar_is_not_created_and_exit_preserved(tmp_path):
    done, records = run_script(tmp_path, 'process.exit(19)', spool=False)
    assert done.returncode == 19 and records == []
    assert not (tmp_path / 'events.jsonl').exists()
    assert b'node_reporter_sidecar_unavailable_progress_incomplete' in done.stderr


def test_checkpoint_does_not_count_assertions_and_is_bounded(tmp_path):
    done, records = run_script(tmp_path, f'''
import {{ checkpoint }} from {json.dumps(ASSET.as_uri())};
for (let i=0; i<2000; i++) checkpoint();
process.exit(23);
''')
    assert done.returncode == 23
    assert b'node_reporter_write_failed_progress_incomplete' in done.stderr
    assert (tmp_path / 'events.jsonl').stat().st_size <= 65536
    assert sum(r['type'] == 'collection' for r in records) == 1
    assert not any(r['type'] == 'unit_report' for r in records)


def test_successful_checkpoint_is_only_liveness(tmp_path):
    done, records = run_script(tmp_path, f'''
import {{ checkpoint }} from {json.dumps(ASSET.as_uri())};
checkpoint();
''')
    assert done.returncode == 0 and done.stderr == b''
    assert records[-2]['type'] == 'heartbeat' and records[-2]['payload'] == {'output': False}
    assert records[-1]['type'] == 'run_finished'


def test_worker_and_fork_do_not_append_to_parent_sidecar(tmp_path):
    done, records = run_script(tmp_path, '''
import { Worker } from 'node:worker_threads';
import { fork } from 'node:child_process';
if (!process.argv.includes('child')) {
  await new Promise((resolve, reject) => {
    const w = new Worker('console.log("worker");', { eval: true });
    w.on('exit', resolve); w.on('error', reject);
  });
  await new Promise((resolve, reject) => {
    const child = fork(process.argv[1], ['child']);
    child.on('exit', resolve); child.on('error', reject);
  });
} else console.log('fork');
''')
    assert done.returncode == 0 and done.stderr == b'', done.stderr
    assert [r['seq'] for r in records] == [1, 2, 3, 4]
    assert b'worker' in done.stdout and b'fork' in done.stdout
