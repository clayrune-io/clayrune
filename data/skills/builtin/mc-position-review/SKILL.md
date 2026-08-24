---
name: mc-position-review
description: Walk this project's standing positions and test whether any of their reasons have expired, then report to the human. TRIGGER when the task begins with a "[Position review]" marker, when a scheduled run asks you to review standing positions or check what has changed in the field, or when the user asks "are any of our decisions stale?" / "review our positions" / "what's changed that we should reconsider?". Do NOT trigger in ordinary sessions.
---

# Reviewing standing positions — what would change our mind, and has it

A **position** is a settled question: a subject, a verdict, a *reason*, and
**`expires_when`** — the condition that would re-open it. That last field is why
this job is tractable. You are not re-reading an archive hoping to notice
something stale; you are testing a short list of named conditions.

This also **is** the daily field-research job (MC-898). An open-ended sweep of
"what's new in agentic mission control" has no stopping condition and no way to
tell an interesting finding from a relevant one. The standing positions supply
the query set: *has anything changed that trips one of our own rulings?*

## The one hard rule

**You report. You never edit a position.** Changing a ruling that steers every
other agent on this project is a human's call — the same reason the learning
system's authority guard refuses artifacts that expand an agent's own
permissions. Your writes go to the review sidecar and nowhere else.

Concretely: never `POST …/memory/positions`, never `DELETE` one, never hand-edit
a `position_*.md`. If a position should change, say so and let Ron do it.

## The cycle

### 1. Get the worksheet

```bash
python tools/position-review.py brief --project <project_id>
```

Deterministic — it decides *which* positions are due (default: every 7 days, or
immediately if a human edited one since its last review). Judging whether a
condition has come true is your job; picking the set is not, because a model
picking it would quietly review something different every night.

### 2. Test each one, on its own terms

Read the `what would change our mind` line and go check **that**, specifically.
The trigger tells you which tool to reach for:

- **An internal claim** ("our own graph machinery stops resolving links") — run
  it. `tools/memory-link-check.py`, a test, a grep. Cheap and conclusive.
- **An external claim** ("X ships a feature that replaces Y") — WebSearch, and
  read the primary source, not a summary of it. A blog post *about* a release is
  not the release.
- **A usage claim** ("its output starts feeding other agents") — check the code
  and the config, not your memory of them.

Two failure modes to refuse:

- **Do not trip a position on vibes.** "Obsidian is popular again" is not the
  recorded condition. Only the named condition counts. If you think the *wrong
  condition* was recorded, that is a finding to report — not licence to
  substitute your own.
- **Do not confirm a position either.** You are testing for expiry, not
  re-litigating the verdict. "Still correct" is a fine and common outcome.

**A position with no `expires_when`** is flagged in the brief as
`NOTHING RECORDED`. Do not test it — *propose a trigger for it*. A ruling nobody
wrote a trigger for is one nobody has revisited since the day it was made, and
that is the most useful thing you can say about it.

### 3. Log the outcome — always, for every position you checked

```bash
python tools/position-review.py record --project <pid> \
    --file position_<slug>.md \
    --finding "what you checked, and what you saw" \
    [--tripped]
```

`--finding` is the point of the record: next review starts from what you already
established rather than re-deriving it. Write what you *checked*, not what you
concluded — "ran memory-link-check.py, 0 unresolved links across 412 notes"
beats "still fine".

`record` prints `newly_flagged`. **True exactly once per trip** — that is your
cue to interrupt a human.

### 4. Report

**Always** append to the journal — `docs/_journal/<item-id>-<slug>.md`, newest
at the bottom, dated. Every position you checked, what you checked it with, and
the outcome. This is the durable log. Unattended cycles never write backlog
notes (`CLAUDE.md`).

**Email only when `newly_flagged` came back true**, or when you have a proposed
trigger for an untriggered position worth a decision. Reuse the mailer, do not
build one:

```bash
python tools/night-review/send_mail.py \
  --subject "[Clayrune positions] DECISION NEEDED: <subject> may have expired" \
  --body-file /tmp/mail_body.txt
```

The body must stand on its own — Ron may read it days later on a phone with no
session context. Include: the position as it stands (verdict + reason), the
recorded trigger, **what you found and where** (link the primary source), and
the three options with your recommendation:

- **Keep** — the trigger did not really fire.
- **Re-open with a new reason** — the verdict may still hold but the reasoning
  moved. Ron edits the reason in the Memory modal; that supersedes in place and
  keeps the old text under `## Previously`.
- **Forget** — the ruling was wrong. Ron uses Forget in the Memory modal.

Then **stop**. An emailed ask is not approval. The next cycle reads his reply
via the `mail` MCP server (`search_email("[Clayrune positions]")`) before
re-raising anything.

**A quiet night gets no email.** Nothing expired is the expected result and the
journal already records it. Mailing a nightly "all clear" is precisely how this
channel stops being read, and then you lose the one night it mattered.

## Where things live

| | |
|---|---|
| the positions | `<project memory dir>/position_*.md` |
| review sidecar | `<same dir>/position_review.json` — yours to write |
| open flags | `python tools/position-review.py flags [--json]` |
| the design | `docs/DAVE_DESIGN.md` §4 (positions), §9 (phase 3) |
| the human's view | project menu → Memory → **Standing positions** |
