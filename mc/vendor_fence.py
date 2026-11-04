"""BUILTINS-5 unattended launch policy for engines other than Claude/Codex.

Gemini/Qwen get an armed payload adapter in a per-launch settings copy plus
the shared pre-spawn self-test. Unverified adapters are explicitly refused,
as Dave decided; attended launches and the human-owned fence are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mc import guardrail_hooks as gh
from mc import launch_marker as lm
from mc import vendor_fence_probe

SETTINGS_ENV = {
    'gemini': 'GEMINI_CLI_SYSTEM_SETTINGS_PATH',
    'qwen': 'QWEN_CODE_SYSTEM_SETTINGS_PATH',
}
UNVERIFIED = {
    'aider': 'Aider has no pre-execution hook, so the steward fence cannot run',
    'opencode': 'OpenCode has no verified per-launch steward fence adapter',
    'goose': 'Goose has no verified per-launch steward fence adapter',
    'kiro': 'Kiro has no verified per-launch steward fence adapter',
}
HOOK_ENTRY = 'vendor-fence'
TIMEOUT_SEC = 10


class VendorFenceError(RuntimeError):
    """Visible refusal handled by the existing dispatch/follow-up error paths."""


def _unattended(session: Optional[Dict[str, Any]]) -> bool:
    return ((session or {}).get('trigger_type') in lm.UNATTENDED_TRIGGER_TYPES
            and lm._fence_enabled())


def _refuse(vendor: str, session: Optional[Dict[str, Any]], reason: str) -> None:
    label = {'opencode': 'OpenCode', 'kiro': 'Kiro'}.get(vendor, vendor.title())
    message = f'{reason}; unattended {label} is refused while fencing is enabled'
    if session is not None:
        session.setdefault('log_lines', []).append(f'[{message}]')
        session.update(status='error', process_alive=False,
                       last_status_change_time=time.time())
    raise VendorFenceError(message)


def check_launch(vendor: str, session: Optional[Dict[str, Any]]) -> None:
    """Check before binary resolution or any follow-up side effects."""
    if _unattended(session) and vendor in UNVERIFIED:
        if vendor == 'aider':
            from mc.aider_launch import enabled
            if enabled():
                return
        _refuse(vendor, session, UNVERIFIED[vendor])


def prepare_command(vendor: str, session: Optional[Dict[str, Any]],
                    cmd: List[str], cwd: str, env: Dict[str, str]) -> List[str]:
    """Replace only guarded Aider's entrypoint; refusal uses the shared error path."""
    if vendor != 'aider' or not _unattended(session):
        return cmd
    check_launch(vendor, session)
    try:
        from mc.aider_launch import prepare
        return prepare(cmd, cwd, env)
    except Exception as exc:
        _refuse(vendor, session, str(exc))
        return cmd  # unreachable


def _shell(vendor: str, env: Dict[str, str]) -> str:
    if os.name != 'nt':
        return 'bash'
    if vendor == 'gemini':
        comspec = env.get('ComSpec') or env.get('COMSPEC') or ''
        if _powershell(comspec):
            return comspec
        return shutil.which('pwsh.exe') or 'powershell.exe'
    return env.get('ComSpec') or env.get('COMSPEC') or 'cmd.exe'


def _powershell(shell: str) -> bool:
    return Path(shell).stem.lower() in ('powershell', 'pwsh')


def hook_command(shell: str, *, self_test: bool = False) -> str:
    tokens = gh.hook_invocation_tokens(
        HOOK_ENTRY, None, Path(__file__).with_name('vendor_fence_hook.py'))
    if _powershell(shell):
        tokens.append('--powershell')
    if self_test:
        tokens.append(gh.FENCE_SELF_TEST_ARG)
    if _powershell(shell):
        base = '. ' + ' '.join("'" + t.replace("'", "''") + "'" for t in tokens)
        return base + gh._FAIL_CLOSED_SUFFIX_PS
    if os.name == 'nt':
        # cmd owns exit-code propagation. Unexpected interpreter exits are
        # converted to the vendors' exit-2 block contract as well.
        return subprocess.list2cmdline(tokens) + ' & if errorlevel 1 exit /b 2'
    return shlex.join(tokens) + gh._FAIL_CLOSED_SUFFIX_SH


def _shell_argv(shell: str, command: str) -> List[str]:
    if _powershell(shell):
        return [shell, '-NoProfile', '-Command', command]
    if os.name == 'nt':
        return [shell, '/d', '/s', '/c', command]
    return [shell, '-c', command]


def prepare_env(vendor: str, session: Optional[Dict[str, Any]],
                env: Dict[str, str], *, binary: Optional[Path] = None,
                cwd: Optional[str] = None) -> Dict[str, str]:
    """Validate the adapter and add it to this launch's existing settings.

    Existing MCP/process-guard settings survive. Missing, malformed or
    unwritable settings refuse the launch rather than losing the guard.
    No file is made for attended launches or while fencing is disabled.
    """
    check_launch(vendor, session)
    if vendor not in SETTINGS_ENV or not _unattended(session):
        return env
    try:
        shell = _shell(vendor, env)
        key = SETTINGS_ENV[vendor]
        src = env.get(key)
        if src and not Path(src).resolve().is_relative_to(gh.hooks_dir().resolve()):
            raise ValueError('system settings source is not Clayrune-owned; cannot copy user configuration')
        data = json.loads(Path(src).read_text(encoding='utf-8')) if src else {}
        if not isinstance(data, dict):
            raise ValueError('system settings must be an object')
        hooks = data.setdefault('hooks', {})
        event = 'BeforeTool' if vendor == 'gemini' else 'PreToolUse'
        groups = hooks.setdefault(event, [])
        for group in groups:
            group['hooks'] = [entry for entry in group['hooks']
                              if entry.get('name') != gh.FENCE_HOOK_NAME]
        groups = hooks[event] = [group for group in groups if group['hooks']]
        # An empty matcher applies to all tools, including reads/writes/MCP;
        # restricting to shell would leave global config writes unguarded.
        groups.append({'matcher': '', 'hooks': [{
            'type': 'command', 'name': gh.FENCE_HOOK_NAME,
            'command': hook_command(shell), 'timeout': 30000 if vendor == 'gemini' else 30,
        }]})
        data['disableAllHooks'] = False
        if vendor == 'gemini':
            config = data.setdefault('hooksConfig', {})
            config['enabled'] = True
            config['disabled'] = [name for name in config.get('disabled', [])
                                  if name != gh.FENCE_HOOK_NAME]
        encoded = json.dumps(data, sort_keys=True, indent=2)
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]
        directory = gh.hooks_dir() / 'unattended'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{vendor}-fence-{digest}.json'
        tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(encoded, encoding='utf-8')
        tmp.replace(path)
        if binary is None:
            raise RuntimeError('CLI binary is missing; cannot verify native fence loading')
        argv = vendor_fence_probe.self_test_argv(
            vendor, binary, cwd or os.getcwd(), path, hook_command(shell, self_test=True))
        ok, detail = gh.run_fence_self_test(
            argv, timeout=TIMEOUT_SEC, what=f'{vendor} native fence loader/runner',
            env=env, cwd=cwd)
        if not ok:
            raise RuntimeError(f'steward fence self-test failed ({detail})')
        env[key] = str(path)
        return env
    except Exception as exc:
        _refuse(vendor, session, str(exc))
        return env  # unreachable; keeps the return contract explicit
