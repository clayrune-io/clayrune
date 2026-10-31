# Wait-only unattended retry budgets (BUILTINS-1)

Dave's decision, 2026-10-31: cap retry waiting, never total productive execution.
Unsupported engines remain explicitly unsupported. Hang recovery belongs to the
existing guardian. This implementation is a branch candidate, not deployed.

## Settings and contract

**Settings > Providers > Unattended retry waits** holds one `unattended_retry`
config block, with durations in whole minutes:

```json
{"max_wait": 30, "max_usage_limit_wait": 120, "usage_limit_recheck": 5}
```

Omitted fields use those defaults. Invalid types, nonpositive values and unknown
fields are refused before persistence. Recheck must be 1–180 minutes, matching
Claude's native range. Malformed hand-edited blocks fall back to safe defaults.
Settings saves retain the existing human passcode gate. Capability metadata is
read-only and cannot be used to enable unsupported enforcement. The config
block is global; no project or live config was changed by this build.

Applies to `schedule`, `workflow`, `dispatch`, `hivemind_orchestrator` and
`hivemind_worker` triggers, using the same trigger set as the launch marker.
Native values apply to the next process launch. Qwen's observed budget is read
live by the guardian. Existing attended behavior is retained. Explicit native
watchdog/retry opt-outs in the server environment are respected; configured
Claude duration values override inherited duration values on unattended launch.

## Engine → mechanism → evidence

Audited 2026-10-31. Installed CLI versions observed without inference:
Claude 2.1.312, Codex 0.166.0, Gemini 0.66.0, Qwen 0.26.4. An UNSUPPORTED
label means this build cannot enforce the requested wait contract; it does
not establish that no vendor version or configuration could ever support it.

| Engine | Mechanism in this build | Evidence and limits |
|---|---|---|
| Claude | **NATIVE**, all three mappings in `launch_env` beside RETRY_WATCHDOG_ENV | [Official env reference](https://code.claude.com/docs/en/env-vars): MAX_WAIT_MS limits per-request 429/529 waiting (2.1.295+), USAGE_LIMIT_RECHECK_MS controls subscription-limit checks (2.1.305+). [Official changelog](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md), 2.1.301: MAX_USAGE_LIMIT_WAIT_MS refuses usage-limit resets beyond its budget. |
| Codex | **UNSUPPORTED**, no surrogate request-count or execution deadline | [Official TOML reference](https://learn.chatgpt.com/docs/config-file/config-reference): model_providers fields request_max_retries, stream_max_retries and stream_idle_timeout_ms are counts or stream-idle limits. Installed exec/resume help provides config overrides; no equivalent wait-duration knob was established. Current runtime has no verified retry-wait lifecycle signal. |
| Gemini | **UNSUPPORTED** | [Official configuration](https://geminicli.com/docs/reference/configuration/): general.maxAttempts is a request-attempt limit; general.retryFetchErrors is a boolean. Installed bundled configuration/settings docs were searched for retry/backoff/timeout. No equivalent duration knob or reliable emitted wait window was established. |
| Qwen | **PARTIAL**, Clayrune caps observed capacity-wait windows using max_wait | Installed 0.26.4 bundled `lib/bundled/qc-helper/docs/features/headless.md`, Persistent Retry Mode, documents QWEN_CODE_UNATTENDED_RETRY and stderr heartbeats. `lib/chunks/chunk-S4KYECMU.js` implements sleepWithHeartbeat; `chunk-EZB3CCOL.js` emits the heartbeat text. These are inspected installed sources, not live API captures. Public [headless docs](https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/) fetched in this audit did not expose that persistent-retry section, so bundled evidence is stated separately. Usage classification and cadence unsupported. |
| OpenCode | **UNSUPPORTED** | [Official config](https://dev.opencode.ai/docs/config/): provider timeout, headerTimeout and chunkTimeout cover request/stream timing. These are not wait budgets. Reliable retry lifecycle signal unverified; no binary on this shell's PATH. |
| Goose | **UNSUPPORTED**, native capability **unverified** | Current GooseRuntime parser has no verified wait signal. Binary not on this shell's PATH. The attempted official environment-variable docs URL returned 404; no vendor absence claim is made from that failure. |
| Aider | **UNSUPPORTED**, retry-signal capability **unverified** | [Official options](https://aider.chat/docs/config/options.html): --timeout/AIDER_TIMEOUT limits an API call, not total retry waiting. Binary not on this shell's PATH; no reliable wait lifecycle signal verified. |
| Kiro | **UNSUPPORTED**, retry-signal capability **unverified** | [Official settings](https://kiro.dev/docs/reference/settings/): api.timeout includes streaming-response execution; streamIdleSoftTimeout/HardTimeout measure silence. Automatic transient retries are documented, but no equivalent wait-duration knob or reliable signal was established. Binary not on this shell's PATH. |

Claude rechecks are vendor-conditional: subscription session/weekly limits,
without usage credits or DISABLE_NONESSENTIAL_TRAFFIC. The max-wait budget is
per API request and the native watchdog may end a request on its next error;
it is not a hard timer that kills the process in mid-request. Usage-reset
refusal and periodic rechecks retain their vendor semantics. No count/timeout
knob from another engine is represented as an equivalent wait budget.

## Qwen's partial enforcement

`mc/qwen_retry_wait.py` recognizes only the complete documented line:

```text
[qwen-code] Waiting for API capacity... attempt 3, retry in 45s
```

The shared Mode-A reader consumes it as waiting status rather than assistant
content. The guardian counts only the announced remaining-sleep interval.
Repeated heartbeats do not reset or double-count the budget. A real protocol
progress/result event ends the interval. A request after the interval expires,
CPU activity, silence and arbitrary stderr text never extend a wait window.

The cumulative observed budget belongs to one spawned turn/process. On reaching
max_wait while still inside a confirmed wait window, the guardian marks that
turn error/needs-attention, logs the cap reason and stops the same session-owned
process through the existing kill helper. It rechecks identity/status under the
project lock and exempts human questions, plan gates and recovery handoffs.
No stale timer can kill a replacement process.

This is a lower-bound measurement, not the complete vendor retry clock: the
first heartbeat arrives during a sleep, short sleeps can emit no heartbeat,
and request-to-request waiting before observation is unmeasured. No timeout is
enforced outside a confirmed window. A delayed guardian tick can therefore
miss the end of a short window; accumulated observed waiting is enforced on a
later confirmed window. Heartbeats do not distinguish 429 from 529 or a
subscription reset, so max_usage_limit_wait is **unsupported** for Qwen.
usage_limit_recheck is also **unsupported**: the vendor's own retry backoff is
retained. Settings labels these limits as PARTIAL.

Unattended Qwen launches enable its documented persistent retry opt-in when
unset, on both dispatch and warm followup. Explicit opt-outs are kept. No
unsupported engine gains a hard active-turn deadline or a new retry loop.

## Modules and validation

Policy/config/environment mapping: `mc/retry_budgets.py`. Capability display
data: `mc/retry_capabilities.py`. Observed Qwen wait accounting/enforcement:
`mc/qwen_retry_wait.py`. UI: `static/js/unattended-retry-settings.js`. Existing
runtime, guardian, launch marker and Settings code contain only wiring.

Tests cover defaults/validation/persistence/passcode gate, native millisecond
mapping, all unattended triggers, attended exclusions, Qwen dispatch/followup
environment boundaries, actual reader consumption, wait-only arithmetic,
replacement processes, human gates and unsupported labels. The component smoke
loads the actual JS and CSS at 1440/390px, exercises full-block save through the
human-proof helper, cancellation, validation and support labels. The dashboard
boot smoke checks the new import alongside the real SPA.

Final verification on this branch: expanded pytest selection **632 passed,
1 live-CLI skipped, 1 expected failure**, rc=0. After the final envelope-prefix
stability regression was added, the targeted resume/rollout set passed **22**,
rc=0 (overlaps the expanded selection). Pyright basic on all five new modules
plus agent_runtime/launch_marker: **0 errors**, rc=0. Boot smoke, new retry
Settings smoke at both widths, settings-providers, engine-fallback-settings and
inline-handler scope check all rc=0. The dependency junction used for smoke
imports was removed; the main checkout's Playwright installation was retained.

No paid model calls, vendor reset redemption, live config mutation, merge, push,
server restart, or edits to steward/fence.py or steward/core.py were performed.
Claude cache-cost measurement remains unrun; its method is in
`docs/BUILTINS_RESUME_CONTEXT.md`.
