# BUILTINS-3 SR-2: cycle directive and composer

Date: 2026-10-31. Base: `9f0b67fe`. Backlog: `1f443c36`.

Status: branch candidate with **unapplied protected-file patches**. This is not
an integrated role implementation. No merge, push, restart, migration or live
configuration change is included. SR-1's execution block remains in place.

## Contract

`mc/steward_role_task.py` owns engine-independent cycle text. Its positional
`build_cycle_task(project, charter_item, refresh='')` signature remains compatible
with the legacy core entry. The optional keyword `cycle_snapshot` accepts a
frozen `CycleSnapshot` with the already-resolved character name/reference,
schedule/charter IDs and relative journal path. Optional fields carry the current
character body, rules refresh, bounded job instructions, delivery/handoff context
and resolved provider/model/effort for disclosure. It does not resolve engines,
authenticate a caller, establish provenance, filter retrieval or authorize work.
SR-3 must perform those checks before calling it; metadata is not human proof.

The task order is the literal `[Steward cycle]` marker, server-local wake time,
assignment/current charter/canonical journal and bounded job instructions,
delivery disclosures, the complete shared directive, then character voice,
current rules and the supplied permitted refresh. Fallback/handoff text never
precedes the marker. No native skill-discovery mechanism is needed to receive
the directive, regardless of the engine named in the snapshot.

The role charter is re-read by exact ID from the supplied current project using
SR-1 validation. A stale positional charter or legacy objective cannot replace
it. Missing, empty, duplicate or closed role charters raise `RoleError`. Journal
confinement reuses SR-1's canonical registered-root and symlink checks. Job
instructions over 8,000 characters are refused, never truncated. The caller
must reload project/rules/character state each fire; a frozen input object does
not keep an old project dict fresh.

Legacy calls preserve their existing objective precedence and current character,
without writing an assignment or changing schedules. Their deterministic journal
is `docs/_journal/<charter-id>-steward.md` under the registered canonical root.
They produce explicit hold instructions when the charter/root is unavailable or
closed. Existing legacy history is not moved or deleted. A migrated role supplies
its existing journal path explicitly; the pilot's journal mapping belongs to SR-5.

`data/skills/builtin/mc-steward/CYCLE_DIRECTIVE.md` is the sole canonical cycle
procedure after the resource patch is applied. It is loaded afresh on each call
from the bundled source, not the installed/user-edited skill. Missing, unreadable
or empty directive data raises `RoleError`; there is no stale-prose fallback.
The builtin skill becomes an entry linking to this file. The frozen-build spec
bundles that skill directory. Existing checksum behavior preserves user-edited
skills, while the generated task still carries the current mandatory directive.

The directive replaces backlog-note/self-rearming instructions with journal and
reply-first procedure. It retains spawner/chat/root-email routing, existing human
proof, untrusted digest handling, configured daily caps/dedupe, unknown-delivery
handling and the learning/guard boundaries. These are instructions only here;
SR-4 owns state and communication enforcement. No mailbox or provider is contacted.

## Protected edits and exact integration

The dispatch explicitly required a patch for human-owned `steward/core.py`.
The unattended hook additionally rejected writes to the builtin skill and
directive as human-owned learning-loop artifacts and prohibited retrying those
writes in this guarded session. They were not applied by another tool.

- `docs/patches/steward-role-core-adapter.patch` only replaces the existing core
  composer with a delegation preserving positional calls plus the snapshot keyword.
- `docs/patches/steward-role-directive.patch` replaces the stale builtin skill and
  adds the canonical resource. It neither installs/promotes a skill nor changes guards.

From a fresh human-started chat or the authorized human checkout, after landing
the branch, review and run these literal commands from the repository root:

```powershell
git apply --check docs/patches/steward-role-directive.patch docs/patches/steward-role-core-adapter.patch
git apply docs/patches/steward-role-directive.patch docs/patches/steward-role-core-adapter.patch
python -m pytest tests/test_steward_role_task.py tests/test_steward_core.py tests/test_steward_refresh.py -q
pyright mc/steward_role_task.py
```

Do not apply the core patch without the directive resource: composition will
fail visibly. Re-run the full suite after authorized integration. Any conflict
must be reviewed; do not force-apply or modify human-owned code from this session.
Before that integration SR-2 is incomplete. SR-3 must still converge timer,
Run Now, live append/revival and successor preparation, correct the legacy
scheduler's preceding continuation header, enforce guard readiness and pass
`consumer_unattended=True` at every retrieval seam. This ticket does not do so.

Rollback before activation is to revert this branch and any separately applied
integration commit together; do not remove a resource while leaving the adapter
active. Preserve all existing project data, charters, journals and schedules.

## Verification

`tests/test_steward_role_task.py` applies both patches only to disposable copies
outside the checkout, then exercises the resulting resource and adapter function.
It checks task ordering, unchanged marker equality with both learning/fence
consumers, current charter authority, identity/body/rules retention, eight engine
metadata values, bounds/errors, canonical journal paths, legacy behavior,
resource failures/reload, builtin checksum preservation and the frozen data entry.
These are composer/patch tests, not eight live provider runs or proof that the
protected files were integrated. Existing core/scheduler tests run against the
unchanged checkout core unless explicitly exercising the disposable adapter.

Initial results: 40 new checks passed, rc=0; selected core/refresh/role/marker/
learning-safety suite passed 186 checks, rc=0. `pyright mc/steward_role_task.py`
reported 0 errors, rc=0 using the installed standalone pyright 1.1.410. The
venv has no `python -m pyright` module; that invocation returned rc=1.

The full suite completed in 2,116.142 seconds with **18,521 passed, 9 failed,
54 skipped and 1 expected xfail; rc=1**. JUnit independently records 18,585 cases,
0 errors and all 40 SR-2 checks passing. This is a failing full run, not a green
subset substituted for it. Command:

```powershell
python -m pytest -q -p no:cacheprovider tests --junitxml=_scratch/sr2_full_suite.xml
```

Full log, XML and captured pytest return code are `_scratch/sr2_full_suite.txt`,
`_scratch/sr2_full_suite.xml` and `_scratch/sr2_full_suite.rc`. The foreground
runner registered pytest PID 22620. After pytest exited, its final tail-print
attempt raised a UTF-8 decoding error on a Windows-encoded log character; the
pytest return code had already been saved and JUnit was intact. The result above
comes from that JUnit plus the saved return code, not the tail-print exception.

All nine failing node IDs were replayed on a detached disposable worktree of
the exact dispatch base `9f0b67fefa053a1c7f181a40e177091008e05967`, using the same
interpreter. **All nine fail there too, with identical failure messages; rc=1**.
Evidence: `_scratch/sr2_baseline.txt`, `_scratch/sr2_baseline.xml` and
`_scratch/sr2_baseline.rc`. This replay is a baseline diagnostic, not a second
full-suite run. The affected runtime/native-MCP source and these existing tests
are unchanged by SR-2. No unrelated MCP/test repairs were made.

| Existing failing area | Cases | Observed failure |
|---|---:|---|
| `test_builtins_resume_context.py`, incognito Codex | 1 | Mock `SimpleNamespace` Popen lacks the context-manager protocol when native MCP config is probed. |
| `test_native_followup_context_routes.py`, Codex followup/interrupt dispatch and incognito | 4 | Same Popen protocol error, either raised directly or recorded as a route/session error. |
| `test_qwen_live_pass_2026_09_19.py::TestQwenMcp::test_other_providers_unchanged` | 1 | Expects empty Codex MCP configuration but receives resolved fixture servers. |
| `test_workflow_step_reconcile.py`, three Codex workflow-wake checks | 3 | Dispatch refuses unresolved native Codex MCP names; test then accesses missing `session_id`. |

The baseline replay used these exact node IDs:

```text
tests/test_builtins_resume_context.py::test_incognito_keeps_only_existing_tool_protocol[codex]
tests/test_native_followup_context_routes.py::test_runtime_dispatch_primes_stash_for_first_route_followup[codex-followup]
tests/test_native_followup_context_routes.py::test_runtime_dispatch_primes_stash_for_first_route_followup[codex-interrupt]
tests/test_native_followup_context_routes.py::test_incognito_never_refreshes_stash_or_retrieves_memory[codex-followup]
tests/test_native_followup_context_routes.py::test_incognito_never_refreshes_stash_or_retrieves_memory[codex-interrupt]
tests/test_qwen_live_pass_2026_09_19.py::TestQwenMcp::test_other_providers_unchanged
tests/test_workflow_step_reconcile.py::test_codex_step_advances_the_run_through_the_live_hook
tests/test_workflow_step_reconcile.py::test_codex_step_advances_through_the_reconciler_when_the_hook_is_missed
tests/test_workflow_step_reconcile.py::test_a_raise_before_the_wake_still_wakes_the_workflow
```

Frozen macOS build and fresh-VM installer tests, live engines, real communication,
scheduler delivery and protected-file integration remain unverified. There are
no UI/static changes: USER_GUIDE, README, frontend smokes and global agent-rule
changes are not needed. The local candidate is not shipped or activated.
