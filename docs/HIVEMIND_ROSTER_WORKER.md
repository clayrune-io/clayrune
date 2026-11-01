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
`resolved_assignment`, plus `character_snapshot`, `pinned_prompt_path`,
`pinned_prompt_sha256` and
`current_agent_session_id`. These are runtime data under the existing hivemind
directory, not project-record sidecars. Live and runtime metadata carry the
same roster identity. A pending agent-log row persists the identity/reference
before any runtime launches, including for a human-created run without a
parent. Only the manifest's OS-attributed creator supplies worker lineage.
Completion retains the same recovery fields.

The complete UTF-8 prompt is stored once per attempt at
`workstreams/worker-<session_id>.prompt.txt` under that hivemind's run directory.
The writer flushes and fsyncs a temporary file in the same directory, then
atomically replaces the destination before saving ownership or launching.
Pending and completion log rows contain only the relative path and SHA-256;
the live session retains `_system_prompt` for live respawns. Workstream JSON
also uses the reference instead of duplicating the prompt. Prompt files must
remain available for retained attempt recovery; this change adds no deletion
or migration of existing user data.

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

Cold recovery always reads the referenced file, confines its resolved path to
the run directory and verifies the digest before dispatch. Missing references,
unreadable files, path escape and digest mismatch refuse recovery even if an
old log row still contains an inline `_system_prompt`. Live context rebuilds
can read the same file when the in-memory prompt is absent.

`RosterRecoveryHeld` carries the refusal through both revive helpers. Both
`/agent/send` and `/agent/followup` return HTTP 409 with `ok: false` and the
specific hold reason. They do not try transcript/default-persona fallback or
allocate a session on a preflight hold. Unexpected roster-adapter errors also
stop fallback; an uncertain runtime launch retains its existing diagnostics.
The parent-callback path routes roster workers of every provider through the
same pinned adapter and reports a hold as `DeliveryBlocked`; it never dispatches
an ordinary non-Claude session. Existing uncertain-parent status gates remain.

## Validation and rollback

`tests/test_hivemind_roster_worker.py` uses recording runtimes for all eight
registered providers and real ordinary context. Its Claude cases cross the
real runtime/server bridge in a temporary git repository with fake Popen.
No live model call, named browser profile or operator roster is used.

Branch validation (2026-11-01):

| Check | Result | rc |
|---|---|---|
| Worker test file, including send-back regressions | 75 passed | 0 |
| All `tests/test_hivemind*.py` | 284 passed | 0 |
| Agent send/followup/revive and delegation tests | 435 passed, 1 skipped | 0 |
| Full suite, send-back | Running; result pending | pending |
| Pyright `mc/hivemind_worker.py` | 0 errors | 0 |

The previous HR-2 commit's full suite passed 18,674 tests with 54 skips and one
expected failure (the human-owned fence-timeout patch check). That result is
not the send-back's full-suite result. The focused delegation skip requires
an explicit `MC_TEST_PROCESS_REGISTER_URL` for its full-startup subprocess
harness. Installed vendor and real-login/model tests retain their opt-in skips. The dispatch
blueprint retains the same 15 type diagnostics as the requested source base;
the new module adds none.

The row-size regression uses a 49,000-byte project-context fixture plus the
worker directive and serializes the same row with UTF-8, `ensure_ascii=False`.
The old shape is reconstructed by replacing the new references with the full
prompt, so these are comparable row sizes, not operator-log measurements:

| Row | Before (bytes) | After (bytes) | Saved (bytes) |
|---|---:|---:|---:|
| Pending | 52,482 | 1,594 | 50,888 |
| Completion | 53,131 | 2,243 | 50,888 |

The added checks exercise every original recovery-hold reason at both HTTP
routes, file loss/tampering/path escape, the second revive helper, successful
Claude/Codex route recovery, parent callbacks and atomic write/sync failure.
Held preflight cases assert zero runtime calls and no session-map changes.

Run from the candidate checkout:

```powershell
python -m pytest tests/test_hivemind_roster_worker.py
python -m pytest (Get-ChildItem tests/test_hivemind*.py | ForEach-Object FullName)
python -m pytest (Get-ChildItem tests/test_*.py | Where-Object { $_.BaseName -match 'agent_send|followup|reviv|resume_revival|agent_routes|delegation' -and $_.BaseName -notmatch 'desk_connect' } | ForEach-Object FullName)
python -m pytest
pyright mc/hivemind_worker.py
```

Runtime execution against installed CLIs is unverified; recording runtimes
prove the assignment boundary, and the real Claude bridge test proves
session/worktree adoption.

Rollback: revert this commit to restore HR-1's temporary roster launch refusal;
retain persisted manifests, attempt records and worktree branches. Do not
delete user work or enable legacy execution for roster rows.
