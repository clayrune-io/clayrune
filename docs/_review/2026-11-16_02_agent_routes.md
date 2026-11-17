# Agent routes and session lifecycle review, pass 2 of 8 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `40fcd230`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, or a read-only
count) or **read, not measured**. `server` was never imported, the live server
was sent nothing but `GET`, and no process that binds a port or launches a
provider CLI was started.

Scope: `mc/blueprints/agent_routes.py`, `mc/agent_worktree.py`,
`mc/delegation_delivery.py`, `mc/delegation_settlement.py`,
`mc/delegation_transcript_reconcile.py`, `mc/delegation_waiting.py`,
`mc/delegation_wake.py`, `mc/delegation_wake_absent.py`,
`mc/delegation_retry.py`, `mc/merge_review_gate.py`, `mc/cold_review.py`,
`mc/cold_review_gate.py`, `mc/cold_review_runner.py`,
`mc/engine_pace_dispatch.py`, `mc/behavior_tail.py`,
`mc/session_mcp_config.py`, `mc/session_mcp_launch.py`,
`mc/session_provenance.py`. `mc/agent_runtime.py`, `mc/core.py` and
`server.py` were pass 1 (`docs/_review/2026-11-16_01_runtime_core.md`); its
findings are not repeated, and where a finding here is the call-site half of
one of them the pass 1 id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology.
Severity, same scale as pass 1: **blocker** = wrong result or lost data on a
normal path today; **high** = wrong result or lost data on a reachable path, or
a cost paid on every turn/dispatch/poll; **medium** = narrower trigger, or a
latent defect behind a disabled flag; **low** = hygiene.

Written incrementally, one commit per section. The ranked summary and the
coverage statement are written last; until they exist, this file is a partial
report and the last section present is where the pass stopped.

## Ranked summary

_Written at the end of the pass. Findings are in the sections below._

---

