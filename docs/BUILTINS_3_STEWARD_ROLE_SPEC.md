# BUILTINS-3: Steward as a character role

Date: 2026-10-31

Status: specification; the dispatching owner selected option A for section 12
in the SR-1 dispatch. No live migration is included.

SR-1 follow-up: the dispatching owner selected Q1 A and Q2 A. The branch-only
[binding implementation contract](BUILTINS_3_SR1_BINDING.md) records schema,
human write boundaries, recovery and the deliberate SR-2/SR-3 execution block.
The remaining tickets and live migration are not implemented by SR-1.

SR-2 branch handoff: [cycle composer and protected patches](BUILTINS_3_SR2_CYCLE_TASK.md).
The composer and packaging entry are present in that candidate; the canonical
directive/skill and tiny core adapter are unapplied human-integration patches.
SR-2 is not integrated and the SR-1 execution block remains until later work.

SR-3 branch handoff: [guarded dispatch contract](BUILTINS_3_SR3_DISPATCH.md).
Role timer/Run Now preparation and filtering seams are present; the branch
now uses merged SR-4 assignment claims, watermark, journal and notifier APIs.
Child drain/continuation handoff checks are now present. Dave selected Option A:
the channel callback checks the existing pilot tools and credential-source
metadata without reading credentials or invoking mail/IMAP/models. Other
projects remain held unless their tool paths match the pilot runners.
Protected SR-2 resources remain explicit delivery blockers; metadata readiness
does not prove authentication or delivery.
No live migration is included.

Parent: [Built-ins platform plan](BUILTINS_PLATFORM_PLAN_2026-10-31.md), item 3.
Implement and verify this item before building item 4.

## 1. Problem and users

Steward is currently a project mode with its own charter, cadence and cycle
prompt. Named-character schedules can already select a persona and engine,
but must restate stewardship in task prose. They do not receive the same
charter refresh, decision handling and cycle contract automatically.

The operator must be able to assign a roster character a field of
responsibility and a recurring wake. The selected character retains its
name, voice, avatar and engine. Steward supplies the cycle procedure and
unattended constraints; it is not a replacement character.

The first migration is the existing Dave backlog run. This is a pilot
procedure, not a public default character or a hardcoded installation ID.
Other projects and schedules are not migrated by this work.

### Binding requirements

1. Every supported engine receives the same role contract and provenance
   filters. An engine without a proven unattended guard is refused visibly;
   the system must not substitute Claude or launch without a guard.
2. The charter and current character reach every cycle, including a live
   continuation and recovery after server restart.
3. Existing human-only gates, learning safety rails, project boundaries and
   the one-steward-per-project invariant remain intact.
4. The cycle reads its journal and checks pending replies before choosing
   work. Unattended progress goes to the journal, never backlog notes.
5. The scheduler remains the only wake mechanism. A role does not introduce
   another loop, process supervisor or dispatch engine.

### Non-goals

- Changes to `steward/fence.py`, permission expansion, unattended publish,
  push, payment, credential creation or workflow activation.
- Hivemind roster workers, multi-steward parallelism within one project,
  new character creation or a new character engine format.
- Memory ranking/origin redesign, automatic skill installation, or changes
  to the Distiller's authority guard or suppression rules.
- A new inbox, SMTP/IMAP implementation, mail authentication mechanism,
  cross-project mandate or automatic conversion of prose schedules.
- General scheduler redesign or a new steward dashboard. The existing
  schedule editor and steward controls receive the fields needed here.

## 2. Verified starting contracts

These are checkout observations, not claims that the proposed design ships.

| Source | Current behavior and consequence |
|---|---|
| `steward/core.py:build_cycle_task` | Builds `[Steward cycle]` from project objective/charter and a refresh string. It still tells the agent to append backlog notes; that instruction must be replaced. |
| `data/skills/builtin/mc-steward/SKILL.md` | Contains the cycle and guardrail directive, with obsolete backlog-note and re-arm guidance. Non-Claude role operation cannot depend on this skill being auto-discovered. |
| `mc/blueprints/steward_routes.py` | Enables a project, seeds one charter and a `steward: true` schedule, installs a fence and exposes the kill switch. Enable currently warns if fence installation fails. The role path must not inherit an unfenced-success result. |
| `mc/blueprints/scheduler_routes.py:_steward_cycle_task` | Rebuilds memory and exploration context with `consumer_unattended=True`, reads a project-wide last-cycle watermark and returns a respawn flag for newly installed skills. |
| Scheduler timer / `schedule_run_now` | Timer refreshes `steward: true` tasks. Run Now uses stored task text. Both can continue a thread before applying current schedule engine settings. These paths must converge for roles. |
| `_scheduled_continue` | Appends to a live Mode B process or revives Claude/non-Claude history. A live append cannot change its engine or original system prompt. |
| `_resolve_character`, `_dispatch_agent_internal`, `_build_agent_context` in `mc/blueprints/agent_routes.py` | Resolve `global:<name>` / `project:<name>`, character engine metadata and persona body. Voice belongs to the character; conduct belongs to rules. Ordinary missing-character recovery is intentionally best-effort. |
| `mc/distiller.py` | `STEWARD_TASK_MARKER` is `[Steward cycle]`; text detection requires it at the start after whitespace. Session detection treats only a literal manual trigger without an unattended marker as interactive. |
| `mc/memory.py` | Unattended filtering applies to memory topics/positions and linked expansion. Its legacy-note treatment differs from the exploration rule; this change preserves both policies. |
| `mc/project_safety_settings.py` | Agent callers cannot change protected project settings, including steward mode/objective/cadence. A body field is not human proof. |

The scheduler currently prepends `[Scheduled run ...]` on continuation.
This defeats a starts-with marker check in context consumers. Any handoff,
fallback disclosure or timestamp added by this feature must follow the
steward marker, not precede it. Trigger provenance remains necessary even
when task text is present.

The plan's counts of projects and named schedules are historical planning
measurements. They are not installation defaults or rollout acceptance
criteria. The live pilot snapshot belongs in the gitignored task journal.

## 3. Proposed data model

The following design uses the section 12 selections recorded in the dispatch.
SR-1 implements Q1/Q2 only; lifecycle and migration belong to later tickets.

### 3.1 Schedule owns execution; project owns the charter

The existing project backlog item remains the human-visible charter. The
schedule owns its role assignment and wake. A project may point to that
schedule for its existing steward controls. Character definitions contain
no project objective, cadence, journal path or steward permissions.

Proposed schedule extension:

```json
{
  "project_id": "<current-project>",
  "character": "project:<existing-character>",
  "steward_role": {
    "version": 1,
    "charter_item_id": "<item-in-this-project>",
    "journal_path": "docs/_journal/<item-id>-<slug>.md"
  },
  "continue_session": true,
  "schedule_type": "interval",
  "interval_minutes": 180,
  "model": "",
  "effort": "",
  "workflow_id": ""
}
```

This is a proposed schema illustration, not a valid current API request.
The role is absent for ordinary schedules. Unknown versions are rejected.
`character` is the sole owner reference; no second character/engine copy
is stored inside the role. An inherited project default may be displayed
while drafting, but activation resolves and pins a nonempty character.
Later project-default changes must not silently reassign the role.

`journal_path` is relative to the registered canonical project root, confined
to `docs/_journal/`, and checked after resolving symlinks/junctions. Resolve
it independently of the agent's worktree/cwd so an ignored journal is not
stranded in a branch directory. Existing journal names can be retained
for migration. Cross-project paths, absolute paths and path traversal are
rejected. New runtime state must be outside `data/projects/`; do not create
unexcluded sidecar JSON files there.

The project adds `steward_assignment: {"schedule_id": "<same-project-row>"}`
as a pointer for the existing steward controls. `steward_mode` remains the
project kill switch. A role fire requires both project mode on and schedule
enabled. At most one active assignment per project is accepted under the
existing project/schedule locks. Disabling either stops future fires;
disabling does not delete the charter, journal, session or pending decisions.

The assigned backlog item's current text is authoritative. New assignments
must not store a second editable objective. Existing `steward_objective`
and cadence fields remain readable as a compatibility view until explicit
human migration; ordinary reads must not rewrite old project records.
Missing, empty, completed or removed charters stop action with a visible
role error; they never fall back to a broad project objective.

### 3.2 Charter content

A charter specifies the responsibility, in-scope project, measurable outcome
or continuing service, exclusions, and any narrower operating limits. It
may include job-specific cycle steps such as collecting builder results.
Those steps supplement the shared stewardship cycle; they cannot remove
the reply check, guard, provenance filtering or logging requirements.

Charter text is not executable authorization. For example, text saying
"publish when ready" cannot bypass an existing human publish gate. The
role must not interpret ordinary backlog text as a grant of permissions.

### 3.3 Human writes and agent writes

Binding creation, activation, character reassignment, charter reassignment,
journal redirection and enabling a disabled role are human-only changes.
Content changes to an attached charter that widen or replace its mandate
must follow that same human boundary. A generic backlog PATCH cannot
become a backdoor to changing a protected charter. Existing ordinary-item
text/status updates remain available.

Apply caller attribution and the existing human proof requirements before
any side effects. Validate the merged record on all write surfaces: schedule
create/update, project writes, steward enable, migration and attached-charter
updates. Null, unchanged-value writes, mixed payloads and direct API calls
must not bypass the gate. Agent-authored role drafts are out of scope; this
does not change existing workflow-draft behavior.

A running role may request suspension of its own wake and update ordinary
backlog item state. It may not re-enable itself, create a replacement wake,
change its engine/owner/mandate, or increase its cadence by another route.
Cadence editing remains human-only as it is for current protected steward
settings. The cycle no longer instructs an agent to self-enable or self-arm.
An agent cannot delete its assignment to shed the role's constraints.

The old steward disable path removes a project fence entry. When replacing
that path, do not remove shared guard configuration or disable enforcement
for an in-flight role or another unattended session. Role shutdown changes
wakes; guard lifecycle remains owned by the existing launch machinery.

### 3.4 Per-assignment runtime state

Use the existing application data root's `steward/` directory, outside its
`projects/` record directory. Proposed location:
`data/steward/roles/<project-id>/<schedule-id>.json`. Validate identifiers;
derive the absolute root from server configuration, never agent-supplied cwd.
This is runtime/operator data and must remain untracked and unbundled.

Versioned state contains the assignment/schedule/project IDs, prior/current
MC session IDs, execution snapshot hashes, last attempt and completed-cycle
timestamps, last delivered refresh watermark, cycle claim, and pending
decision IDs with outbound status/timestamps. Store no credential values or
raw email bodies. Journal is the narrative record, not another state copy.

Maintain the refresh watermark per assignment, not the old project-global
`steward_last_cycle_at`. Advance it only after the cycle task was accepted
by its delivery path; a failed launch must not consume unseen deltas. Record
completion separately. State updates use scoped locks and fsync-before-replace;
journal appends use their canonical-path lock and durable writes. Failure
must be logged and visible, never a silently reset empty history.

## 4. Character, engine and cycle delivery

### 4.1 Resolution

One resolver produces an immutable execution snapshot per fire:
assignment version, project and charter IDs, charter revision hash, character
reference/body hash, effective provider/model/effort with provenance,
directive revision, journal location and schedule/trigger identifiers.
Use the current `mc.engine_selection` and character services, not a second
precedence table embedded in steward code.

- The schedule pins the character. Existing nonempty schedule model/effort
  values override character pins; missing values inherit through the current
  resolver. Empty-value handling must retain current schedule semantics.
- Provider comes from the resolved character, then existing project/global
  defaults. This feature introduces no new schedule-level provider field.
- A missing character at fire time is a visible blocked role, not persona-free
  work or fallback to the project default. Apply this strictly to role fires;
  do not change ordinary chat recovery behavior globally.
- Unsupported model/effort, missing CLI/login, exhausted allowance or failed
  guard produces the existing visible refusal. A role does not enable engine
  fallback or pace rerouting. If either already applies, preserve its current
  opt-in/override rules, disclose the requested and effective engine, and
  verify the effective engine's guard before any launch or append.

The execution snapshot is server-produced diagnostic metadata. User-supplied
`unattended: false`, `trigger_type: manual`, approval booleans or fabricated
snapshot fields have no authority.

### 4.2 One task builder, no generic steward persona

`steward.core.build_cycle_task` remains the compatibility entry point. The
role-specific body is composed in a separate module and delegated to by a
small adapter; do not add another concern's implementation to core or the
agent/scheduler monoliths. A proposed optional keyword carries the resolved
cycle snapshot while preserving the current positional call signature.

The built task must have this order on every engine:

1. Literal `[Steward cycle]` as the first non-whitespace bytes.
2. Local-time wake label, role/character identity, schedule and charter
   references, current objective and bounded job-specific instructions.
3. Shared role directive: orient, check replies, select one next step, act
   within existing authority, record, escalate only when needed, finish.
4. Current character voice and rules refresh required by that delivery path,
   then permitted memory/exploration/skill deltas.

The directive says "You are <character>, holding the steward role for this
project." It must not rename the character Steward or replace its persona
body. Conduct remains above voice. Job steps do not override project rules.
The character's chosen name/avatar remain the session identity; role and
charter are separate metadata, not edits to the character file.

The canonical cycle directive must reach the model as text through Clayrune
on Claude, Codex, Gemini, Qwen, OpenCode, Goose, Aider and Kiro. CLI skill
discovery may supplement delivery but cannot be a prerequisite. Keep one
canonical directive resource and make the builtin skill a documented entry
to it; verify packaging includes the resource in frozen builds. Updating
builtins preserves locally edited copies according to existing checksums;
the task must still carry the current mandatory role contract.

### 4.3 Continuing, reviving and changing engines

Timer, Run Now and restart recovery call the same preparation/dispatch
adapter. It resolves the binding, checks enabled state/guard, obtains a
single-cycle claim and builds the current task before selecting a delivery
path. Run Now remains an unattended role cycle even though a human clicked.

When the effective owner and engine are unchanged, continue the same MC
session where supported. Inject the freshly built cycle text on live stdin
append; use the normal persona-aware context rebuild on cold revival.
Changes to charter text, shared rules, character body, directive or skill
inventory must reach that next turn through the current refresh mechanism;
an immutable live system prompt is not an excuse to retain stale conduct.
Do not use a bare `_build_agent_context(project, task=...)` rebuild that
drops character or incognito policy.

If owner, provider, model or effort changed, never append to a process with
the old selection. Wait until its cycle ends, then use an explicit successor
session with the current resolution and journal/child-work handoff. Native
provider conversation IDs are never resumed across vendors. Preserve a
visible predecessor link; do not falsely report a fresh provider conversation
as a native resume. The thread strategy is Q3; this successor design is the
recommendation, not permission to silently change ordinary chat identity.

At most one role cycle may be active per project, including while a previous
turn is actively waiting on a question or delegated work. Outstanding children
recorded after a closed cycle do not by themselves hold that cycle's lease;
the next cycle may collect them. A cadence tick or Run Now during an active
cycle is skipped/returns busy, not queued for duplicate execution.
Use existing scheduler slot claims and project/session locks, adding a
role-specific durable in-flight guard if needed for crash recovery. An
ambiguous claim/recovery state must not dispatch a second cycle. Record the
refusal; do not infer that an old lease is safe to steal from elapsed time
alone. The role must not kill a process it did not start.

## 5. Learning and unattended safety: unchanged invariants

### 5.1 Marker and provenance

Keep `mc.distiller.STEWARD_TASK_MARKER` equal to `steward.fence.STEWARD_MARKER`.
Retain the equality regression test. Do not rename, translate or hide the
marker. Timestamps, fallback disclosure, handoff context and truncation
must not move or remove it. Test both actual task text and context input.

Every role fire records trusted `trigger_type='schedule'` and its schedule
ID, plus role/charter provenance. A recovery retains unattended provenance.
For manual invocation of a role cycle the marker still forces unattended
handling. Unknown/missing provenance fails conservative session detection;
a human message joining a scheduled thread does not relabel its history as
interactive or provide a fence bypass.

### 5.2 Read floor

At initial context creation, live-turn refresh, cold revival and successor
creation, explicitly use `consumer_unattended=True` for role memory,
explorations and standing-position/negation-ledger retrieval. Link expansion
must apply the same eligibility predicate to linked candidates. Forward the
marker to task-only consumers; do not rely solely on provider or project mode.

Preserve existing policies exactly:

- Exploration artifacts without `origin: interactive` are withheld from
  unattended consumers, including unstamped artifacts. Do not infer
  interactive origin from a filename, promotion suggestion or named persona.
- Memory topics and positions stamped unattended are withheld, including
  indirect retrieval. Preserve the memory subsystem's current legacy-note
  handling; this feature must not silently tighten or relax it.
- Respect `exploration_readback_enabled`, top-k/link expansion settings and
  incognito exclusions. On a retrieval failure omit that enrichment and log
  the failure; never retry using an unfiltered corpus.

The role's own journal, charter and child-result handoffs are operational
continuity, not newly promoted learning artifacts or standing rules. Keep
their provenance and project scope. They may record unfinished work and
decisions; they cannot bypass the read-floor policy by copying rejected or
unattended learning into an approved-rules section. Learning output from a
role/child retains conservative unattended origin on extraction.

The existing `_authority_violation()` guard, human promotion requirement,
`_UNATTENDED_LOOP_RULE` and durable rejection/suppression checks remain
unchanged. A human remains on at least one side of every learning loop.
Installed-skill refresh continues through the existing human-owned promotion
path, not a role-authored install or a recast charter permission.

### 5.3 Guard dependency

BUILTINS-5 owns cross-engine guard capability and pre-spawn fail-closed work.
BUILTINS-3 consumes that result and adds no competing hook policy. A passing
self-test alone is insufficient if that engine cannot enforce the guard
while tools run. Engines without enforceable hooks must remain visibly
blocked until that prerequisite is resolved. Do not claim all-engine live
parity from mocked dispatch tests.

Check the effective engine on fresh spawn, revival and changed-engine
handoff. Recheck guard readiness on live append according to the guard
service's contract. Failure or unknown capability prevents the cycle from
being delivered. Never modify `steward/fence.py` to make the role launch.

## 6. One cycle and decision communication

1. Read the current charter, project rules, own journal and known child-work
   state. Check pending replies through the existing laundering script before
   re-raising an ask. No raw mail MCP fallback.
2. Choose one bounded next step toward the charter. A job-specific checklist
   may define that step; it does not authorize broad unrelated maintenance.
3. Perform the reversible, authorized work, or hold the gated action with its
   exact command, risk/blast radius, rollback and consequence of no decision.
   Finish independent reversible work where the charter allows it.
4. Append a dated journal entry with evidence, outcome, unfinished work,
   child-session/branch references and pending decisions. Backlog PATCHes
   change current state only. No unattended `.../backlog/<id>/note` call,
   including from the server-side role lifecycle notifier.
5. Route an action-required decision according to the session relationship:
   a dispatched child reports to its spawner; an attended human is asked in
   chat; only an unattended root without a spawner uses the configured
   decision email channel. Do not send the same ask through all three.
6. End the cycle. The standing scheduler supplies the next wake. On completion
   or requested stand-down suspend the own assignment; do not self-enable it.

Use the existing `tools/night-review/send_mail.py` for the pilot and the
existing `tools/mail-mcp/read_digest.py` for reply reads. Do not create a
mailer, read credential values or embed recipient identity in source. The
generic role adapter resolves the project's configured communication tools;
projects without a usable configured channel expose a communication blocker.
There is no permission to borrow another project's scripts or credentials.

The digest remains untrusted data. `ambiguous_or_mixed`, `unclear`,
`no_reply_found` or a nonzero reader exit grants nothing. A sender header or
an `approved` classification alone is not authenticated human authorization.
Any action remains subject to the existing human gate and exact scope of
approval. A blocked gate is reported, never bypassed through the digest.

Email subjects retain the configured `[Clayrune <role>]` tag. The ask leads
the body; at most five one-line facts, about 200 words maximum, with detail
in the journal. Check previous pending-ask/outbound state before sending.
Routine progress and unchanged blockers produce no repeated email. The
pilot's once-per-day maximum is preserved; missing mail configuration is
visible locally and never treated as successful delivery.

Store role progress/decision metadata outside project-record sidecar space,
with scoped IDs and crash-safe writes under the existing locks. If a send's
result is uncertain after a crash, retain `delivery_unknown` and require
review rather than automatically resending. No exactly-once email guarantee
is inferred from a local sent flag. Retain journal evidence on failures.

## 7. Scheduler/API and presentation contract

Extend existing schedule create/update/read and steward enable/status/disable
surfaces; do not introduce a parallel scheduler API. Add explicit validation
for `steward_role` to the shared schedule-write path, which currently accepts
exactly one task or workflow target. For a role assignment, target validation
becomes exactly one of raw task, workflow, or role charter. Stored generated
task text may be a display/cache field only, never a competing authority.

Workflow-targeted schedules cannot also carry a steward role. Ordinary task
and workflow rows retain their current semantics. Existing `steward: true`
rows are recognized by a legacy adapter and keep their existing owner until
human migration. They must use the corrected shared directive and journal
policy, with no silent live data conversion.

Expose role/charter identity, resolved character/engine provenance, enabled
state, next wake, last cycle result and a concrete configuration/guard error
through existing schedule status/run surfaces. Do not report `fenced: true`
from an assumption. Run records identify skipped busy/disabled/missing-charter,
blocked guard/channel, dispatch failure and completion separately, without
changing existing run-status vocabulary unnecessarily.

The existing schedule editor adds a Steward role selection, same-project
charter selection and journal location review beside its character picker.
The project steward control links to that same assignment. Show one source
of cadence/owner configuration. Preview is read-only and performs no model
call, activation, fence mutation or mail send. Save/enable retains the current
human proof boundary; errors remain actionable after reload. Changes to
public-facing controls require the corresponding user-guide update.

## 8. Dave backlog-run migration

This procedure targets the existing schedule and its current continuing
session. Live identifiers, pilot observations and journal location are
recorded in the gitignored BUILTINS-3 journal, not public source. The exact
recoverable task/configuration snapshot is captured at migration time.
This specification does not execute the migration.

1. **Inspect and snapshot.** Re-read the selected same-project schedule,
   current character definition/engine, latest session, charter candidates,
   pending asks, owned children and journal. Save an operator-local recovery
   snapshot outside the repo's tracked files. Record the exact pre-migration
   schedule revision; reject applying against a changed row.
2. **Prepare the charter.** Put the backlog responsibility and job-specific
   procedure in the same-project charter. Preserve collect/review, local
   landing, smokes after each merge, ready-to-push evidence, child ownership
   and notification callbacks, and the no-push human gate. Keep the existing
   journal and once-daily mail tag/limit. Shared cycle prose moves into the
   role directive; job-specific acceptance criteria must not disappear.
3. **Review conflicts.** The pilot's stored prose and later standing rules
   differ on builder selection, fan-out and restart handling. Present that
   diff to the dispatching owner. Do not silently pick the stale task or
   rewrite current policy during migration. Record the selected mapping.
4. **Quiesce and switch.** After explicit human activation, suspend future
   fires, wait for the current cycle to finish, and capture outstanding
   child results. Reuse the schedule ID and its 720-minute cadence,
   continuation setting, run condition and current model/effort inheritance.
   Bind the current character explicitly. Do not create an additional
   project-default steward schedule. Failed validation leaves the old
   schedule paused and recoverable, not partially enabled.
5. **Handoff.** Under Q3's selected thread policy, preserve the current MC
   thread when the engine/identity are unchanged, or visibly link its
   successor. Do not switch while child callbacks still target an abandoned
   thread. Either retain that receiving session until deliveries drain or
   use an existing durable delivery handoff; if neither is possible, hold
   migration until children finish. No fire-and-forget callback retargeting.
6. **Verify and resume.** Human Run Now exercises one role cycle. Confirm
   marker first, correct character and effective engine, current charter,
   journal continuity, filtered read floor, pending-reply check, no duplicate
   dispatch and no mail without the existing reason/limit. Re-enable the
   same schedule through the human path only after evidence passes.

Rollback disables the role's wake and waits for its cycle to end, restores
the saved schedule configuration under the same ID with human authorization,
and relinks the retained prior thread where supported. Preserve the charter,
journal, pending decisions, children and run history. Never delete user data
or undo completed merges as a side effect of configuration rollback.
The legacy task must retain the corrected journal/communication rules;
rollback must not restore known unsafe guidance or weaken installed guards.

No migration may publish, push, restart the server or send mail as a test.
A required server reload/restart and any real decision email are separate
operator actions under their existing gates.

## 9. Test and acceptance plan

Tests use temporary projects, fake clocks/dispatchers and fake mail tools.
Do not import production `server` in a new standalone probe or launch login,
provider, browser, tunnel or mail processes from unit tests.

| Area | Required evidence |
|---|---|
| Model/validation | Role round-trip; invalid version; missing/foreign/completed charter; deleted character; pinned inherited owner; role/task/workflow exclusivity; symlink/path escape; one active assignment; no DATA_DIR sidecar pollution. |
| Write authority | Agent refusal for role creation/re-enable/reassignment/charter widening, project pointer writes and mixed payloads; unchanged/null/spoofed-human fields; human saves use existing proof; own suspension cannot be used to re-enable or shed guards. |
| Task/voice | Canonical directive delivered without native skill discovery on all eight engines; real character body/name retained; no generic persona overwrite; no backlog-note URL or self-activation guidance; packaged directive resource present. |
| Marker/provenance | Equality with fence constant; marker remains first after timestamp/fallback/handoff; schedule, Run Now, revival and child delivery retain unattended origin; unknown provenance never becomes interactive. |
| Read floor | Initial, live append, cold revive and successor pass `consumer_unattended=True`; unattended exploration and unstamped exploration excluded; unattended memory/positions and linked expansions excluded; existing legacy-memory handling and kill switches unchanged. |
| Learning rails | Existing authority-violation, suppression, human-promotion and origin regression tests remain green; role artifacts are stamped unattended; journal/charter text cannot become approved learning or install skills. |
| Engine lifecycle | Current resolver precedence, unsupported pin errors, no silent fallback; live append cannot retain a changed engine; same-engine revival carries persona/rules; native IDs never cross vendors; opt-in rerouting discloses effective engine and checks its guard. |
| Concurrency/recovery | Timer versus Run Now; duplicate ticks; restart during cycle/claim; disabled assignment; quiescing migration; guard failure on append/revive; ambiguous lease does not dispatch; no unowned process termination. |
| Decisions | Reply reader runs before re-raise; spawner/chat/root email routing; ambiguous digest/reader error gives no approval; no raw mail fallback; daily cap/new-event dedupe; uncertain send is not retried blindly; unavailable channel is visible. |
| Migration | Repeat application is idempotent; revision conflict fails without partial enable; same schedule/cadence/journal/children retained; old and new wake cannot both fire; rollback preserves records and guards. |

Existing suites to retain/run when their seams change:
`tests/test_steward_core.py`, `tests/test_steward_refresh.py`,
`tests/test_steward_routes.py`, `tests/test_scheduler_character.py`,
`tests/test_scheduler_routes.py`, `tests/test_scheduler_slot_claim.py`,
`tests/test_distiller_safety.py`, `tests/test_fence_steward_marker.py`,
`tests/test_codex_steward_fence.py` and the BUILTINS-5 guard suites.
New modules under `mc/` pass pyright basic without adding errors.

For UI changes run the existing frontend boot check and focused schedule/
steward editor smoke, desktop and phone width. After each integrated builder
branch, run its affected tests/smokes before merging the next branch. Reuse
the main checkout's smoke dependencies in a worktree; do not install another
browser or recursively remove a dependency junction.

Release evidence separates adapter tests from actual provider runs. Run one
controlled read-only role cycle on each installed, configured, guard-capable
engine using test project data and a fake/no-send decision channel. Verify
its observed identity, input, guard readiness and journal output. Report any
unavailable engine as unverified or visibly blocked, never as a parity pass.
Unattended enablement for an engine waits for BUILTINS-5's demonstrated guard.

## 10. Build tickets and file ownership

Each ticket is one fresh builder dispatch, with one owner of the named files.
Build serially where adapters touch the same existing file. Workers must not
revert other sessions' edits; rebase/adapt and review the integrated result.
New filenames below are proposals and must be checked for existing equivalent
modules before creation. No ticket owns `steward/fence.py`.

| Ticket | Scope / ownership | Completion and dependency | Estimate |
|---|---|---|---|
| SR-1: Binding and write boundary | New `mc/steward_role_model.py`; its unit tests; focused wiring in `mc/project_safety_settings.py`, scheduler/steward/project backlog write routes. | Schema, merged validation, strict owner lookup, project pointer, protected charter and one-active invariant; ordinary schedule behavior unchanged. Q1/Q2 resolved first. | 0.5 day |
| SR-2: Cycle directive and composer | New `mc/steward_role_task.py` and canonical directive resource beside the builtin skill; update that skill; tiny `steward/core.py` adapter; task tests and resource packaging entry. | One role contract on every engine, marker first, character voice preserved, journal/reply check instructions replace stale notes/self-arming. SR-1. | 0.5 day |
| SR-3: Shared fire/continue adapter | New `mc/steward_role_dispatch.py`; minimal scheduler timer/Run Now and agent-context seams; dedicated dispatch/concurrency tests. | Same prepare path for timer/Run Now/recovery, explicit filtered refresh and provenance, engine-change policy, guard dependency, durable in-flight behavior. SR-1/2, Q3 and BUILTINS-5 capability contract. | 0.75 day |
| SR-4: Journal and decision lifecycle | New `mc/steward_role_state.py` and `mc/steward_role_comms.py`; lifecycle notifier adapters; fake-mail/recovery tests. | Scoped crash-safe state, journal lifecycle events, no note writes, pending-reply-first policy, spawner routing, dedupe/cap/unknown-send behavior. Reuse existing mail tools. SR-1/2; coordinate scheduler seams after SR-3. | 0.5 day |
| SR-5: Existing controls and pilot handoff | New `static/js/steward-role-editor.js`; thin scheduler/steward UI registration and existing HTML/CSS hooks; focused smoke; migration command in a new tool module; user-guide/scope docs. | Human role edit/preview, accurate errors, dry-run/revision-checked pilot migration and rollback. SR-1 through SR-4; Q4/Q5 and human pilot activation. No automatic live mutation on deploy. | 0.75 day |

The estimate is approximately three builder days after selections, plus
review and live verification; unresolved guard support can block engine
rollout independently. Each builder hands off its branch SHA, explicit
changed paths, validation commands/results and any remaining blocker.

Files containing protected fence bootstrap code may also be refused by the
current unattended fence. Do not change that fence or conceal edits in a
different tool. If a necessary tiny core adapter is refused, hand off a
reviewable patch and exact integration step to the owner; the ticket is
not complete until the authorized integration is verified.

## 11. Definition of done

The item is complete when the approved role model is saved through the
existing human controls, the Dave pilot performs a verified cycle under its
current roster identity/engine with one wake and preserved journal/children,
the safety/continuation/migration checks pass, and every enabled engine has
demonstrated its guard. Unsupported engines are visibly blocked and listed
as a limitation. No product question below is silently decided in code.

This document alone completes only the specification deliverable. It does
not mark the role implemented, migrate a schedule, enable a cycle or authorize
a merge, restart, push or email.

## 12. Product selections for the dispatching owner

These are selections, not implementation discretion. The dispatching owner
selected option A for every question in the SR-1 dispatch on 2026-10-31.
Q1/Q2 govern SR-1; Q3-Q5 govern later tickets. The options and original
recommendations below remain as the decision record; no selection was made
by the specification author or the SR-1 builder.

| ID | Options and consequence | Recommendation |
|---|---|---|
| Q1: Attachment and multiplicity | A: schedule owns assignment to a project charter, project points to it, one active role/project. B: project-only role with generated schedule; simpler project control but named jobs remain special cases. C: multiple active schedule roles/project; enables independent mandates but changes the existing locked invariant and needs resource/conflict policy. | A. Supports named schedules while preserving the original one-steward boundary; defer C as separate product work. |
| Q2: Charter authority | A: assigned backlog item is canonical; legacy objective becomes a compatibility view, owner pinned at activation. B: schedule embeds a charter snapshot; independent editing but two visible objectives can diverge. C: role lives in reusable character definition; shares behavior but entangles mandate with identity across projects. | A. Keep the ask visible in one project record and the roster reusable. |
| Q3: Thread and engine changes | A: continue unchanged identity/engine; explicit successor on owner/provider/model/effort change with journal/child handoff. B: fresh session every cycle; simpler refresh but more tab churn/cost and callback handoffs. C: pin original engine until manual reset; cheapest continuity but roster engine edits do not take effect next cycle. | A. Preserve normal continuity and disclose transitions; never mutate a live process's identity. |
| Q4: Rollout surface | A: add role fields to existing schedule/steward editors, activate pilot after a human review. B: API and operator migration tool first, UI later; faster backend delivery but no complete self-service assignment. | A. A human-only assignment needs a reviewable human surface; keep it within existing editors. |
| Q5: Dave procedure conflicts | A: preserve job outcome, cadence/journal/mail cap and reconcile stale task prose against current standing rules with Dave's recorded mapping. B: copy all existing prose unchanged and flag conflicts each cycle; less migration work but contradictory instructions remain. | A. The dispatching owner supplies the exact current builder/fan-out/restart mapping; no new push or restart authority is inferred. |

Safety gates, learning-origin rules, no unattended backlog notes, no raw mail
fallback, and `steward/fence.py` ownership are binding constraints, not options.
