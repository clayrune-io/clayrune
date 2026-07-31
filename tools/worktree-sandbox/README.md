# Worktree isolation sandbox

A self-contained proof that per-agent git worktrees (`agent_worktree.py`,
backlog `b264200a`) actually prevent concurrent agents from clobbering each
other — measured, not asserted.

## Why this exists

The clobbering bug is a **race**, so it can't be reasoned about convincingly —
it has to be reproduced. And it must never be reproduced against the Clayrune
working tree, because the whole point of the bug is that it destroys work.

This harness builds a throwaway git repo in a temp dir, runs the race in it,
and deletes it. **The Clayrune tree is never touched.**

## Run

```bash
python tools/worktree-sandbox/sandbox_test.py
```

Exit `0` = worktrees preserved both agents' work. Exit `1` = they didn't.

## What it does

Two threads simulate the exact shape of an agent turn — **read the whole file,
pause (the model's "thinking" window, where the sibling races in), write the
whole file back**. That whole-file rewrite is what an `Edit`/`Write` tool does,
and it's why the loser's change vanishes with no error.

Both agents edit **different functions in the same file** — the realistic case:
not "two agents assigned the same task", but two agents on unrelated features
that happen to share a file.

| Scenario | Setup | Expected |
|---|---|---|
| **A — shared tree** | both agents in one checkout (today) | 1/2 survive — silent loss |
| **B — worktrees** | each agent in its own worktree, then merged | 2/2 survive — clean merge |

## Result (2026-07-29, this machine, 3 consecutive runs)

```
VERDICT   shared tree: 1/2 survived   |   worktrees: 2/2 survived
```

Stable across runs: Agent B's edit is silently destroyed every time on the
shared tree, and survives every time with worktrees.

## Extending it

- To exercise the **conflict** path instead of the clean-merge path, have both
  agents edit the *same* function. `sync_into()` should return `conflict`, abort
  the merge, and leave the agent's tree exactly as it was.
- To test **turn-gating**, leave an uncommitted change in a worktree before
  calling `sync_into()` — it should return `skipped / worktree dirty` rather
  than fight the agent's in-flight edits.

## Note on scope

This proves the **isolation engine**. It does not exercise MC's dispatch
wiring, because that wiring doesn't exist yet — `agent_worktree.py` is not yet
imported by the server. When it is, the end-to-end test should register a
*throwaway* project in MC and dispatch real agents at it — never
mission-control itself.
