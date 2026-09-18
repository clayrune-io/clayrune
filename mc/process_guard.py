"""Pure process-termination guard for agent Bash/PowerShell tool calls.

This is a tripwire, not a sandbox: commands that cannot be parsed are allowed.
Interactive and delegated sessions remain unrestricted; callers opt into this
guard only for an agent's shell hook.  Image-name termination is rejected for
every image, not a hand-maintained protected-process list.  PID termination is
rejected only for currently detected Clayrune listener PIDs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Iterable, Optional


_IMAGE_PATTERNS = (
    re.compile(r"\btaskkill\b[^\n;&|]*?/(?:i|im)\s+(?:\"[^\"]+\"|'[^']+'|\S+)", re.I),
    re.compile(r"\bstop-process\b[^\n;&|]*?-name\s+(?:\"[^\"]+\"|'[^']+'|\S+)", re.I),
    re.compile(r"\bget-process\b[^\n;&|]*\|\s*stop-process\b", re.I),
    re.compile(r"\b(?:pkill|killall)\b(?![^\n;&|]*--?pid\b)[^\n;&|]*\S", re.I),
    re.compile(r"\bwmic\b[^\n]*?process[^\n]*?\bname\s*=", re.I),
)
_PID_PATTERNS = (
    re.compile(r"\btaskkill\b[^\n;&|]*?/(?:p|pid)\s+(\d+)", re.I),
    re.compile(r"\bstop-process\b[^\n;&|]*?-id\s+(\d+)", re.I),
    re.compile(r"\bkill\b\s+(?:-\d+\s+|-9\s+|-KILL\s+|-f\s+)?(\d+)", re.I),
)


def guard_command(command: str, listener_pids: Iterable[str]) -> Optional[str]:
    """Return a human-readable block reason, or ``None`` to allow."""
    if not isinstance(command, str) or not command.strip():
        return None
    for pattern in _IMAGE_PATTERNS:
        match = pattern.search(command)
        if match:
            return (
                "process guard: image-name termination is blocked "
                f"({match.group(0).strip()!r}); only terminate a PID you spawned"
            )
    listeners = {str(pid).strip() for pid in listener_pids if str(pid).strip()}
    for pattern in _PID_PATTERNS:
        for match in pattern.finditer(command):
            if match.group(1) in listeners:
                return (
                    "process guard: refusing to terminate Clayrune listener "
                    f"PID {match.group(1)}"
                )
    return None


def clayrune_listener_pids() -> set[str]:
    ports = {"5199"}
    configured = os.environ.get("MC_PORT", "").strip()
    if configured:
        ports.add(configured)
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            timeout=5, check=False)
    except Exception:
        return set()
    found: set[str] = set()
    for line in (result.stdout or "").splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[1].rsplit(":", 1)[-1] in ports:
            found.add(parts[-1])
    return found


# Shell-tool names across the four vendor CLIs this guard is installed into:
# Claude Code and Qwen Code's PreToolUse both name their shell tool "Bash" /
# "PowerShell"; Gemini CLI's BeforeTool and Qwen Code's own native shell tool
# (confirmed live, 2026-09-18: qwen-code bundles gemini-cli's shell tool
# verbatim) both name it "run_shell_command" instead. Codex CLI's own shell
# tool is named "shell" (mc/agent_runtime.py CodexRuntime, confirmed by its
# parse_event handling and tool_use event fixtures) and its PreToolUse hook
# payload shape matches Claude's (tool_name/tool_input, confirmed by reading
# codex.exe's embedded JSON schema strings, 2026-09-18 — see
# docs/GUARDRAIL_PARITY_EVIDENCE.md §1). One guard, one set.
_SHELL_TOOL_NAMES = {"Bash", "PowerShell", "run_shell_command", "shell"}


def hook_main(payload: object) -> int:
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") not in _SHELL_TOOL_NAMES:
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    reason = guard_command(command or "", clayrune_listener_pids())
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    raise SystemExit(hook_main(payload))
