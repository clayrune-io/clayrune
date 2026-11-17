# Agent Mail Worker review, pass 05a (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `f0373733`.
Review only: no source file was changed. Every line cited was opened. A cost or
a wrong result is marked **measured** (a scratch script against the pure
function, run with Node outside the repo tree) or **read, not measured**.
`server` was never imported, the live server was sent nothing, no model call
was made, no mail was sent, no external service was called, and no process
that binds a port was started.

Scope: everything under `workers/agent-mail/` (Cloudflare Worker: 62 source
files, about 10,000 lines; 6 migrations; 85 test and fixture files, about
19,600 lines). Source first, tests at structure level.

Finding-id prefix for this pass: **W**.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology,
**F** feature does not work. Severity, same scale as passes 1 to 3:
**blocker** = wrong result or lost data on a normal path today, or a security
boundary that one ordinary request gets around; **high** = wrong result or lost
data on a reachable path, or a cost paid on every request; **medium** =
narrower trigger, or a latent defect behind a setting that ships off;
**low** = hygiene.

STATUS: IN PROGRESS. Written incrementally. The ranked summary, feature table
and streamlining section at the top are filled in last; until then, the
"Working notes" section at the bottom is the live record. If this line is
still here, the pass did not finish: continue from "Progress log".

## Ranked summary

(written last)

## Feature table (lens 4)

(written last)

## Streamlining

(written last)

## Findings in detail

(candidates are appended under "Working notes" as they are found, then moved here)

## Prior reviews

(written last)

## Coverage

(written last)

## Working notes

### Progress log

- 2026-11-16: report created. Prior pass summaries (01, 02, 03) read: none
  covers `workers/agent-mail/`. Next: design docs, then source in dependency
  order.

### Candidate findings

