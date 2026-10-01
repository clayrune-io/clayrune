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


def install_method(binary_path: Any, platform: Optional[str] = None) -> str:
    """'standalone' | 'standalone-windows' | 'npm' | 'brew' | '' (unknown),
    read off WHERE the resolved binary lives -- never off what is merely on PATH.

    standalone: OpenAI's own installer (chatgpt.com/codex/install.sh), whose
      script sets STANDALONE_ROOT=$CODEX_HOME/packages/standalone and links
      ~/.local/bin/codex into it (verified 2026-10-01); on Windows the native
      installer's %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin.
    npm: the real path sits under node_modules/ or an npm/nvm prefix.
    brew: the real path sits under a Cellar/ or Caskroom/.
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
        if '/node_modules/' in c or '/.nvm/' in c or '/.npm-global/' in c or '/npm/codex' in c:
            return 'npm'
        if '/programs/openai/codex/' in c:
            return 'standalone-windows'
    return ''


def update_command(name: str, binary_path: Any, platform: Optional[str] = None) -> Optional[str]:
    """Shell command string that updates CLI `name` the way it was installed, or
    None when there is no safe answer (unknown install method, or a CLI this
    module has no table for). None means: report, do not guess an updater."""
    if name in _SELF_UPDATING:
        return _SELF_UPDATING[name]
    if name == 'codex':
        return CODEX_UPDATE_COMMANDS.get(install_method(binary_path, platform))
    return None
