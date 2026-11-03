# BUILTINS-5: fail-closed engine coverage

2026-10-31. Backlog `51344245`. Sol_Tobin. **Branch implementation after
Dave's Aider decision. No merge, push or server restart.** The initial
decision-gated audit is preserved in commit a0d23078.

## Final engine status

| Engine | Hook mechanism | Fail-closed method | Verified how / result |
|---|---|---|---|
| Claude | Per-launch `PreToolUse` settings | Existing shared self-test plus `onFailure: block` on CLI >= 2.1.295; unchanged | Existing regression suites; implementation not redone. |
| Codex | Inline `PreToolUse` override | Existing armed wrapper and shared self-test; unchanged | Existing regression suites; implementation not redone. |
| Gemini | Per-launch `GEMINI_CLI_SYSTEM_SETTINGS_PATH`, `BeforeTool` | New armed payload adapter plus shared native loader/runner preflight before dispatch and every follow-up. Failed loading refuses the launch. | **Installed 0.66.0 on Windows is refused:** its loader ignores user-owned system settings under its administrator-ownership/ACL requirement. Actual loader/registry proof and refusal test; no permissions or trust checks bypassed. Adapter's allowed launch path is covered at mocked process/native-preflight boundaries, not certified on another real installation. |
| Qwen | Per-launch `QWEN_CODE_SYSTEM_SETTINGS_PATH`, `PreToolUse` | New armed adapter plus shared native loader/runner preflight before dispatch and every follow-up; unexpected adapter failures become exit 2 | **Installed 0.26.4 passes:** native settings loader registers the injected hook; native runner and aggregator preserve real fence self-test denial, reversible-command allow and `git push` refusal. Safe-mode suppression is separately verified to refuse startup. |
| OpenCode | Native plugin API exists | Visible unattended refusal while fencing is enabled; no verified Clayrune adapter | Runtime resolver found no installed CLI. Refuse/pass tests at dispatch and follow-up plus HTTP error tests. Installing the CLI alone does not certify an adapter. |
| Goose | Native plugin `PreToolUse` exists | Visible unattended refusal while fencing is enabled; no verified Clayrune adapter | Runtime resolver found no installed CLI. Same boundary/HTTP tests; no live native integration claimed. |
| Aider | Restricted source-pinned adapter; default OFF | Existing unattended refusal unless a human opts into the restricted 0.86.2 runner | Real-Aider bypass and edit controls, per-spawn source/preflight checks, dispatch/follow-up and flag-off tests. [Part-2 contract and limits](BUILTINS_5B_RESTRICTED_AIDER.md). Attended/fence-disabled behavior is unchanged. |
| Kiro | Native `PreToolUse` exists | Visible unattended refusal while fencing is enabled; no verified Clayrune adapter | Runtime resolver found no installed CLI. Same boundary/HTTP tests; no live native integration claimed. |

Attended launches remain available. All six remaining engines use the existing
`UNATTENDED_TRIGGER_TYPES` set and the live `fence_unattended_enabled` switch.
No refusal falls back to another engine. Session state becomes `error`, the
reason appears in `log_lines`, and dispatch/send/follow-up/interrupt return an
HTTP 409 error for `VendorFenceError` rather than success. Existing dispatch
failure persistence remains responsible for run history.

The Aider reason is:

> Aider has no pre-execution hook, so the steward fence cannot run; unattended Aider is refused while fencing is enabled

Dave selected this restriction after the initial audit. The unfenced exception
was rejected as a Ron-only gate change; an Aider interception layer is outside
this ticket. Gemini's rejected file is also a failed guard-load check, not an
exception allowing it to run unfenced.

BUILTINS-5b follow-up (`10796682`, 2026-11-02): the installed-Aider audit
reproduced gaps in a central writer/shell adapter and Python open auditing.
Part 1 added no production bridge or enabling flag; see
[the boundary decision and native proof](BUILTINS_5B_AIDER_INTERCEPTION_AUDIT.md).
Part 2 implements Dave's restricted allowlist behind the default-OFF
`aider_restricted_adapter_enabled` flag. No live enablement is part of that
branch candidate. See [the adapter contract](BUILTINS_5B_RESTRICTED_AIDER.md).

## Implementation and verification contract

- `mc/vendor_fence.py` owns launch policy and content-addressed settings copies.
  It copies only Clayrune-owned hook/MCP settings, retains other entries, removes
  duplicate Clayrune fence entries, and never copies global user configuration.
  Malformed/missing referenced settings, write failure, missing Node/package,
  unknown CLI exports, disabled hooks or failed preflight refuse startup.
- `mc/vendor_fence_hook.py` translates native shell/file/read/search tool names
  to the existing fence vocabulary, preserves cwd and call identity, and always
  calls the human-owned fence with `--armed`. PowerShell hooks carry the native
  shell hint. Parse/translation/load failures emit a reason and exit 2.
  `read_many_files` is explicitly denied until every target can be translated;
  individual `read_file` calls remain usable. No changes to `steward/fence.py`.
- `mc/vendor_fence_probe.py` finds the launched package's active import graph,
  imports its native settings loader/registry/runner, and checks real fence
  self-test denial, allow and refusal without a model turn or tool execution.
  Qwen standalone's entry-selection prefix is evaluated only through selection;
  selecting a different managed npm build refuses rather than testing a stale
  bundled copy. Discovery is repeated on each spawn, so a changed CLI does not
  reuse stale module paths. Internal vendor APIs can change; unknown shapes
  are a visible refusal, not a compatibility fallback.
- `mc/guardrail_hooks.run_fence_self_test` remains the **only** Python runner.
  It now accepts optional child env/cwd; Claude/Codex defaults are unchanged.
  The Node proof returns exit 2 plus the actual fence token only after every
  native check passes. Hook invocations have fail-closed shell tails; native
  timeout/crash behavior outside those tails remains a CLI limitation.
- `mc/hook_entry.py` registers the adapter for frozen builds. No extra resource
  files are required: the small Node probe is emitted from its Python module.
  Source and frozen argv/entry contracts are tested; no packaged-app build or
  live server run was performed. Runtime/route monolith edits are boundary
  calls and existing-error-path wiring; the new concern lives in three modules.

The startup proof is not a sandbox or a guarantee against a hook timing out
after launch. Gemini/Qwen expose no verified hook-level equivalent to Claude's
`onFailure: block` in the reviewed installed implementations. Runtime hook
timeouts can still fail open; the 30-second hook budget is separate from the
10-second startup check. These limits also exist in the prior Codex approach.

## Final validation

Portable regression command (six requested suites, new tests and related route,
frozen-entry/context/fallback coverage):

```text
python -m pytest -q -o addopts='' --tb=short tests/test_fence_fail_closed_unattended.py tests/test_codex_steward_fence.py tests/test_guardrail_injection.py tests/test_guardrail_hooks_boot.py tests/test_install_hooks.py tests/test_launch_marker.py tests/test_vendor_fence.py tests/test_vendor_fence_probe.py tests/test_vendor_fence_routes.py tests/test_frozen_hook_entry.py tests/test_native_followup_context_routes.py tests/test_agent_routes.py tests/test_engine_fallback.py
```

**rc=0: 500 passed, 1 xfailed in 35.89 seconds.** The existing expected failure
is `test_project_fence_entry_timeout_is_seconds_not_ms`, awaiting the human-owned
timeout patch; this ticket does not change it.

Installed proof (PowerShell, opt-in; no model turns):

```powershell
$env:CLAYRUNE_TEST_INSTALLED_FENCES='1'
python -m pytest -q -o addopts='' --tb=short tests/test_vendor_fence_installed.py
```

**rc=0: 3 passed** (Qwen load/deny/allow, Qwen safe-mode refusal, Gemini
ownership-rejection refusal). The opt-in tests deliberately pin measured
installation behavior; they are skipped unless explicitly enabled.

```text
pyright mc/vendor_fence.py mc/vendor_fence_hook.py mc/vendor_fence_probe.py mc/agent_runtime.py mc/guardrail_hooks.py mc/hook_entry.py
```

**rc=0: 0 errors.** Including `mc/blueprints/agent_routes.py` returns rc=1
with **15 existing errors**. Compared its HEAD source with the edited source
using rule/message diagnostic multisets: before 15, after 15, **0 new**.
The guide/changelog document refusal behavior. README and agent instructions
need no changes: installation steps and agent authority are unchanged. No new
dependencies.

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
