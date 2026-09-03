# A CI job that passed for three months without testing anything

We had a CI job that checked our installer works on a Mac with no Python
installed. It had been red since roughly 2026-06-09, parked behind
`continue-on-error`, and blamed on a dependency drifting its install location.

That diagnosis was wrong. The job had never reached the dependency, and it had
never tested the thing it existed to test.

## What it was doing

The job simulated "no Python" by stripping entries out of `PATH`.

Our installer has a function that refreshes `PATH` so a just-installed binary
works without opening a new shell. It unconditionally prepends `/usr/local/bin`,
plus a handful of other standard install locations. The runner image keeps a
`python3.12` in `/usr/local/bin`.

So the installer handed the directory straight back to itself, found the
interpreter that the job had just hidden, and used it. The log said so, in the
job whose entire purpose was that no interpreter existed:

```
Using: python3.12 (Python 3.12.10)
```

The fallback branch then never ran, so the tool it was supposed to install was
never installed, and an assertion two steps later failed with a message about
that tool being missing. Which reads like a bug in the tool, and is not one.
Identical in every archived run we checked.

## Why it survived three months

Because it failed in a way that pointed somewhere else. A red job with a
plausible-looking cause attached gets triaged as "known issue, blame upstream"
and then stops being read. `continue-on-error` finished the job off: the failure
had no consequence, so nothing forced anyone back to it.

The product was never broken. We confirmed the real behaviour by hand on a Mac
with the package manager off `PATH`: the fallback runs, the tool installs where
it should, and the environment is built on it correctly.

So the cost was not a shipped bug. The cost was three months of believing we had
coverage on a first-run path that had never once been exercised, which is the
more expensive of the two.

## The general shape

**If a test simulates an absence, it has to prove the absence first.**

`PATH` is not yours to control once someone else's script is running. Ours
rebuilds it deliberately, and it is right to. Any test that assumes it can
subtract something by editing `PATH` is testing the script's `PATH` handling, not
the condition it meant to create.

## What we changed

- Hide the interpreters **on disk**, not behind `PATH`.
- Assert up front that the simulation actually holds, and that the
  system-provided interpreter is still too old to satisfy the version check.
  Otherwise the job can silently stop testing anything again.
- Assert the installer **printed the fallback line**, so a future leak fails with
  that sentence instead of a misleading one.
- Assert the created environment points at the fallback interpreter. An
  environment existing only proves the step finished, not that it finished the
  way we wanted.
- Drop `continue-on-error`. The job blocks again.

The installer itself changed by comments only. There was nothing wrong with it.
