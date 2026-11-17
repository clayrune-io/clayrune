# Orchestration and scheduling review, pass 9 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `3f51b0e0`.
Review only: no source file was changed. Every line cited was opened. A cost or
a wrong result is marked **measured** (a scratch script against the pure
function, or a read-only count) or **read, not measured**. `server` was never
imported, the live server was sent nothing but `GET`, no model call was made,
no mail was sent, and no process that binds a port or launches a provider CLI
was started.

Scope: in `mc/` and `mc/blueprints/`, every module whose name starts with
`hivemind`, `scheduler`, `schedule`, `steward`, `coordination`, `automation`,
`wake`, `question`, `limit`, `allowance`, `usage`, `engine` or `model`, except
`mc/engine_pace_dispatch.py` (read fully by pass 2). 47 files, about 19,400
lines. Earlier passes (`docs/_review/2026-11-16_01` to `_04`) are not repeated;
where a finding here is the other half of one of theirs, the id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology,
**F** does the feature work. Severity, same scale as passes 1 and 2:
**blocker** = wrong result or lost data on a normal path today; **high** =
wrong result or lost data on a reachable path, or a cost paid on every
turn/dispatch/poll; **medium** = narrower trigger, or a latent defect behind a
disabled flag; **low** = hygiene.

Finding ids in this pass start with `OS`.

STATUS: IN PROGRESS. Written incrementally. The ranked summary, feature table
and streamlining section are assembled last from the candidate findings below;
until then read "Candidate findings" and "Progress log".

---

## Ranked summary

(assembled last)

## Feature table (lens 4)

(assembled last)

## Streamlining

(assembled last; candidates are collected under "Streamlining candidates")

---

## Candidate findings (in the order found)

## Streamlining candidates

## Sound (notes)

## Progress log

- Files read fully so far: none.
