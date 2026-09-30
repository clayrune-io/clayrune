"""PATH / binary-directory helpers for macOS and Linux.

Leaf module (stdlib only) so both the launcher (`app.py`), the server
(`server.py` boot), the provider runtimes (`mc/agent_runtime.py`) and the
Providers-panel install flow can share ONE definition of "where do user-level
CLIs live".

Why this exists: `installer/start.sh` runs `python server.py`, which inherits
whatever PATH the desktop entry / systemd unit gave it — typically without
~/.nvm, ~/.npm-global or ~/.local/bin. A CLI installed through nvm
(`~/.nvm/versions/node/<ver>/bin/claude`) therefore latched `cli_not_found`
forever, and since the npm shim is `#!/usr/bin/env node`, resolving the shim
by absolute path is not enough — `node` has to be on PATH too.
"""
import os
import re
import sys
from pathlib import Path
from typing import List, Optional


def _home() -> Path:
    return Path(os.environ.get('HOME', str(Path.home())))


def _semver_key(name: str):
    """Sort key for an nvm version dir such as 'v20.20.2' (numeric, not
    lexical — v20 must beat v9). Non-numeric dirs sort lowest."""
    nums = re.findall(r'\d+', name)
    return (1, tuple(int(n) for n in nums)) if nums else (0, ())


def nvm_bin_dirs(home: Optional[Path] = None) -> List[Path]:
    """The nvm-managed node `bin` dir to search, as a list (empty = no nvm).

    `$NVM_BIN` wins when set and it exists (it is the node the operator's
    shell is actually using). Otherwise the newest
    `~/.nvm/versions/node/*/bin` by semantic version. Returns existing
    directories only; never raises."""
    try:
        env_bin = os.environ.get('NVM_BIN', '').strip()
        if env_bin and os.path.isdir(env_bin):
            return [Path(env_bin)]
        root = (home or _home()) / '.nvm' / 'versions' / 'node'
        if not root.is_dir():
            return []
        versions = sorted((d for d in root.iterdir() if (d / 'bin').is_dir()),
                          key=lambda d: _semver_key(d.name), reverse=True)
        return [versions[0] / 'bin'] if versions else []
    except Exception:
        return []


def augment_unix_path() -> bool:
    """Make Homebrew/npm/nvm/native-installer binaries visible on macOS/Linux.

    GUI apps launched from Finder/Dock inherit launchd's minimal PATH
    (/usr/bin:/bin:/usr/sbin:/sbin), and a server started by start.sh has the
    desktop session's, so node/npm/claude installed under Homebrew,
    ~/.local/bin, ~/.claude/bin or nvm are invisible — both to our own
    `claude --version` check and to the `claude` shim when it execs `node`.

    All dirs are PREPENDED, nvm's included, matching what nvm's own shell
    init does with $NVM_BIN. The CLI shim is `#!/usr/bin/env node`, so it must
    run under the node it was installed with: appending nvm would let a distro
    node win (Ubuntu 22.04's apt nodejs is v12, below Claude Code's minimum)
    and break the very CLI this resolves. Only directories that exist are
    added; idempotent. Returns True when PATH changed. No-op on Windows (PATH
    there is refreshed from the registry)."""
    if sys.platform == 'win32':
        return False
    home = _home()
    front = [
        '/opt/homebrew/bin',                  # Homebrew (Apple Silicon)
        '/usr/local/bin',                     # Homebrew (Intel) / npm default
        str(home / '.local' / 'bin'),         # native installer / pipx
        str(home / '.claude' / 'bin'),        # Claude native installer
        str(home / '.npm-global' / 'bin'),    # npm custom prefix
        str(home / '.nvm' / 'current' / 'bin'),
    ]
    back = [str(d) for d in nvm_bin_dirs(home)]
    parts = os.environ.get('PATH', '').split(os.pathsep)
    changed = False
    for d in front:
        if d and os.path.isdir(d) and d not in parts:
            parts.insert(0, d)
            changed = True
    for d in back:
        if d and os.path.isdir(d) and d not in parts:
            parts.insert(0, d)
            changed = True
    if changed:
        os.environ['PATH'] = os.pathsep.join(p for p in parts if p)
    return changed
