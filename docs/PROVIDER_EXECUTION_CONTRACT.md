# Provider-neutral execution contract — implementation branch

Status: offline implementation, not a live activation declaration. This refines
`PROVIDER_HARDENING_PROGRESS.md` after independent review. The current dormant
SQLite schema is version 2 on the feature branch, with explicit schema-1
migration. Lifecycle reductions and evidence now commit transactionally; live
capture, trusted transports and consumer cutover remain unactivated.

## Identity and authority

- Conversation owns requested engine/settings revision and immutable privacy.
  Requested model/effort/account reference are distinct from observed telemetry.
  A provider alias resolution is not consent to change continuation settings.
- Accepting a request records its original input, attachment references,
  provenance and settings revision. It queues work; it neither launches a
  process nor revokes the currently executing attempt.
- Claiming an attempt checks conversation owner epoch, expected aggregate/request
  revisions and absence of unresolved execution. Persist `launch_intent` before
  spawning. Retry is an explicit claim for the same accepted request, not another
  user message. A duplicate claim is idempotent only with identical evidence.
- Owner takeover increments epoch and makes unresolved execution `uncertain`.
  It does not prove the previous process died or that its side effects did not
  occur. Reconcile before adopting, retrying or cancelling.
- Tokens fence mutations, not authorization. API project access and account
  authorization must be checked by the execution service before storage access.

## Attempt state machine

| State | Allowed next states |
|---|---|
| launch_intent | spawning (guarded launch), running (legacy primitive), failed_before_launch, uncertain |
| spawning | running, failed_before_launch, uncertain |
| running | cancel_requested, completed, failed, blocked, uncertain |
| cancel_requested | cancelled, completed, failed, uncertain |
| uncertain | explicit reconciled running or terminal outcome |
| terminal | none; another execution requires a new attempt |

Transitions require expected revisions and current owner authority. Native-handle
binding is immutable/idempotent per attempt. Cancellation request is not a
cancellation acknowledgment. Normal process completion is not domain acceptance
of a Hivemind deliverable. No exactly-once guarantee is made for external effects.

The guarded launch path consumes `launch_intent` into durable `spawning` before
calling a trusted process creator. The second write transaction revalidates
authority and holds takeover/deletion until creation returns and `running` is
committed. An unknown creation failure records `uncertain`; an event/commit
failure leaves the durable `spawning` marker. Either requires reconciliation,
not another launch. The process reference is distinct from a native thread ID.
Legacy direct transition APIs remain offline lifecycle primitives, not process
launch authorization. Production callers must use the enforcing service.

The creation/authorization callbacks must be bounded and cannot recursively write
the store or wait for reader persistence. Early output requires transport-owned
buffering. Callback time limits and external configuration/account revocation
are not enforced by a SQLite lock; the database-wide guard is not yet a certified
production transport. Tests use fake process creation, not real CLIs.

## Evidence versus authoritative state

The event protocol has its own version, independent of the SQLite version.
Content needs request/attempt/message/block identity, delta ordering and explicit
partial/final status. Tool identity is `(attempt_id, call_id)`. Original user
input must not be inferred from a CLI message containing injected context.

Attributable late evidence from a known old attempt is valuable history. Preserve
it as late/non-authoritative while privacy generation is still valid; it may not
change current status, trigger completion notifications, or unblock dependencies.
After deletion or restore, prior-generation producers cannot append. Lifecycle
names cannot be forged through the generic content-append method.

Unknown content is retained but makes projection coverage incomplete unless its
protocol version explicitly classifies it as ignorable metadata. Scribe sees
attempt/outcome and partial-output boundaries, not a concatenation that presents
failed speculation as final accepted work. Unknown origin never becomes manual
origin to enable learning. Preserve unattended classification, authority checks,
suppression, memory ownership and source ranges through Distiller and retrieval.

## Snapshots, deletion and memory commits

A snapshot fixes the inclusive sequence high-water mark, privacy generation,
coverage revision and source designation. Read pages cannot cross that boundary.
Appending later events does not invalidate an otherwise complete earlier span.
Deletion and restore each increment generation; both invalidate older snapshots.

Preserve existing hide/recover semantics; do not imply erasure of already-written
memory, native transcripts or backups. New derivation from deleted source must
not publish. A derivation commit checks generation, coverage and previous cursor
atomically. Timeout, partial map success and unknown projection never advance
the acknowledged cursor. Explicit skipped-content dispositions are recorded.

**Cross-store gate:** checking SQLite generation and then independently writing
MEMORY.md is a race. Publication and deletion need a shared cross-process guard
or a transactional derivative plus guarded materialization. Existing leaf locks,
atomic MEMORY.md writes and the shared permanent archive remain mandatory.
This publication mechanism is not yet implemented; the gate remains open.

## Coverage and cutover

Coverage states: `legacy_only`, `partial`, `canonical_verified`, `capture_gap`.
Canonical-first means canonical-verified-first, never blindly replacing richer
native history with an empty/partial store. Native import stays inside adapters;
source offsets/IDs prevent stream/import duplicates. No dual Scribe writer may
acknowledge the same span independently.

Rollback must retain access to canonical-only history; reverting the reader to a
native file alone would lose that data. Schema-1 migration is explicit/offline,
backed up and transactional. Preserve original events/sequences, label unknown
provenance/coverage, set old execution uncertain, and inherit no valid owner.
Unknown schemas fail without modification. Schema-1 migration now holds a writer
lock while creating a consistent, exclusively named backup and applying the
transactional schema change. Legacy conversations remain legacy-only; migration
does not falsely reinterpret old records as managed execution. No production
database has been migrated.

## Resource and validation gates

### Offline authorization boundary

`execution_policy.py` represents immutable requested provider/model/effort/account
and environment identity, with distinct interactive, unattended and tool-free
transform profiles. Capability claims are supported, unsupported or unverified;
missing, expired, wrong-profile or wrong-environment evidence fails closed.
Explicit empty model/effort means native default; omitted values remain unresolved.
Authentication and quota blockers are scoped to provider/account. A stated quota
reset time does not clear a blocker or authorize a provider/account switch.

This module does not certify an installed CLI. A trusted certification runner and
an enforcing transport still need to bind executable identity, configuration,
account and operation profile immediately before launch and input delivery.
An in-memory permit is not a sandbox and does not survive revocation checks by
itself. Caller-authored capability records must never become production evidence.

### Versioned content boundary

`conversation_contract.validate_protocol_event` defines protocol 1 independently
of the database schema. Incoming message blocks carry stable IDs and explicit
partial/final status; deltas carry a nonnegative index. Tool results explicitly
state whether they represent an error. Unknown fields/kinds/versions are rejected;
an adapter must emit an explicit `capture_gap` when it cannot map native content.
Store-owned `lifecycle.*` events cannot be appended through this evidence API.
Legacy schema-1 validation remains unchanged for backward-compatible readers.

Capture gaps must retain approved source references for later recovery. Rejecting
an unknown event is not proof of full capture; adapters must stop acknowledgment
at the gap. Raw diagnostics containing credentials or private environment state
are not an acceptable fallback archive.

Count-limited pages are insufficient for arbitrary large tool output. Define
byte-bounded transport with chunk/blob-backed source retention, batching/durable
flush boundaries, backpressure and a visible persistence-failure state before
activation. No silent best-effort write fallback. Benchmark actual disk/latency;
do not assume WAL or a large unit-test count proves the operational contract.

Required fault cases include concurrent acceptance/claims, stale retry, crash
before/after spawn, live old-owner receipts, terminal/output races, cancellation,
delete/restore during summarization, snapshot/export during writes, missing
coverage, malformed/unknown protocol, disk full/locks/corruption, migration,
backup and rollback. Profile certification covers tools/MCP/hooks/plugins/config
isolation before sensitive input and is invalidated by relevant environment
changes. Clean single-provider platform tests remain the final parity gate.

## Startup isolation gate

Code worktrees do not isolate user-home data or OS keyrings. Standard boot starts
background services and a Claude inference-based authentication probe; remote
module import may start an enrolled tunnel before boot. A nonproduction port
alone still binds externally. Do not call such a launch an isolated test.

A limited route/UI harness may omit boot, bind loopback and redirect every data,
home, provider, temporary and vault path in an allowlisted child environment,
with remote features disabled and file-backed test secrets. It must not inherit
operator API keys or call remote identity routes. Such a harness is NOT proof
of clean production startup. Full boot testing requires a disposable account/VM
or a tested startup-effects policy, neither provided by a worktree alone.
