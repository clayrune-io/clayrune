# BUILTINS-5: fail-closed engine coverage

2026-10-31. Backlog `51344245`. Sol_Tobin. **Investigation stopped at the
task's explicit product-decision gate; this is not an implementation report.**
No runtime, hook configuration, global CLI configuration or human-owned fence
file was changed. No merge, push or server restart is authorized for this task.

## Scope and current coverage

The runtime registry at the end of `mc/agent_runtime.py` registers exactly
eight engines. Unattended means the existing
`mc.launch_marker.UNATTENDED_TRIGGER_TYPES`: `schedule`, `workflow`,
`dispatch`, `hivemind_orchestrator`, `hivemind_worker`. Preserve the existing
`fence_unattended_enabled` switch and attended-launch behavior.

The process guard (`mc/process_guard.py`) and steward fence
(`steward/fence.py`) are different policies. Finding the former in a launch
does not establish that the latter runs. A successful pre-spawn self-test
alone also cannot establish in-engine enforcement.

| Engine | Hook mechanism | Fail-closed method / current status | Verified how |
|---|---|---|---|
| Claude | Per-launch `--settings`, `PreToolUse` | Already implemented: pre-spawn self-test; `onFailure: block` on CLI >= 2.1.295. Not redone. | Read `mc/fence_fail_closed.py`, `mc/launch_marker.py`, and installer fence injection. Existing measurement: `docs/FENCE_FAIL_CLOSED_MEASUREMENT.md`. |
| Codex | Inline `hooks.PreToolUse` override and hook-trust bypass | Already implemented: armed wrapper plus `codex_fence_self_test`, delegating to `run_fence_self_test`. Timeout remains a documented fail-open limitation. Not redone. | Read `mc/guardrail_hooks.py` and `CodexRuntime.dispatch`/follow-up command construction. |
| Gemini | `BeforeTool` through per-launch `GEMINI_CLI_SYSTEM_SETTINGS_PATH`; session settings can merge MCP configuration | Current installer adds process guard only, no steward fence. Needs fence injection/arming and shared pre-spawn test before dispatch and follow-up. Native failure-policy knob not assessed before stop. | Read `tools/guards/install_hooks.py` (`VENDOR_CONFIGS`, `EXTRA_HOOK_SHAPES`), `_inject_gemini_env` and `GeminiRuntime.dispatch`. Prior native-hook proof: `docs/GUARDRAIL_PARITY_EVIDENCE.md`. |
| Qwen | `PreToolUse` through per-launch `QWEN_CODE_SYSTEM_SETTINGS_PATH`; no `--bare` | Current installer adds process guard only, no steward fence. Needs fence injection/arming and shared pre-spawn test before dispatch and follow-up. Native failure-policy knob not assessed before stop. | Read installer shapes, `QwenRuntime.dispatch`/`write_followup` and `_inject_guardrail_env`. Prior native-hook proof: `docs/GUARDRAIL_PARITY_EVIDENCE.md`. |
| OpenCode | Native plugin `tool.execute.before` (V1; V2 has a different plugin API) | No Clayrune fence injection. A plugin adapter is possible; installed-version support, per-launch loading and failure semantics remain unverified. Do not classify this as hookless. | Read runtime command/dispatch/follow-up; upstream plugin docs list the event. No real OpenCode launch performed. |
| Goose | Native plugin `PreToolUse` command hooks | No Clayrune fence injection. Needs adapter, failure-policy assessment and per-launch loading verification. Do not classify this as hookless. | Read runtime dispatch/follow-up and upstream hook reference. Native failure policy has not been exercised through a real CLI here. |
| Aider | No documented pre-execution command hook in the reviewed CLI/configuration and edit/shell execution paths | **Decision required.** No in-engine fence mechanism established. Pre-spawn testing cannot protect subsequent edits or shell calls. No blanket unattended refusal added. | Read `AiderRuntime.build_command` (`--yes`), dispatch/follow-up, upstream options and `Coder.apply_updates`, `handle_shell_commands`, `Commands.cmd_run`. Lint/test callbacks are after edits. Upstream hook-system request remains open in the reviewed page. No real Aider launch performed. |
| Kiro | Native `PreToolUse`, inline profile hooks or standalone `.kiro/hooks/*.json` | No Clayrune fence injection. Upstream documents blocking on nonzero exit; per-launch injection, timeout and load-failure behavior need verification. Do not classify this as hookless. | Read runtime headless command (`--trust-all-tools`) and official hook actions/migration references. No real Kiro launch performed. |

Current runtime code establishes the injection gaps; upstream documentation
establishes available native extension mechanisms, not a successful Clayrune
integration. Older parity documents cover four engines and are not evidence
of eight-engine fence coverage.

## Primary sources reviewed

- OpenCode: <https://opencode.ai/docs/plugins/> and
  <https://dev.opencode.ai/v2/docs/build/plugins/migrate-v1/>.
- Goose: <https://github.com/aaif-goose/goose/blob/main/documentation/docs/guides/context-engineering/hooks.md>.
- Kiro: <https://kiro.dev/docs/hooks/actions/> and
  <https://kiro.dev/docs/cli/v3/hooks-migration/>.
- Aider options: <https://aider.chat/docs/config/options.html>.
- Aider post-edit checks: <https://aider.chat/docs/usage/lint-test.html>.
- Aider source: <https://github.com/Aider-AI/aider/blob/main/aider/coders/base_coder.py>
  (`apply_updates`, `handle_shell_commands`) and
  <https://github.com/Aider-AI/aider/blob/main/aider/commands.py> (`cmd_run`).
- Aider hook-system request: <https://github.com/Aider-AI/aider/issues/5712>.

## Decision for Dave (not selected)

1. **Recommended: refuse unattended Aider launches while the fence is enabled.**
   Small, explicit product restriction; attended Aider stays available. Apply
   the same guard on dispatch and every respawn/follow-up, via existing spawn
   failure paths. Requires Dave's decision under this task's STOP AND ASK rule.
2. Keep unattended Aider available with a documented fence exception. Lowest
   implementation cost, but BUILTINS-5 cannot claim every-engine enforcement.
3. Build an Aider interception adapter or confinement layer first. Largest
   scope; must cover both direct file edits and subprocess paths, and prove
   enforcement. A shell-only wrapper or a startup self-test is insufficient.

After Dave decides, resume implementation for supported hooks in a separate
module, reuse `mc.guardrail_hooks.run_fence_self_test`, and test refuse/pass
at actual dispatch and follow-up boundaries for every covered engine. Do not
modify `steward/fence.py`. No per-engine refusal/pass tests were added during
this decision-gated investigation; there is no new runtime behavior to test.

## Validation

Existing related fence/guardrail regression command, **rc=0**:

```text
python -m pytest -q tests/test_fence_fail_closed_unattended.py tests/test_codex_steward_fence.py tests/test_guardrail_injection.py tests/test_guardrail_hooks_boot.py tests/test_install_hooks.py tests/test_launch_marker.py
```

One existing expected failure:
`test_project_fence_entry_timeout_is_seconds_not_ms`, pending the human-owned
`steward/core.py` timeout patch (`docs/patches/fence-timeout-seconds.patch`).
Pyright is not applicable: no `mc/` module was edited.
No model turns, authenticated CLI runs, or live provider enforcement probes
were performed. No user-visible change: changelog, user guide, README and
agent instructions need no edits for this investigation.
