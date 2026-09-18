# Guardrail parity — vendor hook capability evidence (W2, 2026-09-18)

Source of truth for `docs/VENDOR_AGNOSTIC_PROGRAM.md` §3. Every claim below was
verified by reading the CLI's own shipped docs/bundled source, or by a live
run on this box — never guessed. Versions: Claude Code (per `~/.claude`),
Gemini CLI 0.59.0, Qwen Code 0.23.4, Codex CLI 0.154.0.

**Correction (Dave's review, second pass):** the first version of this
document said Codex has no hooks mechanism at all. That was wrong — `codex
exec --help` names `--dangerously-bypass-hook-trust`, which only makes sense
if hooks exist. §1's Codex row below is the corrected version, built from
that flag plus offline strings extracted from the installed `codex.exe`
(0.154.0) — no Codex launch, as required. The rest of this document
(Qwen/Gemini/Claude findings, the live taskkill proof) is unchanged from the
first pass.

## 1. Can each CLI's hook system BLOCK a shell call before it runs?

| Vendor | Pre-execution block? | Event name | Config location | Config shape |
|---|---|---|---|---|
| Claude Code | **Yes** (already live on this box) | `PreToolUse` | `~/.claude/settings.json` (global), `.claude/settings.json` (project) | `hooks.PreToolUse: [{matcher, hooks:[{type:"command", command, timeout}]}]`. Exit 2 blocks, `stderr` is the reason returned to the agent. |
| Gemini CLI | **Yes** | `BeforeTool` | `~/.gemini/settings.json` (global), `.gemini/settings.json` (project) | Identical shape to Claude's (`hooks.BeforeTool: [{matcher, hooks:[{type:"command", command}]}]`). `matcher` is compared against the tool name (`run_shell_command` for the shell tool). Exit 2 blocks; `stderr` → agent. Structured `{"decision":"deny","reason":...}` on stdout with exit 0 is the documented idiomatic form; exit 2 is documented as the "emergency brake" — both work. Source: `@google/gemini-cli` bundle's own shipped docs, `bundle/docs/hooks/reference.md` and `bundle/docs/hooks/writing-hooks.md` (not third-party — these ship inside the npm package). |
| Qwen Code | **Yes** | `PreToolUse` | `~/.qwen/settings.json` (global), `.qwen/settings.json` (project) | Confirmed byte-identical event/shape to Claude Code by reading the bundled settings schema (`@qwen-code/qwen-code`'s `chunk-P6XVYPWA.js`, `HOOK_DEFINITION_ITEMS`: same `matcher`/`sequential`/`hooks:[{type,command,url,prompt,...}]` object, same event list: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `PreCompact`, etc.). Qwen Code is a fork of Claude Code's own agent engine (not gemini-cli's, despite qwen-code bundling gemini-cli's shell tool — see below), which is why the hook layer matches Claude's exactly while the shell tool's own name doesn't. **Caveat, load-bearing:** the CLI's own `--bare` flag (used unconditionally by this runtime before 2026-09-18) unconditionally zeroes `hooks`/`userHooks`/`disableAllHooks` regardless of settings.json content — confirmed in the bundled config loader, `chunk-QM2MRAG4.js`: `hooks: bareMode \|\| safeMode ? void 0 : settings.hooks`, `disableAllHooks: bareMode \|\| safeMode ? true : ...`. There is no CLI-flag or settings.json channel that survives bare mode to pass a hook config through it — `hooksConfig` is a `loadCliConfig()` **library** parameter with zero CLI-flag surface (confirmed by reading the same chunk; Clayrune shells out to the `qwen` binary, it does not embed the library). So `--bare` and hooks were mutually exclusive; see §2 below for how this runtime now resolves that. |
| Codex CLI | **Yes** (best-evidence, NOT independently confirmed by a real load — no Codex launches permitted) | `PreToolUse` (also `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`) | `~/.codex/hooks.json` (`CODEX_HOME`, referenced by `config.toml`'s `hooks = "./hooks.json"` — a separate file, not embedded in `config.toml` itself) | Offline strings extraction from the installed binary (`.../codex-win32-x64/.../bin/codex.exe`, 0.154.0) surfaced the literal error strings `"Tool call blocked by PreToolUse hook: "`, `"Command blocked by PreToolUse hook: "`, `"PreToolUse hook exited with code 2 but did not write a blocking reason to stderr"` — same exit-code-2-blocks convention as the other three vendors — plus a hook payload shape with `tool_name`/`tool_input`/`tool_use_id` fields identical to Claude's, and an internally-tagged `HookHandlerConfig` with `command`/`matcher`/`timeout_sec`/an async-or-sync mode (plus richer handler types this installer doesn't use: `mcp_tool`, `prompt`, `agent`). Codex's shell tool is named `shell` (`mc/agent_runtime.py` CodexRuntime; confirmed independently by the same binary strings). **Trust gate, load-bearing:** the same strings show an interactive-only flow — `"Hooks need review"`, `"Trust all and continue"`, `"Continue without trusting (hooks won't run)"` — gating whether a hook actually runs, plus a `bypass_hook_trust` config.toml key and the `--dangerously-bypass-hook-trust` CLI flag that skip it. **Not done here:** `CodexRuntime.build_command` does not yet pass that flag — Codex is out of allowance until 2026-09-24 and changing a live dispatch command with no way to verify it is a separate, riskier change from writing the installer's config. Without it, a freshly-written hooks.json entry likely sits untrusted and inert in a headless run. Flagged for whoever runs the Sep-24 live proof, not silently skipped. |

Gemini's shell tool and Qwen's own native shell tool are BOTH named
`run_shell_command` (qwen-code bundles gemini-cli's shell tool verbatim,
confirmed by grepping both packages' bundles for the literal string) — NOT
`Bash`/`PowerShell` the way Claude's and Qwen's OWN hook *event* naming would
suggest. Codex's own shell tool is named `shell` instead. `mc/process_guard.py`'s
`hook_main` recognizes all four names
(`_SHELL_TOOL_NAMES = {"Bash", "PowerShell", "run_shell_command", "shell"}`,
added 2026-09-18) so the SAME guard script serves every vendor's hook config
unmodified — only the per-vendor event name, matcher string and config file
location differ, and that's encoded in the installer
(`tools/guards/install_hooks.py`), not in the guard.

## 1a. What happens when the hook script path is broken (missing/unreadable)?

Dave's review flagged this as untested: an installer that bakes a bad path
(a deleted worktree, a moved checkout) doesn't just silently disable the
guard — what actually happens? Live-tested 2026-09-18 against all three
launchable CLIs, each pointed at a hook command whose script file does not
exist:

| Vendor | Result | Evidence |
|---|---|---|
| Gemini | **Fails closed.** Tool call blocked; the requested command never ran. | `tool_result` status `"error"`, `"Tool execution blocked: ...python.exe: can't open file '...process_guard.py': [Errno 2] No such file or directory"`. |
| Qwen | **Fails closed.** Same hook error surfaced as the tool result; a marker-file side effect (`echo ... > marker.txt`) was checked afterward and the file was never created, despite the model's own reply text incorrectly claiming the command "executed successfully" — the model's narration is not reliable evidence here, the filesystem is. |
| Claude | **Fails closed.** No marker file created; the model's own reply explicitly named the cause ("A PreToolUse hook blocked it before it started... I didn't try running it through PowerShell or any other route, because that would get around the hook") and did not attempt a workaround. |

All three treat a hook command that cannot even execute as a deny, not a
silent pass-through. This is a byproduct of coincidence, not a designed
safety net: Python's own interpreter returns exit code 2 for "can't open
file" (verified directly: `python <missing path>` → exit 2), which happens
to be the SAME code every vendor's hook protocol treats as "block." A
DIFFERENT missing-executable error (a shell that returns exit 1, or a
`command not found` before any process even starts) is not guaranteed to hit
this same coincidence. Practical consequence: a broken guard path doesn't
expose the machine, it makes the affected vendor's shell tool completely
unusable until fixed — which is exactly why `--repo-root` / `--python-exe`
(below) resolve to something stable rather than leaning on this fail-closed
side effect as a safety margin.

## 1b. Path and interpreter resolution (Dave's blocker)

The first version of this installer baked `Path(__file__).resolve().parents[2]`
(its own script location) and a bare `"python"` string into every written
hook command. Both are wrong for a real install:

- **Path:** when this installer runs from a throwaway agent worktree (as it
  did during initial development — `.clayrune/agents/<id>/mc/process_guard.py`),
  that path is deleted the moment the worktree is torn down, silently
  breaking the hook the next time the CLI fires it (see §1a: breaks CLOSED,
  blocking every shell call for that vendor, not quietly).
- **Interpreter:** a bare `"python"` in the hook command resolves against
  the EXTERNAL CLI's own process environment when the hook fires later, not
  this installer's — a machine with `python3`/`py` instead of `python` on
  that PATH, or a different Python without a needed dependency, breaks
  silently at hook-fire time.

Fixed: `guard_command()` now takes an explicit `guard_script` path and
`python_exe` string, both resolved by the CALLER at install time — never
guessed by the installer itself:
- The manual CLI (`tools/guards/install_hooks.py --repo-root <path>`) takes
  an explicit `--repo-root` (a human names the permanent checkout) and
  `--python-exe` (defaults to `sys.executable` of whoever runs the script).
- The server-startup wiring (`server.py`'s `_install_guardrail_hooks_on_boot`,
  called from `boot()`) passes `_APP_DIR` (server.py's own already-correct
  frozen-vs-dev resolution, `_resolve_dirs()`) and `sys.executable` of the
  RUNNING SERVER PROCESS — both always correct for wherever Clayrune is
  actually installed and running from, live-verified by importing `server`
  and calling `_install_guardrail_hooks_on_boot(home=<tmp dir>)`: it wrote a
  hook command quoting this box's real running interpreter
  (`...pythoncore-3.14-64\python.exe`) and this checkout's real
  `mc/process_guard.py` path, for every vendor CLI actually installed here.

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

**Finding surfaced by this proof, not something this task set out to find,
corrected per Dave's review:** Ron's REAL, currently-live Claude hook is
**not** `mc/process_guard.py` — it is a separate script,
`~/.claude/hooks/process-guard.py`, which blocks image-name kills only for a
**hand-maintained list** of process names (`chrome|chromium|msedge|firefox|
brave|opera|python|pythonw|python3|claude|node|code|explorer|powershell|pwsh|
cmd|conhost|WindowsTerminal|wt`) — deliberately, not by staleness: it is a
narrower, intentional policy, not a bug. `notepad` is not on that list. The
first live-proof attempt against Claude, run with Ron's real, unmodified hook
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

SHARED_RULES.md's "never kill by image name" carries no process-list
exception, so Dave's call: bring Claude onto the same universal guard as the
other three vendors rather than leave the narrower list as Claude's only
protection. `tools/guards/install_hooks.py` now includes a `claude` vendor
entry (`~/.claude/settings.json`, `PreToolUse`, matcher `Bash|PowerShell`)
that ADDS Clayrune's own tagged hook group alongside the existing untagged
one — identified and, on re-install, replaced in place by the `name:
"clayrune-process-guard"` tag on the hook entry it writes
(`_find_group_index`), never by matching or touching the pre-existing
group. Ron's existing list-based entry is left completely alone; whether to
remove it is his call, not this installer's.

Re-tested live with BOTH entries present in the same `PreToolUse` array (an
isolated home, both the legacy list-based group and the new
`clayrune-process-guard`-tagged group; Ron's real OAuth credentials copied
into a throwaway, gitignored scratch dir for the duration of the test only,
deleted immediately after): a fresh notepad, `taskkill /IM notepad.exe` via
Claude's own Bash tool. Blocked — Claude's reply named the NEW guard
specifically (`"stopped before it ran by a hook in this project,
mc/process_guard.py"`, quoting its exact denial message) and declined to
route around it; notepad survived. Confirms the two hook groups coexist on
the same event with no conflict and no silent bypass — the addition is
strictly additive protection, not a replacement that could have gone wrong.
