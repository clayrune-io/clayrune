# Pace-aware engine routing (MC-1071)

## Contract

Fresh character dispatches through `POST /api/project/<project_id>/agent/dispatch`
can use a human-configured alternate before a provider runs out of weekly
allowance. The pure policy lives in `mc/engine_pace.py`; cached usage reads and
character resolution live in `mc/engine_pace_dispatch.py`. The existing dispatch
route supplies only a small hook; allowance and model gates still run afterward.

The weekly window is the seven days ending at the provider's `resets_at`.
Elapsed percent = `100 * (1 - (resets_at - now) / 7 days)`. Reroute when utilization
is strictly greater than elapsed percent plus the margin, or strictly greater
than the ceiling. Equality stays with the original. Each dispatch recomputes the
policy: back on pace or an elapsed reset clears the reason automatically.

Config keys are accepted by the existing human-only, passcode-protected settings
API. There are no new Settings UI controls in this slice:

| Key | Default | Meaning |
| --- | --- | --- |
| `engine_pace_enabled` | `true` | Disable all pace routing with `false`. |
| `engine_pace_margin_points` | `5` | Allowed lead over elapsed window percent. |
| `engine_pace_ceiling_percent` | `85` | Weekly utilization ceiling. |
| `engine_pace_alternates` | `{}` | Map original `scope:name` to alternate `scope:name`. |

Mappings are install data, never seeded in tracked config. No mapping means no
swap or usage read. One dispatch considers exactly one alternate; no recursive
chain. Explicit provider/model/effort overrides retain the existing engine-pick
precedence. Resumes and explicit cross-provider handoffs are excluded. Only this
HTTP dispatch route is hooked; schedule/workflow/internal dispatches are outside
this slice. A project-default character participates when no character is named.

Usage comes from the same Claude seven-day OAuth cache and Codex weekly rollout
reader used by `/api/system/usage`; it never calls that HTTP route or aggregates
token history. Unknown providers, missing/invalid usage, timezone-free reset
timestamps, elapsed resets and reset dates over seven days away keep the original
choice and log the unavailable reading. A deleted/unresolvable alternate or read
exception also keeps the original ask and logs why. Invalid numeric settings
produce no swap. The alternate's normal dispatch gates remain authoritative.

Successful reroutes add `rerouted_from` and `reroute_reason` to the JSON response.
These fields are absent on ordinary dispatches. A display-task prefix names both
characters and the reason, preserving the actual model input and the spawner
callback. It reaches existing chat/log/task displays. There is no dedicated Floor
label: its existing 110-character task summary can clip this prefix. A complete
Floor reason/badge needs a separate UI change and was deferred under Dave's scope
limit; the complete reason is in the dispatch response and full task text.

## Verification and delivery

Tests cover pace/ceiling equality, custom thresholds, disabled/no/self/stale
alternate, missing/malformed usage, reset recovery, read exceptions, cached usage
sources, explicit engine pins/resume exclusions, unchanged response shape,
settings round-trip, and a real dispatch-handler-to-runtime seam selecting the
alternate's provider/model/persona with the spawner callback preserved.

This is a branch-only implementation. No live settings changes, dispatches,
merge, push, server restart or live UI walkthrough were performed.

Validation on 2026-10-07:

```text
python -m pytest tests/test_engine_pace.py tests/test_engine_pace_settings.py tests/test_engine_fallback.py tests/test_allowance_dispatch_refusal.py tests/test_character_persona.py tests/test_character_engine_fallback.py tests/test_codex_weekly_usage.py tests/test_settings_routes.py tests/test_settings_routes_unattended_gate.py tests/test_human_proof_guard.py tests/test_claude_dispatch_hook_contract.py
272 passed in 14.80s; rc=0

pyright mc/engine_pace.py mc/engine_pace_dispatch.py
0 errors, 0 warnings; rc=0

git diff --check
rc=0
```

The first `python -m pyright` attempt returned rc=1 because this worktree's
Python environment has no Pyright module. The installed `pyright` executable
ran the same two-file check successfully; no dependencies were installed.
Frontend boot smokes are not applicable: no HTML, JavaScript or CSS changed.
`USER_GUIDE.md` was updated; README, agent instructions and rules need no changes
because installation, permissions and contributor conventions are unchanged.

## Task checkpoint

The required home-directory plan write was refused by the unattended tool fence
as out of project scope. This document records the implementation and handoff
within the worktree instead. No global configuration was modified.
