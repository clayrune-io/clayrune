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
| Reviewer lineage check | `mc/review_lineage.py` |
| Tests | `tests/test_merge_review_gate.py`, `tests/test_review_lineage.py` |

## Setting

`merge_requires_review` on the **project record**. Absent or anything other
than boolean `true` = off, so a fresh install (which may have no reviewer
agent) behaves exactly as before. There is no code default that turns it on.

Turn it on in the project record through a human browser call, not an agent
session or a code change. Human browser writes need no extra prompt.
For a human HTTP client, the request must carry the browser Origin header:

    curl -s -X POST localhost:5199/api/project/<pid> -H 'Content-Type: application/json' -H 'Origin: http://localhost:5199' \
      -d '{"merge_requires_review": true}'

Turn it off with `false`. Reverting the feature entirely = delete the setting;
`merge_back` is then byte-for-byte the old behaviour.

### Project safety settings (MC-1076 / a881d0f1)

`mc/project_safety_settings.py` protects these exact project-update keys:
`merge_requires_review`, `worktree_isolation`, `worktree_base_branch`,
`worktree_shared_runtime`, `code_sync_branch`, `steward_mode`,
`steward_objective`, and `steward_cadence_minutes`.

`POST /api/project/<id>` calls the guard before record reads, workspace
creation, logging, lifecycle updates, or persistence. An agent submitting
any protected key receives HTTP 403 with the key names in `error` and a
sorted `protected_keys` array. Presence counts even for an unchanged value
or `null`. The **whole body** is refused; ordinary fields in a mixed request
are never partly applied. The same guard covers project creation.

Caller detection reuses `workflow_routes._is_agent_caller` (no Origin means
agent) and the existing `mc.caller_attribution` process-tree lookup. A
managed agent remains refused even when it sends an Origin header or claims
`source: ui` / `client: mobile`. A browser request not attributed to an agent
continues to save normally with no new passcode or frontend prompt. If OS
attribution is unavailable, the existing Origin distinction remains; its
detached-process/forged-Origin limitations are not closed here. Ordinary
agent updates with no protected keys remain allowed and skip attribution.

The same refusal now guards both steward enable routes:
`POST /api/project/<id>/steward/enable` and
`POST /api/steward/standalone/enable`. They share
`project_safety_settings.is_agent_caller` through the same refusal helper,
using the three steward keys implicitly written by bootstrap. The guard
runs before parsing the body or provisioning anything, including when no
objective or cadence was submitted. Refused requests cannot edit a project,
create a standalone workspace, seed a charter, install a fence, create or
refresh a schedule, or send the enabled notification. Human browser enables
and edits retain their existing behavior.

`POST /api/project/<id>/steward/disable` deliberately remains open to agents:
it is the kill switch and only reduces autonomy. It still turns steward mode
off, removes the standing schedule and steward fence, and keeps the charter.
This applies to ordinary projects and standalone steward pseudo-projects.

The safety-key guard excludes `id` and `project_path` routing, which has a
separate caller-and-passcode guard in MC-1080 / f0d66b8e (see
`docs/PROJECT_ROUTING_GUARD.md`). It also excludes update endpoints beyond
these three guarded routes, and direct disk edits.
Defaults are unchanged. Tests:
`tests/test_project_safety_settings.py`, plus the existing project CRUD and
merge-review suites and `tests/test_steward_routes.py`. This is a branch
build, not a live settings change.

Validation on 2026-10-07 (MC-1076 branch): **143 passed, rc=0**, including
all 13 POSIX hook checks. Exact command used on Windows to expose the
already-installed Git `sh` to pytest (no package installation):

```powershell
python -c "import os,pytest; os.environ['PATH']=r'C:\Program Files\Git\bin'+os.pathsep+os.environ['PATH']; raise SystemExit(pytest.main(['tests/test_project_safety_settings.py','tests/test_merge_review_gate.py','tests/test_project_routes.py','-o','addopts=-ra','-q']))"
pyright mc/project_safety_settings.py
git diff --check
```

Pyright: 0 errors/warnings, rc=0. Diff check: rc=0. The initial command
`python -m pytest tests/test_project_safety_settings.py tests/test_merge_review_gate.py tests/test_project_routes.py -q`
also returned 0, with 13 shell checks skipped; the complete rerun above
removed those skips. Live production settings and a real browser save were
not exercised; the route was verified through Flask clients and temp files.

Send-back validation for both steward enable routes on 2026-10-07:
**169 passed in 38.04s, rc=0, no skips**. Exact commands:

```powershell
python -c "import os,pytest; os.environ['PATH']=r'C:\Program Files\Git\bin'+os.pathsep+os.environ['PATH']; raise SystemExit(pytest.main(['tests/test_project_safety_settings.py','tests/test_merge_review_gate.py','tests/test_project_routes.py','tests/test_steward_routes.py','-o','addopts=-ra','-q']))"
pyright mc/project_safety_settings.py mc/blueprints/steward_routes.py
git diff --check
```

Pyright and diff check: rc=0; Pyright 0 errors/warnings. The steward blueprint
had three existing typing errors before this extension: two nullable project
rereads and an inferred string-only bootstrap response. Reread misses now
return 404, and the bootstrap response is explicitly typed. No production
enable/disable calls or real browser changes were made.

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

### Reviewer lineage (MC-1077)

Not being the owner is not independence: a child (or grandchild) the builder
dispatched is the author's own. `mc/review_lineage.py` walks the reviewer's
dispatch ancestry before `gate.record()` and refuses (403) when the branch owner
is anywhere in it. Spawners are read from the live session's `_spawned_by` and
the agent-log row via `agent_routes._durable_spawner_of_record` (the immutable
`dispatched_by_session_id`; the re-armable `_notify_session` /
`spawned_by_session_id` callback target is not lineage). Logs of every project
are searched, since a spawner can sit in another project.

Fails closed: a reviewer or ancestor that is neither live nor in a readable
agent log, an unreadable log hiding one, a missing data dir, or a chain over
200 sessions all refuse with 403 "cannot establish the dispatch lineage".

Limit (not closed here): the recorded spawner is the `notify_session` the
dispatching caller typed (`agent_dispatch`), not the OS-attributed caller. A
builder that dispatches a reviewer without naming itself leaves no lineage.
Hivemind workers (`hm_*`) are not linked by `_spawned_by` either.

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
