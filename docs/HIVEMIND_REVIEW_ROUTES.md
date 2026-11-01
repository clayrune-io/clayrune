# Hivemind reviewer verdict routes (BUILTINS-4 HR-4b)

`mc/blueprints/hivemind_review_routes.py` is the HTTP adapter over HR-3's
`ReviewStore`. It holds no policy: who may pass a candidate is decided by
`ReviewStore.record` (OS-attributed caller, designated reviewer, independence
from every contributing worker, immutable first result). It does not import
`mc/hivemind_reviewer.py` (HR-4a), launch anything, or advance integration.

## Endpoints

```
GET  /api/hivemind/<hivemind_id>/review/<workstream_id>/<integration_attempt>[?merged_sha=&incoming_tip=]
POST /api/hivemind/<hivemind_id>/review/<workstream_id>/<integration_attempt>
     {verdict: "pass"|"changes_requested", merged_sha, report_path?, incoming_tip?, project_generation?}
```

Local auth is the app-wide `local_auth_gate` before_request, same as every
other `/api/hivemind/*` route; the blueprint adds none of its own.

The candidate is **found, never supplied**: the route scans the run's
`reviews/` directory (written by `designate` before launch), rebuilds each
`Candidate` from the pins inside its record, and accepts it only if
`ReviewStore.find` validates the digest and the pins hash to the file name.
Body/query pins (`merged_sha`, `incoming_tip`, `project_generation`) are
*claims* that must equal the stored pins. `merged_sha` is required on POST: it
is the SHA the reviewer says it examined.

| Status | Cause |
|---|---|
| 400 | body not an object; unknown field; identity-shaped field (`reviewer_session`, `reviewer`, `session_id`, `run_dir`, `candidate`, ...); missing/non-string `merged_sha`/`verdict`; bad verdict; mistyped pins |
| 403 | caller not OS-attributed; caller is a contributing worker or descends from one; caller is not the designated reviewer (all raised by `ReviewStore.record`) |
| 404 | hivemind, project or designated review for that workstream attempt not found (a newer integration attempt never inherits an older pass) |
| 409 | claimed pins differ from the designated candidate; ambiguous candidates (give `merged_sha`); candidate generation differs from the manifest's persisted generation; lifecycle says the generation is stale/deleted; an untrusted record file exists and nothing trusted matches; conflicting verdict replay; store busy/corrupt |
| 500 | unexpected error; logged, body carries no detail |

An identical replay returns 200 with the original result (no write). A
conflicting replay is 409, including after `consume_pass`.

## Wiring

`server.py` calls `hivemind_review_routes.wire(hivemind_dir=, load_project_fn=,
assert_project_generation_fn=, verdict_guard_fn=, reviewer_status_fn=)` right
after the hivemind blueprint. Both lifecycle callables are **required**
keywords (no default, no `nullcontext`): without them a cancelled or timed-out
reviewer's late verdict would be accepted. Generation is checked with the same
validator the hivemind routes use (`agent_routes._assert_runtime_project_generation`)
against the manifest's persisted `project_generation`.

**HR-4a lifecycle.** `lifecycle_for(hivemind_routes)` returns
`(verdict_guard_fn, reviewer_status_fn)` that call `mc.hivemind_reviewer.
verdict_guard(api, candidate)` / `reviewer_status(api, candidate)`, importing
the module **at call time**. If it is missing or lacks either function, the
guard raises `ReviewRefused(409, 'reviewer lifecycle unavailable; integration
held')` and the state read shows a hold: fail closed. On a branch without
`mc/hivemind_reviewer.py` every POST is therefore refused; that is intended.
The guard wraps `ReviewStore.record` only (never git or a wait) and must raise
`ReviewRefused(status, message)` to hold a verdict.

**Projection.** Every GET, and the POST response, calls `reviewer_status`.
`result` is the lifecycle's `result` (HR-4a returns `None` once cancelled,
timed out or held), `hold_reason` is the lifecycle's, falling back to
`ReviewStore.hold_reason`, and `lifecycle` carries `state`, `can_record`,
`deadline_at`, `stop_error`. If the status call raises, `result` is `None` and
`hold_reason` is the refusal message (or a generic "lifecycle unavailable"):
a lifecycle problem never renders green. An empty `hold_reason` with a
non-null `result` is the only passing shape.

**Record scan.** `reviews/<key>.runtime.json` (HR-4a's sidecar, name matched
exactly as 64 hex + `.runtime.json`) is not a review record and is skipped. Any
other file that fails validation still counts as untrusted and holds.

`verdict_path(candidate)` returns the server-relative POST path so the launcher
builds the reviewer's `verdict_url` from this module rather than inventing one.

## Not done here

- Reviewer deadline (Q7-A, 45 min) is enforced by HR-4a's guard, not here.
- Q2-A (character resolved per attempt, fail visibly if missing) is the
  launcher's; the route reports the stored `reviewer_character` only.
- No index: candidate lookup scans `reviews/*.json` (a handful per run).

## Verification

```powershell
python -m pytest tests/test_hivemind_review_routes.py
python -m pytest tests/test_hivemind*.py tests/test_*review*.py
pyright mc/blueprints/hivemind_review_routes.py
python -m pytest
```

Counts and exit codes are recorded in `docs/_journal/f2605ff9-builtins4.md`.
Rollback: remove the `server.py` stanza; no data format changed.
