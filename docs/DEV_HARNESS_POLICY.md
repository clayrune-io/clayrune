# Dev harness policy foundation (DH-M1)

DH-M4 supplies native recipes and source-bound changed-line coverage through
the M3 runner; see `DEV_HARNESS_CHECKS.md`. Native command receipts do not
upgrade full-practice coverage or satisfy activation/independent review.

DH-M3 adds frozen native-command execution and report-only headless inspection;
see `DEV_HARNESS_RUNNER.md`. Its internal runner receipts are execution
provenance only, cannot authorize a merge and are not measured Preview
activation evidence. Native adapters and managed integration remain M4/M5.

DH-M2 adds pinned guidance inspection/installation and context projection on top
of this contract; see `DEV_HARNESS_GUIDANCE.md`. Its installation receipt is
revalidated before Enforce activation. M1 policy/evidence formats and all human
gates remain unchanged; deterministic/reviewer check coverage is still Unsupported.

MC-960 / `d813ea78`. Implements only the DH-M1 row of
`DEV_HARNESS_SPEC.md` revision 3. This is a backend contract, not the usable
six-session MVP. This document describes M1; M2 guidance and M3 runner coverage
are documented separately. No UI, merge hold or release gate is delivered.

## Ownership and catalogue

- `mc/dev_harness_catalogue.py` loads and validates data from
  `data/dev_harness_catalogue/practices/P01.json` through `P22.json`, plus
  `profiles/local-hobby.json` and `profiles/external-product.json`. One entry
  per file; the registry contains identities, not rule bodies.
- `mc/dev_harness_policy.py` resolves fresh project-local profile selections,
  toggles, severities and caps; validates exact-input activation prerequisites.
- `mc/dev_harness_store.py` owns trusted state and compare-and-swap revision
  updates. `mc/blueprints/dev_harness_routes.py` owns three HTTP contracts.
  `server.py` adds only blueprint composition.

Each descriptor preserves the full section 5 target, source/subpath/immutable
research pin/licence, applicability, adapters/configuration, stages, severity,
baseline policy, evidence and repair instructions. Numeric requirements have
names but **no prefilled values**. The source references use the spec's Addy
selection; these metadata records do not copy, install or verify guidance
resources. DH-M2 owns resource closure, notices and installation verification.
The pinned upstream README confirms `skills/<name>/` and warns that shared
repository references must also be preserved:
[pinned source](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/README.md).

**All deterministic and per-practice reviewer target coverage is Unsupported
in DH-M1.** Guidance coverage is a reference-only `guidance_only` label. Existing
independent review still exists outside this subsystem; it is not relabelled
as the unimplemented structured practice assessments. The full product profile
cannot activate Enforce. Future tickets must update delivered coverage only for
actually implemented contracts. A command pass must never claim the entire
semantic practice passed.

## Portable draft and resolved policy

`<project_path>/.clayrune/dev-harness/policy.json` is optional editable input:

```json
{
  "profile": "local-hobby",
  "practices": {
    "P02": {"enabled": true, "severity": "required", "caps": {"changed_line_floor_percent": 75}}
  },
  "digests": {
    "sources": "<sha256 of approved source/tool lock>",
    "baseline": "<sha256 of measured approved baseline>",
    "native_rules": "<sha256 of approved native rule/config manifest>"
  }
}
```

The example's 75 is illustrative, not a default. Digest placeholders above
are documentation only; supplied digests must be 64 lowercase hex characters.
Missing digests/caps are allowed in Preview drafts and rejected when needed for
Enforce. Unknown fields, practices, severity values and cap names, boolean or
nonfinite/negative numeric values, and percentages over 100 are rejected.
Selecting a profile or reading/importing a draft cannot select a mode. The
profile is a project-local proposal, and cannot modify another project or a
catalogue preset. Existing review/safety gates are untouched by its toggles.

## HTTP contracts

Prefix: `/api/project/<project_id>/dev-harness`.

| Method/path | Result |
| --- | --- |
| `GET /catalogue` | Versioned descriptors, profiles, catalogue digest and Python/JS/TS language identities. The languages are planned first-class coverage, not a claim that M1 executes their checks. |
| `GET` | Project ID, configured/status/mode/revision, frozen active snapshot, optional resolved repository draft and draft error, language identities, and `merge_enforcement_available: false`. Unconfigured projects return `not_configured`, revision 0 and Preview without writing anything. |
| `POST /activate` | Human-only atomic activation/mode transition with optimistic revision check. See request below. |

There are no exception, install, install-plan, preview-run or run-result routes
in M1. A corrupt draft is visible as Error while the frozen active snapshot
remains available; branch/release catalogue changes never silently replace it.
Unknown projects return 404. Invalid proposals/prerequisites return 400;
revision conflict 409; caller/proof refusal 403 (shared passcode throttling can
return 429); unreadable/corrupt trusted state or failed I/O returns 500/Error.

Activation request fields, all other fields refused:

```text
expected_revision: integer >= 0 (0 for first activation)
draft: portable draft object above
mode: off | preview | enforce
passcode: proof re-entered for this call, never persisted
evidence_id: server-stored measured Preview ID (required for Enforce)
acknowledged_losses: exact sorted practice IDs losing required protection
```

The exact loss set comes from `lost_protections(old_policy, resolved_policy,
mode, catalogue)`. It compares both the selected proposal and prior active bar:
disabled/downgraded requirements, mode outside Enforce, and changed established
caps require explicit acknowledgement. Initial Preview also discloses that the
profile's proposed requirements are not enforced. The stored snapshot carries
these acknowledgements so M6 can display what the owner selected.

Activation first calls `project_safety_settings.is_agent_caller()` (existing
Origin distinction plus OS attribution), then the existing shared
`_require_human_passcode`. An Origin/body claim or an authenticated browser
session alone is insufficient. Existing no-passcode bootstrap and per-IP
throttle behavior remain unchanged. Neither proof nor request-only fields enter
the snapshot. No exception or authority-expanding write path is added.

## Trusted evidence and persistence

Wiring passes `DATA_DIR.parent` (the runtime data root), never `DATA_DIR`.
Runtime state lives at `data/dev_harness/<project_id>/` and is gitignored:

```text
activated/active.json       one atomic snapshot, never under data/projects/
evidence/<evidence_id>.json future runner-owned measured Preview input
```

The `activated` directory distinguishes a previously attempted activation with
a missing snapshot from an unconfigured project. Missing snapshots under that
directory and malformed, NUL-filled, truncated or integrity-invalid records are
Error; none becomes Off, unconfigured or a pass. An initial I/O failure may leave
an empty `activated` directory, which also fails closed and requires operator
repair rather than a silent default. Evidence alone does not mark a project
configured. Snapshot/evidence IDs cannot escape the runtime project directory.

The process lock serializes revision comparison and atomic replacement in the
single server. The snapshot holds revision/mode, resolved policy, complete frozen
catalogue, sources/baseline/native-rule digests, evidence and timestamp together,
with a content digest for accidental-corruption detection and append-only
activation/mode audit history committed in that same record. It uses existing
`write_json_atomic` serialization, flush/fsync, same-directory replace and temp
cleanup. Validation/revision refusal leaves the existing bytes untouched; replace
failure preserves the entire prior record. This is not a signature or a sandbox
against unrestricted local filesystem edits or a second server process.

No route accepts an evidence body or writes measured Preview receipts. DH-M3
owns their creation; headless output must not mint trusted receipts. M1 consumes
only the selected project-local stored record. Its required fields are:

```text
kind = measured_preview; status = pass
project_id; active_revision (the current revision before activation)
policy_digest = canonical SHA-256 of the resolved policy
catalogue_digest; digests = exact sources/baseline/native_rules map
source_verification = baseline_verification = native_rule_verification = pass
practices[ID]: status=pass; coverage=exact descriptor delivered_coverage;
               caps=exact selected caps; guidance=verified; adapter_fixtures=pass
```

For each required selected practice, unsupported/guidance-only machine checks or
missing independent review coverage prevent activation. Required numeric caps,
verified guidance/resources and positive/negative adapter fixture evidence must
exist. Future runner evidence must bind underlying tools/config/resources to
these digests; setting these strings in a client request cannot satisfy them.
No full-product activation or real checker execution was performed for M1.

## Verification and next owners

```powershell
python -m pytest tests/test_dev_harness_catalogue.py tests/test_dev_harness_store.py tests/test_dev_harness_routes.py
pyright mc/dev_harness_catalogue.py mc/dev_harness_policy.py mc/dev_harness_store.py mc/blueprints/dev_harness_routes.py
python -m pytest tests/test_atomic_json.py tests/test_project_safety_settings.py tests/test_workflows.py
```

Supported-subset Enforce tests simulate a future delivered capability and stored
runner evidence on disposable fixtures. They prove the activation boundary, not
that any native command or structured reviewer adapter has been delivered.

Final DH-M1 verification: focused command above **257 passed, rc=0**; scoped
Pyright **0 errors, rc=0**. The combined focused + atomic/project-safety/workflow
command passed **393 tests, rc=0** before the final store/data refinements;
the subsequent 257-test focused run covers those final changes. No static
assets changed, so frontend boot/feature smokes do not apply to this session.

DH-M2 owns installed guidance and context delivery; M3 receipts/runner/headless;
M4 recipes/coverage; M5 merge integration; M6 the panel, user guide and end-to-end
acceptance. Catalogue data must be explicitly included when frozen-app packaging
is taken up; installer/packaging edits are outside this session's authorized
scope. Packaged builds and the running server were not exercised or changed.

The unattended fence rejected creating the required global plan file under
`~/.claude/plans/` (global configuration outside project scope). The six-step
plan is recorded in the task journal instead; no protected global file was
written. Engram tools were not exposed in this session; the journal preserves
the implementation handoff without altering the auto-maintained memory index.
