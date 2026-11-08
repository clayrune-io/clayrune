"""MC-1164: block a landing whose merged tree does not pass the project's verify commands.

Called under repository landing ownership, before the target is modified.
The agent checkout is borrowed while detached; neither branch ref is moved.

The rule is exit codes only: the merged candidate passes when every configured
command exits 0 with no timeout, infrastructure error or oversize output.
Nothing parses runner output to decide a verdict, and nothing is compared with
the target's own state, so a red target blocks every branch until it is fixed.
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from mc.dev_harness_diff import FreezeError, git_bytes, merge_tree, resolve_commit
from mc.landing_serialization import Ownership, assert_held

DEFAULT_TIMEOUT_SECONDS = 1200
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
TAIL_CHARS = 12000

Reviewer = Callable[[dict[str, Any]], dict[str, Any]]


def settings(project: dict[str, Any]) -> tuple[list[str], float]:
    """A list means ordered shell commands, not an argv array."""
    value = project.get('verify_command')
    if value is None or value == '' or value == []:
        return [], DEFAULT_TIMEOUT_SECONDS
    commands = [value] if isinstance(value, str) else value
    if (not isinstance(commands, list) or not 1 <= len(commands) <= 128
            or any(not isinstance(c, str) or not c.strip() or '\0' in c for c in commands)):
        raise ValueError('invalid_verify_command')
    timeout = project.get('verify_timeout_seconds', DEFAULT_TIMEOUT_SECONDS)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid_verify_timeout_seconds')
    return list(commands), float(timeout)


def run_command(command: str, cwd: Path, timeout: float, project_id: str) -> dict[str, Any]:
    """Run one exact platform-shell command with the existing owned-tree cleanup."""
    from mc.blueprints.agent_routes import _register_process, _unregister_process
    from mc.blueprints.secrets_routes import _kill_process_tree, _win_job_object_for
    result: dict[str, Any] = {'exit_code': None, 'output_tail': '', 'error': None}
    proc = None
    job = None
    with tempfile.TemporaryFile() as output:
        try:
            kwargs: dict[str, Any] = {'start_new_session': True} if os.name != 'nt' else {
                'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
            proc = subprocess.Popen(command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, **kwargs)
            _register_process(proc, 'landing verification', 'agent_job', '', project_id)
            if os.name == 'nt':
                job = _win_job_object_for(proc.pid)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['error'] = 'timeout'
                _kill_process_tree(proc, job)
                proc.wait(timeout=10)
            result['exit_code'] = proc.returncode
        except Exception as exc:
            result['error'] = f'command_unavailable:{type(exc).__name__}'
        finally:
            if proc is not None:
                # Also reap descendants of a shell that exits before its children.
                if proc.poll() is None:
                    _kill_process_tree(proc, job)
                    proc.wait(timeout=10)
                if os.name == 'nt' and job:
                    import ctypes
                    from ctypes import wintypes
                    ctypes.windll.kernel32.TerminateJobObject(job, 1)
                    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                    ctypes.windll.kernel32.CloseHandle(job)
                _unregister_process(proc.pid)
        size = output.tell()
        output.seek(max(0, size - TAIL_CHARS * 4))
        result['output_tail'] = output.read().decode('utf-8', 'replace')[-TAIL_CHARS:]
        if size > MAX_OUTPUT_BYTES:
            result['error'] = 'output_limit'
    return result


def _checkout(repo: Path, commit: str) -> None:
    git_bytes(repo, ['checkout', '--detach', commit])


def evaluate(commands: list[str], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge the candidate runs. PASS only if every command exited 0 with no error.

    A command that timed out, could not start or overflowed the output cap fails even
    when its exit code was 0, and a missing run (fewer runs than commands) fails too.
    """
    failures: list[str] = []
    errors: list[str] = []
    lines: list[str] = []
    for i, command in enumerate(commands):
        run = runs[i] if i < len(runs) else {'exit_code': None, 'error': 'not_run', 'output_tail': ''}
        code, error = run.get('exit_code'), run.get('error')
        if code == 0 and not error:
            continue
        reason = f'exit {code}' if code is not None else 'did not run'
        if error:
            reason += f' ({error})'
            errors.append(f'candidate:{i + 1}:{error}')
        failures.append(f'{i + 1}:{reason}')
        lines.append(f'Command {i + 1} failed with {reason}: {command}')
        tail = (run.get('output_tail') or '').rstrip()
        if tail:
            lines.append(tail)
    header = (f'Landing blocked: the merged tree failed {len(failures)} of {len(commands)} '
              'verify command(s). Nothing lands while a command is red.')
    message = '\n'.join([header, *lines]) if failures else ''
    return {'status': 'blocked' if failures else 'pass', 'failures': failures, 'errors': errors,
            'message': message, 'candidate': runs}


def verify(project: dict[str, Any], worktree: Path, target: str, incoming: str,
           owner: Ownership, *, reviewer: Reviewer | None = None) -> dict[str, Any] | None:
    """Return None for the historical path. Reviewer is a future trusted callback.

    Its only accepted verdict is {'status': 'pass', ...}; absence is no extra
    policy. Existing human review approval remains owned by merge_review_gate.
    """
    if not project.get('verify_command') and reviewer is None:
        # False/0 are malformed settings, not an opt-out.
        if project.get('verify_command') is None or project.get('verify_command') in ('', []):
            return None
    result: dict[str, Any] = {'status': 'blocked', 'failures': [], 'errors': [], 'message': '',
                              'candidate': []}
    restored = False
    original_ref = ''
    original_tip = ''
    borrowed = False
    try:
        assert_held(owner)
        commands, timeout = settings(project)
        target_tip = resolve_commit(worktree, target)
        incoming_tip = resolve_commit(worktree, incoming)
        original_tip = resolve_commit(worktree, 'HEAD')
        original_ref = git_bytes(worktree, ['symbolic-ref', 'HEAD']).decode().strip()
        if original_tip != incoming_tip or git_bytes(worktree, ['status', '--porcelain', '--untracked-files=normal']):
            raise ValueError('agent_checkout_changed')
        tree = merge_tree(worktree, target_tip, incoming_tip)
        result.update(target_sha=target_tip, incoming_sha=incoming_tip, merged_tree=tree)
        if commands:
            # Synthetic commit, no ref: make exactly the eventual merged tree checkoutable.
            merged = git_bytes(worktree, ['-c', 'user.name=Landing verification',
                '-c', 'user.email=landing@localhost', 'commit-tree', tree,
                '-p', target_tip, '-p', incoming_tip, '-m', 'Landing verification candidate']).decode().strip()
            borrowed = True
            _checkout(worktree, merged)
            candidate = [run_command(c, worktree, timeout, project['id']) for c in commands]
            result.update(evaluate(commands, candidate))
            if resolve_commit(worktree, 'HEAD') != merged:
                raise ValueError('verification_checkout_changed')
            # Never throw away tracked output written by a verifier. Such commands
            # are unsuitable for this borrowed checkout and block for inspection.
            if git_bytes(worktree, ['status', '--porcelain', '--untracked-files=no']):
                raise ValueError('verification_modified_tracked_files')
        else:
            result['status'] = 'pass'
        if reviewer is not None:
            try:
                verdict = reviewer(dict(result))
                if not isinstance(verdict, dict):
                    raise ValueError('invalid_reviewer_verdict')
            except Exception as exc:
                verdict = {'status': 'blocked', 'error': type(exc).__name__}
            result['reviewer'] = verdict
            if verdict.get('status') != 'pass':
                result['status'] = 'blocked'
                result['errors'].append('reviewer_hold')
        if (resolve_commit(worktree, target) != target_tip
                or resolve_commit(worktree, incoming) != incoming_tip):
            raise ValueError('landing_inputs_changed')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result['status'] = 'blocked'
        result['errors'].append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    finally:
        if borrowed:
            try:
                # Ordinary checkout refuses verifier edits/collisions. No reset,
                # clean, deletion or force checkout may erase agent/test output.
                _checkout(worktree, original_tip)
                if resolve_commit(worktree, original_ref) != original_tip:
                    raise ValueError('agent_branch_changed')
                git_bytes(worktree, ['symbolic-ref', 'HEAD', original_ref])
                restored = True
            except (FreezeError, OSError, ValueError) as exc:
                result['status'] = 'blocked'
                result['errors'].append('checkout_restore_failed:' + str(exc))
        else:
            restored = True
    result['checkout_restored'] = restored
    return result
