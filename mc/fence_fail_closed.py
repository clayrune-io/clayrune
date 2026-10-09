"""Fail-closed steward fence for UNATTENDED Claude launches (2026-10-09).

Claude Code treats a PreToolUse command hook that cannot start, times out or
exits with an unexpected code as a non-blocking error: the tool call RUNS.
So an unattended run whose fence interpreter is gone, or whose fence.py
crashes (exit 1), passes every call unreported. Codex already closes this for
armed launches (mc/guardrail_hooks.py: fail-closed suffix + a self-test before
spawn). This module is the Claude half, applied only to the launches that
mc/launch_marker.py already treats as unattended. Attended launches are handed
back the SAME cmd list, untouched.

Two layers, both applied by `launch_cmd` at every Claude spawn site:

 1. Pre-spawn self-test (any CLI version). The fence command is run with
    `--self-test` (steward/fence.py exits 2 with a fixed token before reading
    stdin). If it does not, the launch is refused with ClaudeFenceSelfTestError,
    the Claude counterpart of CodexFenceSelfTestError. Does not exercise
    Claude's own hook shell; layer 2 covers the runtime.

 2. `onFailure: "block"` on the fence entry (CLI >= 2.1.295 only). The
    per-launch `--settings` file the cmd already names is copied with the fence
    entry (found by its `name`) given `onFailure: "block"` and a timeout in
    SECONDS. Measured on a real 2.1.295, all with `--settings` plus a project
    .claude/settings.json (docs/FENCE_FAIL_CLOSED_MEASUREMENT.md):
      * hooks from the project file and from `--settings` MERGE;
      * byte-identical commands collapse to ONE run and the `--settings` entry
        is the one kept, so its onFailure/timeout win and the fence does not
        run twice per call;
      * a different command runs in BOTH, and the `onFailure` one still blocks.
    A 2.1.293 CLI ignores the key, so older versions are left on the plain file.

`launch_cmd` raises only ClaudeFenceSelfTestError. Every other problem (no
`--settings`, version unreadable, copy not writable) degrades to the plain cmd,
the same posture as mc/skill_scoping.scoped_settings_path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mc import guardrail_hooks as _gh
from mc import launch_marker as _lm

# `onFailure` landed in Claude Code 2.1.295 (npm latest 2026-10-08).
FAIL_CLOSED_MIN_VERSION: Tuple[int, int, int] = (2, 1, 295)
# Seconds (Claude Code hook timeouts are seconds). With onFailure "block" a
# timeout now BLOCKS the call, so this sits far above the fence's real runtime
# (Codex's measured max was 0.36 s; its budget is the same 30 s).
FAIL_CLOSED_TIMEOUT_SEC = 30
# Held while a project lock may be held at the spawn site: keep it short.
SELF_TEST_TIMEOUT_SEC = 10
_VERSION_PROBE_TIMEOUT_SEC = 15
_VERSION_RETRY_AFTER_SEC = 300.0


class ClaudeFenceSelfTestError(RuntimeError):
    """Raised by launch_cmd() when an unattended Claude launch's steward fence
    failed its pre-spawn self-test. Claude Code runs a hook that cannot start
    as a non-blocking error, so launching anyway would run with no fence."""


def is_unattended_launch(trigger_type: Optional[str]) -> bool:
    """Same predicate launch_env uses to set the unattended marker."""
    return (trigger_type or '') in _lm.UNATTENDED_TRIGGER_TYPES and _lm._fence_enabled()


# ── Layer 1: pre-spawn self-test ────────────────────────────────────────────

def claude_fence_self_test(*, timeout: float = SELF_TEST_TIMEOUT_SEC) -> Tuple[bool, str]:
    """Run the exact fence command a Claude hook entry carries, plus
    `--self-test`. Returns (ok, detail); never raises."""
    try:
        from steward import core as _steward_core
        argv = shlex.split(_steward_core._fence_command(), posix=True)
    except Exception as e:
        return False, f'could not build the fence command: {e}'
    return _gh.run_fence_self_test(argv + [_gh.FENCE_SELF_TEST_ARG], timeout=timeout,
                                   what='the fence')


# ── Layer 2: CLI version, cached ────────────────────────────────────────────

_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_version_cache: Dict[Tuple[Any, ...], Tuple[Optional[Tuple[int, int, int]], float]] = {}
_version_lock = threading.Lock()


def _binary_stamp(binary: str) -> Tuple[Any, ...]:
    """Identity of the installed CLI. An npm upgrade replaces the package under
    node_modules but leaves the `claude.cmd` shim alone, so the package.json
    next to the shim is part of the key (mirrors CodexRuntime._binary_stamp)."""
    stamp: List[Any] = [str(binary)]
    for p in (binary,
              str(Path(binary).parent / 'node_modules' / '@anthropic-ai'
                  / 'claude-code' / 'package.json')):
        try:
            real = os.path.realpath(p)
            st = os.stat(real)
            stamp += [real, st.st_mtime_ns, st.st_size]
        except OSError:
            stamp += [None, 0, 0]
    return tuple(stamp)


def claude_cli_version(binary: str) -> Optional[Tuple[int, int, int]]:
    """`claude --version` as a tuple, memoised per binary identity (one
    subprocess per install, not per spawn). A failed probe is retried after
    _VERSION_RETRY_AFTER_SEC. None when unreadable."""
    key = _binary_stamp(binary)
    now = time.monotonic()
    with _version_lock:
        hit = _version_cache.get(key)
        if hit is not None and (hit[0] is not None or now - hit[1] < _VERSION_RETRY_AFTER_SEC):
            return hit[0]
    ver: Optional[Tuple[int, int, int]] = None
    try:
        kwargs: Dict[str, Any] = {}
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        r = subprocess.run([str(binary), '--version'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=_VERSION_PROBE_TIMEOUT_SEC, **kwargs)
        m = _VERSION_RE.search((r.stdout or r.stderr or ''))
        if m:
            ver = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception as e:
        _warn(f'claude --version probe failed: {e}')
    with _version_lock:
        _version_cache[key] = (ver, now)
    return ver


def supports_on_failure(binary: str) -> bool:
    ver = claude_cli_version(binary)
    return ver is not None and ver >= FAIL_CLOSED_MIN_VERSION


# ── Layer 2: the fail-closed copy of the per-launch settings file ───────────

def fail_closed_settings_path(src: Path, out_dir: Optional[Path] = None) -> Optional[Path]:
    """Copy `src` with every hook named FENCE_HOOK_NAME given `onFailure:
    "block"` and `timeout: FAIL_CLOSED_TIMEOUT_SEC`; return the copy's path.

    Content-addressed, like mc/skill_scoping.scoped_settings_path, so a respawn
    gets byte-for-byte the same file. None when `src` has no fence entry or the
    copy cannot be written (the caller then keeps `src`)."""
    try:
        data = json.loads(Path(src).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return None
        found = False
        for groups in (data.get('hooks') or {}).values():
            for group in groups if isinstance(groups, list) else ():
                for h in (group.get('hooks') or []) if isinstance(group, dict) else ():
                    if isinstance(h, dict) and h.get('name') == _gh.FENCE_HOOK_NAME:
                        h['onFailure'] = 'block'
                        h['timeout'] = FAIL_CLOSED_TIMEOUT_SEC
                        found = True
        if not found:
            return None
        text = json.dumps(data, indent=2, sort_keys=True)
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
        out_dir = out_dir or (_gh.hooks_dir() / 'unattended')
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'claude-settings-{digest}.json'
        try:
            current = path.read_text(encoding='utf-8') if path.is_file() else None
        except OSError:
            current = None
        if current != text:
            tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_text(text, encoding='utf-8')
            tmp.replace(path)
        return path
    except Exception as e:
        _warn(f'fail-closed settings copy failed ({e}); keeping the plain file')
        return None


def _warn(msg: str) -> None:
    try:
        from mc.core import _log
        _log(f'[fence-fail-closed] {msg}', level='warn')
    except Exception:
        pass


# ── The one entry point the spawn sites call ────────────────────────────────

def launch_cmd(cmd: Sequence[str], trigger_type: Optional[str]) -> List[str]:
    """`cmd` as it should be handed to Popen for a session of `trigger_type`.

    Attended: `cmd` itself, unchanged. Unattended: runs the fence self-test
    (raises ClaudeFenceSelfTestError on failure) and, on a CLI that supports
    `onFailure`, swaps the `--settings` file for its fail-closed copy."""
    if not is_unattended_launch(trigger_type):
        return cmd  # type: ignore[return-value]
    ok, detail = claude_fence_self_test()
    if not ok:
        raise ClaudeFenceSelfTestError(
            'refusing unattended Claude launch: the steward fence self-test '
            f'failed ({detail}). A Claude hook that cannot run lets the tool '
            'call through, so this run would have had no fence.')
    out = list(cmd)
    if '--settings' in out:
        i = out.index('--settings')
        if i + 1 < len(out) and supports_on_failure(out[0]):
            repl = fail_closed_settings_path(Path(out[i + 1]))
            if repl is not None:
                out[i + 1] = str(repl)
    return out
