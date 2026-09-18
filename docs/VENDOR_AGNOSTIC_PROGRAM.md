# Vendor-agnostic program: plan of record

Status: DRAFT v2 (Dave, 2026-09-18). v1 was reviewed by Codex Astra, whose run hit the
OpenAI usage limit partway through; its 3 findings are applied below (marked [Astra r1]).
Supersedes nothing. It orders and owns the work that `PROVIDER_HARDENING_PROGRESS.md`
(architecture) and `MULTI_PROVIDER_PARITY_MATRIX.md` (feature cells) describe.

## 1. The bar (Ron's decisions, recorded as standing positions)

1. **Allowance is the only gate.** Any installed, signed-in agent can be used on any
   feature. The one legitimate refusal is "vendor X is out of allowance until T".
   Missing CLI and missing login are setup states that the UI resolves. They are not
   refusals.
2. **A capability gap is a bug.** Examples: no resume, no ask-user, no MCP, no usage
   figures. Close each one by bridging it in Clayrune, or show evidence that it
   cannot be bridged and get Ron to accept that gap.
3. **Never substitute silently.** A vendor that fails or runs out of allowance is
   surfaced to the user. Work is never silently rerouted to another vendor.
4. **Install Clayrune first.** The user then picks vendors on first run. **Several
   vendors may be selected.** Each selected vendor is installed (prerequisites
   included) and signed in, and one is chosen as the default.
5. **Power is set by the job, not the vendor.** Interactive and delegated agent work
   runs at full capability on every vendor. Codex runs unsandboxed on purpose,
   which matches dispatched Claude's skip-permissions. Text transforms run
   tool-free on every vendor.

## 2. Execution profiles (applies to every adapter)

| Profile | Used by | Tools | Enforcement per vendor |
|---|---|---|---|
| `interactive` | chats, dispatched agents, workflow agent steps | full | Claude `--dangerously-skip-permissions`; Codex `--dangerously-bypass-approvals-and-sandbox`; Gemini/Qwen `--yolo` |
| `unattended` | scheduler, steward, night review | full, confined | MC-949 (next week): workspace-write sandbox + per-job network. Until then the EXISTING `codex_unattended_sandbox` switch keeps working as-is (currently off by Ron's choice); nothing here removes a restriction that exists today [Astra r1]. Plus the guardrail hooks in §3 |
| `transform` | Scribe, condense, Distiller, Claydo, character generation, mail laundering, anything fed third-party or transcript text | **none** | Claude `--allowedTools '' --strict-mcp-config`; Codex `-s read-only` + empty temp cwd; Qwen `--exclude-tools`/`--core-tools` empty + `--bare`; Gemini equivalent. **An adapter that cannot prove tool-freedom refuses.** |

Every launch goes through `execution_policy.authorize_execution(profile, provider)`.
Zero bypass sites. The architecture guard test enforces this and must also catch
the three escape shapes Fenn reproduced (`get_runtime(x).oneshot()` outside the
seam, aliased subprocess imports, keyword `args=` launches).

## 3. Guardrail parity

Guardrails are a **Clayrune policy**, installed into each vendor's own hook system.
All four CLIs have one: `~/.claude/settings.json` hooks, Codex hooks,
`gemini hooks`, `qwen hooks`.

- Minimum set for every vendor: `process-guard` (never kill by image name, never
  kill the Clayrune port owner). Stop and reply guards follow wherever the vendor
  exposes an equivalent turn-end event.
- **Qwen caveat [Astra r1]:** QwenRuntime launches with `--bare`, which disables user hooks along with project config. Replace `--bare` with targeted flags that stop native `.mcp.json`/skills discovery but keep hooks, or pass the hook config explicitly. `--bare` was added to close an MCP capability leak, so whatever replaces it must keep that leak closed (re-run the leak probe).
- One source of truth: `tools/guards/*.py`, plus a per-vendor installer that writes
  each CLI's hook config. It is idempotent and never overwrites user hooks.
- Proof: for each vendor, an agent is told to `taskkill /IM notepad.exe` against a
  notepad the test started, and the attempt is blocked.

## 4. Allowance as a first-class state

- Each adapter maps its CLI's real exhaustion signals to one normalized event:
  `ALLOWANCE_EXHAUSTED{vendor, limit_kind, resets_at|unknown, raw_ref}`.
  Sources: Claude `rate_limit_event`; Gemini 429/`RESOURCE_EXHAUSTED`; Codex
  `task_complete.error.codex_error_info = "usage_limit_exceeded"` with the reset time
  in the message ("try again at Sep 24th, 2026 7:58 AM"; captured live 2026-09-18,
  when Clayrune showed it only as a generic `error` with a partial summary). Qwen as
  observed. Fixtures come from **real captured text**. Ron's Claude outage
  on 2026-09-17 left logs to mine. Nothing is invented.
- Allowance state is kept per vendor (server-side, outside DATA_DIR) and shown on the
  Floor, in the chooser and in chat: "Out of allowance, resets 14:00".
- Dispatch to an exhausted vendor is refused, naming the vendor and the limit. There
  is never a fallback.
- **A terminal failure is never content.** An adapter's oneshot or stream failure,
  quota JSON included, raises a typed failure. It is never returned as text (Fenn
  #4). Memory, Scribe and workflows treat a failure as a failure.

## 5. Capability bridging (target: every cell ✅ or 🟡, none ⛔)

| Capability | Gap today | Bridge |
|---|---|---|
| Resume after restart | Gemini none; Qwen/Codex fixed 2026-09-16 | Gemini `--resume` / session files; replay from canonical history where the CLI has none |
| Ask the user | Codex, Qwen report `supports_ask_user_question=False` | `mc:question` fence protocol already works via prompt; flip only after live proof per vendor |
| MCP | Qwen `--bare` drops it | pass Clayrune's resolved MCP set explicitly (settings/flag), never native discovery |
| Usage / cost | only Claude emits cost | normalize tokens where emitted; cost from a price table; unknown stays unknown |
| Skills / read-floor / persona | prompt-injected for non-Claude | keep one injection path; verify persona survives resume per vendor |
| Plan mode | Claude only | Clayrune-level plan gate (`mc:plan` fence) |
| Images | varies | verify per vendor; bridge via file path in prompt |
| Checkpoint / Scribe for non-Claude | uses Claude parser (Fenn dormant #3) | per-adapter transcript → canonical turns |

## 6. Cross-vendor orchestration

Every flow must work for **every ordered pair** of the 4 vendors: dispatch with the
`notify_session` callback, roster hire, workflow steps on mixed vendors, a hivemind
with mixed workers (it has 2 direct launches that currently fail the guard), and a
conversation handoff where a chat started on vendor A is continued by an agent on
vendor B from canonical history. The durable delegation ack happens only after the
real write (Fenn #3).

## 7. Multi-vendor first run

A multi-select chooser. Onboarding installs prerequisites itself: Node for the npm
CLIs, and pip or uv for Aider (Fenn #5). It then installs each selected CLI, runs
each sign-in, shows per-vendor state, and asks for a default. Proof comes from
clean-VM runs (Windows 11 and Ubuntu VMs exist; macOS on the build Mac): pick 2+
vendors, all install, all sign in, and each chats.

## 8. Workstreams, owners, order

Owners are picked by skill, and engines are spread across vendors on purpose
(dogfooding + allowance resilience). At most **3 builders run at once**. Every
unit is its own branch. **Merges are serialized with smokes after each merge**
(CLAUDE.md), because nearly every unit touches `agent_runtime.py` or
`agent_routes.py`. Any second server instance runs with `MC_REMOTE_ENABLED=0`.

| # | Workstream | Owner (engine) | Reviewer | Depends on |
|---|---|---|---|---|
| W0 | Stabilize: Fenn's 5 blockers, 3 dormant defects, guard escapes, master's 5 failing tests, empty commit bodies, and the branch's rollout importer rejecting the installed Codex 0.154.0 [Astra r1]. Vector's work in progress was checkpointed as `c14c2fd` when he hit the usage limit | Vector (Codex Sol), author of the branch | Fenn | none. **Gates every merge** |
| W1 | Execution profiles + `authorize_execution` on every path (§2) | Vector (Codex Sol) | Wren (security) | W0 |
| W2 | Guardrail parity via vendor hooks (§3) | Tobin (Claude Sonnet) | Wren | none; parallel with W0 |
| W3 | Allowance state + typed failures (§4) | Tobin, after W2 | Bram (verifies no failure reads as success) | W0 #4 |
| W4 | Capability bridges (§5), one vendor at a time: Gemini, then Qwen, then Codex | Kestrel (Codex Astra), with Rusk (Qwen) for the Qwen cells | Fenn | W0 |
| W5 | Cross-vendor orchestration matrix (§6) | Vector (Codex Sol) | Fenn | W1, W4 partial |
| W6 | Multi-vendor first run + prerequisites (§7) | Tobin (UI and back end), Tilda for UI polish | Wren (installer runs code on user machines) | none; parallel |
| W7 | Live test plan + certification run | Dave owns; `docs/PROVIDER_LIVE_TEST_PLAN.md` maintained by each owner | Fenn + Codex Astra | all |

Wave 1 (now): **W0 (Vector) and W2 (Tobin)** in parallel, with no file overlap
beyond the hook installer. Wave 2: W1, W3 and W6. Wave 3: W4 and W5. Then W7.

## 8a. Allowance reality (2026-09-18)

The OpenAI/Codex account is **out of usage until Sep 24, 7:58 AM**. Ron: *nothing is
parked; our own agents do the work.* Every workstream runs now on Claude, Gemini or
Qwen engines, whatever its original owner:

| # | Now owned by | Session |
|---|---|---|
| W0 stabilize branch | Bram (Claude Opus) | 4377d59b48db |
| W2 guardrail parity | Tobin (Claude Sonnet) | a36ac17b16ce |
| W6 multi-vendor first run + prerequisites (incl. Fenn #5) | builder (Claude Sonnet) | 9e68b2942e16 |
| W1 execution profiles, W3 allowance, W4 bridges, W5 cross-vendor | builders on Claude/Gemini/Qwen, dispatched as W0 lands | pending W0 |
| Code review | Dave, plus a Claude-engined reviewer | per merge |

Codex-specific **live** checks (sending a Codex agent a prompt) are the only thing
that waits for Sep 24. They are physically impossible without allowance, which is
the plan's own gate. Codex install, auth detection and offline fixtures proceed now.
Plan certification by a second vendor resumes when Codex returns. Until then Ron
has approved proceeding.

## 8b. Codex live-run gate (Ron, 2026-09-18)

Ron will buy more Codex allowance **only when everything else is ready**. So the Codex
live pass is one scripted run, prepared in full in advance. Nothing gets explored or
debugged while paid allowance runs. Dave reports READY and asks Ron for allowance only
when every item below is checked:

1. W0 is merged: branch green, full suite + smokes, and the Codex 0.154 rollout
   importer is proven on a fixture.
2. W1 is merged: Codex `transform` is tool-free (`-s read-only` + empty cwd), proven
   offline by command construction, and the architecture guard is green.
3. W2 is merged: the Codex hook config is installed, and its exact diff has been
   reviewed. Its live block test is the first item of the Codex run.
4. W3 is merged: `usage_limit_exceeded` is parsed from the **captured real** error
   (Sep 18), so the Floor shows "out until …" and no quota text becomes content.
   This proves itself today, while Codex is actually out: Clayrune must show the
   real exhausted state for Codex.
5. W6 is merged: Codex install and auth detection pass on the clean VM.
6. Every Codex cell of `PROVIDER_LIVE_TEST_PLAN.md` has an exact scripted step
   (prompt, expected evidence, file path) and is runnable in order by one driver
   script, with an estimated token cost.
7. The same cells are already green on Claude, Gemini and Qwen, so any Codex failure
   is Codex-specific, not a platform bug found on paid time.

## 9. Definition of done

1. Full pytest is green on master, and every smoke is green, run from a clean worktree.
2. `docs/PROVIDER_LIVE_TEST_PLAN.md` has every flow × vendor cell filled with
   evidence, from the list in §5, §6 and §7 plus: new chat, follow-up, stop,
   restart + resume, image paste, scheduled run, Scribe write + read-floor,
   drag-to-hire, and the allowance-exhausted display.
3. A live certification run on Ron's instance happens after one Ron-approved
   restart, with Dave and Codex Astra both signing the result.
4. Master is pushed. No module ships without a production caller.

## 10. Not doing

- No second, permanent execution path. Consumers migrate to the boundary.
- No sandbox on interactive work (Ron, 2026-09-18).
- Changes to Ron's own `~/.codex/config.toml` trust list are flagged, never made.
- MC-949's unattended sandbox is not pulled forward.
