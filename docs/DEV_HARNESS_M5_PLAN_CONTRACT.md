# DH-M5 native execution-plan contract

MC-960 / `d813ea78`; requested option-A step 1; Sol_Tobin; 2026-10-30.
Original handoff based on master `8062511c`; A2b builds from `a7a9eb3a`.
Sections 1-8 retain the reviewed contract/file map. Section 9 records the branch
implementation candidate and validation; no live activation or deployment.

## 1. Existing foundation and scope

The [preflight](DEV_HARNESS_M5_PREFLIGHT.md), commit `cb8f25b8`, identified
missing plan, approval, dispatch and landing-serialization contracts. Dave's
2026-10-30 journal decision selects option A: specify them before building.
The dispatch-base master already includes the [approved M5 contract](DEV_HARNESS_M5_CONTRACT.md)
and [A2a approval implementation](DEV_HARNESS_NATIVE_APPROVAL.md). This document
consolidates the requested plan contract against that current source; it does
not replace those decisions or commission a second approval implementation.

| State at this base | Owners / remaining boundary |
| --- | --- |
| Implemented, nonexecuting A2a | Policy v2, immutable native-plan proposals, snapshot binding, native losses, receipt-based activation-evidence composition, POST `/native-plan`, existing Activate fields and 16 fixture manifests. |
| A2b branch candidate (section 9) | Applicability, shared landing lock and hook lease, pending-index runner input, trusted Preview/candidate receipts, coordinator, run dispatch/read and managed/manual landing wiring. |
| Not delivered by either | Full deterministic/reviewer coverage for the 22 catalogue practices, CI/release proof, repair agents, exceptions, live dogfood or M6 panel acceptance. |

At the dispatch base, `merge_enforcement_available` is false. A2a synthetic receipts prove
approval validation, not a working execution path. A2b may report availability
only when an intact, currently valid native Enforce plan and its actual landing
integration exist. Required full practices whose coverage is Unsupported remain
unactivatable. Native enforcement is explicitly `named_native_checks`, never a
claim that a scanner delivers the complete Pxx practice. Dave's prior split
(A2a before A2b) and warning-only coverage amendment remain binding.

## 2. Immutable shape and trust

Let `R` be the active snapshot revision, `P` its resolved policy, and `D` the
native-plan digest. `B/H/M/T` are M3's exact target/incoming/unique merge-base
commits and merged tree; `S` is the frozen Preview HEAD/index/content manifest.
All hashes use `mc.dev_harness_catalogue.digest`: SHA-256 of sorted compact
JSON with nonfinite numbers refused. IDs/digests are checked before path use.

The immutable envelope is exactly `{plan_digest: D, payload: payload}` with:

```text
D = digest(payload) = P.digests.native_rules
payload.policy_inputs_digest = digest(P with only digests.native_rules removed)
digest(payload.baseline_identity) = P.digests.baseline
```

This removes the circular hash without dropping source/baseline pins, caps,
catalogue or native selections from the binding. Store proposals atomically at
`data/dev_harness/<project_id>/native_plans/<D>.json`, outside project records
and repository drafts. An existing digest must read back identically; never
overwrite its payload. The active atomic snapshot embeds the same envelope and
D with policy, catalogue, mode, evidence and audit. Neither proposal storage nor
a copied envelope is approval. Trust is server-owned provenance within the
existing local-writer boundary, not protection against unrestricted disk writes.

The payload has exactly these fields; all are required:

| Field | Value / binding |
| --- | --- |
| `schema_version`, `kind` | `1`, `native_execution_plan`. |
| `project_id`, `project_path`, `git_common_dir` | Registered project and canonical local repository identity; null common dir cannot enforce a Git merge. |
| `policy_inputs_digest`, `catalogue_digest` | Exact resolved policy projection above and catalogue identity. |
| `baseline_identity` | `{kind: native_measurement_baseline_v1, source: S, caps: <enabled-practice cap map>}`; activation measurement identity, not a later merge baseline or future legacy ratchet. |
| `checker_digest` | Server-owned runner/diff/native adapter and plan/policy/store/evidence identities; A2b must bind its applicability, pending-input, coordinator, serialization and execution-dispatch semantics too. No candidate-provided checker replacement. |
| `environment_policy` | Literal `inherit_executor_freeze_each_run`; approval permits actual executor inheritance, frozen/rechecked per execution, without storing plaintext environment values or accepting client overrides. |
| `steps`, `groups` | Ordered nonempty arrays, each at most 64; exact definitions below. |

Policy v2 `native_checks` maps unique check IDs to exactly `{enabled: bool,
severity: required|warning, practice_ids: [Pxx, ...]}`. Practice IDs are sorted,
unique, known and nonempty; enabled checks require enabled guidance practices.
Profiles do not seed native checks. Full-practice severity/coverage remains
independent. Every enabled native check has exactly one step, and vice versa;
disabled selections have no step. Preserve v1 snapshots and their digests on
read; missing native plans never become an implicit empty passing Enforce plan.

Each approved step contains exactly:

| Field | Constraint / producer |
| --- | --- |
| `id`, `recipe_id` | Unique `[a-zA-Z0-9_-]{1,64}` check ID and a shipped M4 recipe. Multiple invocations of a recipe use distinct check IDs. |
| `launcher` | Nonempty argv prefix; inspection resolves element zero to an absolute installed executable; execution does not reselect through PATH. |
| `targets`, `configs`, `tool_roots` | Explicit contained repository paths and absolute installed tool closures, with M3/M4 discovery, transitive-config and symlink/resource rules. |
| `command_args` | Nonempty argv tail only for configured-command recipes; otherwise null. No shell program/string or replacement fixed scanner flags. |
| `coverage` | M4 coverage object or null; report, format, source roots, explicit floor and diff launcher; floor equals the selected P02 cap. No invented default. |
| `timeout_seconds` | Finite positive number, at most 7200 and subject to the enclosing managed-job limit. |
| `recipe_digest`, `config_digests` | Server-computed recipe and complete declared/discovered transitive configuration-byte pins. |
| `tool_closure_digest`, `launcher_digest` | Installed closure inventory and executable-byte pins, including diff-cover when used. |
| `fixture_contract_digest` | Server-verified shipped positive/finding/crash adapter manifest identity; not a caller-provided compatibility assertion. |

Inspection accepts only `id`, `recipe_id`, `launcher`, `targets`, `configs`,
`tool_roots`, `command_args`, `coverage`, `timeout_seconds`, never the five
derived digest fields. Compile through M4 `make_step` and M3 preparation;
do not persist client-precompiled argv,
prepared-native objects, fail codes, report bodies or inline secrets. Strict
JSON rejects duplicate keys, unknown fields, duplicate IDs, nonfinite values,
oversized input (4 MiB), unsafe paths and unsupported closures. Inspection reads
Git/file metadata but invokes no native tool, version probe, install or job.

Re-read store/embedded copies, D/P/native_rules, registered repository and live
config/tool/recipe/fixture/checker identities before execution and landing.
Drift yields `plan_inputs_changed` or a safe error; no old-plan/draft fallback.
Changing selections, severity, caps, argv, scope, mapping or pinned native inputs
requires reinspection and human approval. Ordinary application source changes
need fresh runs, not renewed approval. Source/ref/index or environment changes
invalidate run evidence. Human replacement may use A2a's private integrity-only
prior snapshot reader to calculate losses after live inputs drift; that reader
never supplies executable authority and still refuses corrupt stored bindings.

## 3. Selection, applicability and severity

Each group is exactly `{id, include, check_ids, not_applicable_check_ids,
reason_code}`. IDs are unique. `include` holds explicit anchored repository
prefixes: `src/` matches that directory, a filename matches itself, `*` matches
all paths. No regex, arbitrary glob, extension guessing or agent classification.
`reason_code` is `source_change|docs_change|config_change|other_change`.
The disjoint check/N/A arrays partition all enabled checks; each group selects
at least one check. N/A is approved mapping data, never runtime free text.

Use integration changes `B..T`, including deleted paths and both rename names;
`M..H` is attribution only. Dirty Preview uses its exact staged/unstaged and
nonignored untracked manifest. Match every path against all groups and union
selected check IDs. Selection by one group wins over N/A in another. Unmapped
paths yield `unmapped_change`; empty scanner output is not applicability proof.
Clean observation may apply all groups. Activation measurement always executes
all enabled steps, independent of groups. Identical candidates report
`nothing_to_merge` without fabricating passes. Preserve binary/path exclusions
and M4's zero-denominator error instead of reporting 100% coverage.

Traceability stays bounded: P02 -> pytest/vitest/coverage-python/coverage-javascript;
P01/P17 -> ruff/eslint; P17 -> pyright/typescript/npm-build; P06 -> gitleaks;
P08 -> bandit/semgrep; P07 -> pip-audit/npm-audit/osv-scanner; P16 -> licence.
These pairs label named checks, not delivered full-practice coverage.
Coverage recipes remain **warning-only**: reject required severity with
`coverage_required_unmeasurable`. M6 must define full-scope coverage measurement
before reopening this; pytest/vitest can be required now.

| Outcome | Applicable required check in Enforce | Warning check in Enforce |
| --- | --- | --- |
| Pass | Satisfies this named check only. | Record result. |
| Fail / Review required | Hold landing. | Visible warning; continue required checks. |
| Error / Unsupported / Pending / missing / cancelled | Hold landing. | Visible incomplete warning; shared integrity errors still hold. |
| Approved group N/A | Record exclusion, never a pass. | Same. |

Evaluate in approved order on disposable copies of the same B/H/M/T or S.
M3's current first-nonpass behavior cannot let a warning suppress later required
checks: compose separately bound per-step receipts or add a narrow internal
collection seam. Shared source/config/tool/environment/checker mutation,
unmapped inputs, stale approval or failed trusted persistence invalidates the
evaluation regardless of which step noticed it. No result is a command pass
unless that command actually ran and its pinned native protocol validated.

Preview observes would-hold results without adding a standards hold. Off runs
no automatic native commands. Existing review, cleanliness and smoke gates
remain independent in every mode. A corrupt active snapshot is Error, never
inferred Off/Preview. Keep native results, full-practice Unsupported gaps and
existing independent-review status separate in output.

## 4. Exact approval through existing human Activate

Keep POST `/api/project/<project_id>/dev-harness/activate` and its existing
`is_agent_caller()` refusal before parsing/state reads, per-call passcode proof,
throttling, unconfigured-passcode behavior, revision CAS and atomic audit.
No second gate, bypass header, repository approval bit or unattended exception.
A2a already added `native_plan_digest` and `acknowledged_native_losses`.

1. POST `/native-plan` inspects `{expected_revision, draft, steps, groups}`;
   returns exact envelope/D, resolved P, compiled commands, scope, gaps and
   losses. Absent/null baseline/native-rule draft pins are computed, supplied
   pins must match. Store proposal only; stale R returns 409.
2. Human Activate Preview submits R, D and the returned P's editable
   `profile/practices/digests/native_checks` projection. Recompute bindings and
   validate live inputs; atomically approve that exact plan at R+1. No measured
   pass is needed to permit its first Preview execution. Planless Preview can
   remain guidance, but never executes a draft plan.
3. A2b jobs execute only current approved D. Activation measurement requires
   approved Preview and exact baseline S/caps. All enabled steps need complete
   measurements; every required step must pass. Warning findings remain visible.
   Existing M2 installed-guidance verification is required.
4. `dev_harness_activation_evidence.compose_evidence` alone composes stored
   trusted M3/M4 receipts into `kind=measured_preview` evidence. It binds R,
   snapshot/P/catalogue/D, baseline source, checker, receipt IDs/digests and
   every step's argv/config/tool/closure/recipe/fixture/environment identity.
   Integration B/H/M/T cannot substitute for baseline S. No uploaded report,
   fixture result or headless CLI claim can supply project measurements.
5. Human Activate Enforce submits current R, same P/D, stored evidence ID and
   exact acknowledged losses. Revalidate evidence/source/live pins and consume
   it in the next atomic snapshot. At least one required native check is
   necessary. Full required Unsupported practices still refuse activation.

Activation evidence currently permits only the input-complete warning error
codes `invalid_native_report`, `empty_diff_coverage`, `unsupported_tool_version`;
all required measurements must pass. Preserve per-step provenance and receipt
aggregate consistency. M4 early failures sometimes lack that provenance: A2b
must either supply accurate pinned metadata through its narrow receipt seam or
leave the measurement incomplete. Never relabel generic `evaluation_error`,
missing identities, timeout/cancellation or integrity failures as acceptable
warning evidence. Evidence composition itself invokes no executable.

Keep `acknowledged_losses` for omitted/weakened full practices. Native losses
separately enumerate prior required checks removed, disabled, downgraded or
changed in scope/config/caps/tool/argv/mapping, and prior required checks when
leaving Enforce. Neither acknowledgement waives required evidence or Unsupported
validation. Changing the approved plan returns through Preview approval and
measurement; importing, upgrading, running or reading never activates it.

## 5. Shared landing-lock seam and exact candidate

`mc/landing_serialization.py` owns a separate lock keyed by canonical Git
common-directory identity, shared by checkout/project aliases. Use a reentrant
thread mutex plus stdlib OS advisory lock on `clayrune-landing.lock` in that
directory (`msvcrt` byte lock on Windows, `fcntl.flock` on POSIX). Owning-thread
recursion shares its handle/refcount. Bounded wait: 30 seconds; busy/error never
continues unlocked. OS ownership ends on process exit; file presence is no lease.

Lock order is **landing -> short snapshot/CAS sections -> worktree lifecycle**.
Do not hold the existing nonreentrant `agent_worktree._lock(project_id)` across
landing/evaluation: runner create/remove needs it. Audit teardown/GC call paths.
Human activation takes landing before store CAS, rechecks plan inputs, commits
and releases. Non-Git planless Preview/Off uses the existing store lock only.

| Landing owner | Hold lock across |
| --- | --- |
| `agent_worktree.merge_back` | Freeze B/H, standards, exact-H independent review where already required, final input revalidation, exact-H merge/abort and T comparison. All callers, including GC, obey this seam. |
| `hivemind_integration._merge_one` | One worker's freeze, standards, exact-H merge, T check, its named smokes and any owned integration rollback; release before next worker. |
| Final integration-to-base | Same coordinator/lock through merge_back, or the manual preflight path; existing worker-review exemption never exempts native standards. |
| `tools/merge-review-check.py` | Standalone or pending-merge evaluation and review. Standalone release cannot authorize a later unlocked merge. |
| Human Activate | Approval/CAS transaction, preventing active-plan change mid-landing. |

Managed Git invokes hooks in a child process while the parent holds the lock.
Avoid deadlock with an internal exact-candidate lease, not disabled hooks:
atomically store `landing_leases/<32-hex-id>.json` under trusted project harness
state, containing schema/content digest, random ID, common-dir lock key,
owner PID/start identity, creation/expiry, B/H/T, R/snapshot/D and evaluation
receipt identities. Pass only the ID in that Git child's environment. Validate
the live owner's held lock, invocation, pending HEAD/merge heads/index T and
lease before reusing the parent's native evaluation; existing review still runs.
Expiry is the merge timeout plus 30 seconds, at most 180 seconds, no renewal;
delete the owned lease in finally. Missing/forged/expired/mismatched leases
cannot skip standards or review. HTTP, CLI reports and repository files cannot
mint/submit them. This uses the same local-writer trust boundary as snapshots.

Immediately before merge, recheck R/D, config/tool/checker/environment, clean
registered inputs, exact B/H/M/T and exact-H review. Merge H, not a mutable
branch ref; verify resulting tree T. External Git does not honor the advisory
lock, so stale/ref-advanced/mutated candidates hold. Preserve branches/worktrees
on `awaiting_standards` exactly as on `awaiting_review`, including GC/teardown
and success bookkeeping. Busy serialization defers/preserves in every mode;
it is infrastructure contention, not a Preview standards hold. Stop hivemind
at the first failure; retain worker branches and existing per-merge smoke
rollback. Never reset a human checkout after a post-landing mismatch; report
Error, preserve work and stop further landings for review.

Manual hooks use the actual pending HEAD B, exact merge heads H (including the
existing reflog-action fallback), `git write-tree` index T and a worktree that
matches the index. `mc/dev_harness_pending_merge.py` freezes these plus M and
an independently computed auto-merge tree; require auto tree == pending T.
Conflicts, manual resolutions with differing T, octopus/ambiguous parents or
unreconstructable inputs hold Enforce as Unsupported/Error. Add a narrow
internal M3 entry for this validated descriptor and disposable T execution;
recheck original pending state after the run. Do not route it through the
ordinary clean-input freeze or substitute clean HEAD. Ordinary dirty integration
refusal stays unchanged. Non-agent merges and review-off still evaluate native
standards; `--respect-setting` controls existing review only. Ordinary commits
without a pending merge remain outside merge preflight. Hook exits: 1 for a
required hold, 2 for unevaluable Enforce/review, 0 for Preview-native observations
alone. No new hook installation or claim to prevent raw Git bypasses; fast
forwards/`--no-verify` remain manual-path limitations.

## 6. Preview/run dispatch and record owners

All routes below are under `/api/project/<project_id>/dev-harness`, in the
existing run-routes leaf; only `/native-plan` exists at this base.

| Route | Exact input / semantics |
| --- | --- |
| POST `/preview` | `{session_id, expected_revision, native_plan_digest, purpose}`; purpose `observation\|activation_measurement`. Frozen dirty S; measurement uses all steps and exact approved baseline. |
| POST `/run` | `{session_id, expected_revision, native_plan_digest, target, incoming}`; both refs resolved to full B/H in registered repo. Observe candidate under landing lock; cannot merge or activate. |
| GET `/runs/<run_id>` | Project-scoped safe original record with current staleness/reason codes. Unknown/wrong-project -> 404; reading never starts work or rewrites old evidence to current Pass. |

Agents may inspect proposals and observe already human-approved plans subject
to existing session rules; they cannot approve or execute drafts. Only active
D is executable, never an older once-approved plan. Planless/unconfigured
observation (`native_plan_digest: null`) returns HTTP 200, manifest-only
Unsupported and no job. Off observations start no native commands; activation
measurement in Off is refused. Job-producing responses are HTTP 202 with
`{project_id, run_id, job_id, status: pending, revision, native_plan_digest,
authorizes_merge: false}`. Pending never appears as Pass.

`mc/dev_harness_dispatch.py` builds bounded server-owned request records and a
fixed internal worker invocation. Reuse
`agent_routes.agent_job_start(..., job_data=...)`, preserving project/session
ownership, incognito refusal, unattended-sandbox refusal, cwd, timeout, immediate
process registration and completion delivery. Shell quoting is for a fixed
internal module invocation and server-generated request ID only; no caller
command/shell/cwd/argv/environment/report/lease field is admitted by harness HTTP.
Do not add harness bodies or bypass flags to the agent-routes monolith.

Server-generated run/request/lease IDs are 32 lowercase hex characters, never
paths. Freeze R/D and exact source/refs into the request; release request-thread
locks before scheduling/waiting. The worker acquires serialization, rechecks all
pins before native spawn and refuses queued drift. Dispatch failure leaves no
runnable orphan request. Outer job and every native child use existing owned,
registered, bounded cancellation/timeout/reaping. Completion persists a safe
terminal record and uses existing session delivery, not a new queue/daemon.
Bad body -> 400, caller refusal -> 403 (retain existing job-specific refusal
codes), missing project/session -> 404, stale R/D or landing busy -> 409, corrupt
trusted state -> 500. Worker failures use fixed safe codes, never raw exceptions.

`mc/dev_harness_integration.py` owns approval/candidate validation, applicability,
M3/M4 receipt composition, severity aggregation and final locked consumption.
`dev_harness_runner` remains execution/receipt owner; add an internal trusted
Preview/pending-descriptor seam, not a second executor or CLI-report import.
Persist coordinator records at `integration_runs/<run_id>.json`, separate from
M3 `runs/`, with content digest, R/snapshot/P/catalogue/D, B/H/M/T or S, selected
groups/N/A, per-step receipt IDs/digests, checker/config/tool/environment pins,
status/required-warning breakdown and optional composed measurement ID. Storage
is under `data/dev_harness/<project_id>/`, never `data/projects/`.

Every persisted/public report remains `authorizes_merge=false`. Only a fresh
in-process coordinator result consumed by its still-locked landing owner may
satisfy that invocation's native prerequisite. HTTP output, historical stored
JSON and CLI reports cannot grant later merge permission. Public projections
exclude raw stdout/stderr, source, environment values, secrets and arbitrary
exceptions; preserve M3/M4 bounded native-report validation/redaction.

## 7. Build acceptance tests (specified, not executed here)

| Area | Tests to write or extend |
| --- | --- |
| Immutable approval | Reuse A2a integrity/schema/paths/fixture, v1/v2, passcode/throttle, caller-before-read, exact D/P, CAS/history/loss tests. Add A2b checker-owner drift. Proposal/draft/CLI/report cannot execute or approve; full required Unsupported still refuses. |
| Applicability | Overlapping groups union; explicit docs/config N/A; unmapped, old/new rename, deletion, binary and dirty untracked paths; same recipe/different IDs; no-op; warning fail never skips required; all-step measurement and zero denominator. |
| Trusted receipts | Preview S and candidate B/H/M/T remain distinct; wrong source/step/order/aggregate/D/pins refused. Actual synthetic commands produce complete trusted receipts. Closed warning errors remain explicit; early incomplete M4 errors are never fabricated complete. Compose evidence and test exact subsequent human Enforce in temporary state. |
| Result gates | Every nonpass/missing/cancelled required result holds Enforce; warnings remain visible. Shared integrity always invalidates. Preview/Off add no standards holds; corrupt active state cannot become Off; existing review/dirty/smoke gates still apply. |
| Serialization | Same repo through two aliases, threads and real OS processes serializes; other repos proceed; timeout, owner crash and reentrancy release correctly. Real runner create/remove under landing and activation-vs-landing races terminate without lifecycle/CAS inversion. Windows and POSIX required, not just thread mocks. |
| Managed landing | Pin H despite branch advance; recheck B/H/M/T and R/D/input mutation; verify landed T. Holds preserve worktree/branch through remove/GC and do not record merge success. No human-tree reset on mismatch. |
| Hivemind | Worker and final-base gates; unchanged independent-review exemption; exact H/T; smokes after each merge; first standards/smoke failure stops sequence and retains workers/owned rollback. |
| Hooks/manual | Real subprocess hooks under parent OS lock validate exact lease without deadlock; forged/expired/PID-reused/wrong repo/ref/index/receipt leases refuse. Pending index differs from HEAD; matching auto tree executes, manual resolution/conflict/octopus refuses. Review-off/non-agent merges still gate; ordinary commit remains unaffected. Standalone pass is not later merge permission. |
| Dispatch/read | Exact schemas, 202 pending/completion, project/session/incognito/sandbox checks, queued drift before spawn, process-registration failure, timeout/cancel/reaping, failed dispatch/persistence, wrong-project/corrupt/stale reads, no command/env/report/lease injection or raw-output leaks. Manifest-only/Off starts no native process. |

Extend `test_later_ticket_routes_are_absent` only for the now-implemented
`/preview`; keep `/exceptions` absent. Extend read availability assertions only
when executable native Enforce is actually wired. Keep full catalogue coverage
assertions and M3/M4 mutation/output tests intact. Use temporary repositories,
synthetic executables and trusted temporary state, never live activation.

Run focused new/changed tests, then:

```text
python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review"
```

Run Pyright basic on all new/changed `mc/` modules. Report actual Windows/POSIX
coverage and skips; synthetic/native-fixture tests do not prove installed real
tool compatibility, packaging, four-vendor probes or dogfood (M6). No static
change is planned, so frontend smokes are not applicable to this build scope.

## 8. Exact expected next-build files and handoff

These are A2b expectations against the dispatch base. Section 9 records the
implementation candidate against that manifest.
A2a modules/tests already exist and must be reused. New concerns stay in leaves;
existing large owners get composition calls only. No new dependency is needed.

| Source files | Test files | Responsibility |
| --- | --- | --- |
| `mc/dev_harness_applicability.py` (new) | `tests/test_dev_harness_applicability.py` (new) | Group selection/N/A and severity aggregation. |
| `mc/landing_serialization.py` (new) | `tests/test_landing_serialization.py` (new) | Shared thread/OS lock and internal child lease lifecycle. |
| `mc/dev_harness_pending_merge.py` (new) | `tests/test_dev_harness_pending_merge.py` (new) | Exact pending index/worktree descriptor and revalidation. |
| `mc/dev_harness_integration.py` (new) | `tests/test_dev_harness_integration.py` (new) | Locked candidate/plan coordination and safe run records. |
| `mc/dev_harness_dispatch.py` (new) | `tests/test_dev_harness_dispatch.py` (new) | Server-built request/worker job and completion lifecycle. |
| `mc/blueprints/dev_harness_run_routes.py` | `tests/test_dev_harness_run_routes.py` (new), `tests/test_dev_harness_native_plan_routes.py` | Run/preview/read schemas; retain existing nonexecuting inspection. |
| `mc/dev_harness_native_plan.py`, `mc/dev_harness_store.py`, `mc/blueprints/dev_harness_routes.py` | `tests/test_dev_harness_native_plan.py`, `tests/test_dev_harness_store.py`, `tests/test_dev_harness_routes.py` | Complete checker pins, landing-before-CAS wiring and truthful read availability; no replacement approval gate. |
| `mc/dev_harness_runner.py`, `mc/dev_harness_diff.py`, `mc/dev_harness_checks/execution.py` | `tests/test_dev_harness_runner.py`, `tests/test_dev_harness_diff.py`, `tests/test_dev_harness_native.py` | Narrow trusted Preview/pending-input/receipt metadata seams; preserve execution/protocol/mutation owners. |
| `mc/agent_worktree.py` | `tests/test_agent_worktree.py`, `tests/test_agent_worktree_teardown_guards.py` | Thin coordinator/lock calls and held-work preservation. |
| `mc/hivemind_integration.py` | `tests/test_hivemind_worktree_isolation.py` | Per-worker standards, exact merge, smoke sequencing and owned rollback. |
| `tools/merge-review-check.py` | `tests/test_merge_review_gate.py` | Standards independent of review setting, actual pending candidate and child lease. |
| Existing evidence/job owners reused, no planned production edit | `tests/test_dev_harness_activation_evidence.py`, `tests/test_agent_jobs.py` | End-to-end receipt compatibility and existing job guard regression. |

If the M4 metadata seam changes adapter bytes, update exactly the 16 existing
`data/dev_harness_catalogue/checks/<recipe>.fixtures.json` validator pins:
`bandit`, `coverage-javascript`, `coverage-python`, `eslint`, `gitleaks`,
`licence`, `npm-audit`, `npm-build`, `osv-scanner`, `pip-audit`, `pyright`,
`pytest`, `ruff`, `semgrep`, `typescript`, `vitest`. Preserve targeted LF
attributes, fixture cases and recipe/fixture contents. These manifests remain
compatibility metadata, never project measurement evidence.

Expected build documentation edits: `docs/DEV_HARNESS_M5_PLAN_CONTRACT.md`,
`docs/DEV_HARNESS_M5_CONTRACT.md`, `docs/DEV_HARNESS_NATIVE_APPROVAL.md`,
`docs/DEV_HARNESS_RUNNER.md`, `docs/DEV_HARNESS_CHECKS.md`,
`docs/DEV_HARNESS_POLICY.md`, `docs/DEV_HARNESS_SPEC.md`, `CHANGELOG.md`.
No panel/user-guide/README, server, CI, installer, human-owned fence, agent-rules,
catalogue-practice coverage or new approval surface is part of this build.
Append operator-specific progress to the main checkout's gitignored journal.

No new product choice is open: preserve Dave's approved contract/amendments and
reuse A2a. Dave's contract review remains the required next handoff, not a new
Ron approval gate. The original handoff committed this document only. A2b
remains branch-only: no merge, push, restart or live settings change.

## 9. A2b implementation candidate

The branch candidate builds on `a7a9eb3a` and reuses A2a's inspected plan,
human Activate, immutable snapshot and activation-evidence owner. No proposal,
HTTP report, headless CLI output or historical run can authorize execution or
a later merge. All public/persisted run records retain `authorizes_merge=false`.

| Owner | Implemented boundary |
| --- | --- |
| `dev_harness_applicability` | Union of all matching approved groups, both rename names/deletions and dirty untracked paths; uncovered paths, explicit N/A, all-step activation measurement, independent warning/required aggregation. |
| `dev_harness_pending_merge` | Actual pending B/H/index T and matching working manifest, unique base and independently reconstructed auto tree; conflicts, manual resolution differences and octopus parents refuse instead of substituting HEAD. |
| `landing_serialization` | Common-Git-dir key; reentrant thread ownership and Windows byte/POSIX flock lock; kernel process-start identity, random exact-candidate child lease, bounded expiry and finally cleanup. Human activation takes this lock before CAS. |
| `dev_harness_runner.run_approved` | Private frozen Preview/pending/candidate seam on disposable copies, candidate config reinspection, one approved step per receipt, existing registration/cancellation/output suppression and source/tool/environment checks. Ordinary dirty-candidate refusal and report-only CLI remain. |
| `dev_harness_integration` | Approved snapshot/plan/source rechecks, ordered independent receipts, safe coordinator records, measured-evidence composition and live locked consumption. A required nonpass holds Enforce; intact Preview/Off add no standards hold. Corrupt active state cannot become Off. |
| `dev_harness_dispatch` and run routes | Exact Preview/run/read schemas; server-generated request ID and fixed internal Python invocation through existing session jobs; queued-input revalidation, safe completion/termination records and project-scoped stale reads. No caller argv/env/lease/report field. |
| `dev_harness_landing` and `dev_harness_manual` | New composition leaves keep the existing large owners thin. Managed/hivemind merges use exact H and verify T; hooks inspect actual pending state and reuse only a live exact child lease. Review-off and non-agent merges still evaluate standards; ordinary commits remain outside merge preflight. |

Normal removal also takes landing before lifecycle, so teardown cannot race the
final merge/T check. Native holds persist separately from successful merges;
remove/GC preserve held workers without caching a successful fingerprint.
Only the owned disposable hivemind integration can use its existing smoke
rollback. A human checkout is never reset after a landed-tree mismatch.

The existing job route has one internal-only completion callback seam and kills
its own child if outer registration fails. Harness completion still calls its
existing durable delivery owner. Native children in the server use the existing
registry directly; worker/manual processes use the existing loopback PID
registration route. Completed external rows retain the registry's existing
dead/live projection; no global process-cleanup endpoint is invoked.

All seven new runtime leaves are included in the checker identity. M4 source
and fixture manifests are unchanged; no validator-pin regeneration is needed.
No product dependency, human gate, config key, CI workflow, installer, fence or
agent rule was added or changed. Scratch-only POSIX test packages do not alter
runtime requirements.

### Validation and remaining delivery

The focused commands below established real Preview/pending receipts and measured-native Enforce,
warning/required independence, real child-hook execution under the parent lock,
held-worker remove/GC preservation, fixed session-job execution and native PID
registration. Tests use disposable repos, synthetic executables and temporary
trusted state. No live project has been activated.

```text
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_applicability.py tests/test_dev_harness_pending_merge.py tests/test_landing_serialization.py tests/test_dev_harness_integration.py tests/test_dev_harness_dispatch.py tests/test_dev_harness_run_routes.py tests/test_dev_harness_landing.py tests/test_dev_harness_coverage.py
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_integration.py tests/test_dev_harness_landing.py tests/test_landing_serialization.py tests/test_dev_harness_dispatch.py
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_integration.py tests/test_dev_harness_landing.py tests/test_dev_harness_dispatch.py tests/test_dev_harness_run_routes.py
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_run_routes.py
python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review"
pyright mc/dev_harness_applicability.py mc/dev_harness_pending_merge.py mc/landing_serialization.py mc/dev_harness_integration.py mc/dev_harness_dispatch.py mc/dev_harness_landing.py mc/dev_harness_manual.py mc/dev_harness_runner.py mc/dev_harness_diff.py mc/dev_harness_native_plan.py mc/dev_harness_store.py mc/hivemind_integration.py mc/blueprints/dev_harness_run_routes.py mc/blueprints/dev_harness_routes.py mc/agent_worktree.py mc/project_sync.py
python3 -m pytest -o addopts= -q --tb=short tests/test_landing_serialization.py tests/test_dev_harness_pending_merge.py tests/test_dev_harness_applicability.py
git diff --check
```

- Broad focused command: **78 passed, rc=0** (109.70s); subsequent final
  integrity/landing/job command: **30 passed, rc=0** (85.58s). Includes actual
  pending-index coverage B-to-T, incomplete zero-denominator warning, required
  continuation, live job dispatch, child hook, hold/GC and post-landing mismatch.
- Required full regression: **1,010 passed, 15 skipped, 16,796 deselected,
  rc=0** (389.15s), without warnings. Skips are 13 existing POSIX-sh hook cases,
  the Windows newline-filename case and optional missing `psutil` inspection.
  The final routing/worker-output, repository-boundary and request-lifecycle guards followed this
  full run; the third focused command covers those final changes, including
  real positive job/hook execution: **28 passed, rc=0** (86.48s). The fourth
  focused command verifies the last pending-record ref-metadata correction:
  **4 passed, rc=0** (4.09s), including a target other than HEAD. Both late
  lifecycle races were reproduced as failing tests before their fixes.
- Scoped Pyright above: **0 errors, 0 warnings, rc=0** across 16 changed/new
  owners, including all seven new leaves. Checking the existing
  `mc/blueprints/agent_routes.py` additionally reports **15 errors, rc=1**;
  a dispatch-base copy checked under the same config produces the identical
  15 diagnostic rule/message pairs. No new error was added there.
- Last pytest command ran under Ubuntu/WSL with scratch-only test packages:
  **15 passed, rc=0** (9.23s). Exercises real POSIX flock, subprocess contention,
  waiter/crash release, aliases, lease integrity, cleanup failure, pending index
  and applicability. The corresponding Windows cases also pass. Full POSIX
  native integration, macOS and packaged execution remain unverified.
- Seven changed implementation docs: **19 local links resolve**; whitespace
  checks pass. No static/UI change: boot/feature smokes, USER_GUIDE and README
  are N/A. No new standing convention: AGENTS/rules/memory-index edits are N/A.

Early coverage/version errors remain explicitly incomplete and cannot mint
activation evidence; they do not skip later required observation steps. The
existing evidence owner still requires complete real input provenance. M4
receives the exact pending B/H/M/T adapter view while receipts preserve the
distinct pending-index descriptor; no M4 source/fixture identity was changed.
Final boundary checks reject a workspace in a different common repository,
keep approved non-Git/unborn observations manifest-only without a job, and
project malformed trusted reads/worker requests to fixed safe error codes.
No traceback or arbitrary exception enters the worker completion tail.
Completion and queue persistence share a short parent-side lock, so an early
completion cannot recreate a runnable request. Workers re-read request/terminal
state after acquiring landing ownership; consumed requests cannot execute again
or overwrite an earlier terminal result. Both races have explicit refusal tests.
Pending records retain the original private target/incoming refs for accurate
staleness reads; those ref strings are excluded from the public projection.

Manual raw-Git limitations remain: fast-forwards and `--no-verify` can bypass
hooks; standalone preflight is never reusable merge approval. Advisory locking
cannot prevent external Git writes, so final ref/index/source checks remain.
Full catalogue practice coverage is still Unsupported; coverage recipes remain
warning-only. M6 still owns the panel/user guide, real installed-tool acceptance,
four-vendor probes, dogfood and packaging/frozen-worker acceptance. No merge,
push, restart, live settings mutation, email or deployment is part of A2b.

### A2b review correction: integration runtime files (2026-10-30)

Dave's [section 9.1 amendment](DEV_HARNESS_M5_CONTRACT.md#91-dave-amendment-2026-10-30-integration-inputs-and-inactive-bypass)
supersedes integration-only untracked-manifest requirements. The additional
`mc/dev_harness_integration_inputs.py` leaf owns tracked HEAD/index/working
identity and private candidate freezing, included in the approved checker
digest. Candidate, pending/source, trusted runner, coordinator/registered-main
and server-built dispatch/read boundaries use it. Ordinary/manual candidate
freezing and full dirty Preview/measurement retain their earlier semantics.

Inactive managed landing (Off, no snapshot, planless Preview) skips all harness
source freezes/manifests. Main-checkout dirt checks consider tracked files so
untracked runtime files reach Git's own merge collision check. A collision
preserves that file, branch, checkout and HEAD; active tracked dirt refuses.
No runtime file is relocated, ignored, staged or removed by this correction.
Inactive hooks use the existing parent lease with Git-only B/H/T metadata;
neither parent nor child hashes source. A subprocess-hook sentinel test enforces
that boundary. Native pending descriptors remain a separate execution input.

Validation below distinguishes earlier focused checkpoints from the final
correction. The earlier A2b validation above remains historical, not proof of
this fix.

Exact correction validation commands (all operate on disposable repositories):

```text
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_integration_inputs.py tests/test_dev_harness_landing.py tests/test_dev_harness_pending_merge.py tests/test_dev_harness_integration.py tests/test_dev_harness_diff.py tests/test_dev_harness_dispatch.py tests/test_dev_harness_run_routes.py
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_landing.py -k inactive_landing
python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review"
pyright mc/dev_harness_applicability.py mc/dev_harness_pending_merge.py mc/landing_serialization.py mc/dev_harness_integration.py mc/dev_harness_dispatch.py mc/dev_harness_landing.py mc/dev_harness_manual.py mc/dev_harness_runner.py mc/dev_harness_diff.py mc/dev_harness_native_plan.py mc/dev_harness_store.py mc/hivemind_integration.py mc/blueprints/dev_harness_run_routes.py mc/blueprints/dev_harness_routes.py mc/agent_worktree.py mc/project_sync.py mc/dev_harness_integration_inputs.py
python -m pytest -o addopts= -q --tb=short tests/test_landing_serialization.py
python3 -m pytest -o addopts= -q --tb=short tests/test_landing_serialization.py tests/test_dev_harness_pending_merge.py tests/test_dev_harness_applicability.py tests/test_dev_harness_integration_inputs.py
```

- First focused command: **65 passed, 1 Windows newline-filename skip, rc=0**
  (159.83s), before the final inactive-hook correction. Additional late
  candidate/index/ref-drift and queued runtime/read tests are covered by the
  required final regression, rather than attributed to this earlier collection.
- Second command after the hook correction: **6 passed, rc=0** (6.56s), proving
  no manifest calls with/without a real hook for no snapshot, Off and planless
  Preview. The no-snapshot hook case was reproduced failing before its fix.
- Final required regression after **all** source/test corrections:
  **1,038 passed, 15 skipped, 16,796 deselected, rc=0** (453.62s), no warnings.
  Includes dev_harness/landing, agent_worktree, hivemind integration, agent_jobs
  and merge_review. Skips: optional missing `psutil`, Windows newline filename
  and 13 existing POSIX-sh review-hook cases. New real inactive/native child-hook
  cases run on Windows and pass; no source correction followed this full run.
- Final Pyright command: **0 errors, 0 warnings, rc=0** (1.7s), all original
  16 owners plus the new integration-input leaf. Tool update notice only.
- Windows serialization command: **10 passed, rc=0** (3.35s), including actual
  process contention, ownership, child leases, cleanup and process-exit release.
- Last command on Ubuntu/WSL with existing scratch-only pytest packages:
  **20 passed, rc=0** (11.37s), after all source changes. Covers real POSIX
  serialization and pending/input boundaries; full POSIX native execution,
  macOS, installed native tools and packaged execution remain unverified.
- Documentation: **12 local links and amendment anchors resolve**; whitespace
  checks pass. No UI changes: boot smokes, README and USER_GUIDE are N/A.

An intermediate full-regression attempt was deliberately interrupted before
the inactive-hook source correction; it is not counted as a passing run. The
final exact rerun above completed after that correction. No merge, push,
restart, live activation/settings change, deployment or M6 acceptance is claimed.
