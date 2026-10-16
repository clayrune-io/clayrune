"""Qwen's standalone layout and official updater (MC-1102).

Sources (verified 2026-10-16): QwenLM/qwen-code, scripts/installation/
INSTALLATION_GUIDE.md, install-qwen-with-source.{sh,bat}, and
install-qwen-standalone.ps1. The installers also update existing installs,
without elevation or prompts. Force standalone: detect mode may fall back to npm.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

_BASE = 'https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/'
UPDATE_COMMANDS = {
    'standalone': ('bash -o pipefail -c "curl -fsSL ' + _BASE +
                   'install-qwen-standalone.sh | bash -s -- --method standalone"'),
    'standalone-windows': (
        'powershell -NoProfile -NonInteractive -ExecutionPolicy ByPass -c '
        '"$ErrorActionPreference = \'Stop\'; $env:QWEN_INSTALL_METHOD = \'standalone\'; '
        'irm ' + _BASE + 'install-qwen-standalone.ps1 | iex"'),
}


def standalone_root(binary: Any, platform: str) -> Optional[Path]:
    """Recognise only installer-managed paths with a matching manifest.

    Unix uses a shell wrapper, not a symlink: ~/.local/bin/qwen calls
    ~/.local/lib/qwen-code/bin/qwen. Windows has the equivalent two cmd files.
    Honour the installer's directory overrides; an unconfigured custom layout
    stays unknown because rerunning the installer would update a different copy.
    """
    windows = platform == 'win32'
    if windows:
        default = Path(os.environ.get('LOCALAPPDATA') or
                       str(Path.home() / 'AppData' / 'Local')) / 'qwen-code'
    else:
        default = Path.home() / '.local'
    root = Path(os.environ.get('QWEN_INSTALL_ROOT') or default)
    lib_parent = Path(os.environ.get('QWEN_INSTALL_LIB_PARENT') or root / 'lib')
    lib = Path(os.environ.get('QWEN_INSTALL_LIB_DIR') or
               (root / 'qwen-code' if windows else lib_parent / 'qwen-code'))
    bindir = Path(os.environ.get('QWEN_INSTALL_BIN_DIR') or root / 'bin')
    filename = 'qwen.cmd' if windows else 'qwen'

    def normal(path: Any) -> str:
        value = os.path.realpath(str(path)).replace('\\', '/')
        return value.lower() if windows else value

    if normal(binary) not in {normal(bindir / filename), normal(lib / 'bin' / filename)}:
        return None
    manifest = lib / 'manifest.json'
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'[qwen-install] reading {manifest} failed: {exc}', flush=True)
        return None
    if not isinstance(data, dict) or data.get('name') != '@qwen-code/qwen-code':
        return None
    return lib


def standalone_method(binary: Any, platform: str) -> str:
    if standalone_root(binary, platform) is None:
        return ''
    return 'standalone-windows' if platform == 'win32' else 'standalone'


def update_argv(method: str) -> Optional[list[str]]:
    """Fixed argv shared by the daily script and server updater."""
    command = UPDATE_COMMANDS.get(method)
    if command is None:
        return None
    if method == 'standalone-windows':
        return ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                'ByPass', '-c', command.split(' -c ', 1)[1][1:-1]]
    return ['sh', '-c', command]


def update_advice(version: Optional[str], binary: Any) -> tuple[str, str]:
    """Use the last daily registry result; health checks never fetch the network."""
    from mc import cli_install, cli_update

    record = cli_update.provider_summary('qwen') or {}
    current = re.search(r'(\d+)\.(\d+)\.(\d+)', version or '')
    latest = re.search(r'(\d+)\.(\d+)\.(\d+)', record.get('latest') or '')
    if not current or not latest:
        return '', ''
    if tuple(map(int, latest.groups())) <= tuple(map(int, current.groups())):
        return '', ''
    return (cli_install.update_command('qwen', binary) or '',
            f'Qwen Code {current.group()} is older than {latest.group()}.')
