"""MC-1164: compare merged-tree command failures with the target's baseline.

Called under repository landing ownership, before the target is modified.
The agent checkout is borrowed while detached; neither branch ref is moved.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from mc.dev_harness_diff import FreezeError, git_bytes, merge_tree, resolve_commit
from mc.landing_serialization import Ownership, assert_held, common_dir

DEFAULT_TIMEOUT_SECONDS = 1200
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
TAIL_CHARS = 12000
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_PYTEST = re.compile(r'^(?:FAILED|ERROR)\s+(\S+\.py(?:::.+)?)$')
_SMOKE = re.compile(
    r'^(?:(?P<tag>SCHEDFAIL|CALFAIL)\s+|[✗✘×]\s*|'
    r'❌\s*(?:FAIL\s*[:—-]?\s*)?|FAIL(?:\s*[:—-]\s*|\s+|$))(?P<name>.*?)\s*$')
_FAILURE = re.compile(
    r'fail|error|exception|fatal|\bnot ok\b|\busage:|'
    r'\b(?:not found|could not|unreachable|is required|must be)\b', re.I)
_SUMMARY = re.compile(
    r'^(?:(?:RTL smoke|desk-studio-passcode-replay|human-proof-busy|'
    r'MC-1070 (?:independent|wired) controls):\s*)?(?:\d+(?:/\d+)?(?: [\w-]+)* '
    r'(?:check|assertion|problem|case|failure|behaviour)(?:\(s\)|s)?'
    r'(?: (?:failed|regressed))?\.?|FAILED(?:[: (]+\d+\)?)?|\d+ FAILED|'
    r'RESULT: FAIL|Checks=\d+ failures=\d+|fails=\d+)$', re.I)
# Runner bookkeeping, not a failure: pytest's closing count line and the TAP / node
# test-runner totals. Counts change when unrelated tests are added or fixed.
_RUNNER_SUMMARY = re.compile(
    r'=*\s*(?:\d+ (?:failed|passed|skipped|deselected|xfailed|xpassed|errors?|warnings?|rerun)(?:, )?)+'
    r'(?: in (?:<duration>|[\d.]+s)(?: \(\d+:\d{2}:\d{2}\))?)?\s*=*|'
    r'=*\s*no tests ran(?: in <duration>)?\s*=*|'
    r'(?:#|ℹ) (?:tests|suites|pass|fail|cancelled|skipped|todo|duration_ms) [\d.]+')
# TAP `not ok 3 - name`: the number is the test's position, not its identity.
_TAP = re.compile(r'^not ok\s+\d+\b\s*(?:-\s*)?(?P<name>.*?)\s*$')
_TAP_SUBTEST = re.compile(r'^# Subtest:\s*(?P<name>.*?)\s*$')
# pytest report heading `____ test_x ____`: the only per-test name when no short
# summary (-r) is printed, now that the closing count line is ignored.
_PYTEST_SECTION = re.compile(r'^_{2,} (?P<name>\S.*?) _{2,}$')
_DURATION = re.compile(r'\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|seconds?|secs?|minutes?|mins?)\b', re.I)
_ABSOLUTE_FILE = re.compile(
    r'(?<![\w>])(?:file://)?(?:[A-Za-z]:[\\/]|/)(?:[^\s<>\"\'():]+[\\/])*'
    r'(?P<file>[^\s<>\"\'():/\\]+\.[A-Za-z0-9]+)(?::\d+(?::\d+)?)?')
# `file.ext:LINE[:COL]` -> `file.ext` for relative and absolute paths alike, so a
# moved assertion keeps its identity. Needs a path separator or a source-file
# extension, so `example.com:8080` keeps its port.
_LOCATION = re.compile(
    r'(?P<file>[\w.\-~@+\\/]*[\w\-]\.(?P<ext>[A-Za-z][A-Za-z0-9]{0,7}))(?::\d+){1,2}(?!\w)')
_SOURCE_EXTENSIONS = frozenset(
    'py pyi js mjs cjs jsx ts tsx json html htm css scss md txt sh bash ps1 bat cmd rs go java kt c h cc cpp '
    'hpp cs rb php swift yml yaml toml ini cfg vue svelte sql'.split())
_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()
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


def _drop_location(match: re.Match[str]) -> str:
    file = match['file']
    if '/' in file or '\\' in file or match['ext'].lower() in _SOURCE_EXTENSIONS:
        return file
    return match[0]


def _stable_line(line: str) -> str:
    # Keep assertion values and file/check names; remove locations and elapsed time.
    line = _ABSOLUTE_FILE.sub(lambda m: '<path>/' + m['file'], line)
    line = re.sub(r'(<worktree>[/\\][^\s:]+):\d+(?::\d+)?', r'\1', line)
    line = _LOCATION.sub(_drop_location, line)
    return ' '.join(_DURATION.sub('<duration>', line).split())


def failure_signatures(output: str, exit_code: int, root: Path | None = None) -> list[str]:
    if exit_code == 0:
        return []
    clean = _ANSI.sub('', output).replace('\r', '\n')
    if root is not None:
        clean = clean.replace(str(root), '<worktree>').replace(root.as_posix(), '<worktree>')
    names: set[str] = set()
    summaries: set[str] = set()
    heading = ''
    subtests: list[tuple[int, str]] = []  # enclosing TAP `# Subtest:` lines, by indent
    for raw in clean.splitlines():
        line = raw.strip()
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip())
        match = _PYTEST.match(line.split(' - ', 1)[0])
        if match:
            names.add('pytest:' + match[1])
            heading = ''
            continue
        line = _stable_line(line)
        subtest = _TAP_SUBTEST.match(line)
        if subtest:
            while subtests and subtests[-1][0] >= indent:
                subtests.pop()
            subtests.append((indent, subtest['name']))
            heading = ''
            continue
        section = _PYTEST_SECTION.match(line)
        if section:
            names.add('pytest-report:' + section['name'])
            heading = ''
            continue
        tap = _TAP.match(line)
        if tap:
            # Qualified by enclosing suites, so the same test name failing in a
            # second suite is still a new failure.
            path = [name for depth, name in subtests if depth < indent]
            names.add('tap:' + ' > '.join([*path, tap['name'] or '<unspecified>']))
            heading = ''
            continue
        match = _SMOKE.match(line)
        detail = match['name'].strip() if match else line
        if _SUMMARY.fullmatch(detail) or _RUNNER_SUMMARY.fullmatch(detail):
            summaries.add(line)
            heading = ''
            continue
        if match:
            tag = (match['tag'] + ':') if match['tag'] else ''
            name = tag + (detail or '<unspecified>')
            names.add('smoke:' + name)
            heading = name if detail.endswith(':') else ''
            continue
        # boot-smoke prints multiple findings under one guard heading. Also keep
        # indented inline-handler/stack diagnostics in such a failure block.
        if heading and raw[:1].isspace():
            names.add('smoke:' + heading + ' ' + re.sub(r'^[•*\-]\s+', '', line))
            continue
        heading = ''
        # Additive: a known pytest/smoke failure must not hide another format.
        if _FAILURE.search(line):
            names.add(f'exit:{exit_code}:' + line)
    if not names:
        lines = [_stable_line(line) for line in clean.splitlines() if line.strip()]
        names.add(f'exit:{exit_code}:' + (sorted(summaries) or lines or ['<no output>'])[-1])
    return sorted(names)


def compare(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    # Scope signatures by command position: one command cannot hide another.
    old = {f'{i + 1}:{s}' for i, run in enumerate(baseline) for s in run['signatures']}
    current = {f'{i + 1}:{s}' for i, run in enumerate(candidate) for s in run['signatures']}
    errors = [f'{phase}:{i + 1}:{run["error"]}' for phase, runs in
              (('baseline', baseline), ('candidate', candidate))
              for i, run in enumerate(runs) if run.get('error')]
    new = sorted(current - old)
    return {'status': 'blocked' if new or errors else 'pass', 'new_failures': new,
            'baseline_failures': sorted(old), 'errors': errors,
            'baseline': baseline, 'candidate': candidate}


def run_command(command: str, cwd: Path, timeout: float, project_id: str) -> dict[str, Any]:
    """Run one exact platform-shell command with the existing owned-tree cleanup."""
    from mc.blueprints.agent_routes import _register_process, _unregister_process
    from mc.blueprints.secrets_routes import _kill_process_tree, _win_job_object_for
    result: dict[str, Any] = {'exit_code': None, 'signatures': [], 'output_tail': '', 'error': None}
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
        elif result['exit_code'] is not None:
            output.seek(0)
            result['signatures'] = failure_signatures(
                output.read().decode('utf-8', 'replace'), result['exit_code'], cwd)
    return result


def _checkout(repo: Path, commit: str) -> None:
    git_bytes(repo, ['checkout', '--detach', commit])


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
    result: dict[str, Any] = {'status': 'blocked', 'new_failures': [],
        'baseline_failures': [], 'errors': [], 'baseline': [], 'candidate': []}
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
            key = hashlib.sha256(json.dumps([str(common_dir(worktree)), target_tip, commands, timeout,
                sorted(os.environ.items())], ensure_ascii=True).encode()).hexdigest()
            with _cache_lock:
                baseline = _cache.get(key)
                # Runtime dependencies/config are not in the key. Only a green
                # baseline is safe to reuse: it cannot exempt any new failure.
                if baseline is not None and not all(
                        run.get('exit_code') == 0 and not run.get('signatures') and not run.get('error')
                        for run in baseline):
                    del _cache[key]
                    baseline = None
                elif baseline is not None:
                    _cache.move_to_end(key)
            result['baseline_cached'] = baseline is not None
            borrowed = True
            if baseline is None:
                _checkout(worktree, target_tip)
                baseline = [run_command(c, worktree, timeout, project['id']) for c in commands]
                result['baseline'] = baseline
                if resolve_commit(worktree, 'HEAD') != target_tip:
                    raise ValueError('verification_checkout_changed')
            result['baseline'] = baseline
            # Never throw away tracked output written by a verifier. Such commands
            # are unsuitable for this borrowed checkout and block for inspection.
            if git_bytes(worktree, ['status', '--porcelain', '--untracked-files=no']):
                raise ValueError('verification_modified_tracked_files')
            if all(run.get('exit_code') == 0 and not run.get('signatures') and not run.get('error')
                   for run in baseline):
                with _cache_lock:
                    _cache[key] = baseline
                    while len(_cache) > 64:
                        _cache.popitem(last=False)
            _checkout(worktree, merged)
            candidate = [run_command(c, worktree, timeout, project['id']) for c in commands]
            result.update(compare(baseline, candidate))
            if resolve_commit(worktree, 'HEAD') != merged:
                raise ValueError('verification_checkout_changed')
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
