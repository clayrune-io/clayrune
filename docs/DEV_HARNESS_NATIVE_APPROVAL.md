# Native-plan approval and activation evidence (DH-M5 A2a)

MC-960 / d813ea78. Implements sections 2 and 4 of the
[approved M5 contract](DEV_HARNESS_M5_CONTRACT.md), with section 9's split and
warning-only coverage amendment. This branch supplies the approval boundary;
A2b execution, applicability and landing enforcement remain unimplemented.
`merge_enforcement_available` remains false. No native command is invoked by
plan inspection, approval, reading or evidence composition.

## Owners and state

- `mc/dev_harness_native_plan.py`: strict bounded JSON, declarative inspection,
  M3/M4 file-only preparation, fixture metadata, content-addressed plan storage,
  stored/live binding checks and native protection losses.
- `mc/dev_harness_policy.py`: new resolutions use version 2 and explicit
  `native_checks`; no profile seeds native checks. Version 1 snapshots retain
  their original data and digest on read. Full-practice validation is unchanged.
- `mc/dev_harness_store.py`: the exact envelope, plan digest and native loss
  acknowledgements join policy/catalogue/evidence/mode in the atomic snapshot
  and its audit event. Existing revision CAS and first-activation staging remain.
- `mc/dev_harness_activation_evidence.py`: composes evidence from stored trusted
  runner receipts and M2's verified installed guidance; has no command executor
  or HTTP upload. Enforce revalidates its inputs and receipts.
- `mc/blueprints/dev_harness_run_routes.py`: only POST `/native-plan` in A2a.
  The existing harness blueprint nests this leaf; no new server.py concern.
- `data/dev_harness_catalogue/checks/<recipe>.fixtures.json`: 16 separate
  compatibility manifests pin recipe, adapter code and the positive/finding/
  crash fixture contract. Runtime requires no test files. Tests verify their
  byte digests and exercise all three cases, including coverage validation.
- `.gitattributes`: LF checkout rules for the byte-pinned adapter sources and
  fixture JSON. Native Windows CRLF would otherwise invalidate shipped manifests
  on a POSIX checkout. Existing M4 source/fixture content is unchanged.

Plans live under `data/dev_harness/<project>/native_plans/<digest>.json`,
separate from M2 guidance plans and `data/projects/`. An intact proposal is
immutable and grants no execution authority. Ordinary application source edits
require new measurements, not renewed plan approval. Changes to native config,
inventory, tool closure, fixtures, recipe or checker make active reads fail.

Human replacement uses a private integrity-only prior snapshot reader to
compute losses even if installed inputs have drifted. It still rejects missing,
corrupt or mismatched stored/embedded plans; it never supplies run authority.
New approval fully reinspects the replacement against the registered project.
This avoids trapping a human behind the stale configuration they must replace.
Snapshot/receipt JSON stays strict without imposing the 4 MiB plan-input limit
on trusted envelopes that embed plan, measured source and growing audit history.
Proposal bodies and stored native plans retain their explicit input size limit.

## API and approval

POST `/api/project/<id>/dev-harness/native-plan` accepts exactly
`{expected_revision, draft, steps, groups}`. Duplicate JSON keys, nonfinite
numbers, unknown fields, duplicate IDs, traversal, unsupported paths/closures,
common inline secret/credential syntax and caller-derived pins are refused.
Null/absent baseline and native-rule pins are computed; supplied pins must
match. Launcher resolution, config discovery, installed closure hashing and
Git metadata inspection execute no native tools or version probes.

The response contains resolved `policy`, `native_plan_digest`, exact `plan`
envelope, compiled `commands`, full-practice coverage, `protection_losses` and
`native_losses`, `coverage_scope=named_native_checks`, `authorizes_merge=false`.
Stale revisions return 409; invalid declarations 400; unknown project 404;
corrupt trusted state 500. Proposal inspection is allowed for agent callers.

Existing human POST `/activate` adds `native_plan_digest` and
`acknowledged_native_losses` only. Agent refusal still precedes JSON parsing or
state reads. Passcode/throttle/unconfigured-passcode behavior is unchanged.
Submit the returned policy's `profile/practices/digests/native_checks` projection
as `draft`, not its server-owned version or catalogue fields.

Human Preview approves exact P/D without measured evidence. Planless Preview
remains valid guidance. Native Enforce requires the same prior approved Preview
P/D, current revision, verified guidance, unchanged measured source and stored
composition evidence. Every enabled check must have a measurement; every
required native check must pass. Warning findings remain visible. At least one
required native check is necessary. Full required Unsupported practices still
refuse activation. Coverage recipes reject required severity with
`coverage_required_unmeasurable`; no default changed-line floor is supplied.

Native losses separately enumerate prior required native checks removed,
disabled, downgraded or changed in scope/config/caps/tool/argv/mapping, and all
prior required IDs on leaving Enforce. Full-practice omissions still need their
own acknowledgement. Acknowledgements waive neither evidence nor coverage.

## Receipt seam for A2b

A2a evidence tests write **synthetic** M3/M4 receipts to temporary trusted state.
They prove composition and approval checks; they are not project execution or
real native compatibility evidence. No A2a route can produce these receipts.

A2b must supply current `runner_receipt` records accepted by M3 `read_receipt`,
with the approved `native_plan_digest`, M5 `checker_digest`, exact frozen
Preview `source`, M3 `steps_digest` and M4 per-step identities/results. A2a
rejects integration receipts as activation measurements: their B/H/M/T source
cannot substitute for approved baseline S. Results must bind argv, executable,
config, recipe, installed closure, environment digest, version and report
contract, with valid exit/status, no cancellation/timeout or structural error.
An input-complete warning result may retain `error` for the closed measurement
codes `invalid_native_report`, `empty_diff_coverage` or
`unsupported_tool_version`; it never becomes a per-check pass. Unknown errors,
missing identities and source/tool/environment integrity failures still refuse
composition. A2b must preserve full provenance for such results; a generic M3
`evaluation_error` with missing results cannot be relabeled as warning coverage.
Multiple receipts may cover all steps, but duplicate or missing check results
are refused. Receipt source and all enabled measurements must match baseline S.

`compose_evidence(store, project, receipt_ids)` rechecks verified M2 installation,
active Preview, live plan inputs and measured source; it writes a server-generated
`evidence/<id>.json` atomically. Safe output contains receipt IDs/digests,
per-check results and separate Unsupported full-practice coverage, with
`kind=measured_preview`, `coverage_scope=named_native_checks` and
`authorizes_merge=false`. Enforce consumes it in the next atomic snapshot.

## Verification and limits

Focused tests cover plan/schema/path/pin/fixture integrity, legacy snapshot
reads, exact approval and loss acknowledgements, synthetic native-only Enforce,
full-practice Unsupported rejection, warning findings, stale measurements,
guidance mutation, failed persistence and existing caller/passcode/CAS gates.

Final validation on Windows:

```powershell
python -m pytest -o addopts= -q --tb=short tests/test_dev_harness_activation_evidence.py tests/test_dev_harness_native_plan.py tests/test_dev_harness_native_plan_routes.py
python -m pytest tests -k "dev_harness or agent_worktree or merge_review"
pyright mc/dev_harness_native_plan.py mc/dev_harness_activation_evidence.py mc/dev_harness_policy.py mc/dev_harness_store.py mc/blueprints/dev_harness_routes.py mc/blueprints/dev_harness_run_routes.py
git diff --check
```

- Approval-focused: **139 passed, rc=0** (111.61s), including warning-error,
  aggregate-status, snapshot-size and byte-pin compatibility cases.
- Required regression: **824 passed, 15 skipped, 16835 deselected, rc=0**
  (281.71s). Skips: 13 require POSIX sh, one Windows newline filename,
  one missing optional `psutil` process-inspection dependency.
- Scoped Pyright: **0 errors, 0 warnings, rc=0**, all six changed/new `mc` owners.
- All four changed documentation files' six local links resolve; diff check clean.

No dependency was added or updated. No live activation, native tool probes, live settings mutation,
restart, merge, push or deployment. Existing M3/M4 regression tests run their
own synthetic subprocess fixtures. POSIX and packaged execution remain
unverified. No static assets changed; frontend smoke, USER_GUIDE and README
changes do not apply. No new standing rule requires AGENTS or rules edits.
