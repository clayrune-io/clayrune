# Native workflow adapter contract (BUILTINS-4 HR-7)

**Status: incomplete-pending-pilot.** The injected adapter and deterministic
fixtures exist; no production Claude driver, native-start invocation, runtime
wiring or native workflow enablement is supplied. A passing fixture is not
proof of Mode A native execution. HR-7 remains incomplete until a separately
authorized real pilot demonstrates the boundary below.

## Admission and authority

`mc/hivemind_native_workflow.py` exposes immutable `Run`, `Parent`, `Child`,
`Capability`, `Observation`, a `Driver` protocol and `WorkflowAdapter`.
`select(parent, driver, opt_in=False, required=False)` always defaults to
ordinary execution. No probe runs without explicit opt-in or for a non-Claude
worker. Non-Claude workers retain the ordinary outer workstream protocol;
they never claim native execution. Required native execution without supported
capability holds. Optional unsupported execution reports `unsupported` with
`ordinary_available: true`; this is availability information, not a launch or
an implicit substitution.

Fake probes cover `supported`, `unavailable`, `headless-unsupported` and
probe errors. A future production probe must establish the actual selected
headless runtime's supported invocation and lifecycle hooks. CLI help can
prove that a flag is advertised; it cannot prove native startup. A prompt
keyword, forwarded subagent text or ordinary multi-engine success cannot
satisfy that evidence gate. No CLI flags or environment variables are invented
by the adapter.

All constructor inputs are trusted **server-owned observations**, never child
output or a request body's authority claims. Pin the current project generation,
run ID, workstream attempt, parent managed session, canonical cwd, branch, full
base commit, literal file/directory ownership, attributed ancestry and inherited
guard configuration digest. `Child` narrows scope, retains the same run/base and
derives ancestry from the parent. Every child has a separately allocated managed
session, worktree and branch in the parent's common Git repository. Unaccounted
descendants, including recursive native fan-out, refuse completion. The driver
must route child admission through this contract before launch; inventing an
ancestry edge for a descendant to make it fit is forbidden.

Use one adapter ledger in the server-resolved `data/hiveminds/<id>` directory,
outside project-record DATA_DIR. `native-workflow.json` and its lock are shared
across all parents in the run. Q7's maximum is **four total child admissions
per run**, which also caps concurrent children at four; a run may choose a
smaller cap. Completed, failed, cancelled and uncertain admissions never refund
slots. Replacement attempts share this ledger and cannot reset the budget.
Only the owning server may supply its directory and pins. Keep the ledger for
the life of the run; deleting it destroys reconciliation and accounting evidence.

## Runtime and completion sequence

1. The runtime bridge resolves current parent ownership/generation through the
   existing managed worker services. Construct the pins; select supported
   capability with explicit opt-in. The leaf does not read live manifests or
   create a second ownership/attribution service.
2. `launch(child, opt_in=True)` validates ownership, scope, clean refs, base and
   repository identity. It durably reserves the shared budget and full child
   contract **before** calling the driver. Atomic JSON writes reuse fsync and
   replace; digest validation rejects damage and duplicate keys. Thread and OS
   locks serialize admissions across adapter instances and server processes.
3. The driver's `launch` must use the existing managed runtime, process
   registration, durable lineage, guard/cap hooks and project-generation
   checks. Its observation must be runtime-derived. Calls must be bounded and
   return without waiting for agent completion; no agent polling/wait happens
   inside the ledger transaction. Ambiguous launch holds and consumes its slot.
   Replayed admission never calls launch again, including after restart.
4. `complete(parent)` checks the inventory of **all** native descendants and
   each exact child binding. Live children return `pending`; failed, cancelled,
   orphaned, unaccounted or unavailable children hold. A failed child holds
   even while another is running. An exit alone cannot claim completion.
5. Each completed child must have clean committed output on its pinned branch,
   descended from the pinned base and within its own scope. The injected
   `import_output` must go through existing native standards and landing gates;
   it is not permission to merge with raw Git in production. Fresh Git checks
   require the exact child commit reachable from the clean parent branch and
   every imported commit within parent scope. Reverted out-of-scope edits still
   fail. Persist import intent first; uncertain import holds rather than retrying.
6. Recheck inventory and parent HEAD, then return the parent branch/commit and
   contributing managed sessions for the parent's ordinary handoff. Repeat
   completion revalidates evidence without importing twice. All descendants
   remain authors for outer reviewer independence. This result neither changes
   the hivemind manifest nor signs an integration review.

Driver errors are surfaced in an explicit capability result, durable parent
hold or raised `NativeHeld`; no lifecycle exception is silently swallowed.
Corrupt/missing-runtime evidence never resets a budget or repairs itself into
a pass. Stale parent/child pins and generation drift refuse.

## Cancellation and retention

`cancel(parent)` persists stop intent, attempts every durably owned child even
if an earlier stop fails, and re-observes both individual liveness and complete
inventory. It never stops unknown/foreign children or trusts a PID from child
output. A live, unknown or unavailable child retains a hold. Repeat cancellation
reconciles the same ownership. Replacement of a stopped/completed workstream
rechecks the previous owner's current inventory and observations first.

Cancellation retains committed branches, worktrees, uncommitted output and
the ledger. No deletion/cleanup of user work is supplied by this leaf. A future
production cleanup hook must use existing worktree ownership and runtime-link
unlink rules after full reconciliation. Completion, cancellation and cleanup
cannot release a child budget for reuse. The adapter never creates reviewers;
HR-4/HR-5's zero automatic replacement reviews remain unchanged.

## Verification and remaining pilot

`tests/test_hivemind_native_workflow.py` uses only a recording fake driver and
disposable real Git repositories. It covers capability selection, eight-engine
opt-in behavior, durable pins, shared/concurrent admission, uncertain launch,
restart/idempotency, child failures/orphans, foreign children, cancellation,
replacement reconciliation, damaged state, scope violations, branch drift and
committed nested output reaching the parent while master/remote refs stay fixed.
The fake importer deliberately uses temporary Git; it is not a production driver.

Read-only local inspection is limited to `claude --version` and `claude --help`;
the exact installed version and help boundary belong in the operator's ignored
item journal. No invocation was verified here. Before production wiring or an
`implemented` status, a **separately authorized** pilot must prove actual native
Mode A startup, pre-launch admission/cap enforcement, real inherited guards and
attributed ancestry, all-child inventory, nested work/parent handoff, cancellation
of owned children and safe cleanup. A production driver's imports must preserve
native standards and landing ownership. No real workflow execution, pilot,
merge, push, release or server restart is authorized by this adapter's tests.
