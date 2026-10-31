"""Opt-in fresh Claude system prompts on resume (BUILTINS-2)."""
from __future__ import annotations

from typing import List, Mapping, Any

CONFIG_KEY = 'claude_resume_fresh_prompt'
_RESUME_FLAGS = frozenset({'-r', '--resume', '-c', '--continue'})
_SNAPSHOT = '--system-prompt-snapshot'


def launch_args(cmd: List[str], config: Mapping[str, Any]) -> List[str]:
    """Apply to resumes only; preserve the original list when disabled.

    Default off pending a separately authorized prompt-cache measurement.
    An enabled policy replaces a previous snapshot flag, making it idempotent.
    This runs before, and independently of, the unattended fence launch gate.
    """
    if config.get(CONFIG_KEY, False) is not True:
        return cmd
    if not any(arg.split('=', 1)[0] in _RESUME_FLAGS for arg in cmd):
        return cmd
    result: List[str] = []
    skip = False
    for arg in cmd:
        if skip:
            skip = False
            continue
        if arg == _SNAPSHOT:
            skip = True
        elif not arg.startswith(_SNAPSHOT + '='):
            result.append(arg)
    return result + [_SNAPSHOT, 'off']
