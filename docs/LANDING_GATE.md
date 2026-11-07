# Agent landing verification (MC-1164)

Branch candidate. No live setting, merge, push or server restart is part of
this change. Existing native standards and human merge review remain in force.

## Project settings

`verify_command` is either one shell command string or an ordered list of
1..128 shell command strings. Missing, null, empty string or empty list retains
the historical merge behavior, including its `(status, string)` return value.
Other malformed values block; they do not disable verification.

`verify_timeout_seconds` is a positive finite number, default **1200 seconds
per command**. Both keys use the existing human-only project safety controls.
The existing project update API stores them; this change adds no settings UI
or default commands. Nothing is enabled on an existing project automatically.

For example, a project record can contain:

```json
{
  "verify_command": ["python -m pytest tests", "node tools/smoke/boot-smoke.mjs"],
  "verify_timeout_seconds": 1200
}
```

Commands use the platform shell (`cmd.exe` on Windows, `/bin/sh` on POSIX),
the agent worktree as cwd and the existing environment. Commands are not split
into guessed subcommands or translated between shells. Shell constructs can
mask exit codes, so configure commands that propagate their actual failures.
Use foreground, read-only checks; do not launch a server or detached daemon.
Tests that read a live server only establish that server's behavior, not the
combined source checkout. Prefer hermetic checks.

## Landing sequence and failure comparison

The existing repository-wide landing lock covers the whole operation. Existing
dirty-tree, review and native standards refusals remain. Before the actual
target merge, `mc/landing_gate.py` resolves the target and agent SHAs and uses
`git merge-tree --write-tree` to build the combined tree without moving refs.
A synthetic commit makes that tree checkoutable, without creating a branch.

The existing agent checkout is temporarily detached at the target tip for the
baseline, then at the synthetic combined commit for the candidate. This keeps
the existing runtime links and dependencies. It restores the original agent
commit and symbolic branch before landing; neither branch ref moves during
verification. The main target checkout is not used to run commands. There are
no nested disposable worktrees, dependency installs, resets or cleans.

Each phase runs all configured commands in order, including after a failing
exit. Results contain actual exit code, parsed signatures, infrastructure
error and the last 12,000 output characters. Up to 32 MiB of combined stdout
and stderr is parsed per command; larger output is an explicit blocking error.
Output is temporarily spooled outside the repository and deleted after reading.
Processes register immediately with the existing process manager. Timeout
uses the existing owned process-tree termination helper, then reaps the child;
Windows also retains and closes the child job handle. As with that existing
helper, Windows job assignment has a short post-spawn race. Detached children
are unsupported, and POSIX daemon cleanup is not independently verified here.

Nonzero command exits normalize to:

- `pytest:<nodeid>` from pytest `FAILED` / `ERROR` summaries, including module
  collection errors and parameter IDs containing spaces.
- `smoke:<check name>` from the repository's cross-mark and `FAIL` lines;
  aggregate failure counts are excluded when check names are present.
- Otherwise `exit:<code>:<last error line>`, falling back to the last nonempty
  output line or `<no output>`. ANSI colors and checkout paths are normalized.

Signatures are scoped by command position. Candidate signatures absent from
the baseline block. Baseline failures are reported even when the candidate
fixes them. A matching nonzero baseline/candidate failure allows landing.
Timeouts, unavailable execution, output overflow, invalid configuration,
changed refs/checkouts or failed restoration always block; they are not waived
as pre-existing findings. A successful shell exit has no failure signatures.

Baselines use a process-local LRU cache (64 entries), keyed by repository
common directory, target commit SHA, exact command list, timeout and environment
digest. Raw environment values are not stored in cache keys or reports. Output
tails are verifier output: do not print credentials or put them in commands.
Infrastructure errors
and dirty baselines are not cached. A new target commit or changed configuration
requires a fresh baseline; server restart clears the cache. Runtime/dependency
changes without an environment or commit change are outside this cache identity.
Use deterministic checks and stable runtime dependencies.

Tracked verifier output is preserved and blocks landing. Normal checkout can
refuse restoration if that output conflicts with the agent tree; the result
then explicitly reports `checkout_restored: false`, preserving the detached
checkout for inspection. It never force-checks out or deletes files to conceal
that failure. Newly produced untracked/ignored output is also left in place.
Verifier commands are trusted local project configuration, not a sandbox.

## Result and extension seam

For configured verification, `merge_back` still returns a two-element tuple:

```text
('blocked', {'landing_gate': <report>})
('clean', {'message': <git output>, 'landing_gate': <report>})
('conflict', {'message': <git output>, 'landing_gate': <report>})
```

The report carries `status: pass|blocked`, `new_failures`, `baseline_failures`,
`errors`, per-command `baseline` / `candidate` results, SHA/tree pins, cache
status and restoration status when available. Earlier historical refusals
retain their existing return shapes. The full gate report is logged. The
session-end handler surfaces blocked landings and keeps the branch/worktree;
stale GC does not cache a blocked verdict, so a later retry can run again.
After verification, existing candidate/standards pins are revalidated before
the real merge. No new gate bypasses the existing review or human-only controls.

`verify(..., reviewer=callback)` is the MC-1165 extension point. It receives the
current report, including target/incoming SHA and combined tree. Only an explicit
`{"status": "pass"}` allows its part of the gate; missing, unavailable or raised
verdicts hold. A reviewer pass cannot erase command failures. No cold reviewer,
model call, reviewer configuration or reviewer dispatch is implemented here.

## Verification

`tests/test_landing_gate.py` exercises parsing, old/new comparison, command
position isolation, actual combined-tree regressions, ref preservation, successful
landing with known failures, historical unset behavior, timeout, process
registration/cleanup, cache invalidation, output limits, checkout mutation,
reviewer holds and stale GC. All repositories and merges in tests are temporary.
The adjacent worktree/native/review/safety suites cover composition. Tests run
through the installation's reported-test launcher. Pyright basic checks the new
module. Exact commands and results are retained in the item journal.
