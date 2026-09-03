# Your agent's commits may not be on the branch it told you

We run coding agents in git worktrees so their work stays quarantined on a
branch. On 2026-08-29 we found an agent that had spent a session committing to
`master` while its own branch sat untouched, and reporting the branch name back
correctly the entire time.

The cause is boring, which is why it is worth writing down.

## What happens

The agent's shell tool resets the working directory to the worktree on every
call. Shell state does not survive between calls, so a `cd` in one command is
gone by the next one.

An agent that notices this works around it the obvious way: it prefixes every
command with a `cd` to the main checkout. There is no "and then it goes back",
because there is no afterwards. Every command runs in the main checkout,
including every `git commit`, and the main checkout is on `master`.

## Why nothing catches it

`git commit` succeeds. It prints a hash and reports a clean tree. The agent's
own branch keeps its old head, so `git log` inside the worktree looks correct
and untouched. Every signal available from inside the worktree agrees that
nothing is wrong.

The drift is only visible from outside:

```
git branch -a --contains <hash>      # says master, not your branch
git log --oneline -1 <your-branch>   # still at the pre-commit head
```

Two commits landed this way and were reported to another agent as being on the
feature branch. They were not. That agent checked instead of taking the claim,
which is the only reason anyone found out.

## The part that actually matters

The misplaced commits are the small half of it.

**A release channel can go stale in silence.** Our updater is
`git pull --ff-only` on the user's current branch, which for everyone who is not
us means `master`. Work sitting on an unpushed `master` means every other user
is frozen on old code, and nothing anywhere says so. One of the two stray
commits was a fix removing a hardcoded local network address from a tracked
public file. Exactly the class of change that should not sit unpushed on one
machine.

**And it defeats the isolation completely.** The agent believes its work is
quarantined on a branch. It is not. Two agents doing this at the same time are
writing to the same branch with no idea the other exists, and neither one's view
of its own history shows any sign of the other.

That is the general shape worth taking away. A worktree is only isolation if
every write actually happens inside it. An isolation mechanism that reports
success while isolating nothing is worse than no isolation, because you stop
checking.

## What we do now

Agents do not `cd` out of their worktree at all. If one needs to read a file
that exists only in the main checkout, it reads it by absolute path. Reading is
fine. It is `cd` plus write that does the damage.

And no agent reports where a commit landed without confirming it first:

```
git branch -a --contains <hash>
```

If you run agents in worktrees, that check costs one command, and we would not
have found this without it.
