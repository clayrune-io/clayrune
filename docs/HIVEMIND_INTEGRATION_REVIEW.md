# Serial reviewed integration (BUILTINS-4 HR-5)

This branch adds `mc/hivemind_integration_cycle.py`. Explicit
`config.roster_version == 1` runs use it after workstream completion; legacy
engine-only runs keep their existing batch integrator. Worker/top-level
completion remains separate from the additive `integration.status` outcome.
This is a branch implementation, with no live pilot, release, restart or native
workflow execution claimed. HR-8 owns dedicated visibility.

## Sequence and ownership

Each actual merge on `hivemind/<id>` proceeds in this order:

1. Revalidate the project generation, current run, completed workstreams,
   pinned worker attempts/tips and clean checkout. Known worker descendants
   must have finished; active, failed or unreconciled children hold integration.
2. Under repository-wide landing ownership, use the existing native standards
   preparation, exact Git-child lease and landed-tree verification to merge
   one branch. Conflict/missing branch/dirty tree/standards failure stops here.
3. Persist the exact merged candidate, run that workstream's named smokes,
   and roll back just that merge when a smoke fails. Empty smoke lists remain
   explicit evidence, and do not waive standards or independent review.
4. Persist a frozen review brief and launch intent. HR-4a launches its configured
   independent reviewer on the retained integration checkout, with HR-4b's real
   verdict endpoint. The brief contains workstream scope, changed files, named
   smoke results and checks not run. A small dispatch facade adds its path and
   SHA-256 to the ordinary task/pending row without changing HR-4's pinned
   system context, identity, launch path or process registration.
5. Release landing ownership while awaiting the structured verdict. Repeated
   ticks read the same reviewer. A pass is consumed only inside HR-4a's
   `verdict_guard`, after reacquiring landing ownership and reobserving candidate
   HEAD/tree, incoming tips, workstream attempts and project generation.
6. Persist the accepted cursor before considering the next branch. Every actual
   merge gets its own exact review; a previous candidate's pass is insufficient.

No-op/ancestor branches are skipped without a reviewer. Non-git analysis-only
runs are explicitly skipped without creating an integration branch. Git code
with missing isolation holds. Code dependencies are retained as a Q6 follow-up
for Dave rather than merged as if workers had seen prerequisite code.

The main checkout, its dirty state, master and remote refs are never modified.
No push or release operation exists in this module.

## Durable state and recovery

The existing run's `integration.json` holds `review_cycle_version: 1`, a
digest-checked queue snapshot, original project identity/generation/path,
branch/base, incoming tips, expected HEAD, cursor, current candidate phase,
reviewer projection and accepted/skipped/failed entries. Atomic writes use
the blueprint's existing writer. Frozen `review-brief-<key>.json` files sit
beside it, outside project-record DATA_DIR and outside HR-3's review scan.
HR-3/HR-4 retain their own immutable ownership/results/lifecycle records.

`advance(api, id)` performs one serialized tick and returns at a pending
review. `start(api, id)` owns one daemon driver per run, polling every five
seconds; short cross-process run locks also exclude duplicate tick consumers.
The existing blueprint start, direct integration thread, read projection and
startup reconciliation delegate roster behavior here. No new public route or
body-supplied candidate/reviewer authority was added.

Recovery rules:

- A valid exact pass, including a pass consumed just before a cursor-write
  crash, can advance once after fresh checks and HR-4a admission. The same
  immutable pins reconcile a previously consumed pass; no new review launches.
- A persisted smoke checkpoint can resume checks on its exact retained merge.
- Interrupted merge intent or launch intent without durable reviewer ownership
  holds for reconciliation. A launch with ownership delegates recovery to
  HR-4a. Missing/corrupt state is never interpreted as permission to start over.
- No verdict, rejection, quota/launch failure, deadline expiry, cancellation,
  deleted generation, stopped/paused run, dirt or HEAD/tip/attempt drift holds
  later merges. Q3 retains the provisional commit on rejection. Holds are
  sticky: this module performs no automatic fix, replacement review or new
  integration attempt. Dave must arrange scoped reconciliation/fix/re-review.
- Reviewer deadlines and zero automatic replacements come from HR-4a's
  45-minute lifecycle. A pending hold attempts cancellation through HR-4a's
  owned runtime handle only; cleanup failures remain recorded. Immutable
  verdicts are never rewritten to obtain cancellation.

The candidate checkout and reports remain available in pending, held and
completed states. Smoke dependency links remain available while a reviewer
needs them. Terminal, reconciled attempts unlink those dependencies without
traversing the shared target; uncertain cleanup retains them. HR-5 never
deletes a worktree. Any later deletion must use existing safe unlink/cleanup
primitives first.

Native workflow launch, durable native-child accounting/admission and the
shared four-child cap remain HR-7. This module refuses known active or failed
managed descendants; that check is not evidence of real native workflow support.

## Verification

`tests/test_hivemind_integration_review.py` uses real temporary Git repos,
HR-3 records and HR-4 lifecycle/guards, with fake provider processes. Coverage
includes serial A/smoke/review then B/smoke/review, blocked A, conflict, dirty
worker, smoke rollback, standards holds, rejection, no verdict, timeout,
HEAD/tip/attempt/check drift, managed native descendants, no-op/empty smokes,
analysis-only, pause/stop/deletion, ambiguous intent, restart after consumption,
cursor-write crash, exact review task evidence, lock release while waiting,
startup delegation, damaged state and safe dependency-link handling.

Frozen-source checkpoint: all **540 hivemind tests passed** in 105.75 seconds,
including **44 new HR-5 cases**. `pyright mc/hivemind_integration_cycle.py`
reports **0 errors, 0 warnings**. `git diff --check` is clean.

The required unfiltered full suite is still running at this checkpoint; no
full-suite result is inferred. Source/tests are frozen in the worktree's
`_scratch/hr5/source_freeze.json`; scoped and full logs/argv/return-code records
are under `_scratch/hr5/{hivemind,full}/`. A final verification update replaces
this pending paragraph once that run terminates.
