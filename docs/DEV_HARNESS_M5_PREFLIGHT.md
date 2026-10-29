# DH-M5 preflight: decision required before implementation

MC-960 / d813ea78. Inspected at base `48b5aa40` on
`clayrune/agent/10d099861510`, 2026-10-29. This is a preflight finding,
not an implemented integration prerequisite. No production code changed.

## Verified interfaces

- M2 importer: `mc/dev_harness_sources.py` creates a persisted guidance install
  plan, requires existing human proof, and dispatches the approved plan through
  `agent_routes.agent_job_start(..., job_data=...)`. Its plan contains guidance
  resources, not native execution steps.
- M3/M4 jobs: `run_managed` accepts an internal `steps` array and process
  registration callbacks. `make_step` compiles explicit launcher, arguments,
  targets, configs and tool roots. The runner freezes B/H/M/T, executes through
  existing jobs and disposable worktrees, and persists runner receipts with
  `authorizes_merge=false`. No repository/HTTP report grants merge permission.
- Policy: `resolve_policy` accepts profile, practice overrides and three digest
  pins only. Neither the active snapshot nor a trusted plan reader supplies
  approved native steps or a practice-to-step applicability mapping.
  `native_rules` is a digest, not a resolvable execution plan.
- Coverage: all 22 shipped practice descriptors still declare Unsupported
  deterministic/reviewer coverage. `validate_enforcement` rejects any required
  practice with that coverage. Native recipe availability does not upgrade it.
  The activation tests deliberately use synthetic future descriptors.
- Worktrees: `create` and `remove` use `agent_worktree._lock(project_id)`;
  `merge_back` does not. Holding this non-reentrant lock around the runner would
  deadlock when the runner calls `create`/`remove`. Hivemind `_merge_one` also
  has no shared integration lock. `project_sync`'s lock serializes fetch/status,
  not these merges. A shared landing serialization seam must be established.
- Hivemind currently merges each worker, runs its named smokes immediately,
  rolls back a failing merge, and retains source branches. Worker-to-integration
  merges bypass the separate base-branch review gate; that exemption must never
  exempt standards. Final integration-to-base gating must be wired as well.
- Manual preflight is `tools/merge-review-check.py`, not an agent_routes
  preflight. It has exact-commit review checks but explicitly is not a lock.
  Hook mode can already be inside a merge, so its evaluation must not silently
  substitute a clean HEAD for the pending candidate/index.
- Harness routes currently provide catalogue, read, install-plan, install and
  activate. Run/read-run/preview dispatch does not yet exist to extend. The
  session-job owner is reusable, but the harness dispatch contracts need adding.

## Decision for Dave

The brief requires stopping on a real design choice. No command source,
approval authority, capability upgrade or activation shortcut was invented.

**A — Explicitly extend the prerequisite scope (recommended).** Define a
trusted, immutable native-check execution plan bound to the active
`native_rules` digest, with explicit check selection/applicability and required
versus warning behavior. Specify how the existing human activation gate
approves that exact plan without claiming the full Unsupported practice
contracts are delivered. Authorize the resulting policy/store/activation tests
and small shared landing-lock seam, plus the absent preview/run dispatch.
Cost: expands beyond the stated new coordinator and composition-only owners;
requires policy/approval contract review before code. No live activation needed.

**B — Keep M5 strictly internal and defer the missing configuration contract.**
Build/test a coordinator with injected approved steps and synthetic policy
fixtures, fail closed when real projects lack that plan, and put production
plan selection/approval and supported-subset activation in a separate ticket.
Cost: composition and candidate tests can proceed, but M5 is not a usable
managed prerequisite for real configured projects until that ticket lands;
M6 cannot call it complete on fixture evidence alone. Shared landing
serialization and missing dispatch owners still need explicit scope correction.

Recommendation is A because it makes approval of executable configuration
reviewable before managed merges can execute it. B is honest scaffolding if
Dave intentionally chooses sequencing over completing the usable gate here.
Neither option relaxes human-only activation, existing independent review,
Unsupported coverage, CI provenance, or release gates.

## Validation

The required existing test selection is baseline evidence only:

```text
python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs"
806 passed, 1 skipped, 16681 deselected, 2 warnings in 238.74s (0:03:58)
rc=0
```

The skip is `test_dev_harness_diff.py:67` (Windows rejects newline filenames).
The two collection warnings are existing invalid escape sequences in
`test_agent_routes.py:2360` and `test_process_sweep.py:510`.
The foreground verification child PID was registered immediately (HTTP 200)
and has exited normally. `git diff --cached --check`: rc=0.

No new module exists to type-check; no
static assets changed, so frontend smokes and user-guide changes do not apply.
No merge, push, restart, installation, activation or live config mutation.
