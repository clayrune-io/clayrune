# MC-1148 Phase 3: telemetry enrollment and ingestion

Status: standalone dark backend. No callers, routes, launcher, reporters, UI,
production configuration or process execution are added. This is phase 3 of
`TEST_RUN_PROGRESS_DESIGN.md`, sections 5-7; the phase-1 storage format in
`TEST_RUN_STORE.md` and phase-2 capture in `TEST_RUN_CAPTURE.md` are unchanged.
Dave's journal decisions Q1-Q4 apply, especially Q4: sandbox and incognito
coverage is deferred for v1. This module refuses both, including the global
incognito project, before enrollment, telemetry submission or spool I/O.
Existing job-route refusals are untouched.

## Trusted owner boundary

`TestRunIngest(store, resolve_owner=callback)` requires trusted server wiring.
For every operation, the callback receives `(project_id, caller_evidence)` and
returns an immutable `OwnerBinding` or `None`. It must independently verify an
existing managed process/session, authorized job, or explicitly authorized
project service. The binding supplies project/session, current incognito and
sandbox policy, and optional cwd/turn/job/process identity and launch source.
Both restriction flags are required booleans. A project mismatch, unavailable
attribution, malformed binding or restricted policy raises `IngestRefused`.

**This callback is a trust seam, not authentication implemented by phase 3.**
Phase 4 must construct caller evidence independently of body fields, claimed
session, environment variables or stdout markers. Managed ancestry must be
verified at enrollment; absent/unavailable attribution must fail closed. Never
decode a producer JSON object into `OwnerBinding`. Null session means an
explicitly authorized project-owned service. It must never mean failed session
lookup. A project-only binding cannot have a turn owner.

`enroll(project_id, caller_evidence, metadata)` returns `(snapshot, receipt)`.
The only allowed metadata keys are `runner`, `unit`, `display_label` and
`target_id`; the store validates their text bounds. All owner/turn/project/cwd/
job/process/source/parent fields, commands and environments are rejected.
Display labels and test names must already be safe for telemetry: this module
cannot discover a credential embedded in a valid name. Text remains untrusted
data and later renderers must escape it. Enrollment gets an independent UUID
for each launch; identical test names/commands never determine ownership.

`producer(project_id, caller_evidence, run_id)` issues a new producer generation
only to the matching current owner of a nonterminal run. Project-only owners
may issue generations for project-only runs, never for a conversation's run.
Changing the active launch turn does not transfer a run to that turn; immutable
enrollment retains its original turn. Policy is re-resolved on every write.

## Scoped producer receipts

`ProducerReceipt` contains run ID, a server-generated producer ID, an opaque
256-bit bearer token, and a wall-clock expiration hint. Every submission still
requires matching independently resolved project/session and the receipt's run
ID. A token alone cannot change owner or project. Producers cannot choose the
reserved process-evidence producer. Receipts never enter stored run records.
Do not log the token or put it in a command line or URL; later transports must
pass it privately. Lookup keys are SHA-256 token hashes in process-local memory.

Engineering bounds: receipts expire after 3,600 monotonic seconds; at most
1,024 live receipts are retained. Expired entries/descriptors are reclaimed on
the next authorized operation. Capacity refusal happens before creating a run.
Long-running reporters need a newly authorized generation before expiration;
there is no automatic renewal from producer telemetry. `revoke(...)` releases
one receipt and its descriptor; `close()` releases all owned descriptors.
Failures propagate rather than silently swallowing I/O errors.

Receipts and spool offsets are intentionally not durable. A new server/service
instance cannot accept old tokens, reopen a producer-supplied path, or assume
process adoption. A currently verified owner can request a new generation for
an existing nonterminal run; restart recovery is not implemented or claimed.

## Wire event and batch contract

`submit(project_id, caller_evidence, run_id, token, events)` accepts a nonempty
list of at most 64 events and 256 KiB of canonical UTF-8 JSON. A wire event has
exactly these four fields:

```json
{
  "schema": 1,
  "producer_seq": 1,
  "type": "unit_report",
  "payload": {
    "unit_id": "tests/test_demo.py::test_case",
    "attempt": 1,
    "phase": "call",
    "outcome": "pass",
    "final": true,
    "revision": 1
  }
}
```

Run/producer identity and observation timestamps are supplied by ingest.
Producer timestamps, owner fields and unknown envelope/payload fields are
rejected. Sequences are positive integers through `2**53 - 1`; booleans are
rejected. Store validation applies to every canonical event, including its
16-KiB maximum, finite timestamps and payload allowlists. Malformed JSON values,
NaN/infinity, invalid schemas/types, oversized names and conflicting revisions
fail before persistence. Caller payloads are detached before reduction.

Exact producer sequence retries preserve the original server observation time
and are no-ops, even when retried later. Conflicting content at the same
sequence is refused. Distinct lower sequences remain eligible; sequence gaps
do not imply passed/missing tests. Producer restarts use a new generation.
Reporter `run_finished` only sets reporter state. Only the existing trusted
capture/store `finish` path can establish real process exit or terminal success.
Terminal snapshots remain frozen; existing receipts may retry against them,
but no new producer is issued for a terminal run.

### Ingest-side batching with frozen storage

The entire submission is validated against the current canonical history before
one atomic envelope replacement. Conflicting duplicates, phase/collection
revisions or a malformed later event cannot leave an accepted prefix behind.
An atomic-write failure preserves the prior record; retrying the batch is safe.

Batching deliberately uses the store's private `_LOCK`, `_read` and `_path`
seams and its public `replay`, with the same `write_json_atomic` primitive.
The lock covers capture/store writes too. The schema-1 envelope remains exactly
`schema`, `enrollment`, `events`, `snapshot`; store implementation and on-disk
format are frozen. Any later change to these private seams must update this
adapter. No new database, journal format or per-event store write is introduced.

A 64-event submission makes one atomic write; an exact replay makes none.
Reads verify existing history once, then one combined replay validates the
batch. Per-event schema checks replay only that event. Whole-history work
remains linear per submission, and cumulatively grows as history grows. This
reduces write/replay frequency by batching, not by compaction. There is no
unbounded queue or asynchronous consumer here; producers/phase-2 adapters must
collect bounded batches and flush final batches before trusted finalization.
No large-suite throughput or retention guarantee is claimed.

## Safe spool attachment

Only `create_spool(project_id, caller_evidence, run_id, token)` can establish a
source. It creates a new random `.jsonl` file in the server-owned sibling
`data_root/test_run_spools/`, outside `data/projects/` and the target workspace.
One spool is allowed per receipt. The directory must resolve to its literal
absolute path; redirected directories are refused. Exclusive creation and
`O_NOFOLLOW` where available ensure an existing path is never opened. The
service retains the newly created file descriptor for subsequent reads.
The configured data root and directory namespace are trusted server storage;
this does not establish an adversarial same-user filesystem sandbox.

`attach_spool(..., path)` only confirms the exact minted path for that receipt.
It never resolves or opens a supplied path. Traversal, arbitrary absolute/UNC
paths, other producers' files, links and alternate-stream names cannot authorize
a read. This does not attach existing job logs or arbitrary workspace sidecars.
Phase 2 retains its separate trusted existing-job-log path. Future launcher
reporters can write this service-created source without importing `mc`.

`poll_spool(...)` reads the pinned descriptor in chunks of at most 64 KiB and
submits at most 64 complete JSONL records. Each line contains one wire event;
duplicate JSON keys, invalid UTF-8, malformed JSON and oversized lines are
refused. A trailing partial line/UTF-8 sequence remains on disk until the
newline arrives. There is no guessed final record without a newline. EOF does
not finalize a run. Producers must flush their final complete record.

The descriptor must remain a singly linked regular file, and size cannot fall
below the committed offset. A path replacement never causes an open/read of
the replacement. Read or submit errors propagate with the offset unchanged;
there is no arbitrary-path fallback or silent prefix skip. A producer may
repair its uncommitted tail and retry. Truncate-and-regrow preserving size is
not a content-integrity guarantee; producer contents are untrusted telemetry.
Progress polling is caller-driven: no thread, process or route is started.

Spool cleanup/retention is not implemented: expiration/revocation/teardown
close owned handles but do not delete telemetry. No restricted-workspace spool
or ephemeral incognito store is supplied. Both remain refused under Q4.

## Validation and integration limits

Focused coverage includes forged enrollment fields, unavailable/mismatched
owners, policy changes, project-only runs, same names across projects, receipt
scope/generations/expiry/revocation/capacity, batch atomicity/dedupe/timestamps,
malformed/oversized telemetry, reporter/process verdict separation, corruption,
concurrent capture writes, arbitrary spool paths, hard links, directory
redirection, pinned reads, UTF-8 partials, bounded backlog and failed commits.

```text
python -m pytest tests/test_test_run_ingest.py
python -m pytest tests/test_test_run_ingest.py tests/test_test_run_store.py tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_background_job_endpoint.py tests/test_dev_harness_runner.py tests/test_atomic_json.py
pyright mc/test_run_ingest.py
git diff --check
```

No new dependencies. No production enrollment, HTTP authentication, real
launcher/reporters, live cross-engine progress, restricted capture, adoption,
UI or deployment has been exercised. Internal-only scope needs no user guide,
README, agent-rule or user-facing changelog change. No static files changed,
so browser smokes are inapplicable. Local candidate only: no merge/push/restart.

Candidate results (2026-11-05): combined selected suite **269 passed in 65.80s**,
exit 0; focused ingest suite **89 passed**. Pyright on the new module: **0
errors, 0 warnings, 0 informations** (non-failing available-version notice).
`git diff --check`: exit 0. Batch-write counting proves 64 events use one
replacement and exact retries use none; it is not a large-suite benchmark.
