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

## Landing sequence and the rule

The existing repository-wide landing lock covers the whole operation. Existing
dirty-tree, review and native standards refusals remain. Before the actual
target merge, `mc/landing_gate.py` resolves the target and agent SHAs and uses
`git merge-tree --write-tree` to build the combined tree without moving refs.
A synthetic commit makes that tree checkoutable, without creating a branch.

The existing agent checkout is temporarily detached at the synthetic combined
commit, which keeps the existing runtime links and dependencies. It restores the
original agent commit and symbolic branch before landing; neither branch ref
moves during verification. The main target checkout is not used to run commands.
There are no nested disposable worktrees, dependency installs, resets or cleans.

**The rule (round 9, Ron 2026-11-07: "block every red branch").** The candidate
passes only if **every** configured command exits 0 with no timeout, infrastructure
error or oversize-output error. Anything else blocks. All commands run, in order,
even after a red one, so one block message reports every failing command.
`landing_gate.evaluate()` is the whole decision: it reads the exit code and the
`error` field of each run and nothing else.

- **No output is parsed to decide a verdict.** Earlier rounds compared failure
  *identities* (which test failed) between a baseline run at the target tip and the
  candidate, so a branch could land onto an already-red master if it added no new
  failure. Nine review rounds each found another way free-form runner output could
  be mis-read (wrapper scripts, duplicate test names, the node `dot` reporter,
  child-directory node IDs), and text parsing of arbitrary commands cannot prove
  identity. That machinery (`failure_signatures`, `failure_totals`, `prove`,
  `compare`, the baseline phase and its cache) is deleted.
- **No baseline phase.** The target tip is never run. Nothing in the report can say
  "master was already red"; the block message shows the candidate's own failure.
- **Accepted cost: nothing lands while master is red.** A branch that is itself
  green but cannot make the merged tree pass (because master is red and the branch
  does not fix it) is blocked, and so is a branch that did not cause the red.
  Fix master first, then re-land. A branch that fixes a red master lands, since
  only the merged tree is judged. Re-adding a report-only baseline run would let the
  message say who broke what, without changing the verdict.
- A flaky verify command blocks like any other red one: configure deterministic
  checks.

Each command result carries `exit_code`, `error` (`timeout`, `output_limit`,
`command_unavailable:<Exception>` or null) and the last 12,000 output characters
(`output_tail`). The block `message` names each failing command and its exit code
(`Command 2 failed with exit 3: <command>`) followed by that tail, and `failures`
lists `<position>:exit <code>` (plus `(<error>)` when set). A timed-out or
oversize command fails even when its exit code is 0. A command that could not start
fails as `did not run`. Up to 32 MiB of combined stdout and stderr is spooled per
command outside the repository and deleted after reading; larger output is an
explicit blocking error. Processes register immediately with the existing process
manager. Timeout uses the existing owned process-tree termination helper, then reaps
the child; Windows also retains and closes the child job handle. As with that
existing helper, Windows job assignment has a short post-spawn race. Detached
children are unsupported, and POSIX daemon cleanup is not independently verified
here.

Timeouts, unavailable execution, output overflow, invalid configuration, changed
refs/checkouts or failed restoration always block. Output tails are verifier
output: do not print credentials or put them in commands.

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

The report carries `status: pass|blocked`, `failures`, `errors`, `message`,
per-command `candidate` results, SHA/tree pins and restoration status when
available. Earlier historical refusals retain their existing return shapes.
The full gate report is logged. The session-end handler surfaces blocked
landings and keeps the branch/worktree; stale GC does not cache a blocked
verdict, so a later retry can run again. After verification, existing
candidate/standards pins are revalidated before the real merge. No new gate
bypasses the existing review or human-only controls.

`verify(..., reviewer=callback)` is the MC-1165 extension point. It receives the
current report, including target/incoming SHA and combined tree. Only an explicit
`{"status": "pass"}` allows its part of the gate; missing, unavailable or raised
verdicts hold. A reviewer pass cannot erase command failures. No cold reviewer,
model call, reviewer configuration or reviewer dispatch is implemented here.

## Verification

`tests/test_landing_gate.py` exercises the exit-code rule (`evaluate`), actual
combined-tree regressions, ref preservation, a red candidate blocking even when
the target is equally red, an all-green candidate and a red-target/green-candidate
landing, one run per command on the merged tree only, historical unset behavior,
timeout, process registration/cleanup, output limits, checkout mutation,
reviewer holds and stale GC. All repositories and merges in tests are temporary.
The adjacent worktree/native/review/safety suites cover composition. Tests run
through the installation's reported-test launcher. Pyright basic checks the new
module. Exact commands and results are retained in the item journal.
