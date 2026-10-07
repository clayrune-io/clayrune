# Merge review gate (MC-1075)

Goal: no agent branch (`clayrune/agent/<sid>`) lands on the project's base
branch unless an adversarial review of its **current tip SHA** is on record
with verdict `pass`.

## Pieces

| What | Where |
|---|---|
| Record store, rules, setting | `mc/merge_review_gate.py` |
| Hold in the auto-merge path | `agent_worktree.merge_back()` returns `awaiting_review` |
| Record route | `POST /api/project/<pid>/agent/<sid>/review` in `mc/blueprints/merge_review_routes.py` |
| Hand-merge check | `tools/merge-review-check.py` |
| Opt-in hooks | `tools/git-hooks/pre-merge-commit` + `tools/git-hooks/commit-msg`, installed together by `tools/install-merge-review-hook.sh` |
| Tests | `tests/test_merge_review_gate.py` |

## Setting

`merge_requires_review` on the **project record**. Absent or anything other
than boolean `true` = off, so a fresh install (which may have no reviewer
agent) behaves exactly as before. There is no code default that turns it on.

Turn it on for one project with its own record (human or Dave, not a code change):

    curl -s -X POST localhost:5199/api/project/<pid> -H 'Content-Type: application/json' \
      -d '{"merge_requires_review": true}'

Turn it off with `false`. Reverting the feature entirely = delete the setting;
`merge_back` is then byte-for-byte the old behaviour.

## Records

`<data root>/data/merge_reviews/<project_id>.json` (gitignored, outside
`data/projects/` per the DATA_DIR pollution rule). Keyed `<branch>@<full sha>`;
each holds `{sha, branch, verdict, reviewer_session, reviewer_character,
report_path, attribution, at}`. The latest record for a branch+SHA wins, so a
later `changes_requested` withdraws an earlier `pass` on the same commit. A
record for an older SHA never counts for a newer tip. A missing/corrupt store
reads as "no review", i.e. the merge is held; a write never overwrites a
corrupt store (it is kept as `.corrupt`).

## Record route rules

* `sha` must be the full 40/64-hex tip of the branch **right now** (409 with the
  current `tip` otherwise).
* Reviewer identity comes from `mc.caller_attribution` (OS process-tree walk),
  never from the request body. Attributed to the branch owner -> 403
  (self-review). Attribution `unavailable` (e.g. no psutil) -> 403, fail closed.
  Unattributed -> 403 as well: an unattributed caller can be a builder's own
  detached helper process, so self-review cannot be ruled out. Reviews are
  recorded by attributed reviewer sessions; there is no human path through this
  route (Dave decision, MC-1075 round 2).

## merge_back behaviour

When the gate is on and the landing target is the project's base branch,
`merge_back` returns `('awaiting_review', reason)` unless the tip has a `pass`.
Nothing is merged, the branch and worktree are kept. With a pass, it merges the
**reviewed SHA itself** (not the branch name) so a commit pushed after the check
cannot ride in. `_worktree_merge_back_on_end` logs it to agent activity like a
conflict. `remove()` already refuses a worktree with commits ahead of base;
`gc_stale` additionally treats `awaiting_review` as preserved and does not
fingerprint-cache it (a pass recorded later does not move HEAD).

An explicit `target_ref` (the hivemind integration path) is not gated.

## Hand merges

`git merge` typed in the main checkout never calls `merge_back`.

    python tools/merge-review-check.py clayrune/agent/<sid>      # exit 0 / 1 / 2

Strict by default (does not consult the setting). `--respect-setting` makes the
project setting the single switch (the hook uses it). Exit 2 = could not decide
(unknown project); the hook treats that as a block.

Opt-in hooks (never installed automatically, not part of `tools/install-hooks.sh`):

    sh tools/install-merge-review-hook.sh              # install both
    sh tools/install-merge-review-hook.sh --uninstall

Two hooks, one check (`merge-review-check.py --merge-head --respect-setting`):

* `pre-merge-commit` refuses the merge as git creates it.
* `commit-msg` refuses a commit that concludes a merge (it returns at once when
  `MERGE_HEAD` does not exist, so ordinary commits are untouched). Needed because
  a rejected merge leaves `MERGE_HEAD` and a merged index behind, and
  `git commit --no-edit` does not run `pre-merge-commit` again.

Hooks live in the common git dir, so linked worktrees share them. The check
resolves the project through `git rev-parse --git-common-dir`, so a merge typed
in a linked worktree finds the main checkout's project record and reads its
setting (gate off = pass).

The agent branches checked are those named by `MERGE_HEAD` (every line, so an
octopus merge counts) or, when git has not written it yet, by the arguments git
exports as `GIT_REFLOG_ACTION`: `merge <args>` **and** `pull <args>`. Any commit
being merged that a `clayrune/agent/*` branch **contains** and `HEAD` does not is
checked, and the pass must be on record for **that exact commit**, not the
branch's current tip. Matching only tips (round 2) let a rejected merge be
finished by advancing the agent branch first, and let `<branch>~1` or a
copy-named branch through.

Which agent branches **own** the commit (round 3): those that contain it and
were not created at or after it, read from each branch's oldest reflog entry.
The commit passes when any owner has a pass for it and none has
`changes_requested`. So agent B forked from agent A's reviewed tip does not
block merging A, and an agent forked from an ordinary feature branch does not
make that branch's commits need an agent review (round 3 required reviews
under every containing branch, which the tip-only review route can never
record). When no agent branch produced the commit, it is ungated only if a
non-agent branch other than `HEAD` also holds it; otherwise every containing
agent branch owns it, so deleting and recreating a branch at its own rejected
commit does not launder it. A missing reflog counts as owning (fails closed).

A rejection is of the commit, not the branch name (round 5): any
`changes_requested` on record for that exact SHA, under **any** branch name,
holds it, whether or not that branch still exists or still counts as an owner.
Ownership comes from the reflog, which expires, and a branch can be renamed or
deleted; without this, renaming the rejecting branch let a second branch's
pass on the same commit land it. `merge_back` (`gate.hold_reason`) applies the
same lookup. A rejected commit stays rejected until that same branch records a
pass on it or the agent commits a fix.

**Client-side hooks are advisory.** `git merge --no-verify` and
`git commit --no-verify` skip them, and anyone can delete or never install them.
The real controls are the server-side `merge_back` hold, and Dave running
`python tools/merge-review-check.py clayrune/agent/<sid>` before a hand merge.

Known limits (measured, not hypothetical):

* **Fast-forward merges never run `pre-merge-commit`** (and there is no commit to
  run `commit-msg` on). If master has not moved since the branch forked,
  `git merge <agent branch>` fast-forwards and the hooks are not called. Run
  `merge-review-check.py <branch>` first, or merge with `--no-ff`.
* During `pre-merge-commit` for a fresh auto-merge git has **not written
  `MERGE_HEAD`** (checked on git 2.51), hence the `GIT_REFLOG_ACTION` fallback. A
  merge of some other branch that merely contains the agent's commits is not
  detected.

## Not covered

Auto-dispatch of the reviewer; the other two adversary moments (contract lock,
test failing twice). The reviewer is dispatched by hand for now.
