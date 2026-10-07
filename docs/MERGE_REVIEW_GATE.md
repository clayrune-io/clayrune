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
| Opt-in hook | `tools/git-hooks/pre-merge-commit`, installed by `tools/install-merge-review-hook.sh` |
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
  Unattributed (a human's curl/UI) -> accepted, recorded with
  `reviewer_session: ""`, `attribution: "unattributed"`.

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

Opt-in hook (never installed automatically, not part of `tools/install-hooks.sh`):

    sh tools/install-merge-review-hook.sh              # install
    sh tools/install-merge-review-hook.sh --uninstall

Known limits (measured, not hypothetical):

* **Fast-forward merges never run `pre-merge-commit`.** If master has not moved
  since the branch forked, `git merge <agent branch>` fast-forwards and the hook
  is not called. Run `merge-review-check.py <branch>` first, or merge with
  `--no-ff`.
* `git merge --no-verify` skips it.
* During `pre-merge-commit` for a fresh auto-merge git has **not written
  `MERGE_HEAD`** (checked on git 2.51). The check therefore reads `MERGE_HEAD`
  when present (concluding a conflicted merge) and otherwise the merge arguments
  git exports as `GIT_REFLOG_ACTION`, resolving each word to a commit. A merge of
  some other branch that merely contains the agent's commits is not detected.

## Not covered

Auto-dispatch of the reviewer; the other two adversary moments (contract lock,
test failing twice). The reviewer is dispatched by hand for now.
