"""Per-launch guardrail hook file locations — shared by the generator
(tools/guards/install_hooks.py, run at Clayrune startup) and every runtime
that injects a launch flag/env var pointing at one (mc/agent_runtime.py,
mc/blueprints/agent_routes.py's `_build_claude_flags`).

W2 redesign (docs/VENDOR_AGNOSTIC_PROGRAM.md §3), per Dave's review: the
first version of this installer wrote into the user's own GLOBAL CLI config
(~/.claude, ~/.gemini, ~/.qwen, ~/.codex) with no uninstall path — a user who
removes Clayrune, or simply runs Claude/Gemini/Qwen by hand with Clayrune
nowhere in the picture, was left with a guard silently changed underneath
them forever. Per-launch injection fixes that at the root: Clayrune passes
the guard ONLY to processes it starts (a flag or env var each CLI resolves
for THAT invocation only — verified per vendor, see
docs/GUARDRAIL_PARITY_EVIDENCE.md §4), and never touches global config.

Files live under `~/.clayrune/hooks/` (NOT DATA_DIR — this is machine/user
config, not a project record, and `~/.clayrune/` already holds the secrets
vault and other Clayrune-owned, non-project state) and are entirely
Clayrune's own — nothing else ever reads or writes them, so generation is a
wholesale overwrite, not a preserve-and-merge like a real global settings
file would need. The one exception is Codex: unlike Claude/Gemini/Qwen (each
empirically verified, 2026-09-18, to MERGE a per-launch settings layer with
the user's real one rather than replace it), Codex's own merge behavior for
its `-c hooks=<path>` override could not be verified without a live launch —
so the codex file is generated as a MERGE of the user's real
`~/.codex/hooks.json` (read-only) with Clayrune's own tagged entry, done in
Python rather than trusted to the CLI, so it is safe either way.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# {vendor: filename under ~/.clayrune/hooks/}
LAUNCH_FILENAMES = {
    'claude': 'claude-settings.json',
    'gemini': 'gemini-settings.json',
    'qwen': 'qwen-settings.json',
    'codex': 'codex-hooks.json',
}


def clayrune_home() -> Path:
    return Path.home() / '.clayrune'


def hooks_dir(clayrune_home_dir: Optional[Path] = None) -> Path:
    return (clayrune_home_dir or clayrune_home()) / 'hooks'


def launch_file_path(vendor: str, clayrune_home_dir: Optional[Path] = None) -> Path:
    return hooks_dir(clayrune_home_dir) / LAUNCH_FILENAMES[vendor]


def launch_file_if_exists(vendor: str, clayrune_home_dir: Optional[Path] = None) -> Optional[Path]:
    """The generated file's path, or None if it hasn't been generated yet.

    Injection call sites use this, never `launch_file_path` directly: a
    dispatch that fires before the boot-time generation step has run (or
    after it failed) must add NO flag at all, rather than point a real CLI
    at a file that doesn't exist — the first version of this design pointed
    at a path that could go missing and learned the hard way (live-tested,
    docs/GUARDRAIL_PARITY_EVIDENCE.md §1a) that every vendor treats an
    unreadable hook command as a DENY, not a silent pass-through. That was
    an acceptable, disclosed tradeoff for a guard that's supposed to always
    be there; it is NOT acceptable for optional per-launch injection, whose
    entire point is that dispatch behaves normally when nothing has gone
    wrong. Missing here means "don't inject," full stop.
    """
    p = launch_file_path(vendor, clayrune_home_dir)
    return p if p.is_file() else None
