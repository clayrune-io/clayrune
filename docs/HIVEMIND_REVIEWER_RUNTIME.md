# Hivemind reviewer runtime (BUILTINS-4 HR-4a)

`mc/hivemind_reviewer.py` is the trusted Python adapter for one independent
reviewer of an HR-3 `Candidate`. It adds no HTTP routes, server wiring, git
landing, pass consumption or feature enablement. HR-4b owns authenticated
request handling; HR-5 owns serial integration and current candidate pins.
The complete roster feature is not shipped by this leaf.

## Python API

The `api` argument is the existing `mc.blueprints.hivemind_routes` dependency
owner. It provides the run-directory/manifest helpers, generation validator
and `_hm_runtime_dispatch`. Public request bodies must never supply this
object, a run directory, a reviewer session ID, ancestry reader or authoritative
candidate pins.

```python
from mc import hivemind_reviewer as runtime
from mc.blueprints import hivemind_routes as hm
from mc.hivemind_review import ReviewStore

status = runtime.launch_reviewer(
    hm, manifest, project, candidate,
    review_cwd=integration_checkout,
    verdict_url=trusted_verdict_endpoint,
)
status = runtime.reviewer_status(hm, candidate)
status = runtime.cancel_reviewer(hm, candidate, reason='integration owner cancelled')
```

`manifest`, `project`, `candidate`, `review_cwd` and `verdict_url` are trusted
server observations. The supplied manifest must match the current persisted
one; project identity and generation must match. A roster manifest is required.
Launch and verdict admission refuse paused, stopped, archived or stale runs,
missing manifests and invalid/deleted project generations. Owned cancellation
remains available after a run stops so its original process can be cleaned up.

`review_cwd` must exist and its actual Git HEAD and tree must equal the exact
candidate `merged_sha` and `merged_tree`. Tracked modifications or an unreadable
checkout hold launch. HR-5 must reobserve drift under landing ownership before
consuming any pass; the runtime does not acquire the git landing lock or alter
refs. The trusted verdict URL is supplied by HR-4b/HR-5 so the prompt names the
actual wired endpoint rather than guessing an HTTP contract.

Every status includes:

| Field | Meaning |
|---|---|
| `candidate_key` | HR-3 candidate digest, covering generation and all pins |
| `reviewer_session`, `reviewer_character` | Immutable designated owner and canonical global reference |
| `project_generation` | Pinned project generation |
| `resolved_assignment` | Pinned character/provider/model/effort/session |
| `state` | `pending`, `pass`, `changes_requested`, `held`, `cancelled` or `timed_out` |
| `hold_reason` | Displayable reason or pending-verdict text |
| `deadline_at` | Unix timestamp, exactly 45 minutes after launch intent |
| `result` | Exact HR-3 result, or `None` whenever lifecycle vetoes it |
| `can_record` | Lifecycle allows an attributed verdict or exact replay |
| `stop_error` | Failed/unknown process cleanup requiring reconciliation |

Missing/corrupt records and unavailable locks raise `ReviewRefused(409, ...)`.
Missing manifests return 404; unprovable or author-descendant lineage returns
403. The HTTP adapter retains responsibility for local authentication, caller
attribution, coordinator authorization and precise request validation.

## Verdict and consumption guard: required adapter contract

An authenticated HR-4b handler must wrap the real HR-3 record operation:

```python
with runtime.verdict_guard(hm, candidate):
    record = ReviewStore(hm._hm_dir(candidate.hivemind_id)).record(
        candidate, request, verdict=verdict, report_path=report_path,
    )
```

`request` is the real Flask request. HR-3 still owns OS attribution, designated
session checks, independent ancestry and immutable verdict/replay validation.
No runtime function accepts a body-supplied reviewer nomination. The launcher
always takes the configured `reviewer_character` from the current manifest.
HR-4b must also authorize whoever requests launch/cancellation; this trusted
Python API is not an HTTP authorization boundary.

HR-5 must use the same guard around `ReviewStore.consume_pass(candidate)`
after its current HEAD/tree/generation/attempt checks. A raw
`ReviewStore.hold_reason()` or unguarded pass is insufficient to advance a
runtime-managed candidate: HR-3 alone does not know cancellation or deadlines.
Hold the guard only for the short record/consume transaction, never for git,
subprocess waits or the entire review. Lock order is runtime lifecycle then
HR-3 store; callers must not enter the guard while holding the HR-3 file lock.

The guard serializes recording/consumption against cancellation and timeout.
It rechecks current run/generation and durable lifecycle before admission.
Late recorded timestamps are suppressed even if another trusted caller wrote
directly to HR-3 without the guard. Process exit and free text never count as
a structured verdict. `changes_requested` keeps the provisional candidate held.
A timely durable verdict survives a later process exit/restart and elapsed
deadline; cancellation of an already recorded verdict is refused rather than
rewriting immutable evidence. A failed/stopped live owner is still a hold.

## Launch, identity and spend bounds

The runtime resolves the current global character once per new candidate
attempt with HR-1 `lookup_character`/`resolve_assignment`. Deleted characters
and unusable engines visibly hold; no project-default persona or provider is
substituted. The same snapshot supplies persona, name, avatar, skills, engine
and ordinary project/shared rules. The server-owned `You run unattended`
task prefix preserves the existing unattended memory/exploration read floor.
It adds an independent-review directive with every exact Candidate pin and
the structured submission contract.

Before dispatch it writes the pinned UTF-8 prompt, lifecycle launch intent,
`ReviewStore.designate` and a pending agent-log row. The pending row persists
the reviewer character, assignment, generation and immutable dispatch ancestry
before a child process can exist. Reviewer ancestry comes only from the
manifest's OS-attributed `created_by_session_id`; authors, descendants and
unknown ancestry refuse before spending. Human-created runs have an empty
parent, never an invented notification-target ancestor.

Dispatch reuses HR-2's `_hm_runtime_dispatch`/`PreparedContext` boundary and
ordinary runtime callbacks. Claude's prepared bridge applies its normal quota
gates once; other providers are checked against the existing allowance/model
quota gates before launch. Runtime fence/guard failures propagate as holds.
Existing process-registration and guardian hooks are passed through once.
Claude consumes the supplied review cwd/context and does not create a second
worktree. No worker workstream is created or modified by reviewer launch.

Live sessions and pending/completion rows carry `hivemind_role='reviewer'`,
`trigger_type='hivemind_reviewer'`, the candidate key as `trigger_id`, and
the run ID. They deliberately have no worker `hivemind_ws_id`. This exposes
identity/role through ordinary session and log projections for Floor/history;
dedicated badges and role-filter UI belong to HR-8. Existing worker admission
counts active workstream records, so an active reviewer uses zero worker slots
and cannot be adopted as a worker during ownership reconciliation.

## Persistence, restart and cancellation

Under the existing server-resolved `data/hiveminds/<id>/reviews/` directory:

- `<candidate-key>.json`: unchanged HR-3 ownership/result evidence;
- `<candidate-key>.runtime.json`: versioned lifecycle, exact pins, assignment,
  owner, start/deadline, original process PID and prompt reference;
- `reviewer-<session-id>.prompt.txt`: pinned UTF-8 context;
- `.runtime.lock`: short, nonblocking cross-process lifecycle transactions.

These are outside project-record DATA_DIR. JSON uses the existing atomic writer
(same-directory replacement, flush/fsync), content digests and structural
validation. Duplicate keys, damaged pins/digests, missing files, unknown phases,
and designation/lifecycle disagreement fail closed. Prompt rows store the
relative path and SHA-256 rather than copying full context into each log row.
No migration or deletion of existing review evidence occurs.

Duplicate calls read the existing owner; they never authorize another dispatch.
An HR-3 designation without its lifecycle, a lifecycle without evidence, a
crashed launch intent or an absent/unreconciled live owner is a visible hold.
No reviewer is automatically revived, retried or replaced. A new review needs
an explicit coordinator-owned integration attempt after reconciliation; changing
the character does not replace the current candidate's owner.

`reviewer_status` reconciles a restored record when called. A pending reviewer
whose live process cannot be established after restart is held; a valid timely
HR-3 verdict remains readable with its original designated owner. HR-4b/HR-5
must call status/guard on recovery rather than using raw HR-3 evidence. This
leaf adds no server startup scan or reattachment of orphan PIDs.

A daemon timer bounds a successful launch at 45 minutes. Admission also checks
the durable deadline, so a lost timer on restart cannot admit a late pass.
Timer cleanup still runs if the manifest becomes paused/stopped/deleted; corrupt
verdict evidence at expiry holds and stops the owned process. An unreadable
lifecycle cannot authorize a PID kill. Failed timer start, uncertain dispatch
and post-launch persistence failures stop only the locally owned handle and
remain held.

Cancellation writes its durable veto before stopping. Cleanup uses the original
managed session, pinned generation, provider, role, candidate and Popen PID,
then calls that provider's ordinary `stop(SessionHandle)`. A different session,
changed PID, absent handle/runtime or stop error is reported for manual
reconciliation; there is no PID-name sweep or process adoption. Generic guardian
recovery excludes these housekeeping sessions. Neither cancellation nor timeout
can yield a pass through the required guarded API. Zero automatic replacement
reviews is fixed by Q7-A; it is not a worker retry setting.

## Verification

All tests use throwaway directories/characters and fake vendor launches.
Real temporary Git repositories and the real Claude bridge verify cwd/HEAD/tree,
single context/guard/registration and no new worktree. The actual Codex fence
refusal is exercised with its probe faked; no vendor call, login, live roster
edit, server restart or production config change is made.

Commands from this checkout:

```powershell
python -m pytest -o addopts=-ra tests/test_hivemind_reviewer.py
python -m pytest -o addopts=-ra (Get-ChildItem tests/test_hivemind*.py | ForEach-Object FullName)
pyright mc/hivemind_reviewer.py
python -m pytest -o addopts=-ra
```

The ignored `_scratch/verify_hr4a.py` runner expands the hivemind glob, records
the exact command/rc and complete log in `_scratch/hr4a_<mode>/`, registers its
pytest PID, and explicitly inherits `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` into
hook children. Clayrune's managed job facility owns long verification runs.
No tests are excluded from the full command.

Final validation of source commit `39d24ae1`:

| Check | Result | Exit code |
|---|---|---|
| New reviewer tests | 77 passed in 7.54s | 0 |
| Every `tests/test_hivemind*.py` file | 453 passed in 38.75s | 0 |
| Basic pyright on the new module | 0 errors, 0 warnings | 0 |
| Complete pytest suite | 18,873 passed, 54 skipped, 1 xfailed in 2156.81s (35m 56s) | 0 |

The full result is from managed job `20eeab463f5a467b86f814ebba36a078`;
the final scoped result is from `55846548e9ae45ef8928e51ee129fca2`. The
runner's saved child rc and the managed job rc both equal zero. Source/test
SHA-256 values matched the pre-run freeze at completion. All 77 new tests
also passed inside the complete suite; no source or test edits occurred during
that final run. The skips are existing platform/optional-install/live-opt-in
checks. The expected failure is the existing human-owned fence-timeout patch
check; no gate was waived or altered.

Earlier 448-test and 453-test scoped runs preceded final prompt-claim
correction and are excluded from this final matrix. An early full run was
deliberately stopped at 4% for that correction; it produced no suite verdict
and is likewise excluded. Complete final logs and exact expanded commands
remain in the scratch directories named above; the journal records the run
and checkpoint history.

No live engine/fence execution, actual 45-minute vendor process, server-restart
reattachment, HTTP/UI integration or real multi-workstream pilot is claimed.
HR-4b and HR-5 must wire the guarded contract, and HR-8 owns dedicated UI.

Rollback: revert this leaf, its new tests and this document. Retain runtime
records, prompts, candidate branches and reports; missing runtime support or
evidence remains a hold, never an implicit pass. Existing HR-1/HR-2/HR-3 source,
routes and behavior are unchanged.
