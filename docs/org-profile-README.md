# Org profile README — ready to ship

**Where this goes.** When Ron creates the GitHub organization, create a repo in
it named exactly `.github`, and put the block below at `profile/README.md`.
GitHub then renders it at `github.com/<org>` **full width, with no file tree
above it**. That is the whole point of the org move: it is the only GitHub
surface where the pitch is the first thing on screen.

**Name note:** `clayrune` is taken. `clayrune-io` is the working choice.

**Do not** paste the repo README here. A visitor who lands on the org page and
then clicks into the repo should get more detail, not the same text twice.
This one sells; the repo README explains.

---

## The block

```markdown
<div align="center">

<img src="https://clayrune.io/assets/claydo-smile-96.png" width="88" alt="Claydo" />

# Clayrune

**Mission control for your Claude Code agents.**

Four terminals open and you cannot tell which one stopped and is waiting on you.

[![License: MIT](https://img.shields.io/badge/license-MIT-1f6feb?style=flat-square)](https://github.com/clayrune-io/clayrune/blob/master/LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/clayrune-io/clayrune?style=flat-square&color=1f883d&label=last%20commit)](https://github.com/clayrune-io/clayrune/commits)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-6e7781?style=flat-square)](https://clayrune.io/download.html)
[![Live demo](https://img.shields.io/badge/live%20demo-clayrune.io-8250df?style=flat-square)](https://clayrune.io/demo)

<img src="https://clayrune.io/assets/shot-board.png" width="88%" alt="The Clayrune board — five projects, every running session listed underneath" />

### [▶ Try the live demo](https://clayrune.io/demo) &nbsp;·&nbsp; [⬇ Download](https://clayrune.io/download.html) &nbsp;·&nbsp; [Source](https://github.com/clayrune-io/clayrune)

</div>

---

Clayrune is a board over the Claude Code sessions you already run. Every project
on it, every agent named, and the ones that need you say so.

**It runs unattended with permissions skipped, and still won't `git push`.**
An unattended agent runs with `--dangerously-skip-permissions`, because stopping
to ask defeats the point. So the constraint is in code: a PreToolUse hook that
hard-blocks 21 irreversible command shapes, fails closed on an unknown session,
and blocks edits to its own source. Then it emails you the command it stopped and
feeds your reply back into the same run.

**Agents that remember, measurably.** Across 374 real sessions our agents opened
a memory file in 5% of them; 91 of 104 notes were never read. A retrieval layer
that does not wait to be asked took reachable notes from 47/104 to 97/104. The
harnesses ship in the repo and re-run in a minute against your own corpus.

**A fleet, not one agent.** Each agent gets its own persona, its own pinned
model, its own session. Schedule them, or hand one a standing charter and let it
work overnight.

Local-first. Your machine, your Claude CLI, no account, no telemetry.
```

---

## Why it is shaped this way

- **Centred `<div>` header.** The org profile is full-bleed, so a centred hero
  reads as a landing page. The repo README is not centred, because it sits under
  a file tree where centring looks lost.
- **Absolute image URLs.** Relative paths resolve against the `.github` repo,
  which has no `docs/assets/`, so every image must point at clayrune.io. Verify
  each one returns 200 before shipping — a broken hero image is worse than none.
- **Three claims, no inventory.** Same three as the repo README, phrased
  shorter. Anything a reader needs beyond this is one click away.
- **No own-subscription line.** Standing position: it is the category's entry
  ticket now, not our edge.
- **The fence scope is implicit here and explicit in the repo README.** If this
  page ever states it as a blanket claim, it becomes false — the fence governs
  unattended cycles, not interactive sessions.

## Before it ships

1. Confirm the final org name and replace `clayrune-io` in all five URLs above.
2. `curl -sI` each of the two image URLs and both badge URLs; all must be 200.
   `shot-board.png` is currently only in the product repo, so it has to be
   copied into the website's `assets/` and deployed first, or the hero renders
   broken.
3. Check the rendered page at `github.com/<org>` before announcing anything.
