# Hivemind roster worker adapter (BUILTINS-4 HR-2)

This is a branch implementation of HR-2 in
`BUILTINS_4_HIVEMIND_ROSTER_SPEC.md`, following section 8 decisions Q1/Q2/Q4.
It does not implement reviewer records, reviewer launch, reviewed integration,
native workflows, or the later UI/routing tickets. It does not establish that
the whole BUILTINS-4 feature is ready for production.

## Ownership and launch

`mc/hivemind_worker.py` owns worker context, attempt preparation, launch policy
and recovery. The blueprint keeps its existing data helpers and
`_hm_runtime_dispatch` runtime/session ownership boundary. Its old worker
context/spawn bodies are extracted into the leaf; non-roster calls retain
their legacy engine fallback and prompt/isolation behavior.

Each new roster attempt reads the current global character once, passes that
record through HR-1 `resolve_assignment`, and composes ordinary project context
with the workstream directive. The character body, name, avatar, skills, rules,
and current handoff reach every runtime. The directive omits the universal
capabilities/pointer block already supplied by ordinary context. The
server-owned task prefix `You run unattended` reaches the existing task-only
memory/exploration read-floor filters. Learning eligibility is unchanged.

Manual spawn, scheduler admission, retry and reconciled restart use the same
adapter. A per-workstream lock re-reads persisted ownership before preparing a
new attempt. An existing live or uncertain owner refuses another launch.
Git projects require an isolated worktree and a readable base SHA; failure
holds rather than running in the shared checkout. Non-git analysis can run
without a branch. Worker output stays on the `hm_` branch for integration.

Before dispatch, the workstream records HR-1 `assignment_attempt` and
`resolved_assignment`, plus `character_snapshot`, `worker_context` and
`current_agent_session_id`. These are runtime data under the existing hivemind
directory, not project-record sidecars. Live and runtime metadata carry the
same roster identity. A pending agent-log row persists the identity/context
before any runtime launches, including for a human-created run without a
parent. Only the manifest's OS-attributed creator supplies worker lineage.
Completion retains the same recovery fields.

## Claude and recovery

The private `PreparedContext` argument is accepted only by internal calls. The
existing Claude bridge adopts the supplied session/cwd and consumes the
prepared character, resolved engine and context once. It uses the ordinary
Claude flags, turn cap, launch guard and process registration. It does not run
another character lookup, context build, auto-router or engine fallback.
Quota/allowance refusals remain visible and never replace the assigned engine.
Ordinary non-roster Claude calls keep their previous path.

Same-attempt respawns use the pinned context even if the roster or context
fingerprint changes. Cold recovery from a terminal agent-log row validates the
manifest generation, workstream attempt/owner, pinned identity and original
cwd/branch before using the same runtime dispatch seam and provider-native
session id. It neither creates another tree nor re-resolves today's character.
An in-progress/unknown launch, missing context/tree, changed generation or
replacement attempt holds for reconciliation. A new attempt resolves the
current roster again, as Q2 requires.

## Validation and rollback

`tests/test_hivemind_roster_worker.py` uses recording runtimes for all eight
registered providers and real ordinary context. Its Claude cases cross the
real runtime/server bridge in a temporary git repository with fake Popen.
No live model call, named browser profile or operator roster is used.

Branch validation (2026-11-01):

| Check | Result | rc |
|---|---|---|
| New worker test file | 45 passed | 0 |
| All `tests/test_hivemind*.py` | 254 passed | 0 |
| Full suite | 18,674 passed, 54 skipped, 1 xfailed | 0 |
| Pyright worker and lineage modules | 0 errors | 0 |

The expected failure is the existing human-owned fence-timeout patch check.
The known live Chromium first-frame timeout did not recur. Installed vendor
and real-login/model tests retain their existing opt-in skips. The dispatch
blueprint retains the same 15 type diagnostics as the requested source base;
the new module adds none.

Run from the candidate checkout:

```powershell
python -m pytest tests/test_hivemind_roster_worker.py
python -m pytest (Get-ChildItem tests/test_hivemind*.py | ForEach-Object FullName)
python -m pytest
pyright mc/hivemind_worker.py
```

Runtime execution against installed CLIs is unverified; recording runtimes
prove the assignment boundary, and the real Claude bridge test proves
session/worktree adoption.

Rollback: revert this commit to restore HR-1's temporary roster launch refusal;
retain persisted manifests, attempt records and worktree branches. Do not
delete user work or enable legacy execution for roster rows.
