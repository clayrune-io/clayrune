"""How an agent CLI was installed, read off WHERE its resolved binary lives, and
the updater that matches.

One place for two callers: the in-app "Update Codex CLI" button
(`mc/agent_runtime.py`, `mc/blueprints/agent_routes.py`) and the daily
`tools/cli-version-check.py`. The updater MUST match the installer that put the
binary there -- running npm over a standalone install leaves two copies and
PATH picks one at random (field case 2026-10-01: a Mac whose codex was
OpenAI's standalone install had no npm on PATH at all).

Stdlib only, no mc imports: the tool imports this from a bare script.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

# From the README of openai/codex (read 2026-10-01). These exact strings are
# also the allowlist the install-launch route accepts for {"update": true} --
# never composed from user input.
CODEX_UPDATE_COMMANDS = {
    'standalone': 'curl -fsSL https://chatgpt.com/codex/install.sh | sh',
    'standalone-windows': ('powershell -ExecutionPolicy ByPass -c '
                           '"irm https://chatgpt.com/codex/install.ps1 | iex"'),
    'npm': 'npm install -g @openai/codex',
    'brew': 'brew upgrade --cask codex',
}

# CLIs whose updater is the same whatever the install flavour.
_SELF_UPDATING = {'claude': 'claude update'}

# CLIs updated by their package manager, and the package each ships as (the
# tools/cli-version-check.py CLIS table). Used ONLY when install_method() reads
# the binary as living under that manager: a package name never picks a manager.
NPM_PACKAGES = {
    'gemini': '@google/gemini-cli',
    'qwen': '@qwen-code/qwen-code',
    'opencode': 'opencode-ai',
}
PYPI_PACKAGES = {'aider': 'aider-chat'}


def sibling_python(binary_path: Any) -> Optional[str]:
    """The interpreter beside (or one level above) a pip-installed console
    script: a venv's bin/ or Scripts/, or a system Python's Scripts/. pip run
    through THAT interpreter updates the copy the script belongs to; a bare
    `pip` off PATH may not. None when there is none to point at."""
    if not binary_path:
        return None
    try:
        real = os.path.realpath(str(binary_path))
    except Exception:
        real = str(binary_path)
    here = os.path.dirname(real)
    for d in (here, os.path.dirname(here)):
        for exe in ('python.exe', 'python3', 'python'):
            cand = os.path.join(d, exe)
            if os.path.isfile(cand):
                return cand
    return None


def install_method(binary_path: Any, platform: Optional[str] = None,
                   name: str = 'codex') -> str:
    """'standalone' | 'standalone-windows' | 'npm' | 'brew' | 'pipx' | 'uv' |
    'pip' | '' (unknown),
    read off WHERE the resolved binary lives -- never off what is merely on PATH.

    standalone: OpenAI's own installer (chatgpt.com/codex/install.sh), whose
      script sets STANDALONE_ROOT=$CODEX_HOME/packages/standalone and links
      ~/.local/bin/codex into it (verified 2026-10-01); on Windows the native
      installer's %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin.
    npm: the real path sits under node_modules/ or an npm/nvm prefix.
    brew: the real path sits under a Cellar/ or Caskroom/.
    pipx / uv: the real path sits under pipx/venvs/ or uv/tools/.
    pip (aider only): a console script with an interpreter beside it.
    `name` only selects the Windows npm shim spelling (<prefix>/npm/<name>).
    The symlink is resolved first: ~/.local/bin/codex alone says nothing."""
    if not binary_path:
        return ''
    raw = str(binary_path)
    try:
        real = os.path.realpath(raw)
    except Exception:
        real = raw
    plat = platform or sys.platform
    for cand in (real, raw):
        c = cand.replace('\\', '/').lower()
        if '/packages/standalone/' in c:
            return 'standalone-windows' if plat == 'win32' else 'standalone'
        if '/cellar/' in c or '/caskroom/' in c:
            return 'brew'
        if '/node_modules/' in c or '/.nvm/' in c or '/.npm-global/' in c or ('/npm/' + name) in c:
            return 'npm'
        if '/programs/openai/codex/' in c:
            return 'standalone-windows'
        if '/pipx/venvs/' in c:
            return 'pipx'
        if '/uv/tools/' in c:
            return 'uv'
    if name in PYPI_PACKAGES and sibling_python(raw):
        return 'pip'
    return ''


def update_command(name: str, binary_path: Any, platform: Optional[str] = None) -> Optional[str]:
    """Shell command string that updates CLI `name` the way it was installed, or
    None when there is no safe answer (unknown install method, or a CLI this
    module has no table for). None means: report, do not guess an updater."""
    if name in _SELF_UPDATING:
        return _SELF_UPDATING[name]
    if name == 'codex':
        return CODEX_UPDATE_COMMANDS.get(install_method(binary_path, platform))
    method = install_method(binary_path, platform, name)
    if name in NPM_PACKAGES:
        return ('npm install -g %s@latest' % NPM_PACKAGES[name]) if method == 'npm' else None
    if name in PYPI_PACKAGES:
        pkg = PYPI_PACKAGES[name]
        return {'pipx': 'pipx upgrade ' + pkg,
                'uv': 'uv tool upgrade ' + pkg,
                # `python` is a placeholder: mc/cli_update.py swaps in
                # sibling_python(binary) so pip runs in the right environment.
                'pip': 'python -m pip install -U ' + pkg}.get(method)
    return None
