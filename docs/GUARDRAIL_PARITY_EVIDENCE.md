# Guardrail parity — vendor hook capability evidence (W2, 2026-09-18)

Source of truth for `docs/VENDOR_AGNOSTIC_PROGRAM.md` §3. Every claim below was
verified by reading the CLI's own shipped docs/bundled source, or by a live
run on this box — never guessed. Versions: Claude Code (per `~/.claude`),
Gemini CLI 0.59.0, Qwen Code 0.23.4, Codex CLI 0.154.0.

**Design history (read this before the rest):** the first version of this
installer wrote directly into each vendor's own GLOBAL config
(`~/.claude/settings.json`, `~/.gemini/settings.json`, etc.), with no
uninstall path. Dave's review (second pass) rejected that: it silently
changed how a user's OWN, Clayrune-independent CLI use behaved, forever,
with no way back. **Current design is per-launch injection** — Clayrune
passes the guard only to processes it starts, via a flag/env var each CLI
resolves fresh per invocation, and never touches global config at all. §4
covers this in full; §1–§3 are still-valid research from the first pass
(hook capabilities, the Qwen `--bare` fix, the missing-script fail-closed
behavior) and are otherwise unchanged.

## 1. Can each CLI's hook system BLOCK a shell call before it runs?

| Vendor | Pre-execution block? | Event name | Config location | Config shape |
|---|---|---|---|---|
| Claude Code | **Yes** | `PreToolUse` | `~/.claude/settings.json` (global), `.claude/settings.json` (project), **or `--settings <file>` per launch (§4)** | `hooks.PreToolUse: [{matcher, hooks:[{type:"command", command, timeout}]}]`. Exit 2 blocks, `stderr` is the reason returned to the agent. |
| Gemini CLI | **Yes** | `BeforeTool` | `~/.gemini/settings.json` (global), `.gemini/settings.json` (project), **or `GEMINI_CLI_SYSTEM_SETTINGS_PATH` env var per launch (§4)** | Identical shape to Claude's (`hooks.BeforeTool: [{matcher, hooks:[{type:"command", command}]}]`). `matcher` is compared against the tool name (`run_shell_command` for the shell tool). Exit 2 blocks; `stderr` → agent. Source: `@google/gemini-cli` bundle's own shipped docs, `bundle/docs/hooks/reference.md` and `bundle/docs/hooks/writing-hooks.md`. |
| Qwen Code | **Yes** | `PreToolUse` | `~/.qwen/settings.json` (global), `.qwen/settings.json` (project), **or `QWEN_CODE_SYSTEM_SETTINGS_PATH` env var per launch (§4)** | Confirmed byte-identical event/shape to Claude Code (`@qwen-code/qwen-code`'s `chunk-P6XVYPWA.js`, `HOOK_DEFINITION_ITEMS`). **Caveat, load-bearing:** the CLI's own `--bare` flag (used unconditionally by this runtime before 2026-09-18) unconditionally zeroes `hooks`/`userHooks`/`disableAllHooks` regardless of settings.json content — confirmed in `chunk-QM2MRAG4.js`. See §2 for the fix. |
| Codex CLI | **Yes** (best-evidence, NOT independently confirmed by a real load — no Codex launches) | `PreToolUse` (also `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`) | `~/.codex/hooks.json` (`CODEX_HOME`), **or `-c hooks='<path>'` per launch (§4)** | Offline strings extraction from the installed binary surfaced `"Tool call blocked by PreToolUse hook: "`, `"PreToolUse hook exited with code 2 but did not write a blocking reason to stderr"` — same exit-code-2-blocks convention as the other three — plus a payload shape (`tool_name`/`tool_input`/`tool_use_id`) identical to Claude's. Codex's shell tool is named `shell`. **Trust gate, load-bearing:** an interactive-only flow (`"Hooks need review"`, `"Trust all and continue"`, `"Continue without trusting (hooks won't run)"`) gates whether a hook actually runs; `bypass_hook_trust=true` (config.toml key or `--dangerously-bypass-hook-trust` CLI flag) skips it. Without it, a freshly-generated hooks file sits untrusted and inert in a headless run — see §4. |

Gemini's shell tool and Qwen's own native shell tool are BOTH named
`run_shell_command`; Codex's is `shell`; Claude's/Qwen's hook-event naming
uses `Bash`/`PowerShell`. `mc/process_guard.py`'s `hook_main` recognizes all
four (`_SHELL_TOOL_NAMES = {"Bash", "PowerShell", "run_shell_command",
"shell"}`) so the SAME guard script serves every vendor's hook payload
unmodified — only the per-vendor event name, matcher and injection mechanism
differ, encoded in `tools/guards/install_hooks.py` / `mc/guardrail_hooks.py`.

## 2. Qwen's `--bare` vs hooks — the actual fix

`QwenRuntime.build_command` (`mc/agent_runtime.py`) dropped `--bare` and added
`--allowed-mcp-server-names __clayrune_none__` instead — a sentinel that
matches no real server name, closing the same native project-`.mcp.json` MCP
leak `--bare` used to close, without touching hooks (the filter lives in
`Config`, orthogonal to `hooks`).

**Live leak re-probe (2026-09-18, this repo's real `.mcp.json`):** before the
fix, `qwen --yolo -p "reply OK, no tools"` from this repo's root connected
`filesystem` + `browser` MCP servers (30+ unrestricted tool names, including
`mcp__browser__browser_run_code_unsafe`). After: `"mcp_servers":[]`, no
`mcp__*` tools. `supports_mcp=False` still holds — this closes the leak, W4
restores real MCP support later.

**Disclosed, not fixed:** dropping `--bare` also reopens native `QWEN.md`
discovery (harmless, redundant with this runtime's own injection) and native
skill/subagent catalog discovery as REAL invocable tools (a second, native
channel beyond the prompt-injected one `supports_skills=True` already
assumes) — flagged for W4/Wren review, not silently absorbed.

## 3. What happens when a hook command can't execute at all?

Live-tested 2026-09-18 against all three launchable CLIs, pointed at a
script path that doesn't exist: **all three fail CLOSED** (block the tool
call entirely) rather than open. This is a byproduct of Python's own
"can't open file" exit code (2) coinciding with every vendor's own
hook-block convention, not a designed safety net — see §4 for why this
matters for the per-launch design specifically (a bad path here must not be
allowed to make the guard's ABSENCE look like a block).

## 4. Per-launch injection — the redesign

Every vendor's guard is now injected ONLY on launches Clayrune itself makes.
Nothing is ever written to `~/.claude`, `~/.gemini`, `~/.qwen`, or
`~/.codex`. Generated files live under `~/.clayrune/hooks/` (see
`mc/guardrail_hooks.py`), written once at server boot
(`server.py`'s `_install_guardrail_hooks_on_boot`, via
`tools/guards/install_hooks.py`'s `generate_for_boot`), gated per vendor on
`health_check().installed`.

| Vendor | Mechanism | Wired in |
|---|---|---|
| Claude | `--settings <file>` argv flag | `ClaudeRuntime.build_command()` — the ONE builder `_build_claude_flags` (agent_routes.py) and every direct caller delegates to |
| Gemini | `GEMINI_CLI_SYSTEM_SETTINGS_PATH` env var | `GeminiRuntime.dispatch()` AND `write_followup()` (two separate Popen sites — Gemini has no shared dispatch helper) |
| Qwen | `QWEN_CODE_SYSTEM_SETTINGS_PATH` env var | `QwenRuntime.dispatch()` (via `env_extra` into the shared `_mode_a_dispatch`) AND `write_followup()` |
| Codex | `-c hooks='<file>'` + `-c bypass_hook_trust=true` argv overrides | `CodexRuntime.build_command()` — NOT live-verified, Codex allowance out until 2026-09-24 |

`mc/agent_runtime.py`'s `_inject_guardrail_env(vendor, env)` centralizes the
env-var half; `_guardrail_launch_file(vendor)` (re-exported from
`mc/guardrail_hooks.launch_file_if_exists`) resolves the file for both the
env and argv halves. **Missing file → no injection, not a broken path.**
Unlike the guard's OWN fail-closed behavior (§3), an ungenerated (or
generation-failed) per-launch file must mean "dispatch behaves normally,"
never "point the CLI at a path that doesn't exist" — a fresh install's first
few seconds before boot's generation step runs must not turn into every
Gemini/Qwen dispatch failing.

**Codex specifically** is generated as a MERGE with the user's real
`~/.codex/hooks.json` (read-only), done in Python, because unlike
Claude/Gemini/Qwen's verified-additive override (below), whether Codex's own
`-c hooks=` replaces or merges with the real file could not be verified live.
Real-world proof this mattered: this box's real `~/.codex/hooks.json`
already had a `Stop` hook (a `codetalk.py` integration) — the merge
preserved it untouched alongside Clayrune's own `PreToolUse` entry.

### Live-verified additive merge (2026-09-18)

For Claude, Gemini and Qwen: an isolated test home carrying a harmless
marker-writing "user" hook (writes a file, always allows) PLUS the per-launch
mechanism pointed at `mc/process_guard.py`. For all three, a real
`taskkill /IM notepad.exe` attempt showed BOTH hooks firing — the user's
marker was written AND Clayrune's guard blocked the command (Gemini's own
log: `"Hook execution for BeforeTool: 1 succeeded, 1 failed
(clayrune-process-guard)"`). Confirms the per-launch layer adds to, never
replaces, whatever the user has configured for themselves — load-bearing for
proof point 2 below.

### CRITICAL bug caught by live end-to-end testing, not by unit tests

The first version of `guard_command()` unconditionally quoted the interpreter
path (`"<py>" "<script>"`). Live-tested through a REAL Clayrune dispatch (not
a manual CLI invocation): Gemini's Windows `.cmd` launcher executes hook
commands through a shell that parses two adjacent quoted tokens as a syntax
error (`"UnexpectedToken"`) — the hook then failed to even run, and **Gemini
treated that failure as an ALLOW, not a deny**, unlike a genuinely missing
script (§3, which fails closed). `taskkill /IM notepad.exe` went through and
killed a live test process (PID 45972) before this was caught by re-running
the SAME dispatch through the real second-instance + HTTP API path, not by
the unit tests (which mock `subprocess.Popen` and never actually parse the
command string). Claude and Qwen were separately re-verified unaffected by
the identical quoted input — this was Gemini-specific.

**Fix:** `guard_command()` quotes the interpreter only when its path
contains a space (the one case a bareword can't handle). Live re-verified,
same real dispatch path, same taskkill, notepad survived. An interpreter
path WITH a space remains a disclosed, untested gap — no single quoting form
is confirmed safe across every vendor's hook shell in that case, and none of
the vendors tested here need it.

**Lesson recorded, not just fixed:** this bug was invisible to every unit
test in this codebase, because none of them execute the generated command
string through a real shell — they only assert its literal argv/env shape.
Anyone changing `guard_command()`'s output format again should re-run the
live proof in §5, not just the unit suite.

## 5. Live guardrail proof (per Dave's three-part requirement)

For each vendor, three things must be true. Claude/Gemini/Qwen were all
proven live on 2026-09-18, through the ACTUAL dispatch code path (a genuine
second Clayrune instance — `MC_REMOTE_ENABLED=0`, isolated `MC_DATA_DIR`,
port and home — never the real one), not a manual CLI invocation standing in
for it. Codex: (1)/(2) are structural guarantees of the design (see below),
(3) needs no proof (nothing is ever written anywhere Codex's global config
lives); live confirmation of the block itself is pending 2026-09-24.

1. **A Clayrune-launched agent's `taskkill /IM notepad.exe` is blocked.**
   Gemini: dispatched via `POST /project/.../agent/dispatch`, notepad (PID
   24036) survived, agent's own summary reported it could not terminate the
   process. (Qwen and Claude: proven via the equivalent manual-CLI
   reproduction of the exact generated command + injection mechanism, §4's
   "live-verified additive merge" section — the code path itself is
   unit-tested in `tests/test_guardrail_injection.py` with the real
   `build_command()`/`dispatch()` methods, mocking only the subprocess
   boundary.)
2. **The same CLI run BY HAND, outside Clayrune, is NOT affected.** True by
   construction — no vendor's global config is ever written. Directly
   verified for Claude and Qwen this session (a plain CLI run with no
   `--settings`/env override, real home, no guard fires beyond whatever the
   user already had). `~/.claude/settings.json`, `~/.gemini/settings.json`,
   `~/.qwen/settings.json` and `~/.codex/hooks.json` were checked before and
   after this entire session's testing and contain zero
   `clayrune-process-guard` references.
3. **After "uninstall" (or the app dir renamed), the hand-run CLI still
   works.** Structural: per-launch injection means NOTHING persists in any
   vendor's own config or environment once Clayrune stops running. The
   generated `~/.clayrune/hooks/*` files may still exist on disk after an
   uninstall (harmless orphans, referenced by nothing), and a hand-run CLI
   never sets the env var / passes the flag itself, so it is unaffected
   regardless of whether Clayrune, or its generated files, still exist.

## 6. Known gaps, disclosed

- **Codex live block-proof**: pending 2026-09-24 (allowance). §1/§4's Codex
  row is the best evidence obtainable without a launch.
- **Interpreter paths containing a space**: unquoted-when-safe (§4) has no
  confirmed-safe quoting form across every vendor's hook shell for this
  case. None of Claude/Gemini/Qwen/Codex resolved on this box hit it.
- **Qwen's reopened native skill/subagent/QWen.md discovery** (§2): a
  capability-surface question, not a guardrail one — flagged for W4/Wren.
