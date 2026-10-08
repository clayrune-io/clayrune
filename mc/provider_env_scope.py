"""Scope saved provider credentials to the provider they were saved under.

Settings > Agent Providers saves env vars (data/provider_env.json) per provider
and the server writes every one of them into its own os.environ so non-Claude
CLIs (Goose, Aider, Qwen, ...) inherit them at spawn. Claude children inherit
the same environ, and Claude Code ranks ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
ABOVE the claude.ai subscription login (and `claude -p` asks no approval), so an
ANTHROPIC_API_KEY saved for Goose/Aider/Qwen silently moved every Claude run
from the user's plan onto API billing.

`scope_claude_env` is applied to the env of every Claude child. For each guarded
name that a NON-claude provider saved, the Claude child gets:
  1. the value saved under the claude provider, else
  2. the value the server's own shell had at process start, else
  3. nothing (the var is removed, so the subscription login is used).
A guarded name nobody else saved is left exactly as it is in the env.

The original-env snapshot is taken at import, before `_hydrate_provider_env_into_os`
(agent_routes.wire) or `agent_provider_set_env` can write anything.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Mapping, MutableMapping, Optional

# Auth vars Claude Code prefers over the subscription login.
CLAUDE_GUARDED_ENV = frozenset({'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'})

CLAUDE_PROVIDER = 'claude'

_ORIGINAL_ENV: Dict[str, str] = {
    k.upper(): v for k, v in os.environ.items() if k.upper() in CLAUDE_GUARDED_ENV
}

_lock = threading.Lock()
# provider -> {guarded name -> saved value}. Guarded names only: every other
# saved var stays reachable through os.environ for all runtimes, as before.
_saved: Dict[str, Dict[str, str]] = {}


def note_saved(provider: str, key: str, value: Any) -> None:
    """Record that `provider` saved `key`=`value`. Empty/None clears the record
    (agent_provider_set_env treats an empty value as "clear the override")."""
    name = (key or '').upper()
    if name not in CLAUDE_GUARDED_ENV:
        return
    with _lock:
        if value is None or str(value) == '':
            _saved.get(provider, {}).pop(name, None)
        else:
            _saved.setdefault(provider, {})[name] = str(value)


def scope_claude_env(env: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """Apply the scoping above to `env` in place and return it."""
    with _lock:
        by_provider = {p: dict(kv) for p, kv in _saved.items() if kv}
    for name in CLAUDE_GUARDED_ENV:
        if not any(p != CLAUDE_PROVIDER and name in kv for p, kv in by_provider.items()):
            continue
        # Windows env names are case-insensitive; drop every spelling.
        for k in [k for k in env if k.upper() == name]:
            del env[k]
        value = by_provider.get(CLAUDE_PROVIDER, {}).get(name) or _ORIGINAL_ENV.get(name)
        if value:
            env[name] = value
    return env


def claude_child_env(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """A scoped copy of `base` (default: the server's environ) for a Claude
    child that has no trigger_type (probes, one-shots, housekeeping). Spawn
    sites that do have one go through launch_marker.launch_env, which applies
    the same scoping."""
    return scope_claude_env(dict(os.environ if base is None else base))
