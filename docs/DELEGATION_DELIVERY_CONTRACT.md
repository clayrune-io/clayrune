# Durable delegation delivery

Status: offline-tested implementation on the feature branch. No live activation
or end-to-end production delivery guarantee is claimed.

## Implemented increment and evidence

`mc/delegation_delivery.py` persists exact per-turn completion sources,
outbox/inbox receipts, turn allocation, fenced claims, explicit blocked and
uncertain states. The existing completion and startup paths now use this store.
Submission is recorded separately from successful task processing; it is not
verification that the parent completed its work.

The selected notification/workflow/spawn regression suite passes 161 tests.
The Codex integration traverses SQLite, drain, Flask inbox and actual dispatch
with a fake runtime, verifies one launch after reopen, and covers both a named
requested model and explicit native-default selection. Claude recovery uses the
real revival helper with a fake process. No live provider is exercised.

Remaining activation gates: real-server restart/delivery verification, provider
submission reconciliation beyond retained uncertainty, and user-visible handling
of blocked/recovery states. The API surfaces these states, but this increment
does not add a Floor badge or certify automatic task acceptance. Storage
retention and privacy deletion of the new delivery records need explicit policy
before general release. Unsupported native revival fails closed.

Windows verification note: a host runner with an invalid inherited stdin handle
can fail the existing cross-thread subprocess test with WinError 50/6. The same
161-test selection passed with stdin redirected from NUL; no assertion was
removed. This is a test-launch condition, not a delivery-code workaround.

## Ownership and semantics

A delegated task is a visible Clayrune session with immutable parent/project
identity and explicit requested provider/model. Completion of a child turn is
not acceptance of its work. Preserve three distinct facts:

1. The child emitted a terminal outcome and result (or failure).
2. The parent's durable inbox accepted the completion event.
3. The parent processed it and verified the task's acceptance criteria.

An HTTP response alone proves none of these without the corresponding durable
record. A completed worker with failing tests must not advance the task graph.

## Delivery protocol

Persist an immutable outbox event keyed by child session and durable turn ID.
Retain the full result or a durable artifact reference; a short chat preview is
not the sole copy. Enqueue before recording that notification was handled.
Transport retries use the same event identity. The receiver persists an inbox
receipt and pending work atomically before acknowledging acceptance.

Concurrent senders use leases or equivalent transactional claiming. Retry with
bounded backoff; failures retain the event and observable reason. Restart
reconciliation recovers both undelivered outbox events and accepted-but-pending
inbox work. No daemon-thread-only state is authoritative.

Parent processing is not exactly-once merely because inbox insertion is
idempotent. Persist dispatch intent and attempt ownership before launching or
writing to a provider. If a crash makes submission uncertain, expose that state
and reconcile evidence; never blindly repeat an actionful model turn. Do not
mark an event processed when it merely entered an in-memory queue.

## Safety boundaries

- A running parent receives pending work without destructive interruption by a
  retrying transport. Existing user-message interrupt behavior is separate.
- Missing/deleted parent, wrong project, quota/auth failure and provider
  unavailability are explicit blocked or pending states, not a fresh dispatch
  using project defaults. No implicit provider/model substitution.
- Completion notifications carry result data, not newly elevated authority.
  Existing unattended-origin and privacy protections remain in force.
- Incognito runs must not leak task/result content into the durable outbox.
- Failed or stale worker liveness never authorizes duplicate child execution.
- Durable bookkeeping is outside the directory whose JSON files are loaded as
  project records. Reuse established database/path/locking conventions.
- Existing workflow callbacks retain their own completion semantics; do not
  accidentally acknowledge them through a spawner-chat receipt.

## Acceptance evidence

Offline tests must exercise integrated production seams, not only a store:

- Failure before/after enqueue, transport timeout, receiver persistence failure,
  lost response after acceptance, concurrent delivery and exact duplicate IDs.
- Reopen after sender/receiver restart, accepted pending work, and uncertain
  provider submission without an automatic duplicate turn.
- Consecutive child turns, duplicated finalization, and delayed prior-turn
  callbacks. A new turn must not reuse a prior completion identity.
- Busy parent, missing parent, quota block, wrong project and incognito child.
- Parent provider/model/effort preservation and no fresh-default fallback.
- Full result retention, explicit delivery/processing/verification states and
  compatibility with existing notification/workflow regressions.

Activation additionally needs a controlled end-to-end test through the running
server. Offline green does not imply that an un-restarted server is fixed.
