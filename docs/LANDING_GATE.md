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
- `smoke:<check name>` from the repository's cross-mark and `FAIL` lines,
  plus `smoke:SCHEDFAIL:<check>` / `smoke:CALFAIL:<check>` for boot guards.
  Indented guard findings receive their own signatures scoped by the heading.
  Pure aggregate failure counts are excluded when detailed failures are present.
- `exit:<code>:<error line>` for **every** other failure/error/exception/fatal
  line (including Node `not ok`, usage and missing-input diagnostics), even when
  pytest/smoke signatures also exist. An unfamiliar failure format cannot hide
  behind a recognized one.
- `tap:<suite> > <test name>` from TAP `not ok N - name` (Node `--reporter=tap`).
  The number is the test's position, not its identity, so it is dropped; the
  enclosing `# Subtest:` names qualify it so a same-named test failing in a
  second suite still blocks.
- `pytest-report:<test>` from the `____ test_x ____` report heading, the only
  per-test name when pytest prints no short summary (`-r`). Only headings under a
  `=== FAILURES ===` / `=== ERRORS ===` banner count: `-rP`/`-rA` print the same
  heading for passing tests with captured output under `=== PASSES ===`.
  A banner's provenance is checked: after a `---- Captured stdout call ----` header
  the text is the test's own output, and a banner-looking line there counts only at
  pytest's own banner width (every real banner is padded to the same width, taken
  from the first banner outside captured output). A test that prints
  `=== PASSES ===` therefore cannot move the parser into the PASSES section and hide
  the next failure heading. The captured block ends at the next `____ name ____`
  heading or at a banner of the right width.
- `spec:<suite> > <test name>` from the Node `spec` reporter's `✖ name (dur)`
  lines (the default for a non-TTY stdout). Suites open with `▶ name`; names are
  qualified by enclosing suites, so a same-named test in a second suite still
  blocks. The live lines carry no file; the trailing `✖ failing tests:` recap does
  (`test at FILE:L:C`). Live and recap entries are paired by test name and order,
  and the signature becomes `spec:<suite> > <test> @<file>`, so the same test name
  failing in a second file is a new identity (and a failure that moves from one
  file to another blocks even at an equal count). Pairing needs the same number of
  recap entries as live lines for that name; otherwise the name stays unqualified
  (stable, and the totals check below still applies). Node's TAP block carries the
  file in its `location:` line and the `tap:` signature gets the same
  `@<file>` qualifier. The qualifier is the **full path relative to the directory
  the command ran in, with forward slashes**, not the basename: `a/same.test.mjs`
  and `b/same.test.mjs` are different files. A path outside that directory stays
  absolute (a stable string, so it still compares). A TAP producer that prints no
  `location:` stays unqualified.
- Runner count lines are bookkeeping, not failures: pytest's closing
  `N failed, M passed in …`, TAP `# tests/pass/fail/…` and Node `ℹ tests/pass/…`
  totals change whenever an unrelated test is added or fixed. They are never a
  failure identity unless no named failure exists, so a summary-only nonzero exit
  still fails closed. Their failure total is used separately, as the count
  backstop below.
- Without detailed findings, an aggregate summary, last nonempty output line
  or `<no output>` supplies a nonzero-exit signature. ANSI colors, checkout
  roots, `:LINE[:COL]` after any path (relative or absolute; needs a separator
  or a source-file extension, so `host.com:8080` keeps its port) and numeric
  durations are normalized; file
  basenames, pytest node IDs and non-timing assertion values remain distinct.

The 2026-11-07 review audited failure-print sites in all 327
`tools/smoke/*.mjs` files. Regression cases cover these output families:

| Format | Representative sources | Handling |
| --- | --- | --- |
| Cross marks, `FAIL`, `FAIL:`, `FAIL —` | `agent-read-request-ui.mjs`, `hivemind-roster.mjs`, `terminal-pty-popout.mjs` | Named smoke signatures |
| `SCHEDFAIL`, `CALFAIL` | `boot-smoke.mjs` | Tagged smoke signatures |
| Guard headings with `•`, `*`, `-` findings | `boot-smoke.mjs`, `walkthrough.mjs` | Each indented detail retained |
| Harness/page errors and uncaught Node errors | `pointer-drag.mjs`, `test-run-card.mjs`, `desk-v1-piece-editor.mjs` | Additive error signatures |
| `capture failed`, `bench failed`, custom smoke errors | `capture-screenshot.mjs`, `desk-v1-exit.mjs`, `desk-v1-journey.mjs` | Additive error signatures |
| Usage, missing selectors/inputs, unreachable registration | `capture-screenshot.mjs`, `installed-app.mjs`, `split-view-real.mjs`, `first-run-signin-flow.mjs` | Additive diagnostic signatures |
| Counts, `FAILED (n)`, `n FAILED`, `RESULT: FAIL`, `fails=n` | `settings-providers.mjs`, `desk-studio-render-scenes.mjs`, `terminal-pty-popout.mjs` | Summary only when no detailed failure exists |
| Assertion errors | Node assertions | Additive error signatures |
| TAP `not ok N - name` | `test-run-data.test.mjs`, `node --test` | Named `tap:` signature, number dropped |
| Node spec `✖ name (dur)` | `node --test` (default reporter, non-TTY) | Named `spec:` signature, suite-qualified, recap skipped |

Signatures are scoped by command position. Candidate signatures absent from
the baseline block. `pytest:`, `pytest-report:`, `tap:` and `spec:` identities
are compared as a multiset: a failure the baseline listed once and the candidate
lists twice is new. Smoke and `exit:` lines stay a set.

**Contract: when the gate cannot prove the candidate adds no failure, it blocks.**
Reading free-form runner output cannot be made unambiguous: a test may print a line
spelled exactly like a runner's total, a banner or a failure marker, and a wrapper
may print after the runner. So the rule is not "find every such case", it is "when
the output cannot be matched one for one between baseline and candidate, block, and
a human merges by hand". A hole the parser has not met yet therefore shows up as a
false block, not as a silent pass.

**Totals check.** Identity says *which* failure is new; the runner's own totals say
*how many*. Each command result records `failure_totals`, an **ordered list**, one
entry per total line the output printed: pytest's closing `N failed` / `N error(s)`
(only a line that ends ` in <time>`) and node:test's `ℹ fail N` / `# fail N` and
`cancelled N`. Totals are never summed, so a total a test printed cannot offset one
the runner did not print. A zero exit is `[]`; output with no recognised total is
`[]` too. A total the parser had to drop (see below) is the explicit marker
`"ambiguous"`, never a gap. For each command position where the **candidate exits
non-zero**, `compare()` blocks, reporting the reason in `unprovable`, when:

- either side has an `ambiguous` total;
- the baseline and candidate lists have different lengths (the reason names both);
- same length, and any element is higher in the candidate: reported in
  `count_increases` (`["1:runner failure count 1 -> 2"]`, or `#2` for the second
  element). A drop in one element does not offset a rise in another.

A candidate that exits 0 passes this check whatever the baseline printed. Smoke
scripts print no such total (`[]` against `[]`, equal) and rely on identities.

**Ambiguous.** Inside a `---- Captured stdout ----` block a pytest-format line is
almost always a test's own `print()`. The one exception is pytest's bare closing line
under `-rN` (no short summary between it and the last captured block), which is the
last thing in the output. So such a line is held back and kept only if nothing
follows it. If anything follows (a wrapper's `verification finished` footer, more
test output, another count line) the gate cannot tell a test's print from the
runner's total, and records `ambiguous` instead of guessing.

**Accepted false blocks.** These harmless changes block by design; the cost is a
manual merge:

- A test that prints a full pytest total (`1 failed in 0.01s`) or a node total
  (`ℹ fail 1`) to output the parser reads as the runner's (under `-s`, `-rP`/`-rA`,
  or any reporter that does not capture it): the totals list gains an entry or an
  `ambiguous` marker.
- A wrapper that prints after pytest's closing line, when that line sits in a
  captured block.
- Renaming a failing test (a new identity), and a runner or reporter change that
  adds or removes total lines (node prints `fail` and `cancelled` as two).
- A baseline that exits non-zero without printing any total against a candidate that
  prints one, or the reverse.

Baseline failures are reported even when the candidate
fixes them. A matching nonzero baseline/candidate failure allows landing.
Timeouts, unavailable execution, output overflow, invalid configuration,
changed refs/checkouts or failed restoration always block; they are not waived
as pre-existing findings. A successful shell exit has no failure signatures.

Baselines use a process-local LRU cache (64 entries), keyed by repository
common directory, target commit SHA, exact command list, timeout and environment
digest. Raw environment values are not stored in cache keys or reports. Output
tails are verifier output: do not print credentials or put them in commands.
Only completely successful baselines (every exit zero, no failure signatures,
no infrastructure errors, clean tracked state) are cached or reused. A failing
baseline is always run fresh before it can exempt a candidate failure: repaired
`node_modules`, `.venv` or `config.json` state cannot leave a stale exemption.
Cached non-green entries are discarded defensively. A new target commit or
changed configuration requires a fresh baseline; server restart clears the
cache. Runtime/dependency contents are outside the cache identity, but reusing
a green baseline cannot waive any candidate failure. Use deterministic checks.

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
`errors`, `count_increases`, `unprovable`, per-command `baseline` / `candidate` results, SHA/tree pins, cache
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
registration/cleanup, cache invalidation and runtime repair after failing
baselines, mixed recognized/unrecognized errors, smoke print families and
timing/path stability, output limits, checkout mutation,
reviewer holds and stale GC. All repositories and merges in tests are temporary.
The adjacent worktree/native/review/safety suites cover composition. Tests run
through the installation's reported-test launcher. Pyright basic checks the new
module. Exact commands and results are retained in the item journal.
