"""Minimum Claude Code version per Claude model id.

The Claude twin of CODEX_GPT6_CATALOG_MIN_CLI (mc/agent_runtime.py), with the
same policy: never hand `--model` a slug the installed CLI predates. Below the
floor the flag is dropped (the CLI's own default model runs) and a sentence
naming the model and the CLI version says so. An unreadable version passes the
model through, the same as the Codex gate: no evidence is not a refusal.

Why it matters: an older CLI does not reject an unknown id. Measured
2026-11-10 with claude 2.1.311 and `--model claude-fable-5-2`: rc=0, it
answered, stderr carried only an `unrecognized_model` warning. The Claude Code
changelog for 2.1.312-2.1.316 describes early compaction and silent model
switches for ids the CLI does not know, so the run looks fine and is not.

A table, pure gate functions, and a memoised `--version` probe.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

Version = Tuple[int, int, int]

# claude-fable-5-2 needs Claude Code >= 2.1.321 (backlog 5781abaa, from the
# review of feat/fable-5-2 @ 0c2ba73c, 2026-11-10). Add a row when a later
# model ships with its own floor; ids not listed here are never gated.
MODEL_MIN_CLI: Dict[str, Version] = {
    'claude-fable-5-2': (2, 1, 321),
}

_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')


def version_tuple(text: Optional[str]) -> Optional[Version]:
    """(major, minor, patch) from `claude --version` text, None if unparseable."""
    m = _VERSION_RE.search(text or '')
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def floor_for(model: Optional[str]) -> Optional[Version]:
    """The minimum CLI for `model`, None when it has no floor. A context-window
    suffix such as `claude-fable-5-2[1m]` is the same model."""
    base = (model or '').strip().split('[', 1)[0].lower()
    return MODEL_MIN_CLI.get(base)


def gate_model(model: str, cli_version: Optional[str]) -> Tuple[str, str]:
    """(model to pass, warning). Below the floor: ('', one-sentence warning).
    Otherwise (model, '') -- including when the version cannot be parsed."""
    floor = floor_for(model)
    if floor is None:
        return model, ''
    v = version_tuple(cli_version)
    if v is None or v >= floor:
        return model, ''
    return '', (f"Model '{model}' needs Claude Code "
                f"{'.'.join(str(x) for x in floor)} or newer, and this CLI is "
                f"{'.'.join(str(x) for x in v)}, so it was not requested; "
                f"Claude's own default model is used for this run. "
                f"Update Claude Code to use it.")


# `claude --version` text per binary, re-probed after _VERSION_TTL so an in-place
# update (npm keeps the same shim path) is seen without a server restart.
_VERSION_TTL = 300.0
_version_cache: Dict[Tuple[str, float, int], Tuple[float, Optional[str]]] = {}
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


def installed_version(binary: Any, *, run=subprocess.run) -> Optional[str]:
    """`claude --version` first line, memoised for _VERSION_TTL; None when it
    cannot be read. Never raises. `run` is injectable for tests."""
    if not binary:
        return None
    try:
        st = os.stat(str(binary))
        key = (str(binary), st.st_mtime, st.st_size)
    except OSError:
        key = (str(binary), 0.0, 0)
    hit = _version_cache.get(key)
    if hit and time.monotonic() - hit[0] < _VERSION_TTL:
        return hit[1]
    ver = None
    try:
        r = run([str(binary), '--version'], capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=30,
                creationflags=_NO_WINDOW)
        raw = (r.stdout or r.stderr or '').strip()
        ver = raw.splitlines()[0] if raw else None
    except Exception as e:
        from mc.core import _log
        _log(f"[claude] --version probe failed: {e}", flush=True)
    _version_cache[key] = (time.monotonic(), ver)
    return ver


def gate_model_for_binary(model: str, binary: Any, *, run=subprocess.run
                          ) -> Tuple[str, str]:
    """gate_model() for the installed `binary`. Probes only for a model that has
    a floor, so every other launch pays nothing."""
    if floor_for(model) is None:
        return model, ''
    return gate_model(model, installed_version(binary, run=run))
