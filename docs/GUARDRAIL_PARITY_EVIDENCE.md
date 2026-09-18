# Guardrail parity — vendor hook capability evidence (W2, 2026-09-18)

Source of truth for `docs/VENDOR_AGNOSTIC_PROGRAM.md` §3. Every claim below was
verified by reading the CLI's own shipped docs/bundled source, or by a live
run on this box — never guessed. Versions: Claude Code (per `~/.claude`),
Gemini CLI 0.59.0, Qwen Code 0.23.4, Codex CLI 0.154.0.

## 1. Can each CLI's hook system BLOCK a shell call before it runs?

| Vendor | Pre-execution block? | Event name | Config location | Config shape |
|---|---|---|---|---|
| Claude Code | **Yes** (already live on this box) | `PreToolUse` | `~/.claude/settings.json` (global), `.claude/settings.json` (project) | `hooks.PreToolUse: [{matcher, hooks:[{type:"command", command, timeout}]}]`. Exit 2 blocks, `stderr` is the reason returned to the agent. |
| Gemini CLI | **Yes** | `BeforeTool` | `~/.gemini/settings.json` (global), `.gemini/settings.json` (project) | Identical shape to Claude's (`hooks.BeforeTool: [{matcher, hooks:[{type:"command", command}]}]`). `matcher` is compared against the tool name (`run_shell_command` for the shell tool). Exit 2 blocks; `stderr` → agent. Structured `{"decision":"deny","reason":...}` on stdout with exit 0 is the documented idiomatic form; exit 2 is documented as the "emergency brake" — both work. Source: `@google/gemini-cli` bundle's own shipped docs, `bundle/docs/hooks/reference.md` and `bundle/docs/hooks/writing-hooks.md` (not third-party — these ship inside the npm package). |
| Qwen Code | **Yes** | `PreToolUse` | `~/.qwen/settings.json` (global), `.qwen/settings.json` (project) | Confirmed byte-identical event/shape to Claude Code by reading the bundled settings schema (`@qwen-code/qwen-code`'s `chunk-P6XVYPWA.js`, `HOOK_DEFINITION_ITEMS`: same `matcher`/`sequential`/`hooks:[{type,command,url,prompt,...}]` object, same event list: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `PreCompact`, etc.). Qwen Code is a fork of Claude Code's own agent engine (not gemini-cli's, despite qwen-code bundling gemini-cli's shell tool — see below), which is why the hook layer matches Claude's exactly while the shell tool's own name doesn't. **Caveat, load-bearing:** the CLI's own `--bare` flag (used unconditionally by this runtime before 2026-09-18) unconditionally zeroes `hooks`/`userHooks`/`disableAllHooks` regardless of settings.json content — confirmed in the bundled config loader, `chunk-QM2MRAG4.js`: `hooks: bareMode \|\| safeMode ? void 0 : settings.hooks`, `disableAllHooks: bareMode \|\| safeMode ? true : ...`. There is no CLI-flag or settings.json channel that survives bare mode to pass a hook config through it — `hooksConfig` is a `loadCliConfig()` **library** parameter with zero CLI-flag surface (confirmed by reading the same chunk; Clayrune shells out to the `qwen` binary, it does not embed the library). So `--bare` and hooks were mutually exclusive; see §2 below for how this runtime now resolves that. |
| Codex CLI | **No** | — | — | `codex --help` (0.154.0) lists no `hooks` subcommand and no hooks-adjacent config surface anywhere in its command tree (`agents`, `exec`, `review`, `login`, `mcp`, `plugin`, `app-server`, `sandbox`, `debug`, `resume`, `queue`, `archive`, `delete`, `migrate-rollouts`, `fork`, `cloud`, `exec-server`, `features` — none are hooks). No hooks doc ships with the package either. This is a genuine vendor gap, not a missed flag: there is currently no way to install `mc/process_guard.py` (or any guard) into Codex's own execution path. Tracked as pending until Codex ships a hook mechanism; not silently worked around. Live proof deferred to after 2026-09-24 (Codex allowance returns) per `VENDOR_AGNOSTIC_PROGRAM.md` §8a — this row itself does not depend on allowance, only the live block-proof does, and Codex has no mechanism to test regardless. |

Gemini's shell tool and Qwen's own native shell tool are BOTH named
`run_shell_command` (qwen-code bundles gemini-cli's shell tool verbatim,
confirmed by grepping both packages' bundles for the literal string) — NOT
`Bash`/`PowerShell` the way Claude's and Qwen's OWN hook *event* naming would
suggest. `mc/process_guard.py`'s `hook_main` recognizes all three names
(`_SHELL_TOOL_NAMES = {"Bash", "PowerShell", "run_shell_command"}`, added
2026-09-18) so the SAME guard script serves every vendor's hook config
unmodified — only the per-vendor event name (`BeforeTool` vs `PreToolUse`)
and matcher string differ, and that's encoded in the installer
(`tools/guards/install_hooks.py`), not in the guard.

## 2. Qwen's `--bare` vs hooks — the actual fix

`QwenRuntime.build_command` (`mc/agent_runtime.py`) dropped `--bare` and added
`--allowed-mcp-server-names __clayrune_none__` instead — a sentinel that
matches no real server name, so `Config.getMcpServers()`'s
`matchesAnyServerPattern` filter (confirmed in `@qwen-code/qwen-code`'s
`chunk-DCRVSIK6.js`) drops every server `assembleMcpServers()` finds,
including a project's own `.mcp.json`, the same leak `--bare` used to close —
but the filter lives entirely inside `Config`, orthogonal to `hooks`, so hooks
now load.

### Live leak re-probe (2026-09-18, this repo's real `.mcp.json`: `filesystem` + `browser` MCP servers)

**Before the fix — bare dropped, nothing else changed** (reproducing the
original leak against this repo's own root):

```
$ qwen --output-format stream-json --include-partial-messages --yolo \
    -m qwen3-coder-plus -p "reply OK, no tools"
...
"mcp_servers":[{"name":"filesystem","status":"connected"},
                {"name":"browser","status":"connected"}]
```
`tools` included `mcp__browser__browser_run_code_unsafe`,
`mcp__filesystem__write_file`, and 30+ other unrestricted filesystem/browser
tool names — the exact leak `QwenRuntime`'s docstring described.

**After the fix — `--allowed-mcp-server-names __clayrune_none__` added, same
repo root, same command:**

```
"mcp_servers":[]
```
No `mcp__*` tool name anywhere in `tools`. `supports_mcp=False` (declared in
`capabilities()`) still holds — this closes the leak, it does not restore MCP
support (that stays W4's job: swap the sentinel for Clayrune's real resolved
server names).

### Disclosed, NOT fixed by this change (flagged for W4/Wren review)

Dropping `--bare` also reopens native `QWEN.md` project-context discovery
(redundant with this runtime's own `context_injection='prepend'`, not a
correctness bug) **and** native skill/slash-command/subagent catalog
discovery from the real Qwen config — live-observed in the same probe: even
from a scratch cwd with no project `.claude`/`.qwen` directory at all, the
`system`/`init` envelope's `slash_commands`/`agents` arrays listed this box's
real global skills and subagents (`mc-memory-search`, `claude-code`, `codex`,
etc.) as real, invocable tools — a second, native channel to the same catalog
`capabilities()` already declares `supports_skills=True` for via prompt
injection. This is a bigger capability change than "hooks now fire" and
deserves its own review before being called intentional; it is NOT closed by
this change and NOT silently absorbed into `supports_skills`'s existing
"true" value.

## 3. Live guardrail proof — Claude, Gemini, Qwen (Codex: pending, no mechanism)

See `docs/PROVIDER_LIVE_TEST_PLAN.md`'s `guardrail` row for the full
per-vendor evidence. Summary: a genuine second Clayrune instance
(`MC_REMOTE_ENABLED=0`, isolated `MC_DATA_DIR`/port/home) dispatched `taskkill
/IM notepad.exe` against a notepad process started and owned outside the
test, on each of Claude, Gemini and Qwen. All three were blocked by
`mc/process_guard.py` once it was the installed guard; notepad survived all
three attempts.

**Critical finding surfaced by this proof, not something this task set out to
find:** Ron's REAL, currently-live Claude hook is **not**
`mc/process_guard.py` — it is a separate, older script,
`~/.claude/hooks/process-guard.py`, which blocks image-name kills only for a
**hand-maintained list** of process names (`chrome|chromium|msedge|firefox|
brave|opera|python|pythonw|python3|claude|node|code|explorer|powershell|pwsh|
cmd|conhost|WindowsTerminal|wt`). `notepad` is not on that list. The first
live-proof attempt against Claude, run with Ron's real, unmodified hook
config, was **not blocked** — Claude executed `taskkill /IM notepad.exe` and
the test notepad (PID 32240) was actually killed. Direct reproduction:

```
$ echo '{"tool_name":"PowerShell","tool_input":{"command":"taskkill /IM notepad.exe"}}' \
    | python "C:/Users/levir/.claude/hooks/process-guard.py"
(exit 0 — allowed)

$ echo '{"tool_name":"PowerShell","tool_input":{"command":"taskkill /IM notepad.exe"}}' \
    | python mc/process_guard.py
process guard: image-name termination is blocked ('taskkill /IM notepad.exe'); only terminate a PID you spawned
(exit 2 — blocked)
```

`mc/process_guard.py`'s own docstring already explains why it exists instead
of a list: *"Image-name termination is rejected for every image, not a
hand-maintained protected-process list."* Ron's live install is running the
list-based predecessor this rewrite was meant to replace. The Claude leg of
the live-proof matrix was re-run against an isolated home pointed at
`mc/process_guard.py` (copying Ron's real OAuth credentials into a
throwaway, gitignored scratch dir for the duration of the test only, deleted
immediately after) to get a clean pass — but the gap in the REAL, currently
protecting-Ron's-machine hook is real, live, and unfixed. **This installer
does not touch Claude at all** (task scope: Gemini and Qwen only, since
Claude was believed to already be covered) — closing this gap means pointing
`~/.claude/settings.json`'s `PreToolUse` hook at `mc/process_guard.py` instead
of the stale duplicate, which is an edit to Ron's real hook config and is
flagged for his decision, not made here.
