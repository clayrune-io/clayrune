# Unattended agent permission exposure — audit + fix (2026-09-14)

Auditor: Wren (security-privacy-auditor). Confirms Dave's claim: every
unattended Clayrune agent runs with every permission guard off. Measures the
exposure from code (not docs), ranks the risk, weighs fix options against a
real, on-box test of each, and implements the safe/reversible part.

Builds on `docs/_review/2026-09-10_security.md` (six implementations of
"is this session unattended?", finding **E**: the fence "enforces only for
steward-cycle sessions... scheduled/hivemind/hired-character runs are
confirmed non-steward and run unfenced — documented, accepted"). This audit
revisits that acceptance at Ron's explicit request and closes it for the
launch paths where closing it is safe.

---

## 1. Exposure map

Measured from `mc/agent_runtime.py`, `mc/blueprints/{agent_routes,
scheduler_routes,workflow_routes,hivemind_routes,steward_routes}.py`,
`mc/workflows.py`, `steward/{core,fence}.py`.

| Launch path | Flags (Claude) | Flags (Codex) | PreToolUse fence armed? | Untrusted input that can reach it |
|---|---|---|---|---|
| Interactive chat (UI) | `--dangerously-skip-permissions` (`agent_runtime.py:1202`, unconditional) | `--dangerously-bypass-approvals-and-sandbox` (`:4037/:4040/:4789`, unconditional) | No (by design — self-gated off) | Web reads, browser pane, repo contents, MCP results — but a human is reading the transcript |
| Dispatched agent (`POST .../agent/dispatch`, `source='agent'`) | same, unconditional | same | **Now: yes**, if steward is enabled for the project (below) | Same untrusted surfaces, no human reading *this* session's tool calls turn-by-turn |
| Scheduled task (`scheduler_routes.py`) | same | same | **Now: yes**, if steward enabled | Whatever the scheduled prompt/skill reads (web, mail digest, repo) |
| Workflow step (`mc/workflows.py:_dispatch_step`, `trigger_type='workflow'`) | same | same | **Now: yes**, if steward enabled | Same, plus prior steps' output as context |
| Hivemind worker (`hivemind_routes.py:~973/1046`) | same | same | **Now: yes**, if steward enabled | Workstream context, repo contents |
| Steward cycle | same | same | Yes (pre-existing, `[Steward cycle]` marker) | Same, plus `AGENT_RULES.md`-directed mail digest |
| Task-tool subagent (in-process, no MC session) | inherits parent process | n/a | Inherits the **parent session's** arming — see residual gap below | Whatever the parent hands it |

**The fence's install footprint did not change and is the real scope
boundary.** `steward/core.py:install_fence_to_project()` only ever writes the
PreToolUse hook into `<project>/.claude/settings.json` when a human turns
steward ON for that project (`mc/blueprints/steward_routes.py:138`). A
schedule, workflow, dispatch, or hivemind worker running in a project that
has never enabled steward gets **zero** fence coverage before or after this
change — "arm the existing fence" only reaches the launch paths inside a
project that already has the hook installed. Installing the hook by default
everywhere is a bigger footprint change, out of this audit's scope.

**Residual gap — Task-tool subagents.** A subagent spawned via the Task tool
never becomes its own MC session; it runs inside the parent Claude Code
process and inherits `CLAUDE_CODE_SESSION_ID` from the parent. The
generalized arming below therefore fences a Task-tool subagent only when its
*parent* session is itself unattended (schedule/workflow/dispatch/hivemind).
A subagent spawned from an interactive chat is unfenced, same as the parent —
consistent with today's posture (a human reads the parent transcript), not a
new gap this change introduces.

---

## 2. Ranked risks

**1 — Codex bypass has no fence at all, on any launch path (HIGH).**
`--dangerously-bypass-approvals-and-sandbox` (`agent_runtime.py:4037,4040,4789`)
skips sandboxing entirely — Codex's own help text: "EXTREMELY DANGEROUS,
intended solely for externally sandboxed environments." `steward/fence.py`
is a Claude Code PreToolUse hook; it has no Codex equivalent, so a Codex
session on ANY launch path (including steward) has never had a backstop.
Prompt injection reaching an unattended Codex session (a scheduled task that
reads a web page, a workflow step that reads a PR) can run any shell command
with no confinement — `git push`, credential exfiltration via
`with-secret.py`-adjacent shell, `curl` to an attacker host. This audit does
not close this gap (§4); §3c shows a real, tested alternative.

**2 — Before this fix, every non-steward unattended launch path was
completely unfenced even in a steward-enabled project (HIGH, now reduced to
MEDIUM — closed for schedule/workflow/dispatch/hivemind, Codex and non-
steward-enabled projects still open).** `steward/fence.py`'s only signal was
the literal `[Steward cycle]` transcript marker (pre-fix `_session_is_steward`
gate, `fence.py` pre-2026-09-14). A prompt-injected instruction reaching a
scheduled task, a workflow step, a dispatched helper, or a hivemind worker
could run `git push --force`, `rm -rf`, `gh pr merge`, `terraform apply
-auto-approve`, or exfiltrate via `curl -X POST` to a non-local host —
anything on the fence's own denylist (`steward/fence.py:_BLOCK_PATTERNS`) —
with zero resistance, because the marker never matched a non-steward task
string.

**3 — Agent-to-agent dispatch was mislabeled `'manual'`, indistinguishable
from a human clicking "+New chat" (MEDIUM, now fixed).**
`POST /api/project/<id>/agent/dispatch` (`agent_routes.py:6306`) never passed
`trigger_type` to `_dispatch_agent_internal`, so it defaulted to `'manual'`
regardless of the existing `source == 'agent'` heuristic (no Origin header,
no `client` field — already used to route dispatch UI presentation). Every
consumer of `trigger_type` that trusts `'manual'` to mean "a human is
watching" — `is_unattended_caller()` (`mc/unattended.py`), the secrets
vault's `detect_unattended_context()` (`mc/secrets_store.py`), `PUT
/api/config`'s unattended-caller gate (`settings_routes.py`), and now the
generalized fence — silently misclassified this path as attended. Fixed at
the one call site (§4).

---

## 3. Fix options, weighed

### (a) Arm the existing fence for every unattended launch — RECOMMENDED, IMPLEMENTED
Generalizes `steward/fence.py`'s enforcement gate from "literal marker in the
transcript" to "marker, OR MC recorded this session's `trigger_type` as one
of schedule/workflow/dispatch/hivemind_orchestrator/hivemind_worker." Reuses
the MC-923 precedent exactly (`CLAUDE_CODE_SESSION_ID` env var → `GET
/api/session/trigger-type` → server-recorded ground truth the session can't
rewrite) instead of inventing a new detector. Fails **open** on ambiguity
(no session id, lookup unreachable, unrecognized trigger_type) — the
opposite of MC-923's fail-closed, deliberately: this hook also runs for
every ordinary interactive tool call in the same steward-enabled project
(mine, writing this document, included), and those must never be blocked by
network noise. Only a **positive, confirmed** match arms it.

**What would have broken, checked against the fence's own denylist:** none
of the four newly-armed launch paths' *legitimate* unattended jobs depend on
a blocked construct — `git push`, `terraform apply`, `gh pr merge`, `rm -rf`
outside `_scratch/`, and mutating non-local `curl` were never something a
schedule/workflow/dispatch/hivemind task should be doing unattended without
a human approving it first (the same posture the steward already has). The
fence already allows local API calls, reads, `git commit`/`add`/`status`,
and scratch-scoped deletes — the actual shape of routine unattended work.

Config flag: `fence_unattended_enabled` (default `True`, one of
`_CONFIG_EDITABLE_KEYS` — an unattended caller cannot flip it off, per the
existing `PUT /api/config` gate, F5 of the 2026-09-10 review).

### (b) Claude: `--permission-mode` instead of skip-permissions — tested, not adopted this pass
Tested directly (not assumed) against `claude` 2.1.270 on this box, `-p`
non-interactive (the same invocation shape MC's Mode B `--print` dispatch
uses):

| Test | Flags | Result |
|---|---|---|
| Bash `echo` | `--permission-mode acceptEdits`, no skip-permissions | Ran immediately, `permission_denials: []`, no prompt, no hang |
| Bash `echo` | **no permission flags at all** | Ran immediately, `permission_denials: []` |
| Write a new file | **no permission flags at all** | File created, no prompt |
| `rm` an existing file in a scratch dir | **no permission flags at all** | Deleted, no prompt, no hang |
| `--permission-mode plan` + `rm` | plan mode | File NOT deleted — plan mode blocks execution entirely, no hang |

**Surprising, load-bearing finding:** this CLI version's non-interactive
`-p` mode does not appear to block on tool-permission prompts at all,
flags or no flags — it just runs the tool call. That undercuts the
documented rationale for `--dangerously-skip-permissions` in `-p` mode
specifically (avoiding a hang nothing can answer), but does **not** mean the
flag is safe to drop: the flag's actual effect could be scoped to something
this quick test didn't isolate (MCP tool access, filesystem reach beyond
cwd, or interactive-mode-only behavior — Mode B's persistent
`--input-format stream-json` process was not tested, only one-shot `-p`).
This needs a dedicated, deeper test against Mode B before any decision to
remove the flag — flagged for a follow-up, not implemented here per the
constraint against removing `--dangerously-skip-permissions` in this change.

### (c) Codex: Windows sandbox mode — tested, real and viable, not wired up this pass
Tested directly on this box, `codex-cli` 0.154.0, `codex exec` (no
`--dangerously-bypass-approvals-and-sandbox`):

| Test | Flags | Result |
|---|---|---|
| Read a real repo file | `-s read-only` | Completed via a real PowerShell subprocess, no prompt, no hang |
| Write inside the sandboxed cwd | `-s workspace-write` (no `--approve-for-me`) | File written directly, no prompt, no hang |
| Write **outside** the sandboxed cwd | `-s workspace-write` | **Denied at the OS/PowerShell layer** (`UnauthorizedAccessException`) — confirmed no file was created |
| Read-only auto-approve | `--approve-for-me` | Works (implies workspace-write; cannot combine with `-s`) |

This is real, working, OS-enforced sandboxing on Windows, with no hang in
non-interactive `codex exec`. It only confines filesystem writes to the
launch cwd (`-C`) — it does not replace the need for something like the
fence for git/gh/cloud-provider mutations run as read-only shell commands
inside the sandbox (those still execute; the sandbox is a filesystem/process
boundary, not a semantic one). Recommend wiring `-s workspace-write` (or
`read-only` for read-heavy tasks) as the Codex unattended default in a
follow-up change, replacing the unconditional bypass flag — out of scope
here (removing the bypass flag is explicitly deferred per the brief).

### (d) Per-schedule opt-in to dangerous mode, off by default for new schedules
Not implemented. Every schedule already goes through the now-generalized
fence when steward is enabled for its project (§3a); a per-schedule flag
would duplicate that control at finer grain for marginal benefit, and
schedules in a non-steward-enabled project get no fence regardless of a
per-schedule flag — the project-level steward toggle is already the
coarser, correctly-scoped switch. Revisit if schedules commonly need to run
constructs the fence blocks (git push, cloud deploys) as legitimate
unattended work — none do today (§3a).

**Recommendation:** ship (a) now (done, §4). Treat (b) and (c) as the next
decision — (c) in particular is ready to wire up and materially reduces the
Codex gap (risk #1) without needing the fence's Python-only hook mechanism
at all.

---

## 4. What was implemented

All changes are config-gated (`fence_unattended_enabled`, default `True`,
human-only to disable) and purely additive — nothing that was fenced before
is now unfenced, and no launch path's actual dispatch flags changed.
`--dangerously-skip-permissions` and the Codex bypass flag are untouched.

1. **`steward/fence.py`** — `_should_arm_for_unattended_trigger()`: looks up
   `CLAUDE_CODE_SESSION_ID` → `GET /api/session/trigger-type`; arms the
   fence when the returned `trigger_type` is one of `schedule`, `workflow`,
   `dispatch`, `hivemind_orchestrator`, `hivemind_worker` AND
   `fence_unattended_enabled` is true. `main()`'s gate: confirmed-steward
   (marker) still always enforces, unchanged; confirmed-non-steward now
   falls through to this check instead of unconditionally allowing.
2. **`mc/blueprints/agent_routes.py`** — `GET /api/session/trigger-type` now
   also returns `fence_unattended_enabled` (piggybacked to halve the fence's
   per-tool-call HTTP overhead: one round trip instead of two, since the
   hook is a stateless subprocess re-invoked on every single tool call).
   `POST /api/project/<id>/agent/dispatch` now stamps `trigger_type='dispatch'`
   when the existing `source == 'agent'` heuristic fires (risk #3), instead
   of leaving every agent-sourced dispatch at the `'manual'` default.
3. **`server.py` / `mc/blueprints/settings_routes.py`** —
   `fence_unattended_enabled` config default `True`, added to
   `_CONFIG_EDITABLE_KEYS` (inherits the existing unattended-caller-cannot-
   write gate on `PUT /api/config`, F5).
4. **Tests** — `tests/test_fence_unattended_arming.py` (new): unit tests for
   the trigger_type/flag decision function (each unattended trigger_type
   arms; `manual`/unknown/unreachable/flag-off do not; the network is never
   called with no session id), plus in-process `fence.main()` end-to-end
   tests proving (i) a scheduled/workflow/dispatched/hivemind session is now
   blocked on a catastrophic command and still allowed a benign one, and
   (ii) a manual session and the steward-marker path are unchanged.
   `tests/test_agent_routes.py`: updated the four existing
   `/api/session/trigger-type` exact-equality tests for the new response
   field, added a test for the flag riding along, and three new tests for
   the dispatch route's `trigger_type` stamping (agent-sourced → `dispatch`,
   UI-sourced → `manual`).

**Left for decision, not implemented:** removing `--dangerously-skip-
permissions` / the Codex bypass flag (explicitly out of scope this pass);
wiring Codex's sandbox mode as the unattended default (§3c, tested and
ready); a deeper Mode B-shaped test of Claude `--permission-mode` (§3b);
installing the fence hook in projects that have never enabled steward.

## 4b. Held back at merge, then RESOLVED: the `dispatch` stamp (2026-09-14)

The fence arming merged as written. The `trigger_type='dispatch'` stamp on
agent-sourced `POST .../agent/dispatch` did NOT. It was backed out to `'manual'`
before landing, because `trigger_type` feeds more than the fence:

- `mc/unattended.py:is_unattended_caller()` returns True if ANY running session
  anywhere has a non-`manual` trigger_type. It gates `PUT /api/config`
  (settings_routes.py), vault create/edit/delete/import (secrets_routes.py
  x4) and distiller_routes.py. With the stamp, Ron's own Settings and vault
  saves would be refused whenever any agent-dispatched child is running:
  18 such sessions in mission_control in the week to 2026-09-14.
- The fence would block those children from `git push`, `gh pr create/merge`,
  external POSTs, the browser API and `.claude/` edits, which breaks the
  usual "builder merges and pushes master" flow.
- `with-secret.py` would treat them as unattended (`allow_unattended=false`
  secrets refused).

The callback that wakes a spawner uses `/agent/send`, not dispatch, so parent
sessions were never affected. `'dispatch'` remains in the fence's allowlist,
so turning the stamp on later is a one-line change, but it has to ship with a
fix to `is_unattended_caller` so it stops locking out the human.

**Resolved the same day (Dave, merging 95ca5ac):** the blocker above was `is_unattended_caller()` judging the whole server instead of the caller. §5-7 below fix it (caller identified by the dashboard's Origin header, same signal as `workflow_routes._is_agent_caller`), so the stamp was re-enabled in the same merge: an agent-sourced dispatch now records `trigger_type='dispatch'` and its child is fenced. The "builder merges and pushes master" flow is intentionally ended: builders merge locally, Dave pushes.

**Full pytest run (§4):** `python -m pytest tests/ -q` → clean, exit code 0. 6
environment-conditional skips (live-auth CLI test, operator-local doc paths),
zero failures.

---

## 5. Follow-up (2026-09-14, same day) — Ron approved the rest, split across sessions

Ron approved implementing the rest of §4's "left for decision" list
("implement everything needed to make sure we are fully secured"), relayed
via a peer session coordinating this work. Two pieces landed on this branch;
installing the fence into other registered projects (apex_trader, day_
trading_engulfing_scanner, clayrune_website) is being done by that peer
session directly in each of those repos — out of this auditor's scope (this
character's binding project boundary: work only inside the dispatched
project, ask rather than reach into another one). That installer also found
no legitimate unattended job in those projects depends on a fence-blocked
construct (checked before installing).

**Correction to the peer's premise:** the coordinating message described
`trigger_type='dispatch'` as "held back at merge" pending an
`is_unattended_caller()` fix. That is not what happened — §4 shipped the
stamp fully live in commit `8a1ec95`. What *is* real is the consequence: once
live, it exposed a genuine pre-existing bug in `is_unattended_caller()` (§6).

## 6. `is_unattended_caller()` scoped to the caller, not the whole server

**The bug.** `mc/unattended.py:is_unattended_caller(project_id=None)` — used
by `PUT /api/config` and every secrets-vault write — returned `True` if ANY
running session ANYWHERE was non-`manual`. `PUT /api/config` calls it with
`project_id=None` specifically because a config key isn't scoped to one
project, so it counted every session on the box. Before §4, agent-to-agent
dispatch was mislabeled `'manual'` (§4 risk #3) so this rarely tripped. After
§4 fixed that mislabeling, it became common: Ron's own dashboard `PUT
/api/config` — a real browser request — would get refused with a 403 for as
long as any unrelated dispatched helper, schedule, or hivemind worker was
running anywhere in the install, on any project.

**The fix.** `is_unattended_caller()` now checks the request first: a
request carrying the browser `Origin` header — the same structural signal
`workflow_routes._is_agent_caller()` already uses to prove a call came from
the SPA and not an agent's Bash/curl tool call, since this app has no
session/CSRF layer to check instead (MC-914's localhost-trust precedent) —
is **never** refused, regardless of server-wide session state. A request
with no Origin header (an agent's own curl call) still hits the exact same
running-session check as before — this narrows the false-positive, it does
not loosen the gate for a genuine agent caller. `has_request_context()`
guards the existing unit tests that call this function directly with no
Flask context at all (`tests/test_unattended_gate.py`), so the session-only
logic they pin is unchanged.

**Consequence check, as asked:**
- *Builders pushing after a dispatched fix.* No character/agent prompt in
  `data/characters/` or `.claude/agents/` instructs an agent to `git push`
  as routine work (grepped, zero hits) — consistent with the standing
  practice that Dave/a human does the merge-and-push. No active workflow was
  found that depends on a dispatched child pushing.
- *Secrets vault, `allow_unattended=false`.* Queried the live vault
  (`GET /api/secrets`): exactly one secret is set to attended-only —
  `google` (global scope, `use_count: 0` — never used). No dispatched-agent
  workflow currently depends on it. Not changed — that flag is a human-only
  write per `CLAUDE.md`'s vault rules; flagged here for Ron to review, not
  altered.

## 7. Fence bug found live during the cross-repo rollout — fail-closed-on-ambiguity was never safe

The peer session installing the fence into other projects (§5) reproduced a
real bug from `apex_trader`'s checkout before rolling out further, and
rolled back 18 installs until it was fixed here.

**The bug.** `steward/fence.py`'s `main()` enforced whenever
`_session_is_steward()` returned `None` — transcript missing, unreadable, or
with no user text yet — on the documented assumption that "the fence is only
ever installed in steward-enabled projects," where that ambiguity was rare
and effectively steward-adjacent. That assumption stopped being true the
moment §5 began installing the hook into every registered project: a
brand-new interactive session's very **first** tool call has no transcript
file yet (identical shape to the ambiguous case), and any transient
transcript-read hiccup has the same shape. Reproduced: `transcript_path`
pointing at a nonexistent file, or omitted entirely, with no
`CLAUDE_CODE_SESSION_ID` set → `git push` blocked with the "you are running
unattended" message, in an ordinary interactive session. The identical latent
bug already existed in mission_control itself before this change; it just
had no chance to fire because the hook was only ever installed in
steward-enabled projects, where the ambiguous case is far rarer.

**The fix.** `main()`'s gate is now: confirmed steward (`marker is True`)
always enforces, unchanged. Everything else — confirmed non-steward **or**
genuinely unknown — falls through to the trigger_type check (§4) and
enforces only on a positive confirmed match, exactly like the non-steward
branch already did. No new gap for steward cycles: they are dispatched as
ordinary schedule fires (`trigger_type='schedule'`,
`mc/blueprints/scheduler_routes.py:712,1367` — the `[Steward cycle]` prompt
marker rides *in addition to*, not instead of, that stamp), so a steward
session whose transcript can't be read yet still arms through the
server-recorded `trigger_type` rather than through blanket fail-closed. No
dispatch-site change was needed — `'schedule'` was already in
`_UNATTENDED_TRIGGER_TYPES`.

Two pre-existing tests encoded the old (now incorrect) assumption and were
updated, not deleted, with the reasoning left in place as a comment:
`test_unknown_session_fails_closed` → `test_unknown_session_with_no_signal_
is_allowed_not_fail_closed` (rc 2 → rc 0), and `test_hook_blocks_with_exit_2_
and_stderr` now uses a confirmed-steward transcript instead of a missing one,
so it is testing the block *contract*, not the session-gating decision.

**Full pytest run (§5–§7):** `python -m pytest tests/ -q` → clean, exit code
0, zero failures. Tail in the chat reply.


---

## 8. Follow-up shipped: Codex unattended sandboxing (Tobin, 2026-09-14)

Closes risk #1 (§2) for the launch paths where it's safe to close: Codex
launches with a `trigger_type` MC itself recorded as unattended
(`schedule`/`workflow`/`dispatch`/`hivemind_orchestrator`/`hivemind_worker`)
now run `-s workspace-write`, confined to the launch cwd, instead of
`--dangerously-bypass-approvals-and-sandbox`. Manual (interactive) Codex
sessions are unaffected — untouched code path, same flag as before.

### What shipped

1. **`mc/agent_runtime.py`** —
   `codex_unattended_sandbox_decision(session_dict, sandbox_config_enabled)`:
   reads `session_dict['trigger_type']` directly (no HTTP round trip — unlike
   `steward/fence.py`'s PreToolUse hook, this runs IN-PROCESS with the
   session dict already in hand). Imports `steward.fence._UNATTENDED_TRIGGER_TYPES`
   for "what counts as unattended" (no second detector), but the actual gate
   is `trigger_type != 'manual'` when the flag is on — a DELIBERATE
   divergence from the fence's own fail-OPEN posture on an unrecognized
   value. The fence runs per-tool-call inside an already-steward-enabled
   project, so a false block only interrupts one action a human can retry;
   this decision runs ONCE, before the process spawns, and sets the posture
   for an entire unattended run nobody is watching — a wrong 'bypass' there
   is not a pause, it's an unconfined shell for the whole session. A module-
   level `assert 'manual' not in _UNATTENDED_TRIGGER_TYPES` pins the
   assumption the shortcut relies on.
   `CodexRuntime.build_command(unattended_sandbox=...)`: exactly one of
   `-s workspace-write` or the bypass flag, on both the fresh-dispatch and
   `exec resume` branches. `dispatch()` computes the decision once and
   stashes it on the session dict (`_codex_unattended_sandbox`) so
   `write_followup` reuses the SAME posture on every respawn rather than
   re-deriving it (a human flipping the config flag mid-conversation must
   not change an in-flight session's posture). `write_followup` reads the
   stash with a `True` (sandboxed) default if it's ever missing.
   `_mode_a_reader`'s `TOOL_USE` branch (shared across all Mode-A providers,
   gated to `runtime.name == 'codex' and session.get('_codex_unattended_sandbox')`
   so it can't mislabel an unrelated permission error on another provider or
   a manually-launched Codex session): detects a sandbox-denied shell command
   and appends a `[hint]` log line. This is NOT caught by the existing
   `rc != 0 -> explain_exit_error()` path — measured live (§ below): a denied
   inner write does NOT fail the `codex exec` process itself (rc stays 0;
   codex just reports the inner command's own nonzero exit in its own text).
   The regex (`_CODEX_SANDBOX_DENIAL_RE`) matches `UnauthorizedAccessException`
   / `PermissionDenied` — the two unbroken tokens a real denied `Set-Content`
   produced on this box (`is denied` itself wraps across a `\r\n` in the real
   output, so it isn't matched).
2. **`mc/blueprints/agent_routes.py`** — `_dispatch_via_runtime` reads
   `state.CONFIG.get('codex_unattended_sandbox', True)` and passes it as
   `unattended_sandbox_enabled` on the `runtime.dispatch(...)` call.
   `agent_runtime.py` deliberately never reads server CONFIG directly (same
   convention as `ClaudeRuntime.build_command`'s "config passed explicitly"
   docstring) — every other runtime's `dispatch()` has a `**_extra` catchall
   so the new kwarg is a no-op for them.
3. **`server.py` / `mc/blueprints/settings_routes.py`** —
   `codex_unattended_sandbox` config default `True`, added to
   `_CONFIG_EDITABLE_KEYS` (same unattended-caller-cannot-write gate as
   `fence_unattended_enabled`).
4. **Tests** — `tests/test_codex_unattended_sandbox.py` (new, 18 tests):
   the decision function's full matrix (each unattended trigger_type
   sandboxes; manual bypasses regardless of the flag; flag off always
   bypasses; an unrecognized future trigger_type fails safe to sandboxed;
   a missing/`None`/exception-raising session_dict fails safe to sandboxed
   without crashing) plus `build_command()`'s flag shape on both branches
   plus the denial regex against the REAL captured PowerShell output (below).
   `tests/test_agent_routes.py` (+4): `_dispatch_via_runtime` threads the
   CONFIG flag to a stub `CodexRuntime.dispatch()` correctly, including the
   config-key-absent-entirely case.

### Jobs affected / write-outside-cwd audit (item 3)

Checked every schedule (`GET /api/schedules`, 29 entries) and the one saved
workflow (`GET /api/workflows`). Neither `day_trading_engulfing_scanner` nor
`apex_trader` (the brief's likely candidates) runs Codex via a **schedule** —
both projects have `agent_provider: None` (defaults to Claude) and there is
no global `agent_provider` override, so every schedule dispatches Claude.

**One live unattended Codex launch exists on this box:** the "Check US
stocks" workflow (`wf-d7942f5a`, schedule-triggered, `apex_trader` project),
whose agent step runs character `global:us-stock-investor` (Vance,
`provider: codex`, `model: gpt-6-astra`) — `trigger_type='workflow'`, so it
IS now sandboxed by this change. Read `~/.claude/agents/us-stock-investor.md`
(the character's own directive) and the workflow definition: the task is
market analysis ("scan the US stock exchange... find opportunities") with a
`notify_operator` (email) action as the workflow's own follow-up step — not
something the Codex agent process itself runs as a shell command. The
character prompt has no file-write directive at all. **No write-outside-cwd
need found** — this job should run unaffected by the new sandbox. If Vance
ever needs to write outside `apex_trader`'s project folder, there is no
per-job override today (§3d of this audit already declined a per-schedule
opt-in) — the human-only `codex_unattended_sandbox` config flag is the only
lever, and it's global, not per-job.

### Real-box verification (item 5)

Two, not one: a hand-typed `codex exec -s workspace-write` CLI call (proving
the CLI flag's raw behavior) AND a second run through the actual wired code
(`CodexRuntime.dispatch()`, `_scratch/codex_sandbox_real_wiring_test.py`,
not committed — gitignored scratch) proving the FULL path: trigger_type ->
`codex_unattended_sandbox_decision` -> `build_command` -> real subprocess ->
denial hint.

```
$ codex exec --json -s workspace-write -C <scratch dir> "write inside.txt; then attempt ../outside_write_test.txt"
```
Result: `inside.txt` written (`inside-ok`), `codex exec` process exit 0, no
hang. The inner PowerShell write to `../outside_write_test.txt` came back as
its own `command_execution` item with `exit_code:1`,
`"Set-Content : Access to the path '...' is \r\ndenied." ... PermissionDenied
... UnauthorizedAccessException` — no file created outside the sandboxed dir
(confirmed by listing).

```
$ python _scratch/codex_sandbox_real_wiring_test.py
[ok] session._codex_unattended_sandbox = True
[ok] hung=False (must be False)
[ok] final status=completed
[ok] inside file exists=True content='wired-inside-ok'
[ok] outside file exists=False (must be False)
...
[hint] That command was refused by the unattended sandbox (-s workspace-write,
confined to this session's own working directory) — not a codex failure. ...
ALL ASSERTIONS PASSED
```
`session['trigger_type'] = 'schedule'` drove the sandbox decision end to end,
the process completed (no hang), the inside write succeeded, the outside
write was denied, and the sandbox-denial hint (item 4) fired correctly in
`log_lines` — all four verified together, not separately.

### Residual gaps (not implemented, out of scope this pass)

- **`CodexRuntime.oneshot()`** (the brief's third named line, `~4789`, now
  shifted) is **unreachable dead code for Codex today** — grepped every
  caller of `.oneshot(` in `mc/`: `mail_launder.py` and `memory.py` (the
  toolless-oneshot pattern) both hardcode `get_runtime('claude')`, never
  `'codex'`. It still carries the unconditional bypass flag, untouched. It
  has no session/trigger_type concept to hook the same decision into (no
  session dict, no dispatch-time stash) — if it's ever wired up for Codex,
  that needs its own design, not a copy of this one.
- **The `dispatch` trigger_type stamp** was written against a base where it was
  still held back. It was re-enabled on master in the same day's §4b/§6 merge,
  so on master an agent-dispatched Codex helper IS stamped `dispatch` and IS
  sandboxed by this change. (Dave, at merge.)
- **Codex sandbox is global, not per-project/per-job** (mirrors §3d's
  reasoning for the fence: no evidence yet that a legitimate unattended job
  needs `git push`/cloud-mutation-equivalent write reach outside its own
  cwd). Revisit if one turns up — Vance's job doesn't need it today.
- Item #4's detection lives in the SHARED `_mode_a_reader` (all Mode-A
  providers), gated to `runtime.name == 'codex'` — a small, deliberate
  provider-specific branch in shared code, same shape as the existing
  `_format_tool_activity` cross-provider dispatch a few lines above it.

**Full pytest run (this follow-up):** `python -m pytest tests/ -q` → clean,
exit code 0, same 6 environment-conditional skips as §4, zero failures.
