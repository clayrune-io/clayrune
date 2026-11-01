# BUILTINS-4: Hivemind roster workers and reviewed integration

Date: 2026-10-31

Backlog: `f2605ff9`

Status: specification for Dave's decision; no implementation authorized by this document.

Source baseline: `9f0b67fefa053a1c7f181a40e177091008e05967`.

Parent: [Built-ins platform plan](BUILTINS_PLATFORM_PLAN_2026-10-31.md), item 4.
Build after [BUILTINS-3](BUILTINS_3_STEWARD_ROLE_SPEC.md) is implemented and
verified. This document does not assert that prerequisite is complete.

## 1. Problem and users

Hivemind workstreams select providers and models, while ordinary dispatches
select named characters with their own voice, skills and engine. Dave cannot
currently express a workstream as a roster assignment. Serial integration
runs named smokes but does not dispatch a reviewer between merges.

For the coordinating character, the workstream must become an assignment to
a real roster character. For the operator, the Floor, run history and
hivemind dashboard must identify who performed it and which merged commit
passed review. For the implementer, the existing runtime, worktree isolation,
lineage and landing services remain the execution boundary.

### Decided requirements from the parent plan

1. A workstream accepts `character: "global:<type>"`; its engine comes from
   that character through the normal engine resolver. No model names are
   hardcoded in hivemind policy.
2. A reviewer seat runs after **each** serial merge, before the next merge.
   The pilot reviewer is Fenn, whose actual reference is
   `global:code-reviewer`, not `global:fenn`.
3. Dave routes an ask containing at least three independent workstreams to
   hivemind rather than launching separate top-level worker dispatches.
4. A Claude worker may use native dynamic workflows **inside** its assigned
   workstream. Hivemind remains the multi-engine owner of workstream state,
   serial integration, smokes and inter-workstream review.
5. Character workers and the review protocol work across every registered
   engine. Native Claude workflows are an optional local execution method,
   not a prerequisite for other engines or an alternative integration owner.

### Non-goals

- A new agent runtime, worktree manager, scheduler, fan-out service, visual
  workflow builder or cross-project mandate.
- Modifying `steward/fence.py`, weakening caller attribution, self-review
  checks, learning-loop rules, human-only workflow decisions or publish gates.
- Creating/editing roster characters, installing skills into a user's home,
  changing live Dave/Fenn definitions, or silently migrating existing runs.
- Automatic landing on `master`, pushing, releasing, posting, spending money,
  changing credentials or authorizing any of those actions by review verdict.
- Reproducing native dynamic workflows on non-Claude engines, or treating
  native child sessions as independent hivemind workstreams.
- Solving dependent code worktrees implicitly. Their base semantics are an
  existing unresolved issue; see Q6.

## 2. Verified starting contracts

These observations describe the baseline checkout, not the proposed system.
The live character lookup was used to confirm identifiers; character engine
settings remain installation data and must be resolved again during implementation.

| Source and entry point | Current contract; implication |
|---|---|
| `server.py`, `_bp_hivemind.wire()` and blueprint registration | Wires the data root, project loader, manager, runtime-generation validator and process registration into `mc/blueprints/hivemind_routes.py`; starts the orchestration loop at boot. New concerns need leaf modules and small wiring adapters. |
| `hivemind_create`, `hivemind_workstream_create`, `hivemind_workstream_update` | Existing POST create/inline create and PUT workstream routes persist `provider`, `model`, `effort`, `dependencies`, `priority` and sanitized `smokes`; no character field. Inline creation skips malformed entries today. |
| Manifest and workstream stores in `hivemind_routes.py` | `data/hiveminds/<hm_id>/manifest.json`, `workstreams/<ws_id>.json`, JSONL bus/findings/decisions/questions, markdown handoffs/context/synthesis; `worktree_base.json` and `integration.json` are separate from the manifest. Nothing new belongs in `data/projects/`. |
| `_hm_spawn_worker_session` | Resolves engine from workstream/config/project, creates an `hm_<8hex>` session, uses `_maybe_isolate_worktree(..., always=True, base_ref=...)`, stamps worktree ownership and passes a dedicated hivemind prompt to runtime dispatch. Isolation failure can currently fall back to the shared checkout. |
| `_hm_build_worker_context` | Adds brief, handoff, findings, bus, decisions and API instructions. It does not itself call the ordinary persona/project context builder. Adding a character field alone does not give a worker its persona, skills or normal project context. |
| `_hm_runtime_dispatch` | Validates persisted `project_generation`, registers session ownership and calls `AgentRuntime.dispatch(mode='A', housekeeping=True, register_process=...)`. Unknown runtimes refuse without substituting Claude. Runtime adapters own launch/parsing/cancellation. |
| `mc/characters.py:read_character`; `agent_routes._resolve_character` | Character identity is scope plus file stem. Metadata includes display/agent name, avatar, engine and skills. Explicit resolution can be strict; replay is deliberately best-effort in ordinary dispatch. BUILTINS-4 must choose its replay contract, not silently change the global rule (Q2). |
| `agent_routes._dispatch_agent_internal`, `_build_agent_context`, `_fresh_context_for` | Ordinary dispatch composes persona and project context, resolves engine/effort and preserves session ownership. A bare context rebuild can lose persona. `_dispatch_agent_internal` exposes metadata, suffix, callbacks and explicit cwd/isolation/session adoption seams. It is not the current direct worker path for every runtime. |
| `mc/engine_selection.py:resolve_engine` | Shared provider/model/effort resolution supports character metadata, native defaults and tier tracking, and rejects known foreign-provider model mismatches. Existing worker code avoids inheriting a manifest's Claude model into a differently owned workstream. Preserve that legacy regression. |
| `mc/distiller.py:is_unattended_task`, `is_unattended_session` | Task-only detection recognizes a steward marker or `you run unattended` near the start; trigger-aware detection treats non-manual sessions as unattended. `_build_agent_context` has task-only read-floor calls. A new worker adapter must prove unattended provenance reaches context construction; a new trigger label alone is insufficient. |
| `mc/hivemind_lineage.py` | Manifest `created_by_session_id` comes from OS-attributed creation. Workers inherit it as `_spawned_by`; `record_worker` durably records lineage before launch. A request-body identity or notification target cannot replace it. |
| `_hm_resolve_dependencies`, `_hm_auto_spawn_workers`, orchestration loop | Pending workers wait for completed prerequisites and available slots. Live ownership reconciles before another spawn; failed prerequisites propagate failure. Dependents still branch from the pinned common base, not prerequisite branch contents. |
| `_hm_terminal_outcome`, `_hm_start_integration` | All successful workstreams currently set manifest `completed` before final synthesis and asynchronous integration start. Integration's status is separate. `integration.json` already existing prevents another start. Successful worker exit is not evidence of successful integration. |
| `mc/hivemind_integration.py:integrate`, `_merge_one_locked` | Creates `hivemind/<hm_id>` in a dedicated worktree; completion-time/id order; checks dirty/missing branches; uses native standards preparation and landing serialization, merges one branch and runs that workstream's sanitized `.mjs` smokes. Smoke failure resets to pre-merge HEAD and stops. An empty smoke list runs nothing and records that fact. |
| `mc/dev_harness_landing.py`, `mc/landing_serialization.py` | Native standards evaluations, candidate revalidation, exact child leases and landed-tree verification already protect managed landing. These services cannot be bypassed to accommodate a reviewer. |
| `mc/merge_review_gate.py`, `mc/blueprints/merge_review_routes.py`, `mc/review_lineage.py` | Existing review record is for `clayrune/agent/<session_id>` at its current tip; request identity is OS-attributed; author/descendant review is refused. The branch-prefix restriction does not accept `hivemind/<hm_id>`. A post-merge review must use a separate record, not broaden the old gate silently. |
| `static/js/hivemind.js`, `static/js/cross-hivemind.js`, `/api/hivemind/<hm_id>/runs` | Existing global list/detail, SSE and run history are the display surfaces. Runs currently classify every non-orchestrator as a worker; adding reviewer sessions needs explicit role filtering. No duplicate per-project tab is required. |
| [Local feature research](research/CLAUDE_CODE_FEATURES_2026-10-31.md), dynamic workflows section | Records native workflow support and a headless keyword limitation. This is research evidence, not a successful hivemind workflow execution test. Do not equate adding `ultracode` to a Mode A prompt with launching a workflow. |

Existing regression anchors: `tests/test_hivemind_routes.py`,
`test_hivemind_worker_model_default.py`, `test_hivemind_worktree_isolation.py`,
`test_hivemind_worktree_dispatch_hook.py`, `test_hivemind_failure_outcomes.py`,
`test_hivemind_lineage.py`, `test_review_lineage.py` and
`test_merge_review_gate.py` (all under `tests/`).

## 3. Contracts for the new path

The requirements in this section are binding outcomes. Field names and
mechanisms labelled **proposed** remain subject to the choices in section 7.
Builders must not turn a recommendation into a decision. Tickets name their
decision prerequisites.

### 3.1 Roster assignment, engine and context

- All three workstream write paths must accept and return the same character
  reference. Explicit malformed or unknown references return a clear client
  error before launching anything. For the new roster-create form, validate
  the whole request before saving an active manifest or dispatching its
  decomposer; a bad second workstream cannot leave the first running.
- The first version must support `global:<type>`. Whether project-local
  characters are also accepted is open in Q1. It must never infer the type
  from a personal/display name or depend on a hardcoded provider/model.
- The worker receives the character body/name/avatar/skills, applicable
  project/shared rules and current hivemind brief/handoff. Compose the normal
  project/character context with a dedicated workstream directive. Avoid
  duplicating the full universal capabilities block twice.
- Every worker/reviewer is unattended even when a person created the run.
  Use existing context APIs with a server-owned unattended signal and test
  that existing provenance filters remain effective. No changes to learning
  eligibility rules or memory ownership are part of this feature.
- Stamp `character` metadata in both live session and runtime metadata so a
  runtime bridge, Floor projection, durable completion and recovery retain
  the same identity. Record the requested reference and resolved
  provider/model/effort per attempt; do not mutate character files.
- Provider/model/effort precedence and character deletion/drift policy need
  Q1/Q2 decisions. Ordinary engine-only records preserve current behavior.
  A quota/auth failure must remain visible; no silent replacement character
  or provider is permitted.
- Code workers on the new reviewed path must have exactly one isolated
  worktree, recorded base SHA and committed branch. Independent workers use
  the common base; Q6 decides dependent bases. A failed isolation attempt
  must hold that code workstream, rather than editing the shared checkout.
  Analysis-only work can complete without producing a merge candidate.
- Manual spawn, automatic spawn, retry and restart must apply the same
  assignment contract. An active worker cannot change persona or engine via
  PUT; assignment changes apply only at an explicit new attempt boundary.
  Existing session IDs/cwd ownership must survive runtime bridge adoption.

### 3.2 Review after every serial merge

For a roster code run, the required sequence is:

```text
candidate validation and existing landing gates
  -> merge workstream N on hivemind/<id>
  -> verify merged tree and run N's named smokes
  -> dispatch independent reviewer against that exact merged HEAD
  -> persist an attributed pass for that candidate
  -> revalidate HEAD, workstream tip and review pins
  -> consider workstream N+1
```

- No reviewer launches for a merge conflict or failed smoke. Existing smoke
  rollback remains intact. A no-op/no-commit workstream is recorded as
  skipped and has no post-merge reviewer. A no-smokes record must remain
  visible; native standards and reviewer checks still apply.
- The review brief includes pre-merge SHA, incoming branch/tip, post-merge
  SHA/tree, workstream brief, changed-file scope and test results, including
  tests not run. It requests defects and evidence rather than code changes.
- A reviewer is a fresh managed session with the configured roster character,
  its engine and its normal context. It reads the frozen merged candidate,
  not the main checkout or an old worker tree. Reviewer edits must not be
  possible through an added write path; unexpected candidate dirt/HEAD drift
  invalidates the verdict and holds integration.
- A reviewer is not an author, a descendant of an author, or a native child
  of the workstream being reviewed. Validate lineage against each contributing
  worker owner represented by the reviewed candidate. Spawning it as a
  coordinator-owned sibling must preserve real durable ancestry, not erase
  it to obtain a pass. Unknown ancestry/OS attribution refuses the verdict.
- A process exit, free-text `pass`, bus message, arbitrary HTTP caller or
  caller-supplied reviewer ID is insufficient. Only the designated, attributed
  reviewer session can submit a structured verdict for its frozen candidate.
  Missing/stale/corrupt verdict, reviewer error, quota failure, cancellation
  or deadline expiry prevents the next merge.
- Candidate identity includes project generation, hivemind ID, workstream ID,
  integration attempt, pre-merge SHA, incoming tip and merged SHA/tree.
  Passing an earlier merge or attempt cannot pass a newer one. Record the
  reviewer reference/session, verdict, report path and timestamp alongside it.
- Persist review ownership before spawning, use bounded runtime dispatch and
  reconcile uncertain launches before retry. No project/landing mutex stays
  held while waiting for an agent. Reacquire ownership and revalidate all pins
  before another merge. Concurrent integration calls cannot advance twice.
- Keep the integration worktree available until review finishes or a durable
  hold is recorded. Current unconditional end-of-run worktree cleanup must be
  adapted for this lifecycle, while preserving runtime-junction unlink rules.
- On restart, an unfinished review remains a hold until its owner/process,
  generation and candidate can be reconciled. Never reconstruct a pass from
  reviewer exit status or start a second reviewer blindly. A resume can reuse
  a persisted exact pass only after revalidation.
- Pause/stop prevents further merges/review launches. Cancel only owned
  runtime attempts through existing lifecycle controls; retain diagnostic
  state. Stale callbacks cannot advance a replacement attempt.
- Existing native standards, branch review gates and human approval gates
  remain additional requirements. Post-merge pass does not authorize release
  or replace a required pre-merge review. Rejection handling, deadline/retries
  and legacy enablement need Q3/Q4/Q7 decisions.

### 3.3 Dave's routing rule

An independent workstream has its own deliverable, explicit file/responsibility
ownership and acceptance checks; it can start from the same base without
requiring another workstream's unfinished output. Three edits in one file are
not three independent workstreams. Reviewing a builder's output is a later
seat, not a third independent worker.

Dave must write the proposed decomposition before dispatch. If it has three
or more independent units, he creates one hivemind with inline workstreams,
selected roster references, dependencies, smokes and a reviewer seat. Inline
creation already avoids an unnecessary second decomposition session. He must
not dispatch those same units separately. For fewer independent units, normal
single dispatch/sequential work remains available. Mixed plans must expose
dependent units and the Q6 limitation, not silently omit them.

Routing does not grant a larger spend allowance. Reuse the existing
`max_concurrent_workers` limit; three eligible units may execute one at a
time when that is the authorized capacity. Dave still makes design selections
and logs them under `DECIDED WITHOUT RON`; workers escalate real choices to
him. The reviewer reports defects; it does not choose product policy. Outward
or irreversible actions still require the existing human approval.

The routing mechanism and its enforceability are open in Q5. A generic server
cannot reliably count independent semantic tasks from arbitrary prose without
a decomposition. Do not claim a prompt-only rule is a server-enforced gate.

### 3.4 Native workflows inside a Claude workstream

- The parent Claude worker owns its one hivemind workstream, worktree branch,
  handoff and completion report. Native child output is supporting evidence;
  it does not directly advance integration or sign an independent review.
- Prove the native workflow can actually start under the selected headless
  runtime/CLI version. Use a fixture/capability adapter and an explicit
  opt-in; do not invent a CLI flag or rely on the research's headless-ignored
  keyword. If unavailable, report `unsupported` and keep the ordinary worker
  method available. An explicitly required native workflow must hold, not
  silently substitute ordinary execution.
- Constrain children to the assigned scope/base. Any native nested worktrees
  must produce committed output on the parent branch before completion;
  their cwd, ref ownership and cleanup need a tested adapter. Preserve guard
  inheritance and author lineage. If those cannot be proved, refuse native
  execution for that run.
- Explicit child concurrency and total-run budgets must fit the authorized
  fan-out. Native defaults must not multiply three outer workers into an
  undisclosed large pool. Q7 selects limits and accounting.
- Parent cancellation/failure must reconcile children before replacement;
  surviving child work cannot write after a workstream is considered stopped.
  Native failures surface in the workstream handoff/status and do not count
  as successful execution merely because the parent exits cleanly.
- Do not use native workflows to create/edit Clayrune workflow definitions or
  approve `/api/workflow-runs/<run_id>/decision`. Existing human-only routes
  in `mc/blueprints/workflow_routes.py` remain unchanged.

## 4. Proposed data and API additions

**Proposed, not yet accepted:** reuse the current create/workstream endpoints;
no new top-level fan-out API. A version discriminator allows explicit
compatibility handling (Q4). Exact keys below are ticket contracts to ratify,
not a claim that current handlers accept them.

```json
{
  "project_id": "<current project>",
  "goal": "<authorized goal>",
  "roster_version": 1,
  "max_concurrent_workers": 1,
  "reviewer_character": "global:code-reviewer",
  "workstreams": [
    {
      "id": "ws_001",
      "title": "<independent deliverable>",
      "description": "<owned files, constraints, acceptance checks>",
      "character": "global:<existing-type>",
      "dependencies": [],
      "priority": 1,
      "smokes": ["<existing-suite>.mjs"]
    }
  ]
}
```

The example's concurrency of one is illustrative, not a chosen global
default. The server stores version/reviewer settings in manifest `config`,
and `character` in each workstream. Omitted legacy engine fields are not
permission to erase them on existing records. Validate new roster settings
on PUT as well as creation; raw `config.update()` cannot bypass policy.

Proposed per-workstream attempt projection:

```json
{
  "character": "global:<existing-type>",
  "assignment_attempt": 1,
  "resolved_assignment": {
    "character_ref": "global:<existing-type>",
    "session_id": "hm_<worker-id>",
    "provider": "<resolved>",
    "model": "<resolved or native default>",
    "effort": "<resolved or native default>"
  }
}
```

Retain existing session/workstream keys (`worktree_session_id`,
`worktree_branch`, `base_commit`, `current_agent_session_id`,
`last_agent_session_id`, retries and completion timestamps). `character` in
session metadata remains the ordinary metadata object, distinct from the
workstream's reference string. No persona body needs exposing in list APIs.

Proposed additive integration state: `phase` (`merging`, `smoking`,
`awaiting_review`, `reviewing`, `held`, `complete`), `attempt_id`,
`current_candidate` and `reviews`. Keep existing `merged`, `skipped`,
`failed`, `unmerged`, `branch` and base/timestamp fields readable. During
review, a merge is provisional; it is not yet an accepted `merged` entry.

Proposed durable review store:
`data/hiveminds/<hm_id>/reviews/<candidate_id>.json`, written atomically by
the review service. Server derives the candidate ID/pins and owning session;
request fields cannot replace them. Record `verdict` as `pass` or
`changes_requested`; runtime errors/timeouts are lifecycle state, not verdicts.

Proposed **new** POST
`/api/hivemind/<hm_id>/integration/reviews/<candidate_id>` accepts
`{ "sha": "<full merged SHA>", "verdict": "pass|changes_requested",
"report_path": "<project-scoped report>" }`. The route validates JSON,
generation, active candidate, exact SHA and OS-attributed designated reviewer,
then calls the record service. Bad shape/reference: 400; missing resource:
404; unauthorized/self/descendant reviewer: 403; changed/obsolete candidate:
409. It cannot update existing agent-branch review records. Repeated identical
submission is idempotent; a conflicting resubmission holds for reconciliation
rather than racing the next merge. Report paths must remain within the
authorized project/run directory.

Reuse GET `/api/hivemind/<hm_id>` for integration/review projections and SSE
`hivemind_integration` for progress. Add reviewer role `hivemind_role=reviewer`
and a reviewer run-history filter; preserve existing worker/orchestrator
filters without counting reviewer sessions as workers. Reuse the existing
unattended `hivemind_worker` trigger classification with a candidate trigger
ID unless a separately audited taxonomy change is approved. Role and trigger
are different fields; a reviewer must not gain manual-session privileges.

API/UI must distinguish `workers complete`, `review pending/held`, and
`integration accepted`. Manifest `completed` alone must never be presented
as reviewed success. Q4 decides compatibility/default enablement; the proposed
additive projection keeps the current manifest vocabulary.

## 5. Build tickets: one concern per module

Each ticket owns its new leaf module and tests. Existing monoliths get only
imports/adapters or extraction of the corresponding old body, not another
policy implementation. `agent_routes.py` and `hivemind_routes.py` are both
listed in [the entanglement audit](ENTANGLEMENT_AUDIT.md). Shared wiring edits
land serially. File names below are proposed new files unless identified as
existing. No builder may edit a different ticket's module to bypass a block.

### HR-1: Roster assignment and persisted schema

Owner: `mc/hivemind_roster.py`; tests `tests/test_hivemind_roster.py`.
Adapters: existing create/inline-create/workstream PUT and manifest PUT.
Decisions: Q1, Q2, Q4.

Implement pure validation/normalization and character/engine resolution
through the existing character and engine services. Keep legacy resolution
available without duplicating its foreign-model protection. Provide one
validated contract used by all workstream entry points.

Acceptance: inline/separate/PUT round-trip; invalid second roster item causes
no writes/launches; unknown/malformed reference refuses; deleted/changed
character follows the selected policy; conflicting engine fields follow Q1;
active assignment edits refuse; fake characters cover all eight registered
providers and tier/native defaults; an old provider/model-only fixture is
unchanged and a non-Claude worker never inherits `sonnet` accidentally.

### HR-2: Worker prompt and runtime assignment adapter

Owner: `mc/hivemind_worker.py`; tests `tests/test_hivemind_roster_worker.py`.
Adapters: extract worker context/spawn concern from the existing blueprint;
reuse `_hm_runtime_dispatch` or an explicit shared-dispatch adapter, preserving
the existing runtime and session ownership seam. Depends on HR-1.

Acceptance: recording runtimes for every provider receive the selected persona,
skills, rules, correct engine, metadata and workstream directive; fresh/retry/
restart use the same contract; unattended read-floor arguments remain true;
Claude bridge does not create a second tree or lose `hm_` ownership; code
isolation failure holds; log-write/generation/unknown-runtime failures launch
nothing; lineage still comes from the manifest's attributed creator; process
registration and existing guard/cap hooks execute once per owned launch.

### HR-3: Durable post-merge review records and attribution

Owner: `mc/hivemind_review.py`; tests `tests/test_hivemind_review.py`.
Depends on HR-1; no git execution or new route bodies in this module.

Implement candidate pinning, atomic records, idempotency and independent
reviewer validation using the existing attribution/lineage primitives.

Acceptance: same/descendant/native-child author and unknown caller refuse;
sibling independent reviewer can pass; wrong designated session, generation,
SHA/tree, attempt or workstream refuses; corrupt/missing store holds; duplicate
identical result does not advance twice; conflicting replay cannot replace an
already-consumed pass; an old pass never applies to a changed integration tip.
Existing `merge_review_gate` prefix/ownership checks remain unchanged.

### HR-4: Reviewer session and verdict HTTP adapter

Owners: `mc/hivemind_reviewer.py` for prompt/spawn; separate new
`mc/blueprints/hivemind_review_routes.py` for HTTP only, with corresponding
`tests/test_hivemind_reviewer.py` and `tests/test_hivemind_review_routes.py`.
Small `server.py` wiring; depends on HR-2/HR-3. These are separable subtickets
HR-4a (runtime) and HR-4b (route), not one combined policy file.

Acceptance: configured review character/engine and candidate fixture reach the
runtime; worker cannot nominate itself or forge reviewer identity; local auth,
400/403/404/409 responses and project generation match existing APIs; reviewer
pending lineage survives restart; deleted reviewer follows Q2; quota/guard/
launch failures hold; owned cancellation and timeout cannot report a pass;
review role appears in logs/Floor and is excluded from worker capacity counts.

### HR-5: Resumable serial merge/review coordinator

Owner: `mc/hivemind_integration_cycle.py`; tests
`tests/test_hivemind_integration_review.py`. Depends on HR-3/HR-4.
Adapters: existing `mc/hivemind_integration.py` exposes one-candidate merge and
smoke operations while retaining native standards and landing serialization;
existing blueprint integration start/read/write delegates lifecycle here.
Decisions: Q3, Q4, Q7 (and Q6 if dependent code is included).

Acceptance: a temporary git repository with two clean worker branches yields
`merge A -> smoke A -> review exact A -> merge B -> smoke B -> review exact B`.
Block A's reviewer and prove B never merges. Exercise conflict, dirty worker,
smoke rollback, standards hold, rejection, no verdict, timeout, HEAD drift,
native-child activity, no-op branch, empty smokes and non-git analysis-only run.
Restart/repeat calls neither duplicate reviewers nor consume stale passes;
pause/stop/deleted project generation cannot advance. Main checkout, master
and remote refs remain unchanged. Keep journal/report artifacts when held;
remove dependency junctions before worktree cleanup and retain the candidate
while a reviewer needs it.

### HR-6: Dave routing contract and decomposition validation

Owner: new `data/skills/builtin/mc-hivemind-routing/SKILL.md` for the reusable
directive; separate `mc/hivemind_routing.py` and
`tests/test_hivemind_routing.py` if Q5 selects structured validation.
Depends on HR-1/HR-5. Do not write a private Dave persona into the public repo.
Split dispatch into HR-6a (directive) and HR-6b (validator) when both are chosen.
Ship the source skill through the existing built-in installer; do not install
or bind it by editing a live user's home during implementation/tests.

Acceptance: fixtures with 0/1/2 independent units retain ordinary routing;
3+ produce one inline hivemind payload with real character refs, file ownership,
reviewer and checks; overlapping ownership and cycles/missing dependencies are
reported rather than counted as independent; review is not a third worker;
configured concurrency one is respected; no duplicate standalone dispatches;
mixed dependent plans disclose Q6 holds. Dave's operator-owned skill binding
is a separately authorized pilot step, not a test that edits home files.

### HR-7: Optional native Claude workflow adapter

Owner: `mc/hivemind_native_workflow.py`; tests
`tests/test_hivemind_native_workflow.py`. Depends on HR-2 and Q7.

Acceptance: fake capability probes exercise supported/unavailable/headless-
unsupported native execution; non-Claude workers use the same outer protocol
without native claims; parent and native-child scope/lineage/budgets remain
pinned; nested tree output reaches the parent branch; child failure/orphan
holds completion; cancellation reconciles all owned children. A separately
authorized, bounded real-Claude pilot must demonstrate actual native workflow
startup in Mode A, child work and parent handoff. Until that evidence exists,
mark this ticket unsupported/incomplete, not implemented by prompt keywords.

### HR-8: Integration and reviewer visibility

Owners: new `static/js/hivemind-review.js` for review UI; a separate small
`mc/hivemind_run_roles.py` for run-role projection if required; tests
`tools/smoke/hivemind-roster.mjs` and `tests/test_hivemind_run_roles.py`.
Wire existing detail/list modules and runs route; depends on HR-4/HR-5.
Split dispatch into HR-8a (UI) and HR-8b (run-role projection); they do not
share policy bodies.

Acceptance: list/detail/Floor/history show requested character and effective
engine; reviewer identity, exact reviewed commit, named checks and hold/report
reason remain accessible at desktop and 390px widths; workstreams complete
while review waits does not render accepted success; legacy/no-review and
analysis-only/skipped states are explicit; stale/missing review is never a green
badge; runs role filters stay compatible and reviewer count is distinct.
Smokes serve this branch's actual assets against controlled APIs; a test
against the main server's old assets is not evidence for this branch.

## 6. Integration checks and definition of done

1. Dave resolves the required section 7 questions and records the selection
   and reversal cost in the item's journal under `DECIDED WITHOUT RON`.
   This specification leaves every question unanswered.
2. All ticket acceptance cases pass with deterministic fake runtime launches
   and real temporary git repositories where branch behavior matters. No unit
   test starts a real login, consumes vendor credits or modifies live projects.
3. Run the existing hivemind, lineage, review and worktree suites named in
   section 2 plus new ticket suites. New modules under `mc/` pass basic
   pyright without adding errors. Run frontend boot and the new branch-served
   smoke when assets change. Re-run affected checks after **each** serial
   merge before taking the next branch; two independently green branches are
   not proof of their combination.
4. A separately authorized pilot uses at least three independent workstreams,
   roster characters on at least two engines, authorized concurrency and an
   independent reviewer after every actual merge. Record each resolved
   assignment, worker branch, smoke target, merged SHA, verdict and master's
   unchanged SHA. Eight-engine adapter coverage is required even if only two
   engines are available for the live pilot; report unavailable live engines.
5. Frontend smokes must target the merged integration assets, not accidentally
   `localhost:5199` serving a different checkout. Existing `_run_smoke` only
   sets cwd; audit each named suite's server/base-URL behavior before claiming
   integrated UI coverage. Use existing main-checkout smoke dependencies via
   safe runtime linking; do not install Playwright in each worktree.
6. Exercise one intentional review hold/recovery and one restart reconciliation
   with controlled test processes. Native workflow proof is separate from
   outer mixed-engine success. No production restart or real fan-out is
   authorized by this documentation task.
7. Update the implementation docs/API reference and `docs/USER_GUIDE.md` when
   behavior ships. Handoff identifies unsupported native execution and open
   live checks. A completed worker manifest is not a completion criterion for
   this feature; every actual merge must have exact candidate smoke/review
   evidence and no remaining required ticket may be silently deferred.

Rollback for the eventual implementation: disable new roster/native creation
at its selected enablement boundary, leave old engine-only records readable,
and hold active roster runs until owned sessions are stopped/reconciled.
Retain branches, candidate records, reports and journals. A code revert must
not reopen pending reviews or delete user work. No database migration or
operator-specific roster change is required by this spec.

## 7. Open design questions for Dave

**No option below has been selected.** Recommendations guide review, not
implementation authority. A builder must return these choices to Dave.

| ID | Question and options | Recommendation and cost | Blocks |
|---|---|---|---|
| Q1 | What does character assignment own? **A:** v1 accepts global refs only and rejects explicit engine fields on roster workstreams; unset character engine keys inherit via the shared resolver, while legacy rows keep their old path. **B:** accept global/project refs and ordinary explicit engine override precedence, displaying departures from the character. | **A.** Smallest contract matching “engine from roster”; keeps two assignment authorities out of a new row. B is more flexible but requires provider-bound model/effort override and recovery tests. Reversing A later is an additive schema change. | HR-1 |
| Q2 | How do deletion and roster changes affect queued/retried workers and reviewers? **A:** resolve the current character at each new attempt, fail visibly if missing, pin engine/persona for that attempt and preserve it on recovery. **B:** snapshot the accepted character assignment for the whole run and replay it despite later roster changes. | **A.** Current roster intent reaches new attempts; active attempts remain stable. Differs from ordinary best-effort missing-character replay, so scope it to the new roster path. B needs a versioned snapshot lifecycle and rules-refresh split; neither may silently use the project's default persona. | HR-1/HR-2/HR-4 |
| Q3 | What remains on the integration branch after `changes_requested`? **A:** retain the provisional commit, mark held, stop all later merges and have Dave arrange a scoped fix/re-review. **B:** reset to the last accepted SHA and require a new candidate attempt. | **A.** Preserves the exact review target and evidence without authorizing automatic edits. UI must distinguish provisional from accepted HEAD. B needs durable rollback ownership and lost-work checks. Smoke failures retain their existing rollback rule under either choice. | HR-5 |
| Q4 | Where is reviewed integration enabled, and what does completed mean? **A:** explicit versioned roster runs require the reviewer and additive integration outcome; legacy runs retain their current execution/status semantics. **B:** enable review for all new hiveminds and redefine top-level completion to wait for integration; migrate existing API/UI consumers. | **A.** Bounded migration and no surprise reviewer spending on old engine-only runs. Both require UI/API success to reflect review state. B costs compatibility/migration work and decisions about non-git analysis runs. No existing manifest is rewritten automatically. | HR-1/HR-5/HR-8 |
| Q5 | Is Dave routing advisory or mechanically checked? **A:** a shipped role skill instructs Dave, and a pure validator checks an explicit decomposition before creating a roster run; no natural-language server classifier. **B:** add a dispatch-time classifier that intercepts arbitrary Dave asks and enforces routing. | **A.** Testable plan/schema validation, lower spend and no hidden interception; semantic independence still remains Dave's judgment. B adds model calls, error handling and persona identification policy. Prompt compliance must not be labelled hard enforcement. | HR-6 |
| Q6 | What happens when a routed ask also contains code that depends on another branch's implementation? **A:** route independent work to hivemind and expose a held dependent follow-up for Dave after reviewed integration; do not claim common-base workers see prerequisite code. **B:** extend orchestration so dependents start from a reviewed prerequisite integration SHA, with new base/ordering rules. | **A for this increment**, with the dependent deliverable explicitly retained in Dave's plan. B is a separate sizable integration/base-pin feature and invalidates the current completion-order assumption. Existing analysis dependencies may continue through handoffs. Dave must choose before any mixed plan runs. | HR-5/HR-6 |
| Q7 | What bounds reviewer waiting/retries and native fan-out? **A:** finite per-run reviewer deadline, no automatic replacement review after uncertain launch, native workflows explicit opt-in with a declared child cap shared across the run. **B:** reuse worker retry/wait settings and let each native worker own a separate child budget. | **A.** Avoids hidden multiplicative concurrency and duplicate review spend; requires an admission/accounting adapter. Dave must set numeric deadline, child cap and retry allowance before the pilot. B is smaller but needs a separately justified total spend ceiling and reconciliation. No native default of 16 children is accepted implicitly. | HR-4/HR-5/HR-7 |

Additional evidence gate, not a policy selection: HR-7 must establish a real
headless native workflow invocation supported by the installed CLI. If that
cannot be demonstrated, Dave receives the exact unsupported boundary; the
native-workflow portion remains incomplete. Ordinary multi-engine execution
must not be presented as proof of the requested native capability.
