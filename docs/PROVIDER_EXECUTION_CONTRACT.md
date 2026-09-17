# Provider-neutral execution contract — implementation branch

Status: offline implementation, not a live activation declaration. This refines
`PROVIDER_HARDENING_PROGRESS.md` after independent review. The current dormant
SQLite schema is version 1; the lifecycle below must be transactionally wired
before capture activation. Pure transition tests alone do not provide durability.

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
| launch_intent | running, failed_before_launch, uncertain |
| running | cancel_requested, completed, failed, blocked, uncertain |
| cancel_requested | cancelled, completed, failed, uncertain |
| uncertain | explicit reconciled running or terminal outcome |
| terminal | none; another execution requires a new attempt |

Transitions require expected revisions and current owner authority. Native-handle
binding is immutable/idempotent per attempt. Cancellation request is not a
cancellation acknowledgment. Normal process completion is not domain acceptance
of a Hivemind deliverable. No exactly-once guarantee is made for external effects.

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
Unknown schemas fail without modification. Migration is not yet implemented.

## Resource and validation gates

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
