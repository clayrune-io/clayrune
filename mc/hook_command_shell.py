"""How a hook command line is spelled for the shell that will run it (MC-1179 AP5).

`guard_shell_command` used to emit one string for every vendor. With an
interpreter path that holds a space (a Windows user name with a space, Python
under `Program Files`) the only spelling that survives is shell-specific:

  bash, cmd.exe  `"<py>" <script>`     a leading quoted word is a command
  PowerShell     `& "<py>" <script>`   a leading quoted word is a STRING
                                       EXPRESSION, so the plain form is a
                                       parse error (measured: exit 1)

Codex and Gemini read a hook that fails to run as ALLOW, so the plain form made
the process guard a silent no-op for them. A path with no space needs none of
this and is emitted bare, exactly as before.

Codex's value travels through npm's `codex.CMD`, whose `%*` line cmd.exe
re-parses, and an `&` there split the `-c` argument and killed the launch at
config load (the same measurement `guardrail_hooks.codex_fence_hook_command`
records). Its PowerShell spelling is the dot operator instead, which carries
no cmd.exe metacharacter and was measured to run the guard (exit 2).
"""
from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

SH = 'sh'
CMD = 'cmd'
POWERSHELL = 'powershell'
# PowerShell, but the string rides inside another process's argv (Codex's
# inline `-c`), so the cmd.exe metacharacter `&` must stay out of it.
POWERSHELL_INLINE = 'powershell-inline'

_POWERSHELL_PREFIX = {POWERSHELL: '& ', POWERSHELL_INLINE: '. '}

# The shell each vendor's hook runner uses on Windows. Gemini runs hooks only
# through PowerShell (gemini-cli getShellConfiguration, every Windows branch);
# Claude runs them through Git Bash; Qwen picks by environment (below).
_VENDOR_SHELL = {'claude': SH, 'gemini': POWERSHELL}


def vendor_hook_shell(vendor: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The shell `vendor`'s hook runner uses, or None when it is not known.

    Qwen runs hooks through PowerShell when `ComSpec` names it and through
    cmd.exe otherwise (Clayrune blanks MSYSTEM for Qwen, so git-bash is out).
    """
    if vendor in _VENDOR_SHELL:
        return _VENDOR_SHELL[vendor]
    if vendor == 'qwen':
        comspec = (env if env is not None else os.environ).get('ComSpec') or ''
        name = comspec.replace('\\', '/').rsplit('/', 1)[-1].lower()
        return POWERSHELL if name in ('powershell.exe', 'pwsh.exe') else CMD
    return None


def quote_hook_tokens(tokens: Sequence[str], shell: Optional[str] = None) -> str:
    """Join `tokens` into one command line for `shell`.

    Each token holding a space is double-quoted. When the interpreter (the
    first token) is quoted and the shell is PowerShell, the call prefix is
    added. Off Windows no PowerShell form is ever emitted: every vendor runs
    hooks through `sh -c` there.
    """
    quoted = [f'"{t}"' if ' ' in t else t for t in tokens]
    prefix = _POWERSHELL_PREFIX.get(shell or '', '')
    if os.name == 'nt' and prefix and quoted and quoted[0].startswith('"'):
        quoted[0] = prefix + quoted[0]
    return ' '.join(quoted)
