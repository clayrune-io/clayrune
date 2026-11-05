# Test-run HTTP boundary (MC-1148 Phases 4/4b)

`mc/blueprints/test_run_routes.py` is a dark backend: no launcher, reporter,
runtime discovery, UI, process execution or cancellation. Phase 4b adds the
launcher-only finalization contract in `TEST_RUN_FINALIZE.md`.
`server.py` only wires the blueprint; `test_run_progress_enabled` defaults to
**false** and is in the existing Settings allowlist. Only literal JSON boolean
true enables it. Disabled endpoints return 404 before attribution, body parsing,
storage or ingest construction. Reads never construct `TestRunIngest`.

The existing app-wide LAN/tunnel authentication handlers still run. The config
flag uses the unchanged unattended-caller and dashboard-passcode gates of
`PUT /api/config`; these routes cannot activate themselves. No live config was
changed during development.

## Read API

Prefix: `/api/project/<project_id>/test-runs`.

| Method/suffix | Response |
| --- | --- |
| `GET` (prefix only) | `{runs:[snapshot,...]}`, newest first; `limit=1..100` (default 100), `offset>=0` (default 0). |
| `GET /<run_id>` | One snapshot, including optional Phase-4b `process_evidence`. |
| `GET /<run_id>/events` | `{events:[event,...],next_cursor,has_more,seq}`; `limit=1..100` (default 100), optional `cursor`. |

All reads accept `?session=<stable MC session ID>`. A live session must match
the project; absent live sessions must exist in that project's persisted agent
log. Run/session mismatch returns 404. Managed agent callers are restricted to
their own project and session even without the query; conflicting session
queries return 403. Unattributed UI/human callers retain project reads, like
the existing job-status API. Attribution **unavailable** returns 403.

Each returned event adds its canonical one-based `seq`. The cursor is
`<run_id>:<number already consumed>`, validated against that run and persisted
history. It needs no server-side cursor/session state and remains usable
after reconnect/restart. The tail returns an unchanged cursor and empty page;
later events appear from that same cursor. Producer replay adds no new index.
An alien, malformed or beyond-history cursor returns 400. List offset paging
does not promise stable positions while new runs arrive. Paging bounds the
response; the Phase-1 store still reads/replays full per-run history.

## Telemetry API

Every POST requires a JSON object, a currently attributed caller and current
server owner/policy validation. Producer operations also require
`Authorization: Bearer <receipt.token>`; never put receipts in URLs. Responses
carry `Cache-Control: no-store`, including reads. No tokens are persisted.

| Method/suffix | Body / response |
| --- | --- |
| `POST /enroll` | `{metadata:{runner?,unit?,display_label?,target_id?},parent_run_id?}` -> 201 `{run,receipt,finalize_capability}`. |
| `POST /<run_id>/finalize` | Exact `{exit_code,signal,termination}` plus launcher capability -> terminal snapshot; conflicting retry 409. |
| `POST /<run_id>/producer` | `{}` -> 201 `{receipt}` for a new producer generation. |
| `POST /<run_id>/submit` | `{events:[wire_event,...]}` -> updated snapshot. |
| `POST /<run_id>/spool` | `{}` -> 201 `{path}` for a new server-created spool. |
| `POST /<run_id>/spool/attach` | `{path:<exact minted path>}` -> `{ok:true}`. |
| `POST /<run_id>/spool/poll` | `{}` -> `{accepted:<records committed>}`. |
| `POST /<run_id>/revoke` | `{}` -> `{ok:true}`; closes that receipt's descriptor. |

Receipt and wire-event schemas, expiry, batching, pinned descriptors and
reporter-versus-process verdict rules are in `TEST_RUN_INGEST.md`. Top-level
body ownership, session, project, cwd, job, process, source and policy fields
are ignored; only the nested metadata allowlist and separately owner-validated
parent reference reach enrollment. Nested owner fields are invalid. Submit
cannot supply canonical producer identity or
observer timestamps. Reporter finish does not establish process success.

Bodies are bounded at 270336 bytes, including requests without Content-Length;
duplicate JSON keys, non-finite numbers, malformed JSON and non-object bodies
are refused. Invalid input returns sanitized 400, missing receipt/attribution
or restricted policy 403, missing records 404, oversized body 413, wrong media
type 415, and corrupt/unavailable storage 500. Storage failures log their class
without echoing producer content, paths or receipts.

## Trusted owner and authority parity

`resolve_owner` is real server code: the loopback TCP peer is resolved by
`caller_attribution.attribute_caller`, using `managed_roots` over live session
processes and tracked jobs. The resolved root must still match the live
session, project and any tracked process record. A body ID, Origin, environment
claim or receipt alone cannot establish an owner. Unknown/unavailable identity
never becomes a project-only enrollment. HTTP project-service enrollment is
not exposed; Phase 3's explicit in-process service-owner seam remains separate.

Tracked jobs can identify their session between CLI turns. Enrollment records
the existing session-cwd resolver's worktree and, since Phase 4b, the actual
HTTP peer PID/creation time (required). Managed ancestry still identifies the
owner; this is not a claim to know a test child PID. No stable launch-turn ID
or job ID is inferred from a request; those remain null for this wrapper
enrollment API. Later trusted
capture integration may provide those identities. Missing live owner state
refuses new telemetry; historical reads can still use persisted run history.

Incognito (including `_incognito`) and `_codex_unattended_sandbox` callers are
refused before telemetry/file operations, matching the existing job API's
restrictions under Q4. Policy is rechecked after enrollment, including spool
poll. These routes have no command, shell, cwd selection, execution, kill,
config write or arbitrary-file read. Only the separately scoped launcher's
finalize operation accepts exit evidence; reporter submit never does. Existing
human-only controls remain on their original routes with their original gates.
The existing attribution module's detached-process/OS lookup limits remain;
this API adds no claim of cryptographic caller authentication.

## Linked storage root review

Keep Phase 3's `folder.resolve() == folder.absolute()` check. Phase 1 already
resolves the configured data root in `TestRunStore.__init__`; consequently the
spool folder is built beneath the **resolved server-owned root**, not beneath
the original symlink/junction spelling. macOS `/var` and linked installation
data roots are normalized before this check. A redirect of `test_run_spools`
itself remains refused. A route-level test uses a real Windows junction (or a
directory symlink on POSIX), confirms successful create through it, then
simulates a redirected spool directory and proves refusal before file open.
No Phase 1-3 implementation or schema change was needed.

## Lifecycle and verification

One lazy, process-local ingest service; `atexit` closes owned descriptors.
The first request after flag disable closes outstanding receipts/descriptors;
reenabling cannot revive them. Disabling does not delete run history or spools.
No polling thread, process or retention policy is introduced.

```text
python -m pytest tests/test_test_run_routes.py tests/test_test_run_ingest.py tests/test_test_run_store.py tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_background_job_endpoint.py tests/test_dev_harness_runner.py tests/test_atomic_json.py tests/test_caller_attribution.py tests/test_settings_routes.py tests/test_human_proof_guard.py
pyright mc/blueprints/test_run_routes.py
git diff --check
```

HTTP tests use an isolated Flask app, real resolver/ancestry algorithm and a
controlled OS process/socket table, plus actual temporary persistence and
junction I/O. No live server, authenticated launcher, cross-engine capture,
deployment or UI verification is claimed. No static changes: browser smokes
are inapplicable. Local branch only; no merge, push or restart.

Candidate verification (2026-11-05): combined command above **530 passed in
77.17s**, exit 0; focused route suite **109 passed**. Pyright on the new module:
**0 errors, 0 warnings, 0 informations**, exit 0 (non-failing available-version
notice only). Working and staged `git diff --check` both exit 0.
