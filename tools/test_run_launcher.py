"""MC-1148 P5: exact local execution, with optional script-unit telemetry.

Standalone stdlib modules live beside test-run.py; target projects need no mc
package. Framework reporters and passive command detection are later phases.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO

from test_run_transport import LocalClient, Telemetry, TransportError
from test_run_pytest import PytestProgress

MAX_MANIFEST_BYTES = 256 * 1024
MAX_UNITS = 128


def warn(code: str) -> None:
    print('[test-run] ' + code, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Unit:
    argv: list[str] | str
    cwd: str
    env: dict[str, str]
    timeout: float | None


def positive(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError('timeout must be positive and finite')
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('timeout must be positive and finite')
    return number


def shell_argv(shell: str, command: str) -> list[str] | str:
    # Exact shell only: never silently replace bash with PowerShell (or WSL).
    path = shutil.which(shell)
    if os.name == 'nt' and shell in ('bash', 'sh'):
        wsl = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / (shell + '.exe')
        if path and Path(path).resolve() == wsl.resolve():
            path = None
        if path is None:
            git = shutil.which('git')
            if git:
                for root in (Path(git).resolve().parent.parent, Path(git).resolve().parent.parent.parent):
                    candidate = root / 'bin' / (shell + '.exe')
                    if candidate.is_file():
                        path = str(candidate)
                        break
    if path is None:
        raise ValueError('requested shell unavailable')
    if shell == 'cmd' and os.name == 'nt':
        # cmd does not decode the C-runtime backslash quoting list2cmdline
        # applies to a list argument. /s removes exactly our outer quote pair;
        # the requested shell statement inside it must remain byte-for-byte.
        return subprocess.list2cmdline([path, '/d', '/s', '/c']) + ' "' + command + '"'
    flags = {'bash': ['-c'], 'sh': ['-c'],
             'powershell': ['-NoProfile', '-Command'], 'cmd': ['/d', '/s', '/c']}
    return [path, *flags[shell], command]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest key')
        result[key] = value
    return result


def unit(data: dict, cwd: str, timeout: float | None) -> Unit:
    if not isinstance(data, dict) or set(data) - {'argv', 'shell', 'command', 'cwd', 'env', 'timeout_seconds'}:
        raise ValueError('invalid manifest unit fields')
    if 'argv' in data:
        argv = data['argv']
        if ('shell' in data or 'command' in data or not isinstance(argv, list) or not argv
                or any(not isinstance(a, str) or '\0' in a for a in argv) or not argv[0]):
            raise ValueError('expected argv strings or an explicit shell command')
    else:
        shell, command = data.get('shell'), data.get('command')
        if shell not in ('bash', 'sh', 'powershell', 'cmd') or not isinstance(command, str) or not command or '\0' in command:
            raise ValueError('expected argv strings or an explicit shell command')
        argv = shell_argv(shell, command)
    folder = data.get('cwd', cwd)
    if not isinstance(folder, str) or not folder or '\0' in folder:
        raise ValueError('invalid cwd')
    resolved = Path(folder) if Path(folder).is_absolute() else Path(cwd) / folder
    if not resolved.is_dir():
        raise ValueError('cwd is not a directory')
    environment = os.environ.copy()
    changes = data.get('env', {})
    if not isinstance(changes, dict):
        raise ValueError('invalid environment')
    for key, value in changes.items():
        if (not isinstance(key, str) or not key or '=' in key or '\0' in key
                or (value is not None and (not isinstance(value, str) or '\0' in value))):
            raise ValueError('invalid environment')
        if os.name == 'nt':
            key = key.upper()  # Match os.environ's case-insensitive Windows keys.
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    limit = data.get('timeout_seconds', timeout)
    return Unit(argv if isinstance(argv, str) else list(argv), str(resolved), environment,
                None if limit is None else positive(limit))


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[Unit], bool]:
    parser = argparse.ArgumentParser(description='Run exact local test commands with optional reported progress.')
    parser.add_argument('--project', required=True, help='Clayrune project ID; ownership is resolved by the server')
    parser.add_argument('--port', type=int, default=5199)
    parser.add_argument('--cwd', default=os.getcwd())
    parser.add_argument('--timeout-seconds', type=positive)
    parser.add_argument('--manifest', help='Explicit ordered schema-1 JSON manifest')
    parser.add_argument('--shell', choices=['bash', 'sh', 'powershell', 'cmd'])
    parser.add_argument('--command', help='Exact command for --shell')
    parser.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not args.project or len(args.project) > 128:
        raise ValueError('invalid port or project')
    args.cwd = os.path.abspath(args.cwd)
    if args.argv[:1] == ['--']:
        args.argv = args.argv[1:]
    if args.manifest:
        if args.argv or args.shell or args.command is not None:
            raise ValueError('manifest cannot be combined with a command')
        with open(args.manifest, 'rb') as source:
            raw = source.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('manifest exceeds size limit')
        manifest = json.loads(raw, object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'stop_on_failure', 'units'}
                or type(manifest['schema']) is not int or manifest['schema'] != 1
                or type(manifest['stop_on_failure']) is not bool
                or not isinstance(manifest['units'], list)
                or not 1 <= len(manifest['units']) <= MAX_UNITS):
            raise ValueError('invalid schema-1 manifest')
        return args, [unit(item, args.cwd, args.timeout_seconds) for item in manifest['units']], manifest['stop_on_failure']
    data = {'argv': args.argv} if not args.shell else {'shell': args.shell, 'command': args.command}
    if (args.shell and args.argv) or (not args.shell and args.command is not None):
        raise ValueError('choose argv or an explicit shell command')
    return args, [unit(data, args.cwd, args.timeout_seconds)], True


def event(kind: str, **payload) -> dict:
    return dict(type=kind, payload=payload)


def events(telemetry: Telemetry, key: str, *items: dict) -> None:
    telemetry.send('events', key, {'events': list(items)})


def result(code: int, termination: str = 'exited') -> dict:
    signum = -code if os.name != 'nt' and -255 <= code < 0 else None
    if termination == 'exited' and signum:
        termination = 'signalled'
    return dict(exit_code=code, signal=signum, termination=termination)


def _pump(source: BinaryIO, target: BinaryIO, output: threading.Event,
          errors: threading.Event) -> None:
    writable = True
    try:
        while True:
            chunk = os.read(source.fileno(), 65536)
            if not chunk:
                break
            output.set()
            if writable:
                try:
                    target.write(chunk)
                    target.flush()
                except (OSError, ValueError):
                    errors.set()
                    writable = False
    except (OSError, ValueError):
        errors.set()
    finally:
        source.close()


def execute(spec: Unit, client: LocalClient, project: str, cancelled: threading.Event,
            telemetry: Telemetry, key: str) -> dict:
    try:
        started = time.monotonic()
        proc = subprocess.Popen(spec.argv, cwd=spec.cwd, env=spec.env, shell=False,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        warn('child_start_failed_' + type(exc).__name__)
        events(telemetry, key, event('diagnostic', code='child_start_failed', capture='partial'))
        return result(127 if isinstance(exc, FileNotFoundError) else 126)
    # Immediately register the captured PID. Registration failure cannot change
    # the command's result; the local command already runs under caller policy.
    try:
        client.register(proc.pid, project)
    except TransportError as exc:
        warn(str(exc) + '_process_registration_failed')
        events(telemetry, key, event('diagnostic', code='process_registration_failed', capture='partial'))
    output, errors = threading.Event(), threading.Event()
    assert proc.stdout is not None and proc.stderr is not None
    pumps = [threading.Thread(target=_pump, args=(src, dst, output, errors), daemon=True)
             for src, dst in ((proc.stdout, sys.stdout.buffer), (proc.stderr, sys.stderr.buffer))]
    for pump in pumps:
        pump.start()
    heartbeat = time.monotonic()
    termination = 'exited'
    while proc.poll() is None:
        if cancelled.is_set() or (spec.timeout is not None and time.monotonic() - started >= spec.timeout):
            termination = 'cancelled' if cancelled.is_set() else 'timeout'
            try:
                proc.kill()  # Only the child Popen PID this launcher created.
            except OSError as exc:
                warn('child_kill_failed_' + type(exc).__name__)
                # Never mint terminal evidence if the child has not exited.
            break
        if time.monotonic() - heartbeat >= 1:
            if output.is_set():
                events(telemetry, key, event('heartbeat', output=True))
                output.clear()
            heartbeat = time.monotonic()
        time.sleep(0.02)
    code = proc.wait()
    deadline = time.monotonic() + 1
    for pump in pumps:
        pump.join(max(0, deadline - time.monotonic()))
    if errors.is_set() or any(pump.is_alive() for pump in pumps):
        warn('output_drain_incomplete')
        events(telemetry, key, event('diagnostic', code='output_drain_incomplete', capture='partial'))
    if output.is_set():
        events(telemetry, key, event('heartbeat', output=True))
    # A process that won the exit race with cancellation keeps its real zero.
    return result(code, termination if code != 0 else 'exited')


def run(args: argparse.Namespace, units: list[Unit], stop_on_failure: bool) -> int:
    client = LocalClient(args.port, args.project)
    telemetry = Telemetry(client, warn)
    cancelled = threading.Event()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: cancelled.set())
    final = result(0)
    completed = 0
    try:
        telemetry.send('enroll', 'parent', {'metadata': {
            'runner': 'launcher', 'unit': 'script', 'display_label': 'Reported test commands'}})
        events(telemetry, 'parent', event('run_started', reporter_version='launcher-1'),
               event('collection', revision=1, selected_total=len(units)))
        for index, spec in enumerate(units, 1):
            if cancelled.is_set():
                final = result(130, 'cancelled')
                break
            key = 'unit-' + str(index)
            reporter = PytestProgress(spec, telemetry, key, warn)
            telemetry.send('enroll', key, {'parent': 'parent', 'metadata': reporter.metadata('Command ' + str(index))})
            events(telemetry, 'parent', event('unit_started', unit_id=key, attempt=1))
            observed = reporter.execute(lambda prepared: execute(
                prepared, client, args.project, cancelled, telemetry, key))
            report = event('unit_report', unit_id=key, attempt=1, phase='process',
                           outcome='pass' if observed['exit_code'] == 0 else 'fail', final=True, revision=1)
            telemetry.send('finalize', key, observed)
            events(telemetry, 'parent', report)
            completed += 1
            if (observed['exit_code'] != 0 and final['exit_code'] == 0
                    or observed['termination'] in ('cancelled', 'timeout')):
                final = observed
            if observed['termination'] in ('cancelled', 'timeout') or (stop_on_failure and observed['exit_code'] != 0):
                break
        events(telemetry, 'parent', event('run_finished', state=(
            final['termination'] if final['termination'] in ('cancelled', 'timeout')
            else 'passed' if final['exit_code'] == 0 else 'failed'),
            exit_code=final['exit_code'], not_run=len(units) - completed))
        telemetry.send('finalize', 'parent', final)
    finally:
        telemetry.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return final['exit_code']


def main(argv: list[str] | None = None) -> int:
    try:
        args, units, stop_on_failure = parse(sys.argv[1:] if argv is None else argv)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        warn('invalid_invocation_or_manifest')
        return 2
    return run(args, units, stop_on_failure)


def entrypoint() -> None:
    code = main()
    if os.name == 'nt' and code > 2**31 - 1:
        # SystemExit's C-long conversion can truncate unsigned Windows status
        # values. Preserve the DWORD actually returned by child.wait().
        import ctypes
        sys.stdout.flush()
        sys.stderr.flush()
        ctypes.windll.kernel32.ExitProcess(ctypes.c_uint32(code))
    if os.name != 'nt' and -255 <= code < 0:
        # sys.exit(-SIG) truncates to 256-SIG. Re-raise the observed signal so
        # the invoking shell/Popen sees the child's actual signal termination.
        if -code not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    raise SystemExit(code)
