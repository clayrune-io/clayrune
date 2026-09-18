# Recoverable canonical memory publication

Status: dormant publication kernel implemented; no memory writer is wired or
activated. Companion to
`PROVIDER_EXECUTION_CONTRACT.md` and `MEMORY_DESIGN_V2_SPEC.md`.

## Problem and existing ownership

The managed writer is `_commit_managed_entry` in `mc/memory.py`; managed entries
live in SESSION_LOG.md. It shares the leaf lock, atomic replacement and archive
helper with structured condense. The outer `_write_session_memory` performs
model calls and dispatches follow-up work; it must not run inside a canonical
database write transaction.

Strict read failures are now enforced for managed commit, split migration and
structured condense. Remaining crash problems are different: publication spans
archive, index and session-log files; terminal retries can duplicate entries;
overflow retries can duplicate archive batches; clearing inline legacy entries
before the session-log replacement can lose data after interruption. Existing
watermarks are garbage-collected and cannot be permanent publication receipts.

Extend the existing writer family. Do not introduce another memory format or
an independent writer that bypasses supersession, ring rotation or archive rules.

## Durable intent and receipt

Use an operator-state directory outside memory corpus globs, bound to the
resolved vault. One active intent per vault contains:

- Format version, operation ID, operation kind and request digest.
- Phase: prepared, applying, applied, aborting or aborted.
- Optional canonical source: project, conversation, consumer/version, privacy
  generation, coverage revision, inclusive source range and result digest.
- Allowlisted logical target names, original existence, before/after SHA-256,
  before-images, staged after-images and exact archive batch identity.

Permanent receipts retain operation ID, request digest, outcome, source scope,
target hashes and any retained archive batch IDs. Receipts survive condense,
ring rotation and watermark garbage collection.

The request digest identifies the logical operation, not the staged after-image:
later legitimate writes may change a recomputed after-image, but must not make
the same acknowledged operation run again. Same ID with a different logical
request is a conflict. Equal content from different operations remains distinct.
Never deduplicate archive writes by date/task or by summary text.

## Kernel interface

The leaf kernel now lives in `mc/memory_publication.py`. Its focused tests cover
durable receipts, explicit recovery, hash conflicts, abort semantics, resource
limits, manifest integrity, and staged-image tamper refusal. Cross-process lock
takeover and full writer-family integration remain activation gates.

Proposed leaf-module API, with caller-supplied paths and no global state:

```python
with MemoryTransaction(root, targets=targets, timeout=5, max_bytes=limit,
                       publisher=atomic_target_writer) as tx:
    receipt = tx.receipt(operation_id, request_digest)
    pending = tx.pending()
    # Caller handles pending canonical recovery under its outer DB guard.
    tx.prepare(operation_id, request_digest=request_digest,
               after_images=after_images, canonical_source=source)
    receipt = tx.commit()
```

Explicit recovery accepts forward or abort plus exact operation/digest identity.
Pending canonical intent is never automatically replayed by a legacy writer.
Targets are supplied by trusted composition code; manifest contents cannot
redirect writes to arbitrary paths. Acquisition has a finite timeout and all
staged bytes have a checked resource budget before any target is replaced.

Flush/fsync staged images, intent and receipt before advancing phases. Preserve
the existing atomic replacement primitive for target publication, verifying and
flushing the result. State platform limitations for directory durability; do not
claim power-loss guarantees from `os.replace` alone.

## Locking and writer integration

Order: canonical SQLite write transaction, then existing memory leaf lock, then
cross-process vault lock. Never acquire canonical storage while holding either
memory lock. Models, condense dispatch, learning and telemetry follow-ups remain
outside the publication section.

Every cooperating legacy writer must enter recover-before-write: managed commit,
structured condense, split migration, index demotion and watermark GC. Audit
editor/append APIs, backup restore and legacy agent condense before activation;
uncoordinated external edits are detected by hashes, not presumed impossible.

Confirmed integration gaps in the current branch:

- `mc/blueprints/project_routes.py:save_memory` and `append_memory` enforce
  the index byte cap but call `Path.write_text` without the managed leaf lock
  or atomic replacement. Both must join the gate; append must read and compute
  its result inside it to avoid lost updates.
- Legacy agent-condense completion in `mc/memory.py` directly restores or
  rewrites the index with `Path.write_text`. Its integrity repair is not a
  substitute for participating in recovery and publication ownership.
- Backup restoration lives in `mc/backup.py` (`restore_backup`, `rollback`),
  not a separate memory-backup module. Its full write and consent paths still
  require review before claiming that every writer is coordinated.

Inside the gate: strictly read, compute existing deterministic transformations,
prepare all images, then publish archive, SESSION_LOG.md and MEMORY.md in that
order. Do not rewrite an unchanged curated index. Write receipt last; only then
may the canonical consumer cursor acknowledge the source span.

The archive helper remains shared. An exact-operation retry compares archive
before/after hashes: before means apply, after means already applied, neither
means conflict. After a receipt exists, subsequent legitimate archive extension
does not invalidate that receipt. The archive is never truncated on rollback.

## Deletion and recovery

Deletion participates in the same vault gate. Authorized recovery may finish
replacements only while canonical privacy/coverage/cursor checks remain valid.

If deletion wins before acknowledgment:

- Fully applied output is historical `applied_unacknowledged`; retain already
  published derivatives under the disclosed policy and do not acknowledge a
  revoked source cursor.
- Partially applied output enters durable aborting state before recovery.
  Restore only non-archive targets whose current bytes match the planned
  after-image. Preserve an already-appended archive batch and record it.
- A revoked or aborting operation cannot resume forward publication after restore.

Any external edit matching neither image causes a conflict without overwriting
it. A legacy writer encountering canonical recovery releases memory locks and
returns RecoveryRequired; it must not query canonical storage under those locks.

## Acceptance gates

Inject interruption before intent persistence, after every staged image and
phase, after each target replacement, after receipt/before database acknowledgment
and during abort. Verify exact-operation replay, distinct equal-content writes,
archive preservation, legacy-writer refusal, external-edit conflicts, deletion
revocation, curated/watermark preservation and lossless split migration. Prove
receipts survive ring/condense/GC. Oversized staging and insufficient storage
must fail visibly before target publication. Cross-process locking and real
platform durability require separate evidence beyond thread-only unit tests.
