# Launcher exit boundary (MC-1148 Phase 4b)

`mc/test_run_finalize.py` owns launcher enrollment/finalization orchestration,
OS peer identity resolution, capability issuance and conflicting-retry checks.
This is dark backend work: `test_run_progress_enabled` remains **false** by
default. No launcher, reporter, execution, cancellation, UI, server restart or
live config change is included. Dave's journal decision of 2026-11-05 08:55
selects launcher-observed finalization before Phase 5.

## Enrollment and parent scope

`POST /api/project/<project_id>/test-runs/enroll` accepts:

```json
{"metadata":{"runner":"pytest","unit":"test"},"parent_run_id":null}
```

The metadata allowlist is unchanged. Optional top-level `parent_run_id` must
identify an existing nonterminal run in the same project with the same stable
owner session. A foreign-project/missing run returns 404; a foreign-session or
terminal parent returns 403; invalid IDs return 400. Parent validation and
child creation share the store lock with finalization. Null means a root run.
Neither parent references nor any other body fields assign ownership. The
ingest method adds only a keyword-only parent seam; nested ownership/parent
metadata is still invalid. Project-only service owners cannot create children.

Response 201 adds a separate capability:

```json
{
  "run":{"run_id":"<id>","process_identity":"pid:<peer>@<creation time>"},
  "receipt":{"run_id":"<id>","producer_id":"<reporter id>","token":"<reporter receipt>","expires_at":123},
  "finalize_capability":{"run_id":"<id>","token":"<launcher capability>"}
}
```

The run/receipt above are abbreviated; existing fields remain. The enrolling
launcher's **actual HTTP socket owner** PID and creation time are recorded.
Phase 4 previously recorded the matching managed ancestor instead. The
managed ancestor still determines project/session ownership; sibling processes
under that ancestor have different launcher identities. Unavailable, changing
or non-finite creation time refuses enrollment. Body PID, process identity,
session, cwd, source, job and policy claims remain ineffective.

The peer lookup reuses caller attribution's system socket table and
unprivileged managed-tree fallback, independently verifies ancestry and checks
creation time twice. No process identity is extracted from diagnostic prose.
OS lookup failures log their exception class and fail closed. This retains
caller attribution's documented OS/ancestry limits; it is not cryptographic
proof of a remote client's identity.

## Finalize API and evidence

`POST /api/project/<project_id>/test-runs/<run_id>/finalize` requires
`Authorization: Bearer <finalize_capability.token>` and an exact body:

```json
{"exit_code":0,"signal":null,"termination":"exited"}
```

- `exit_code`: required integer in `[-2147483648, 4294967295]`; booleans/null
  are refused. Preserves negative POSIX return codes and Windows exit values.
- `signal`: required null or integer 1..255; never guessed from console output.
- `termination`: `exited`, `signalled`, `cancelled` or `timeout`. `signalled`
  requires a signal. `exited` requires a nonnegative code and null signal.
  Every other termination requires a nonzero code.

The server rechecks the live owner, restrictions and exact enrolling process
identity on every request. A receipt alone, another run's capability, another
session/project, sibling socket owner or reused PID cannot finalize. The
launcher must perform these HTTP calls in its own process rather than spawn a
fresh curl process for each call. Reporter subprocesses receive only producer
receipts, never the launcher's capability.

Ordinary zero exit becomes `passed`, ordinary nonzero/signalled exit becomes
`failed`; explicit cancelled/timeout remains that state. Existing known test
failures/collection errors still override a passing process result. This
boundary records process results; runner-specific `no_tests` classification
is not inferred from a numeric exit code here. `run_finished` on `/submit`
still sets **reporter_state only**, even if the reporter supplies exit zero.
Reporter payloads cannot supply process evidence.

The reserved store process event contains this allowlisted evidence:

```json
{"source":"launcher_observed","process_identity":"pid:<peer>@<creation time>","signal":null,"termination":"exited"}
```

It is stored as `run_finished.payload.evidence` and projected as snapshot
`process_evidence`. It never says `server_observed`: the launcher attests to
its child's actual wait result; the server does not observe that child's exit.
There is no assertion that these backend tests exercised a real launcher.
Phase 5 must finalize only **after it observes the actual child exit**.

Same capability/process/result retries return the original snapshot, timestamp
and sequence without a write. Any changed exit/termination/signal is 409 and
leaves history unchanged, including when an existing trusted capture event
already finalized the run. Validation errors return 400; authorization/policy
refusals 403. Existing JSON limits, duplicate-key refusal, no-store responses
and sanitized storage errors apply. Unknown body fields, including timestamps,
verdict, ownership and evidence-source overrides, are invalid.

## Capability and store lifetime

Capabilities are domain-separated HMACs over project/run/session/enrolling
process using a random service-local signing key. They do not share the
reporter grant table or producer receipt namespace. Neither tokens nor the key
are persisted. Capabilities have no elapsed-time expiry (a live launcher's run
can exceed one hour), no unbounded in-memory per-run grant table, and always
require current process identity and policy. Only enrollment issues one; the
reporter-generation endpoint never exposes one. No endpoint recovers one.

Close, observed flag disable, rewire and server restart invalidate all
capabilities. Canonical terminal evidence survives restart; old capabilities
do not. Restart adoption/recovery is outside this phase. Disabling the feature
does not delete history. Disabled finalize returns 404 before attribution,
body parsing or storage, like every other test-run endpoint.

The minimal store seam adds optional evidence to trusted `finish()` and
replays it only for the reserved process producer. The schema-1 envelope stays
unchanged; old records without evidence replay identically. New records with
evidence require this version's reducer. The existing full-history read/replay
cost remains; there is no throughput/compaction improvement claim.

Parents and children have separate counts, collections and frozen terminal
snapshots. Parent finalization never adds child tests to its denominator,
finishes children or rewrites child evidence. A parent's own script-unit
reports remain its only script denominator; Phase 5 owns composite execution.

## Verification

```text
python -m pytest tests/test_test_run_routes.py tests/test_test_run_ingest.py tests/test_test_run_store.py tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_background_job_endpoint.py tests/test_dev_harness_runner.py tests/test_atomic_json.py tests/test_caller_attribution.py tests/test_settings_routes.py tests/test_human_proof_guard.py tests/test_test_run_finalize.py
pyright mc/test_run_finalize.py mc/test_run_ingest.py mc/test_run_store.py mc/blueprints/test_run_routes.py
git diff --check
```

New tests exercise actual temporary persistence and the real HTTP ownership
resolver over a controlled OS table, both socket lookup paths, identity reuse,
forged/reporter tokens, restricted modes, parent scope/terminal states,
concurrent conflicting finalizers, atomic write failure/retry, canonical replay
and parent denominators. The existing route test's single identity expectation
changes from the ancestor PID to the actual socket PID; its body-forgery checks
are retained. No live server, authenticated launcher, cross-engine, deployment
or UI verification is claimed. No static changes: browser smokes do not apply.

Verified 2026-11-05: combined command **614 passed, 1 warning in 80.71s**,
exit 0. The warning is an existing invalid escape in an imported agent-route
test docstring. The new finalizer file contributes **84 passing tests**.
Pyright all four changed modules: **0 errors, 0 warnings, 0 informations**,
exit 0 (available-version notice only). `git diff --check` exits 0.
