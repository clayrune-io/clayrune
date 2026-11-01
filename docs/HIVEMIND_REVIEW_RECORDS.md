# Hivemind post-merge review records (BUILTINS-4 HR-3)

`mc/hivemind_review.py` is the durable evidence leaf for HR-4/HR-5. It adds no
HTTP endpoint, git execution, runtime launch, integration loop, or enablement
change. It does not broaden `merge_review_gate`'s `clayrune/agent/` prefix or
its branch ownership rules. The full roster feature remains incomplete.

## Candidate and record contract

Construct a frozen `Candidate` from server-owned observations:

- `project_id`, `project_generation`, `hivemind_id`, `workstream_id`;
- `integration_attempt` and HR-2's `assignment_attempt` as `workstream_attempt`;
- `pre_merge_sha`, `incoming_branch`, `incoming_tip`, `merged_sha`, `merged_tree`;
- `contributing_sessions`, a nonempty tuple containing every contributing
  worker owner represented by the merged candidate, including earlier merges.

Full lowercase SHA-1 and SHA-256 Git object IDs are accepted; a candidate must
use one object format. IDs and positive attempt numbers are validated.
Contributor ordering does not affect identity. Every other pin does.

`ReviewStore(run_dir)` uses the existing server-resolved
`data/hiveminds/<id>` directory. Records live at
`reviews/<SHA-256-of-candidate-pins>.json`, outside project-record DATA_DIR.
The versioned record includes the exact pins, designated reviewer session and
global character reference, designation timestamp, structured result, and
consumption timestamp. The result contains verdict (`pass` or
`changes_requested`), report path, timestamp and OS-attribution label.
Paths in reports are evidence references only; this module never opens them.

Writes reuse `write_json_atomic`: same-directory temporary file, flush,
fsync, atomic replace, and temporary-file cleanup on failure. A process-local
lock plus a nonblocking OS file lock serializes short read/modify/write
transactions across threads, store instances and overlapping server processes.
Content digests and structural validation reject damaged records; malformed
JSON, duplicate keys, nonfinite constants, missing files and I/O errors hold.
Existing corrupt records are never overwritten or quarantined automatically.

## Adapter sequence

1. **HR-4/HR-5:** validate the merged candidate with existing landing/native
   gates and smokes. Allocate a fresh managed reviewer session using normal
   attribution and coordinator-owned sibling ancestry. Resolve the configured
   global character through HR-1's per-attempt contract.
2. Call `designate(candidate, reviewer_session=..., reviewer_character=...)`
   **before launch**. Ownership is immutable for that candidate. An exact
   repeated designation returns the original record; it is not permission to
   launch another reviewer. Replacement needs an explicit new integration
   attempt after owner/process reconciliation, consistent with Q7's zero
   automatic replacements after an uncertain launch.
3. Call `record(candidate, request, verdict=..., report_path=...)` from HR-4's
   authenticated, generation-validated route. Supply the real Flask request.
   Never accept a reviewer ID, ancestry reader, run directory, or authoritative
   candidate pins from the body. `spawner_of` is an internal test/adapter seam.
4. `record` reuses `hivemind_lineage.creator_of_request` for OS attribution and
   `review_lineage.check` for independence from **every** contributing owner.
   Authors, descendants, unknown callers and unknown ancestry refuse. Native
   subprocesses attribute to their managed parent; separately managed native
   children must retain real durable ancestry. Neither notifications nor
   body-supplied identities can establish independence.
5. The first structured result is immutable. Exact verdict/report replays
   return it without a write; conflicting replays refuse, including after
   consumption. Changes requested retains the provisional candidate (Q3-A).
   A corrected candidate/re-review uses a new integration attempt.
6. **HR-5:** reacquire existing landing/integration ownership and reobserve
   generation, attempts, HEAD/tree, incoming branch/tip, authors and candidate
   cleanliness. Reconstruct the current `Candidate`; never reuse a cached
   candidate after drift. Call `consume_pass(current_candidate)`. Only its
   first successful claim returns `True`; duplicates return `False`, and an
   absent/nonpassing/different candidate raises `ReviewRefused`.

`find` raises a hold for missing/corrupt evidence; `hold_reason` returns a
displayable reason, or an empty string for an exact recorded pass. An empty
reason alone does not authorize a next merge. All operations return detached
JSON values, so callers cannot mutate durable state through a returned dict.

Consumption does **not** move a git ref or advance the integration cursor.
HR-5 must persist/reconcile its cursor under its existing landing ownership.
A crash after consumption but before cursor persistence remains a recovery
hold until those two records are reconciled; `False` must never cause another
advance. This leaf's file lock is not a replacement for repository landing
ownership, and no lock is held while waiting for a reviewer.

HR-4 owns local authentication, reviewer engine/context, deadlines, launch
reconciliation and lifecycle controls. HR-5 owns git observations, paused/stopped
state, stale callbacks and integration progression. There is intentionally no
route or production wiring in HR-3.

## Verification and rollback

Commands from the candidate checkout:

```powershell
python -m pytest tests/test_hivemind_review.py
python -m pytest (Get-ChildItem tests/test_*.py | Where-Object { $_.Name -like 'test_hivemind*.py' -or $_.Name -like 'test_*review*.py' } | ForEach-Object FullName)
pyright mc/hivemind_review.py
python -m pytest
```

The scratch runner records the expanded command, complete output and exit
code under `_scratch/hr3_<mode>/`; it registers each suite's child process.
It is a local verification artifact, not part of the committed implementation.
The focused command selects all `tests/test_hivemind*.py` and
`tests/test_*review*.py` files. Tests use temporary directories, fake OS/runtime
tables, and existing lineage readers; no live reviewer/model call is made.

Validation in this branch: 92 new tests passed (rc=0); final focused suite
483 passed, 13 skipped (rc=0); basic pyright returned 0 errors (rc=0). The
13 skips are the existing POSIX-shell gate checks on Windows. An initial
full-suite process ended without an exit code and is excluded from validation.
A new complete run is owned directly by Clayrune's durable job facility;
its result remains pending. The original branch review gate and lineage files
are unchanged; existing gate tests are included in the focused run.

Rollback: revert the HR-3 source/tests/docs commit. Retain any runtime review
records and provisional candidate branches. Missing evidence remains a hold;
do not translate reviewer process exit or free text into a pass.
