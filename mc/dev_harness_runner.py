"""Frozen native-command execution; bounded summaries, internal receipts (DH-M3/4).

Ordinary output goes to DEVNULL; native adapters validate bounded stdout in memory.
The existing managed jobs never persist scanner text.
An exit-code pass proves the named command only, never an entire practice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mc import agent_jobs
from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import (ERROR_CODES, FreezeError, candidate_worktree, freeze_candidate,
                                 git_bytes, manifest, preview, preview_worktree, safe_path)
from mc.dev_harness_store import HarnessStore, StoreError

RUNNER_VERSION = 'dh-m4-v1'
MAX_STEPS = 64
MAX_CONFIG_BYTES = 4 * 1024 * 1024
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def prepare_steps(root: Path, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate recipes before any execution; freeze argv/tool/config identities.

    Approved argv is internal data, never a shell command. Config files are
    explicit; M4 owns tool-specific version/report/fixture validation.
    """
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise ValueError('invalid_steps')
    prepared, identities = [], set()
    for step in steps:
        if not isinstance(step, dict) or set(step) not in (
                {'id', 'argv', 'timeout_seconds', 'fail_codes', 'configs'},
                {'id', 'argv', 'timeout_seconds', 'fail_codes', 'configs', 'native'}):
            raise ValueError('invalid_step_fields')
        identity, argv = step['id'], step['argv']
        if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in identities:
            raise ValueError('invalid_step_identity')
        identities.add(identity)
        if (not isinstance(argv, list) or not argv or len(argv) > 256
                or any(not isinstance(v, str) or '\0' in v or len(v) > 8192 for v in argv)
                or not argv[0]):
            raise ValueError('invalid_argv')
        seconds = step['timeout_seconds']
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 7200:
            raise ValueError('invalid_timeout')
        codes = step['fail_codes']
        if (not isinstance(codes, list) or any(type(c) is not int or not 0 < c <= 255 for c in codes)
                or len(codes) != len(set(codes))):
            raise ValueError('invalid_exit_contract')
        configs = step['configs']
        if 'native' in step and isinstance(configs, list) and all(isinstance(v, str) for v in configs):
            from mc.dev_harness_checks.recipes import config_closure
            configs = config_closure(root, step)
            step = {**step, 'configs': configs}
        if not isinstance(configs, list) or any(not isinstance(v, str) for v in configs) or len(configs) > 128:
            raise ValueError('invalid_configs')
        config_digests = {}
        for name in configs:
            path = safe_path(root, name)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
                raise ValueError('invalid_config_file')
            config_digests[name] = _file_digest(path)
        executable = (str(root / argv[0]) if '/' in argv[0] or '\\' in argv[0]
                      else shutil.which(argv[0]))
        if executable is None:
            raise ValueError('tool_unavailable')
        executable_path = Path(executable).resolve()
        # Relative argv executables are resolved against the candidate, not the server.
        if '/' in argv[0] or '\\' in argv[0]:
            executable_path = (root / argv[0]).resolve()
        if not executable_path.is_file():
            raise ValueError('tool_unavailable')
        extra = {}
        if 'native' in step:
            from mc.dev_harness_checks.recipes import prepare
            extra['native_prepared'] = prepare(root, step)
        prepared.append({**step, **extra, 'argv': [str(executable_path), *argv[1:]],
                         'argv_digest': digest(argv), 'tool_digest': _file_digest(executable_path),
                         'config_digest': digest(config_digests), 'config_digests': config_digests})
    return prepared


def exit_code(status: str) -> int:
    return 0 if status == 'pass' else 1 if status in ('fail', 'review_required') else 2


def _execute(step: dict[str, Any], root: Path, logs: Path, project_id: str,
             session_id: str, cancel: threading.Event,
             register: Callable[[Any, str], None], unregister: Callable[[int], None]) -> dict[str, Any]:
    """Use the existing watcher, process ledger and timeout implementation."""
    from mc.blueprints.secrets_routes import _kill_process_tree, _win_job_object_for
    finished = threading.Event()
    outcome: dict[str, Any] = {}
    holder: list[Any] = [None]
    capture: list[Any] = [None]

    def complete(result: dict[str, Any]) -> None:
        outcome.update(result)
        finished.set()

    def kill(proc) -> None:
        _kill_process_tree(proc, holder[0])

    def spawn(argv, **kwargs):
        if step.get('_capture_stdout'):
            kwargs['stdout'] = subprocess.PIPE
        if os.name != 'nt':
            kwargs['start_new_session'] = True
        else:
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(argv, **kwargs)
        try:
            register(proc, step['id'])
            if os.name == 'nt':
                holder[0] = _win_job_object_for(proc.pid)
            if step.get('_capture_stdout'):
                from mc.dev_harness_checks.output import Capture
                capture[0] = Capture(proc.stdout)
        except BaseException:
            kill(proc)
            proc.wait(timeout=10)
            raise
        return proc

    job = agent_jobs.start_job(project_id=project_id, session_id=session_id,
        command='dev-harness:' + step['id'], cwd=str(root),
        timeout_minutes=step['timeout_seconds'] / 60, log_dir=logs,
        direct_argv=step['argv'], popen=spawn, on_complete=complete, kill_fn=kill)
    cancelled = False
    try:
        while not finished.wait(0.05):
            if cancel.is_set() and not cancelled:
                cancelled = True
                kill(job['proc'])
        if job['proc'].poll() is None:
            raise RuntimeError('process_not_reaped')
        cancelled = cancelled or cancel.is_set()
        code = outcome['exit_code']
        status = ('error' if cancelled or outcome['timed_out'] else
                  'pass' if code == 0 else 'fail' if code in step['fail_codes'] else 'error')
        result = {'id': step['id'], 'job_id': job['job_id'], 'status': status,
                'exit_code': code, 'duration_seconds': round(outcome['duration_s'], 3),
                'cancelled': cancelled, 'timed_out': outcome['timed_out'],
                'argv_digest': step['argv_digest'], 'tool_digest': step['tool_digest'],
                'config_digest': step['config_digest'], 'coverage': 'native_command_only'}
        if capture[0] is not None:
            result['_stdout'] = capture[0].take()
        return result
    finally:
        if not finished.is_set():
            # Ctrl+C/other interrupted waits must not orphan the argv child.
            kill(job['proc'])
            if not finished.wait(10):
                raise RuntimeError('process_not_reaped')
        # Clean up descendants even if a parent exited normally after spawning them.
        if os.name == 'nt' and holder[0]:
            import ctypes
            from ctypes import wintypes
            ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            # The parent is reaped; act on our persistent job HANDLE only,
            # never on a numeric PID that the OS could already have reused.
            ctypes.windll.kernel32.TerminateJobObject(holder[0], 1)
            ctypes.windll.kernel32.CloseHandle(holder[0])
        unregister(job['pid'])


def evaluate(*, project: dict[str, Any], steps: list[dict[str, Any]], logs: Path,
             target: str | None = None, incoming: str | None = None,
             cancel: threading.Event | None = None,
             register: Callable[[Any, str], None] = lambda proc, name: None,
             unregister: Callable[[int], None] = lambda pid: None,
             _frozen: dict[str, Any] | None = None,
             _validate_workspace: Callable[[Path], None] | None = None) -> dict[str, Any]:
    """Report-only evaluator, also used inside existing session-managed jobs.

    Commands run in disposable integration or dirty-preview checkouts. Unborn
    and non-Git projects have file previews without merge authority.
    """
    run_id = uuid.uuid4().hex
    report: dict[str, Any] = {'version': 1, 'kind': 'headless_report', 'trusted': False,
        'authorizes_merge': False, 'run_id': run_id, 'project_id': project['id'],
        'checker_digest': digest({'version': RUNNER_VERSION,
            'runner': _file_digest(Path(__file__)),
            'diff': _file_digest(Path(__file__).with_name('dev_harness_diff.py')),
            'native': {p.name: _file_digest(p) for p in sorted(
                Path(__file__).with_name('dev_harness_checks').glob('*.py'))}}),
        'created_at': datetime.now(timezone.utc).isoformat(), 'results': [], 'status': 'error'}
    cancelled = cancel if cancel is not None else threading.Event()
    repo = Path(project['project_path']).resolve()
    try:
        if (target is None) != (incoming is None):
            raise ValueError('both_refs_required')
        if _frozen is not None:
            frozen = _frozen
            _revalidate_source(repo, frozen, target, incoming)
            workspace = (preview_worktree(project, frozen) if frozen['kind'] == 'preview'
                         else candidate_worktree(project, frozen))
        elif target is None or incoming is None:
            frozen = preview(repo)
            if not steps or not frozen['head']:
                report.update(source=frozen, status='unsupported', code='manifest_only')
                return report
            workspace = preview_worktree(project, frozen)
        else:
            frozen = freeze_candidate(repo, target, incoming)
            workspace = candidate_worktree(project, frozen)
        report['source'] = frozen
        # The shared lifecycle's location must already be excluded, without
        # changing the source's .gitignore after freezing it.
        git_bytes(repo, ['check-ignore', '-q', '.clayrune/agents/dh_probe'])
        original = manifest(repo)
        with workspace as root:
            if _validate_workspace is not None:
                _validate_workspace(root)
            before = manifest(root)
            report['source_manifest_digest'] = before['digest']
            changed_names = ([v['new_path'] for v in frozen['integration_changes']]
                             if frozen['kind'] in ('integration', 'pending_merge') else list(before['files']))
            if any(before['files'].get(n, {}).get('kind') == 'binary' for n in changed_names):
                raise FreezeError('binary_source_requires_adapter')
            prepared = prepare_steps(root, steps)
            report['steps_digest'] = digest([{k: v for k, v in s.items() if k != 'argv'} for s in prepared])
            for step in prepared:
                if cancelled.is_set():
                    report['code'] = 'cancelled'
                    break
                if 'native_prepared' in step:
                    from mc.dev_harness_checks.execution import execute
                    # M4's candidate adapter consumes the same B/H/M/T for an
                    # exact pending merge; receipts retain the pending descriptor.
                    adapter_source = {**frozen, 'kind': 'integration'} if frozen['kind'] == 'pending_merge' else frozen
                    try:
                        result = execute(step, root, adapter_source, lambda command: _execute(
                            command, root, logs, project['id'], run_id, cancelled, register, unregister))
                    except ValueError as exc:
                        if str(exc) not in ('coverage_classification_required', 'empty_diff_coverage'):
                            raise
                        # No complete command measurement exists. Keep the
                        # warning observable without inventing version/exit/pin
                        # provenance that could satisfy activation evidence.
                        result = {'id': step['id'], 'status': 'error', 'code': 'empty_diff_coverage'}
                else:
                    result = _execute(step, root, logs, project['id'], run_id, cancelled, register, unregister)
                report['results'].append(result)
                if manifest(root) != before:
                    report['code'] = 'source_mutated'
                    break
                if _file_digest(Path(step['argv'][0])) != step['tool_digest']:
                    report['code'] = 'tool_mutated'
                    break
                if result['status'] != 'pass':
                    break
            # A later verifier can mutate an earlier verifier's external tool
            # or ignored config; recheck the complete frozen set at the end.
            for step in prepared:
                if _file_digest(Path(step['argv'][0])) != step['tool_digest']:
                    report['code'] = 'tool_mutated'
                if any(_file_digest(safe_path(root, name)) != expected
                       for name, expected in step['config_digests'].items()):
                    report['code'] = 'config_mutated'
                if 'native_prepared' in step:
                    from mc.dev_harness_checks.recipes import closure, config_closure, load_recipe
                    native = step['native_prepared']
                    if (closure(native['tool_roots']) != native['closure_digest']
                            or digest(load_recipe(native['recipe'])) != native['recipe_digest']
                            or config_closure(root, step) != step['configs']
                            or digest(dict(os.environ)) != native['environment_digest']):
                        report['code'] = 'native_inputs_changed'
            current = _revalidate_source(repo, frozen, target, incoming)
            if manifest(repo) != original or current != frozen:
                report['code'] = 'inputs_changed'
            if cancelled.is_set():
                report['code'] = 'cancelled'
            if 'code' not in report:
                report['status'] = ('pass' if len(report['results']) == len(prepared)
                    and all(r['status'] == 'pass' for r in report['results']) else
                    report['results'][-1]['status'] if report['results']
                    and report['results'][-1]['status'] in ('fail', 'review_required') else 'error')
    except (Exception, KeyboardInterrupt) as exc:
        # Only our fixed vocabulary enters reports. No exception strings, paths,
        # argv, scanner output or traceback can reach managed-job activity.
        safe_codes = {'invalid_steps', 'invalid_step_fields', 'invalid_step_identity', 'invalid_argv',
            'invalid_timeout', 'invalid_exit_contract', 'invalid_configs', 'invalid_config_file',
            'tool_unavailable', 'both_refs_required', 'process_not_reaped'}
        report['code'] = ('cancelled' if isinstance(exc, KeyboardInterrupt) else
                          str(exc) if str(exc) in ERROR_CODES | safe_codes else 'evaluation_error')
        report['status'] = 'error'
    return report


def _revalidate_source(repo: Path, frozen: dict[str, Any], target: str | None,
                       incoming: str | None) -> dict[str, Any]:
    if frozen['kind'] == 'pending_merge':
        from mc.dev_harness_pending_merge import freeze_pending
        current = freeze_pending(repo)
    elif frozen['kind'] == 'preview':
        current = preview(repo)
    else:
        current = freeze_candidate(repo, target or frozen['B'], incoming or frozen['H'])
    if current is None or current != frozen:
        raise FreezeError('inputs_changed')
    return current


def run_approved(*, store: HarnessStore, project: dict[str, Any], snapshot: dict[str, Any],
                 frozen: dict[str, Any], check_id: str,
                 register: Callable[[Any, str], None], unregister: Callable[[int], None],
                 cancel: threading.Event | None = None,
                 _workspace_project: dict[str, Any] | None = None) -> dict[str, Any]:
    """Internal M5 receipt seam, one approved step on the exact frozen source.

    No CLI or HTTP input selects commands, a descriptor or workspace callback.
    Preview and pending-index provenance retain their distinct source kinds.
    """
    from mc.dev_harness_native_plan import INPUT_FIELDS, compile_step, validate_plan
    if store.read(project['id']) != snapshot or snapshot['mode'] == 'off' or not snapshot.get('native_plan'):
        raise StoreError('Active approved native plan required')
    validate_plan(store, project, snapshot['policy'], snapshot['native_plan'])
    payload = snapshot['native_plan']['payload']
    item = next(v for v in payload['steps'] if v['id'] == check_id)
    _, prepared = compile_step(Path(project['project_path']), {k: item[k] for k in INPUT_FIELDS}, snapshot['policy'])
    step = {k: prepared[k] for k in ('id', 'argv', 'timeout_seconds', 'fail_codes', 'configs', 'native')}

    def validate_workspace(root: Path) -> None:
        try:
            computed, _ = compile_step(root, {k: item[k] for k in INPUT_FIELDS}, snapshot['policy'])
        except (ValueError, OSError, KeyError):
            raise FreezeError('plan_inputs_changed') from None
        if computed != item:
            raise FreezeError('plan_inputs_changed')

    report = evaluate(project=_workspace_project or project, steps=[step], logs=store.project_dir(project['id']) / 'jobs',
                      _frozen=frozen, _validate_workspace=validate_workspace,
                      register=register, unregister=unregister, cancel=cancel)
    if store.read(project['id']) != snapshot:
        report.update(status='error', code='active_snapshot_changed')
    validate_plan(store, project, snapshot['policy'], snapshot['native_plan'])
    report.update(kind='runner_receipt', trusted=True, active_revision=snapshot['revision'],
                  policy_digest=digest(snapshot['policy']), catalogue_digest=snapshot['catalogue']['digest'],
                  digests=snapshot['policy']['digests'], stage='native_commands',
                  native_plan_digest=snapshot['native_plan_digest'], checker_digest=payload['checker_digest'])
    report['receipt_digest'] = digest(report)
    path = safe_path(store.project_dir(project['id']), 'runs/' + report['run_id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, report, indent=2, allow_nan=False)
    return report


def run_managed(*, store: HarnessStore, project: dict[str, Any], steps: list[dict[str, Any]],
                target: str, incoming: str, register: Callable[[Any, str], None],
                unregister: Callable[[int], None], cancel: threading.Event | None = None) -> dict[str, Any]:
    """Internal coordinator seam; no CLI/HTTP body can submit a receipt.

    This is execution provenance only. M4/M5 must validate recipe reports,
    practice/review prerequisites and recheck inputs before authorizing a merge.
    It deliberately cannot satisfy M1's measured_preview activation evidence.
    """
    snapshot = store.read(project['id'])
    if snapshot is None:
        raise StoreError('Active snapshot required for managed execution')
    folder = store.project_dir(project['id'])
    report = evaluate(project=project, steps=steps, logs=safe_path(folder, 'jobs'), target=target,
                      incoming=incoming, register=register, unregister=unregister, cancel=cancel)
    if store.read(project['id']) != snapshot:
        report.update(status='error', code='active_snapshot_changed')
    report.update(kind='runner_receipt', trusted=True, active_revision=snapshot['revision'],
                  policy_digest=digest(snapshot['policy']), catalogue_digest=snapshot['catalogue']['digest'],
                  digests=snapshot['policy']['digests'], stage='native_commands')
    report['receipt_digest'] = digest(report)
    path = safe_path(folder, 'runs/' + report['run_id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, report, indent=2, allow_nan=False)
    return report


def read_receipt(store: HarnessStore, project_id: str, run_id: str) -> dict[str, Any]:
    """Read exact internal provenance; corrupt/stale records are never passes."""
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid run identity')
    folder = store.project_dir(project_id)
    record = store._read_json(safe_path(folder, 'runs/' + run_id + '.json'))
    snapshot = store.read(project_id)
    if (record.get('kind') != 'runner_receipt' or record.get('trusted') is not True
            or record.get('authorizes_merge') is not False or record.get('run_id') != run_id
            or record.get('project_id') != project_id or record.get('version') != 1
            or record.get('status') not in ('pass', 'fail', 'review_required', 'error', 'unsupported')
            or record.get('receipt_digest') != digest({k: v for k, v in record.items() if k != 'receipt_digest'})):
        raise StoreError('Invalid runner receipt')
    if (snapshot is None or record.get('active_revision') != snapshot['revision']
            or record.get('policy_digest') != digest(snapshot['policy'])
            or record.get('catalogue_digest') != snapshot['catalogue']['digest']
            or record.get('digests') != snapshot['policy']['digests']):
        raise StoreError('Stale runner receipt')
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Report-only frozen native checks; never grants merge permission.')
    parser.add_argument('--project', required=True)
    parser.add_argument('--project-id', default='headless')
    parser.add_argument('--target')
    parser.add_argument('--incoming')
    parser.add_argument('--steps', help='JSON recipe array (report-only; cannot activate policy)')
    args = parser.parse_args(argv)
    try:
        project = {'id': args.project_id, 'project_path': args.project}
        if not _ID.fullmatch(args.project_id):
            raise ValueError('invalid_identity')
        steps = []
        if args.steps:
            path = Path(args.steps)
            if path.stat().st_size > MAX_CONFIG_BYTES:
                raise ValueError('recipe_limit')
            steps = json.loads(path.read_text(encoding='utf-8'))
        import tempfile
        with tempfile.TemporaryDirectory(prefix='clayrune-harness-logs-') as logs:
            report = evaluate(project=project, steps=steps, logs=Path(logs), target=args.target, incoming=args.incoming)
        # Paths are structured JSON data; angle brackets/control bytes remain
        # escaped so terminal/HTML consumers never treat names as markup.
        output = re.sub(r'"(?:\\.|[^"\\])*"', lambda match: match.group()
            .replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('[', '\\u005b').replace(']', '\\u005d').replace('`', '\\u0060'),
            json.dumps(report, ensure_ascii=True))
        print(output)
        return exit_code(report['status'])
    except Exception:
        print(json.dumps({'kind': 'headless_report', 'trusted': False,
                          'authorizes_merge': False, 'status': 'error', 'code': 'invalid_headless_input'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
