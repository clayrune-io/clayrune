"""Which condense executor a config selects, and the one place the default is
spelled (MC-1179 ML16).

`condense_mode` picks how auto-condense does its work:

  * `'structured'`: one toolless model call returns a JSON plan and the server
    applies it under the memory write lock.
  * `'agent'`: a tool-capable `claude -p` agent is launched with the Write
    tool and rewrites the files itself.

The default is `'structured'`. It was `'agent'` until 2026-11-17, kept "until
the structured path is telemetry-validated"; the default was then the one mode
its author did not run. The old default was spelled as a literal in four
places (the config defaults, both readers in `mc/memory.py`, the memory eval
tool), which is how a default drifts. Everything reads it from here.

A config that says `'agent'` still gets agent mode. Nothing rewrites a saved
config. Note that saving any setting writes the whole config, so an install
that saved one before this change has `'agent'` on disk and keeps it.

A missing, empty or null value is the default. Any other value that is not
`'structured'` selects the agent path, as it always has.

Stdlib only, no imports from `mc`.
"""
from __future__ import annotations

from typing import Any, Mapping

KEY = 'condense_mode'
STRUCTURED = 'structured'
AGENT = 'agent'
DEFAULT = STRUCTURED


def configured(config: Mapping[str, Any]) -> str:
    """The mode this config selects; the default when it does not say."""
    return config.get(KEY, DEFAULT) or DEFAULT


def is_structured(config: Mapping[str, Any]) -> bool:
    return configured(config) == STRUCTURED
