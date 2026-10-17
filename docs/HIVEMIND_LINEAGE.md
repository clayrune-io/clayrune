# Hivemind worker dispatch lineage (MC-1107)

Hivemind workers inherit the managed session that created the hivemind as their
spawner. A branch owner cannot obtain an independent merge review by creating a
hivemind and asking its worker to review that owner's branch.

## Identity and persistence

- `mc/hivemind_lineage.py` captures the creating request through
  `dispatch_lineage.attributed_caller_session`: loopback peer port, owning PID,
  process ancestry and managed roots. JSON identity fields and Origin do not
  select the spawner.
- Creation saves `created_by_session_id` at the manifest's top level. The PUT
  allowlist does not change it; nested config fields do not affect it.
- Every later manual spawn, automatic tick and retry reads the saved creator.
  Workers carry `_spawned_by` in both their live session and runtime metadata.
  The metadata matters because Claude's dispatch bridge rebuilds the session.
- Before runtime launch, an attributed worker gets a minimal pending agent-log
  row with `dispatched_by_session_id`, its trigger and project generation. The
  ordinary pending writer excludes housekeeping sessions. This module uses
  the existing serialized log transaction without triggering memory or usage
  turn-start processing. A failed write prevents launch; completion replaces
  the pending row through the existing writer.
- No `_notify_session` callback is armed. The review guard and chat banner
  already read `_spawned_by` / `dispatched_by_session_id`; their code is unchanged.

Human/UI creation, unavailable OS attribution, and legacy manifests record no
creator. There is no retroactive inference from an orchestrator, later spawn
caller, notification target or request body. Existing attribution limitations
(including detached caller process ancestry) remain those of dispatch lineage.
The hivemind id itself is not a managed session and is not inserted into ancestry.

`mc/hivemind_integration.py` merges branches and does not spawn sessions; it
requires no change. The route module only wires the new concern; agent_routes
does not grow. No UI assets or approval gates change.

## Verification

`tests/test_hivemind_lineage.py` exercises real creation/spawn handlers with fake
OS verdicts and provider runtimes, plus the real Claude dispatch bridge with fake
process launch. It checks spoofed fields, protected creator identity, manual and
automatic workers across providers, durable ancestry, log-write refusal, legacy
manifests, and idempotent recording. `tests/test_review_lineage.py` also covers the
owner -> creating session -> hivemind worker chain and an unrelated worker.

Validated on the build branch:

```text
python -m pytest tests/test_review_lineage.py tests/test_dispatch_lineage.py tests/test_hivemind_lineage.py tests/test_hivemind_routes.py tests/test_hivemind_worktree_isolation.py tests/test_hivemind_worktree_dispatch_hook.py tests/test_hivemind_worker_model_default.py tests/test_hivemind_failure_outcomes.py
# 145 passed
pyright mc/hivemind_lineage.py
# 0 errors, 0 warnings
git diff --check
# passed
```

No live hivemind is launched by these checks. Deployment and live banner checks
remain with the integrating session after merge and an approved server restart.
