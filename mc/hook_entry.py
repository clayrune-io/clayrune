"""In-process hook entry for FROZEN builds (MC-975 follow-up, 2026-09-25).

A hook command is normally `<python> <script.py>`. In a PyInstaller build
`sys.executable` is the Clayrune app binary, and the scripts exist only as
bytecode inside the bundle, so `<app> <.../steward/fence.py>` does not run
the fence: app.py ignored the extra argv and booted a second copy of the app.
The Codex fence self-test therefore failed and every unattended Codex launch
was refused on the Mac .app.

Instead the frozen build invokes its own binary with a flag:

    <app> --clayrune-hook fence [--armed] [--self-test]
    <app> --clayrune-hook process-guard

app.py checks for HOOK_FLAG before anything else runs and exits with
run_hook()'s code, so no server, window or CLI check ever starts. Commands
are built by mc.guardrail_hooks (hook_invocation_tokens). The imports below
are static so PyInstaller's analysis bundles both hook modules.

An unknown hook name exits 2: every hook served here is a safety gate, and
exit 2 is the block contract in every vendor CLI we drive.
"""
from __future__ import annotations

import json
import sys
from typing import List, Sequence

HOOK_FLAG = '--clayrune-hook'
FENCE = 'fence'
PROCESS_GUARD = 'process-guard'


def _ensure_std_streams() -> None:
    """A windowed PyInstaller build on Windows sets sys.stdin/stdout/stderr
    to None even when the parent handed it pipes. Rebind them to the
    inherited descriptors so the hook can read its payload and write its
    block reason. Best effort: if the descriptor is not valid either, the
    stream stays None and the hook fails the way it would anyway."""
    for fd, name, mode in ((0, 'stdin', 'r'), (1, 'stdout', 'w'), (2, 'stderr', 'w')):
        if getattr(sys, name, None) is not None:
            continue
        try:
            setattr(sys, name, open(fd, mode, encoding='utf-8', closefd=False))
        except Exception:
            pass


def _run_process_guard() -> int:
    from mc import process_guard
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    return process_guard.hook_main(payload)


def _run_fence(args: Sequence[str]) -> int:
    from steward import fence
    return fence.main(list(args))


def run_hook(argv: Sequence[str]) -> int:
    """argv is everything after HOOK_FLAG: the hook name, then its own args."""
    _ensure_std_streams()
    args: List[str] = list(argv)
    name = args[0] if args else ''
    if name == FENCE:
        return _run_fence(args[1:])
    if name == PROCESS_GUARD:
        return _run_process_guard()
    if sys.stderr is not None:
        sys.stderr.write(f'Clayrune: unknown hook {name!r}; blocking fail-closed.\n')
    return 2
