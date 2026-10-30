# DH-M5 contract: approved native checks and managed landing

MC-960 / `d813ea78`, DH-M5 A1, 2026-10-29, Sol_Tobin.
**A1 contract approved by Dave (section 9); A2b branch candidate noted in section 10.**
This is section 10.1a's sibling addendum to [the specification](DEV_HARNESS_SPEC.md).
Authority is Dave's `DECIDED WITHOUT RON: DH-M5 scope, option A, staged` journal
entry. Dave reviews A1 before dispatching A2. The
[preflight](DEV_HARNESS_M5_PREFLIGHT.md) establishes the missing interfaces;
[M3](DEV_HARNESS_RUNNER.md) and [M4](DEV_HARNESS_CHECKS.md) remain authoritative
for frozen inputs, native reports, tool compatibility and output suppression.

## 1. Boundary and identities

A2 adds enforcement of **named native checks**, not full catalogue practices.
All 22 descriptors retain their shipped Unsupported deterministic/reviewer
coverage. `validate_enforcement` must continue rejecting every enabled required
full practice whose delivered coverage is Unsupported. Existing independent
review is evaluated separately; it is never per-practice assessment evidence.
No remote CI, deployment evidence, exceptions, new installer or repair agent.

Use these identities throughout:

| Identity | Definition |
| --- | --- |
| `R` | Current active snapshot revision, zero if genuinely unconfigured. |
| `P` | Resolved proposed policy, including explicit native-check selections. |
| `D` | Digest of the immutable execution-plan payload below. |
| `B/H/M/T` | M3's full target commit, incoming commit, unique merge base and merged candidate tree. |
| `S` | M3 dirty-preview manifest, index and HEAD identities; never a merge candidate. |

Canonical digests use `mc.dev_harness_catalogue.digest`, strict JSON-compatible
values, finite numbers and SHA-256 lowercase hex. Reject duplicate JSON keys,
unknown fields and duplicate identifiers. No commands, reports or approval
claims from repository files, HTTP bodies or CLI output become trusted evidence.
Trusted means server-origin storage/provenance within the existing local-writer
trust boundary, not a signature or protection against unrestricted disk access.

## 2. Execution plan and policy schema

### 2.1 Policy extension without capability substitution

Version 2 resolved policy adds `native_checks`, an object keyed by unique check
ID. Each value has exactly `enabled: bool`, `severity: required|warning` and
`practice_ids: [Pxx, ...]` (nonempty, sorted, known catalogue IDs). No inferred
checks are seeded from a profile. Missing selections resolve to `{}`. Existing
practice toggles/caps and all three digest pins retain their semantics.
Draft input accepts exactly the existing profile/practices/digests fields plus
`native_checks`; the resolver supplies version 2 and catalogue identity.
Practice IDs here are traceability, not capability claims. A selected native
check requires its referenced practices to be enabled for guidance; they may
remain warning. Making any of those full practices required still invokes the
unchanged full-practice coverage/cap/evidence validation.

The explicitly narrower native-only policy therefore has full practices at
warning/disabled with acknowledged omissions, plus individually required native
checks. It is displayed as `named_native_checks`, never "P02 supported" or
"external-product enforced". Product profile defaults are not silently edited.
At least one enabled required native check is necessary to claim native merge
enforcement available. Empty/all-warning plans cannot claim a blocking bar.

Read existing version 1 snapshots with their original digest/semantics; do not
rewrite on read. Missing plans in an existing Enforce snapshot mean an
unavailable standards prerequisite, not Off or an empty pass. A new human
activation writes version 2. No automatic upgrade or mode transition.

### 2.2 Content-addressed payload

`native_rules` becomes resolvable: `P.digests.native_rules == D == digest(payload)`.
Keep the M2 guidance install plan in its existing separate namespace. Store a
new plan envelope atomically at
`data/dev_harness/<project_id>/native_plans/<D>.json`, outside `data/projects/`
and outside the repository's proposed policy/config tree. An envelope contains
exactly `{plan_digest: D, payload: {...}}`; an existing D is read/compared, never
overwritten with different contents. Persistence alone grants no execution.

Payload fields (all required):

| Field | Schema and meaning |
| --- | --- |
| `schema_version`, `kind` | `1`, `native_execution_plan`. |
| `project_id`, `project_path`, `git_common_dir` | Valid store ID and canonical resolved local paths; bind the registered project and repository. Non-Git inspection uses null common dir, cannot enforce. |
| `policy_inputs_digest` | Digest of resolved P with only `digests.native_rules` removed; includes catalogue digest, sources/baseline pins, practices/caps and native selections. No circular D reference. |
| `catalogue_digest` | Exact catalogue used to resolve P; not "latest at execution". |
| `baseline_identity` | `{kind:native_measurement_baseline_v1, source:S, caps:<enabled-practice cap map>}` frozen during inspection; its digest must equal P.digests.baseline. This anchors activation measurement, not later application-source runs. |
| `checker_digest` | M3 runner/diff and M4 adapter-code identity, extended with A2 plan/applicability/evidence/coordinator code identities. No repository-supplied checker code. |
| `environment_policy` | Literal `inherit_executor_freeze_each_run`; no per-plan environment values or credential material. M4 freezes the complete actual inherited environment digest per run and checks it again afterward. Approval permits this declared inheritance, not arbitrary caller-supplied environment overrides. |
| `steps` | Ordered, nonempty array, at most M3's 64 steps, as defined below. Order is approved data. |
| `groups` | Ordered, nonempty verification-group array, as defined below. |

Each `steps` entry has exactly these fields:

| Field | Schema |
| --- | --- |
| `id` | Unique `[a-zA-Z0-9_-]{1,64}`; also a key in P.native_checks. |
| `recipe_id`, `recipe_digest` | One M4 released recipe and exact digest of its JSON descriptor. |
| `launcher` | Nonempty argv-prefix array; first element is a resolved absolute installed executable, no PATH lookup at execution. No shell-string parser. |
| `targets`, `configs`, `tool_roots` | Explicit arrays accepted by M4; configs/targets are contained repo-relative paths, roots absolute installed files/directories with M4 closure rules. No downloads. |
| `command_args` | Explicit nonempty argv tail for configured-command recipes; null for fixed-argument recipes. Fixed scanner flags cannot be overridden. |
| `coverage` | Exact M4 coverage object for coverage recipes, otherwise null. Floor must equal the human-selected changed-line cap on P02; no default floor. |
| `timeout_seconds` | Positive finite value within M3's existing maximum. Fail exits/report protocol come exclusively from the pinned recipe. |
| `config_digests` | Exact path-to-byte-digest map of the complete discovered plus declared transitive native config/rule/lock closure. |
| `tool_closure_digest`, `launcher_digest` | M4 inventory digest and actual executable-byte digest, including installed diff-cover in coverage closure. |
| `fixture_contract_digest` | Digest of the shipped adapter fixture-contract manifest for this recipe/code identity; supplied/verified by server code, never a caller boolean. |

Call M4 `make_step` with these explicit values, then set the unique M3 step ID
to the plan ID (multiple scoped invocations of a recipe are allowed).
Do not persist caller-supplied precompiled `argv`, fail codes or prepared-native
objects. Config inventory is inspected without running commands. Existing M3/M4
path/size/symlink limits apply; reject traversal, NUL, unsupported closures and
secrets in plan data. Do not accept arbitrary inline environment values.
Every enabled policy check must have exactly one step and vice versa; disabled
entries have no executable step. Severity/practice mapping comes from P, never
an inconsistent second copy in the plan.

Inspection input steps contain exactly `id`, `recipe_id`, `launcher`, `targets`,
`configs`, `tool_roots`, `command_args`, `coverage`, `timeout_seconds`.
Reject caller-supplied derived digests. Server resolves launcher paths and
config discovery, computes the remaining fields and baseline, and returns P
with server-computed baseline/native_rules pins; supplied pins must match or
inspection fails. Null/absent baseline/native_rules pins in the draft request
mean compute them, not trust a caller's assertion; strip those null placeholders
before calling the strict existing resolver. Human Activate submits the
profile/practices/digests/native_checks projection of returned P as `draft`;
the server recomputes P and checks its exact binding to D. Resolved version and
catalogue fields are server-owned, not extra accepted draft keys.

Each shipped `<recipe>.fixtures.json` manifest has exactly `schema_version:1`,
`recipe_id`, `recipe_digest`, `validator_digest`, `fixture_file`,
`fixture_file_digest`, `cases:[positive,finding,crash]`. The fixture file names
the existing `tests/fixtures/dev_harness/<recipe>.json`; coverage uses its
existing Python/JS fixture. A2 tests recompute file/code digests and exercise
all three cases; runtime verifies the shipped manifest and current code/recipe
identities without requiring test files in a packaged install. A manifest is
adapter compatibility metadata, not runtime project pass evidence.

Each verification group has exactly `id`, `include`, `check_ids`,
`not_applicable_check_ids`, `reason_code`. IDs are unique; check-ID arrays are
disjoint and contain only enabled checks. `include` is a nonempty array of
anchored repo-relative path prefixes (directory prefixes end in `/`, a bare
filename matches that file only, `*` matches every path). No regex, negation,
extension inference or agent classification. `reason_code` is
`source_change|docs_change|config_change|other_change`. A group declares how
all checks outside its `check_ids` are inapplicable: their IDs must appear in
`not_applicable_check_ids`. The two arrays partition all enabled checks.
There are no free-text N/A approvals at runtime.

### 2.3 Binding and invalidation

The active atomic snapshot embeds the exact plan envelope and its D, alongside
P/catalogue/mode/evidence/audit; `native_plans/` alone is not approval. Its audit
entry records D and the native omissions acknowledged by the human. Reading an
active plan checks embedded D, store copy, P/native_rules and all identity
bindings. Missing/corrupt/mismatched records are Error, never fallback to a
draft, a previous plan, a receipt's embedded commands or an empty set.

Changing project location/repository, policy/check selection/severity/caps,
sources/baseline pins, catalogue, recipe/fixture/checker code, argv/targets,
scope/group mapping, native config contents/inventory or tool inventory requires
a new inspected plan and human approval. Candidate edits to these inputs never
weaken the active bar: report `plan_inputs_changed` and hold in Enforce.
Ordinary application source changes need a new run, not a new plan. Ref/index/
manifest changes and inherited-environment changes invalidate run evidence;
environment is frozen afresh under the plan's declared policy, not stored as
plaintext or automatically treated as approved config supplied by the caller.

## 3. Selection, applicability and result aggregation

Mapping is explicit data, not an inferred scanner-to-practice upgrade. Allowed
MVP traceability pairs are P02 -> pytest/vitest/coverage-python/coverage-javascript;
P01 or P17 -> ruff/eslint; P17 -> pyright/typescript/npm-build;
P06 -> gitleaks; P08 -> bandit/semgrep; P07 -> pip-audit/npm-audit/osv-scanner;
P16 -> licence. Reject other pairs in A2; later adapters extend the allowlist in
their own ticket. Do not claim P09/P10 assessment from a P08 scanner.

For integration, classify M3's `B..T` changed paths, considering both old and new
rename names and deleted paths. Branch `M..H` remains attribution only. Preview
uses the frozen dirty manifest/index diff, including nonignored untracked paths;
a clean Preview may measure the configured full scopes by applying all groups.
Match all groups for each path; union their check IDs. A check requested by any
matching group runs even if another group lists it N/A. Record matched group
IDs and explicit per-check N/A reason codes in the safe result.

Every changed path must match a group. An uncovered path is
`unmapped_change`, not docs-only or a pass. A docs/config-only group may omit
coverage and name another approved verification check; each group must execute
at least one check. No-op/identical candidates report `nothing_to_merge` without
manufacturing a command pass. Coverage that actually runs keeps M4's nonzero
denominator rule; no executable denominator is Error, never invented 100%.
An approved group mapping, not a scanner's empty output, provides N/A.

Run selected steps in approved order on disposable copies of the same frozen
candidate/preview. M3 currently stops at its first non-pass; A2 coordinator may
invoke it per step to collect independent results, but must prove all receipts
share the same B/H/M/T or S and active plan/snapshot. Never let a warning failure
skip a later required step. A structural input-integrity error invalidates the
whole evaluation, even if first noticed by a warning step.

| Result | Enforce, applicable required check | Enforce, warning check | Preview/Off standards effect |
| --- | --- | --- | --- |
| Pass | Satisfies this named check only | Recorded | No new hold |
| Fail / Review required | Hold | Visible warning | No new hold |
| Error / Unsupported / Pending / missing result / cancelled | Hold | Visible incomplete warning, unless shared integrity is invalid | No new hold |
| Explicit group N/A | Recorded exclusion, not pass | Same | No new hold |

Enforce also holds for missing/corrupt plan, uncovered paths, stale snapshot or
candidate, registration/persistence failure and any shared integrity failure.
Warnings never override an existing review hold, dirty-tree refusal or smoke
failure. Preview reports what would hold; it never turns that into a merge hold.
Off executes no automatic native checks. Native status, full-practice coverage
and existing review status are separate response fields; no shared green badge
that implies missing full coverage is delivered.
The no-new-hold rule applies when Preview/Off is known from an intact snapshot
or genuine unconfigured state. A corrupt active snapshot is Error and cannot
be reinterpreted as Preview/Off to bypass a possibly active Enforce bar.

## 4. Approval and measured Preview through the existing gate

Keep `/dev-harness/activate`, `is_agent_caller()` refusal **before parsing or
state reads**, and `_require_human_passcode` with its existing throttle and
unconfigured-passcode behavior. Add only `native_plan_digest` and
`acknowledged_native_losses` to the existing activation fields. No approve route,
agent bypass, magic header, unattended exception or repository approval flag.

1. Inspect a draft plan without invoking executables. Return D, exact command
   arrays/config/tool pins, mappings, required/warning selection, Unsupported
   full-practice gaps and proposed/prior protection losses for human review.
   Inspection persists an immutable proposal; it grants nothing.
2. A human calls existing Activate with mode Preview, expected R, draft P and D.
   Store verifies P/native_rules == D and all pins, then atomically records the
   plan approval with mode Preview at R+1. No measured pass is required merely
   to approve Preview. Invalid/unavailable tool/config closure cannot be
   approved as executable; a planless Preview snapshot remains valid guidance.
3. Only now may server jobs execute that exact approved plan in Preview.
   Repository/client/CLI plans have no executable authority. This avoids using
   Enforce as permission to perform the very first measurement it requires.
4. Server evidence owner composes native measured Preview from trusted M3/M4
   receipts, verified M2 installation, all enabled-step measurements and exact
   policy/catalogue/plan/fixture/config/tool identities. It revalidates inputs,
   then atomically writes `evidence/<id>.json`. Only this owner writes that
   evidence; no upload accepts a report body. All enabled required native steps
   must have passed on measured scopes, even steps N/A to a particular diff;
   activation measurement runs all plan steps rather than accepting docs-only
   evidence as proof of tools never exercised. Warning findings stay explicit.
   Measurement S/caps must match `baseline_identity`; no caller-chosen baseline
   digest is accepted as verified. Clean-source coverage may remain unavailable
   under M4's denominator rule; report that gap rather than manufacturing data.
5. Human Activate Enforce supplies current R, same P/D, stored evidence ID and
   exact losses. Evidence binds that pre-activation R and unchanged measured
   HEAD/index/manifest; the new R+1 snapshot records its consumption. A stale
   source, plan or failed required measurement cannot activate. Changing caps
   or check selections means a new plan, Preview approval and measurement.

Evidence keeps M1 `kind=measured_preview`, exact project/revision/policy/digest
bindings and source/baseline/native-rule verification fields. Add
`coverage_scope=named_native_checks`, D, receipt IDs/digests and per-check
results; full-practice outcomes retain their separate Unsupported coverage.
Verified baseline here means the exact frozen measured source/caps inputs, not
DH-A1's future legacy ratchet. M3 `runner_receipt` remains non-activation evidence
and `authorizes_merge=false`; the CLI cannot write either evidence type.
Synthetic compatibility fixtures never count as actual project measurement.
Its top-level `status=pass` means every required native measurement passed and
shared integrity is valid; warning outcomes remain separately visible and do
not make missing full-practice evidence pass.

Existing `acknowledged_losses` still enumerates every omitted/downgraded full
practice. `acknowledged_native_losses` enumerates sorted IDs of previously
enabled required native checks removed, disabled, downgraded, or with changed
scope/config/cap/tool/argv/mapping, plus all prior required native IDs on exit
from Enforce. No loss acknowledgements can waive Unsupported full-practice
validation or missing required native evidence. Store compare-and-swap, first
snapshot atomicity and audit history guarantees remain unchanged.

## 5. Shared landing serialization and final revalidation

New leaf `mc/landing_serialization.py` owns `landing_lock(project)` keyed by
canonical Git common-directory identity (not session, checkout or project ID).
Use a process-local reentrant mutex plus a process-shared OS advisory lock file
in that common Git directory, `clayrune-landing.lock`: `msvcrt` byte lock on
Windows and `fcntl.flock` on POSIX, stdlib only. Reentrant calls in the owning
thread reuse one OS handle/refcount. Wait at most 30 seconds; return a typed
busy/Error result, never proceed as if locked. OS release on process exit
prevents stale lock-file presence from becoming ownership. Preview lock failure
is a reported observation gap, not `awaiting_standards`. A managed landing
that cannot obtain serialization returns its existing busy/skipped deferral
and preserves the branch in every mode; it never merges concurrently without
the lock. This infrastructure deferral is distinct from native evidence holds.
Non-Git planless Preview/Off activation retains the existing store lock only;
it cannot approve executable native plans or Enforce integration.

| Caller | Critical section |
| --- | --- |
| `agent_worktree.merge_back` | Before freezing B/H, through native evaluation, existing base review, final revalidation and exact-H merge/abort. All call paths including GC obey it. |
| `hivemind_integration._merge_one` | Per worker: freeze integration B/worker H, native gate, exact-H merge, tree comparison, named smokes and any owned integration rollback. Release only after that step, before the next worker. |
| Final integration-to-base | Same coordinator and lock via merge_back when used; manual path below when Dave/human lands it. Worker-to-integration review exemption never exempts native standards; base landing retains existing independent review. |
| `tools/merge-review-check.py` | Shared preflight under same lock, including standalone and hook modes. Standalone releases when it returns and explicitly cannot authorize a later unlocked Git command. |
| Human Activate | Acquire landing lock before the store's existing activation/CAS lock; commit snapshot then release. Plan inspection need not hold it across hashing; activation revalidates. |

Lock order: **landing -> short store CAS/read sections -> runner lifecycle
create/remove**. Never hold `agent_worktree._lock(project_id)` while acquiring
landing or running checks. That existing non-reentrant lock stays confined to
create/remove; runner-created disposable worktrees can take it while landing
is held. Audit callers that invoke merge_back; move any lifecycle-lock-held
call outside that lock. Never convert the lifecycle lock into the landing lock.

Managed `git merge` can spawn installed pre-merge/commit-msg hooks in another
process while the parent owns the OS lock. Avoid that second deadlock with an
**internal exact-candidate lease**, not by disabling hooks: before spawning Git,
the owner atomically writes a bounded lease under trusted runtime storage with
random ID, owner PID, repo identity, B/H/T, active snapshot/D and evaluation
receipt digests. Pass its ID only in that Git child's environment. The hook
validates owner/lease identity, existing held OS lock and its own pending
HEAD/merge heads/index tree against that lease; it reuses the locked native
evaluation instead of acquiring again. Existing review still runs normally.
An invalid/expired/mismatched lease is Error in Enforce; never a skip-review
flag. Delete the owned lease in finally, bound to the live merge invocation;
HTTP/run/CLI report callers cannot mint or submit leases. Same local-writer
trust boundary as snapshots; the lease is not user activation or a reusable
merge approval. Test this with real subprocess hooks, not only thread mocks.
Lease storage is `data/dev_harness/<project_id>/landing_leases/<32-hex-id>.json`;
include schema version, common-dir lock key, owner process start identity,
creation/expiry timestamps and content digest. Expire after the existing merge
timeout plus 30 seconds, at most 180 seconds. No renewal outside that invocation.
No lease is admitted as a harness route parameter.

Immediately before landing, recheck snapshot/D, actual config/tool/environment
and checker identities, registered source/worker cleanliness, B/H/M/T and review
of the exact H. Merge the immutable H, then verify landed tree equals T. Ref
advancement or mutation holds and preserves source branches. Existing hivemind
smokes run after each merge, before the next; failures roll back only its owned
disposable integration, retaining workers. Do not reset a human's main checkout
on a post-landing mismatch; report Error, preserve work and stop subsequent
landings for review. Runtime holds return `awaiting_standards` in merge_back;
GC/teardown treat it like `awaiting_review` (preserve, no success fingerprint).
Hivemind reports a standards failure with safe code/run ID and stops.

Manual/hook inputs are the **actual** pending merge: HEAD B, exact merge heads H
(including the existing reflog-action fallback), and `git write-tree` index T.
Require the worktree to match that index. If auto-merged T differs from pending
T (manual resolution), stage is conflicted, parents are ambiguous/octopus, or
candidate cannot be reconstructed, report Unsupported/Error and hold in Enforce;
never evaluate clean HEAD as a substitute. In Preview, report the gap and add
no standards hold. `--respect-setting` controls only the existing review gate;
review-off must not early-return past standards, nor may non-agent branches.
No hook installation or claim to prevent raw Git bypasses: fast-forwards and
`--no-verify` remain disclosed limitations of manual Git.
An ordinary commit with no pending merge remains outside merge preflight; do
not invent a merge or require standards evidence just to complete that commit.

M3 currently refuses dirty integration inputs, so pending-index support is an
explicit A2 prerequisite, not a call to its existing clean-source entry point.
`mc/dev_harness_pending_merge.py` owns a frozen pending-input descriptor: full
B/H/M, actual index tree T and matching working manifest, plus independently
computed auto-merge tree. Require those trees to agree. A narrow **internal**
runner entry accepts that validated descriptor, checks original pending state
again after execution and runs only its disposable T checkout. Ordinary
freeze_candidate retains its dirty refusal; HTTP/headless callers cannot inject
this descriptor. Standalone branch preflight uses normal B/H freezing instead.
Both manual modes call the internal coordinator directly, never import a CLI
report as trusted provenance. Hook exit 1 means required native/review hold;
exit 2 means unevaluable Enforce/review prerequisite; Preview native errors
alone keep exit 0 and are reported as observations.

## 6. Harness route and job dispatch contracts

All paths below are under `/api/project/<project_id>/dev-harness`. New handlers
live in a separate run-routes leaf registered alongside existing harness routes;
do not grow agent_routes with harness logic. Existing authentication applies.

| Method/path | Accepted JSON / response |
| --- | --- |
| POST `/native-plan` | Exactly `{expected_revision, draft, steps, groups}`. Validate bounded declarative fields above, resolve registered project and installed metadata; no executable invocation. Server adds identity/digest/fixture fields, computes baseline/native_rules pins and returns resolved P, D, payload, omissions and losses. Stale R -> 409. No approval. |
| POST `/preview` | Exactly `{session_id, expected_revision, native_plan_digest, purpose}`; purpose `observation` or `activation_measurement`. Uses active human-approved plan only, current dirty project source S; never arbitrary steps, paths, env or report bodies. Planless/unconfigured observation returns manifest-only Unsupported without starting native commands; measurement requires an approved Preview snapshot and all-step execution. |
| POST `/run` | Exactly `{session_id, expected_revision, native_plan_digest, target, incoming}`. Resolve/validate refs to full B/H in the registered repo; both required. Runs observation against exact integration candidate under the shared lock. Cannot request merge, hold a branch, approve, install or activate. |
| GET `/runs/<run_id>` | Project-scoped safe record, status plus `stale`/fixed reason codes and original revision/D/input identities; unknown/wrong-project ID -> 404. Read never starts work, changes mode or adds holds. Stale historical evidence is displayed as stale, never silently rewritten as current pass. |

Plan proposals may be inspected by agents, and already human-approved plans may
be observed by agents, subject to existing caller/session policy. Agents cannot
activate/approve and cannot execute draft plans. `/activate` owns all approval.
Mode Off observations execute no native commands. Native-plan execution requires
the exact active D, not any older plan that once received approval.
For planless observation, native_plan_digest is null and response is HTTP 200
with manifest-only Unsupported and no job ID. All job-producing paths use 202.

Job-producing routes first validate exact JSON, project/session ownership,
expected R/D and registered repository. Reuse
`agent_routes.agent_job_start(..., job_data=...)` and its incognito/sandbox/cwd/
timeout/registration/completion-delivery checks. A2's leaf dispatch builds a
fixed internal argv/quoted module invocation; no caller command/shell/cwd or
internal direct_argv field is admitted by harness HTTP. Persist a server-built
request record with run ID and frozen input pins, then pass only its ID to the
worker. Worker revalidates approval and frozen inputs before spawn; queued drift
returns stale Error without executing changed input. Do not use the report-only
CLI to produce trusted records. Outer job and every native child remain owned,
registered immediately and cancellable; existing limits bound total execution.
The route releases any short freeze lock before starting/waiting on a job;
the worker owns evaluation serialization. All coordinator/request/lease IDs
are server-generated 32-character lowercase hex, never filesystem paths.

Successful dispatch returns HTTP 202 with `{project_id, run_id, job_id, status:
pending, revision, native_plan_digest, authorizes_merge:false}`. No pending job
looks like Pass. Invalid bodies -> 400, refusal -> 403, unknown project/session
-> 404, revision/plan drift or landing busy -> 409, corrupt trusted state -> 500.
Failed dispatch cannot leave a runnable orphan request. Terminal status uses
M3/M4 pass/fail/review_required/error/unsupported and safe codes only; preserve
warning/required breakdown independently of aggregate status.

Coordinator records live under `integration_runs/<run_id>.json`, separate from
M3 `runs/` receipts, with content digest, R/snapshot/P/catalogue/D, B/H/M/T or S,
group selection, per-step receipt IDs/digests, checker/config/tool/environment
identities, status and optional measured-evidence ID. Public projections exclude
raw stdout/stderr, source, arbitrary exceptions, secrets and environment values.
Records always have `authorizes_merge=false`. Only the in-process landing owner
can consume a fresh coordinator result under its held lock; an HTTP result,
stored run or forged JSON cannot be presented later as merge permission.

## 7. A2 verification obligations

These are required tests for A2, **not results claimed by this documentation**.
Use temporary repositories/trusted state, synthetic executables and real
subprocess concurrency where specified; no live activation or tool installation.

| Area | Required acceptance cases |
| --- | --- |
| Plan integrity | Strict schema/size/ID/path limits; duplicate keys/IDs rejected; D recomputation; native_rules/policy-input binding without circular hashes; wrong repo/project, missing/corrupt store/embedded copy, changed recipe/checker/config/tool/closure/fixture all refuse; proposal/repo draft/CLI cannot approve or execute. |
| Approval/evidence | All existing caller/passcode/throttle/CAS/atomic-history tests stay green; agent refusal before reads; human Preview approves only exact D; native-only Enforce succeeds after real synthetic project measurements; full required Unsupported product fails; stale R/source/D/evidence, client report, lost native/full protections without exact acknowledgement all refuse without partial writes. |
| Selection | Python and JS/TS recipe mapping; multiple recipe invocations unique; warnings do not skip required steps; full-scope activation measurement; docs/config explicit groups; zero denominator, unmapped/deleted/renamed/untracked/binary changes; overlapping groups select union; N/A cannot hide a selected/required failing check. |
| Merge behavior | Required fail/review/error/pending/cancel/mutation hold; warnings report; Preview/Off add no hold even when evidence corrupt/unavailable; existing review still holds; dirty/ref-advanced candidates refuse; exact H/T landed; all held branches survive GC/remove; standards and review independent. |
| Serialization | Two threads and two OS processes serialize on the same repo through different checkout/project aliases; different repos do not block; timeout and owner crash release correctly; real runner create/remove under landing lock terminates without lifecycle-lock deadlock; activation races serialize before CAS. |
| Hivemind | Worker-to-integration and final base standards gates; review exemption applies only at its existing boundary; check T per worker; first failed standard/smoke stops sequence, preserves workers and existing rollback behavior; smokes run after each successful merge before next. |
| Manual/hooks | Standalone lock release never becomes reusable approval; review-off and ordinary branch still evaluate standards; installed hook under managed lock validates child lease without deadlock; expired/forged/wrong candidate lease refuses; real pending index tested, conflict resolution/octopus refused rather than clean-HEAD substitution; existing ownership/review-rejection cases preserved. |
| Dispatch/read | Routes exist with exact schemas; session/project/incognito/sandbox guards retained; 202 pending plus completion mapping; queued drift, dispatch failure, registration failure, timeout/cancel and persistence failure; read wrong project/missing/corrupt/stale record; Preview never changes mode/holds; no HTTP argv/report/lease injection or raw-output leakage. |

Existing test changes are explicit: remove `/preview` alone from
`tests/test_dev_harness_routes.py::test_later_ticket_routes_are_absent`;
`/exceptions` remains 404. Extend policy invalid-field/roundtrip cases in
`test_dev_harness_catalogue.py`; retain catalogue coverage assertions unchanged.
Extend store fixtures for version 2/native evidence while preserving synthetic
future-full-practice fixtures and negative Unsupported cases. Adjust the
`merge_enforcement_available` read assertions only for genuinely approved Enforce
native plans. Extend `test_merge_review_gate.py` review-off/base-only cases to
assert native standards still run; do not change the review exemption itself.
Extend worktree/teardown and `tests/test_hivemind_worktree_isolation.py` with new
hold statuses and same-lock sequencing. M3/M4 output/receipt/coverage invariants
stay intact; changes to expected optional metadata do not relax those tests.

Required commands after A2: focused new/changed tests, then
`python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review"`,
and scoped Pyright basic for every new/changed `mc/` owner. No static changes
are specified; UI/boot smokes attach only if scope is separately expanded.
Windows and POSIX lock tests are required; report any platform not exercised.
Real native toolchain/dogfood, packaging and four-vendor probes remain M6
acceptance; synthetic tests cannot claim those verified.

## 8. A2 file ownership and implementation order

Each new concern gets a leaf. No new dependency, queue, general scanner engine,
character, human gate, CI workflow or runtime config change is required.

| Files | Responsibility |
| --- | --- |
| `mc/dev_harness_native_plan.py`, `tests/test_dev_harness_native_plan.py` | Strict payload construction/storage/read, digest/recipe/config/tool/fixture binding and argv compilation. |
| `mc/dev_harness_applicability.py`, `tests/test_dev_harness_applicability.py` | Pure selection/group/N/A/traceability and severity aggregation. |
| `mc/dev_harness_pending_merge.py`, `tests/test_dev_harness_pending_merge.py` | Exact pending-index freeze/revalidation for manual hooks; separate from clean candidate and dirty Preview semantics. |
| `mc/dev_harness_activation_evidence.py`, `tests/test_dev_harness_activation_evidence.py` | Trusted measured-native Preview composition/validation; no caller evidence. |
| `mc/landing_serialization.py`, `tests/test_landing_serialization.py` | Repo-wide thread/OS lock and internal child lease lifecycle; no standards policy. |
| `mc/dev_harness_integration.py`, `tests/test_dev_harness_integration.py` | Candidate/plan verification, runner composition, safe coordinator records and landing-time consume. |
| `mc/dev_harness_dispatch.py`, `tests/test_dev_harness_dispatch.py` | Fixed job requests, worker entry, pending/completion lifecycle through existing jobs. |
| `mc/blueprints/dev_harness_run_routes.py`, `tests/test_dev_harness_run_routes.py` | Native-plan/run/preview/read HTTP validation and safe projections. |
| `data/dev_harness_catalogue/checks/<recipe>.fixtures.json` (one per existing recipe) | Server-shipped positive/finding/crash fixture-contract identity tied to its existing separate fixture; no assertion of real-tool acceptance. |
| `mc/dev_harness_policy.py`, `mc/dev_harness_store.py`, existing tests | Versioned policy/snapshot extensions, losses, atomic plan approval; delegate native evidence/plan logic to leaves. |
| `mc/blueprints/dev_harness_routes.py`, existing route tests | Thin activation/read projections, registration of the run-routes leaf using existing wiring; preserve caller proof gates. |
| `mc/agent_worktree.py`, `mc/hivemind_integration.py`, `tools/merge-review-check.py`, existing tests | Thin lock/coordinator calls, held-state preservation and exact-candidate hooks; no plan bodies in these owners. |
| `mc/dev_harness_runner.py`, `mc/dev_harness_diff.py`, existing tests; M4 recipes only if metadata seams require it | Internal validated pending-descriptor runner seam and optional plan/step identity metadata; unchanged ordinary entry-point behavior and dirty refusal, no duplicate execution engine. |
| `docs/DEV_HARNESS_M5_CONTRACT.md`, `docs/DEV_HARNESS_RUNNER.md`, `docs/DEV_HARNESS_CHECKS.md`, `CHANGELOG.md` | Record implemented contracts/limits and verification; no shipped claim before A2 verification/integration. |

Implement plan/policy/evidence first; prove approval boundaries before wiring
execution. Then land serialization/coordinator/dispatch in the same reviewed A2
unit, with thin existing-owner changes last. `agent_routes.py` needs no new
harness body; reuse its existing job_data seam. Shipped practice descriptor
coverage is not edited. A2 stays branch-only unless Dave explicitly changes its
brief. A1 itself changes only this document and the specification link/row;
progress/handoff is appended to the main-checkout gitignored journal.

## 9. Dave review of A1 (2026-10-29): approved with two amendments

The contract is approved as the A2 basis. Two amendments bind A2 and override
any conflicting line above.

1. **A2 ships as two reviewed units, not one.** A2a: native plan, policy v2,
   store/snapshot binding, activation evidence and the `/native-plan` route
   (sections 2, 4, and the plan/approval rows of 6-7). No executable runs, no
   landing changes. A2b, dispatched only after A2a is reviewed and merged:
   applicability, landing serialization and lease, pending-merge descriptor,
   coordinator, dispatch and the `/preview`, `/run`, `/runs` routes, plus the
   thin `agent_worktree`/`hivemind_integration`/`merge-review-check` wiring.
   Why: the approval boundary must be proven before anything can execute
   against it, and a single unit touching both is too large to review well.
2. **Coverage recipes are warning-only in A2.** Plan inspection rejects
   `severity: required` for `coverage-python` and `coverage-javascript` with
   reason code `coverage_required_unmeasurable`. Why: section 4.4 requires every
   required step to pass during activation measurement on a clean tree, and
   M4's changed-line denominator is zero there, so a required coverage check
   could never activate Enforce. `pytest`/`vitest` remain eligible as the
   required P02 checks. Reopen in M6 once a full-scope coverage measurement
   is defined.

## 10. A2b implementation status (branch candidate)

The [handoff section 9](DEV_HARNESS_M5_PLAN_CONTRACT.md#9-a2b-implementation-candidate)
records the A2b implementation, ownership, verification and remaining M6 limits.
Applicability, pending-index execution, common-repository serialization/leases,
trusted receipts/coordinator, existing-job dispatch and thin managed/manual
landing composition are implemented on the builder branch. A2a remains the
approval/evidence owner. This does not authorize a live activation, merge,
push, restart, settings change or deployment. Full-practice Unsupported coverage
and section 9's warning-only coverage rule remain unchanged.
