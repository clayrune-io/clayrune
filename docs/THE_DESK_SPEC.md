# The Desk — an in-house marketing department

**Status:** v0 draft, 2026-09-09. Written from Ron's own framing over four
messages: *"I personally suck at marketing and outreach, so I need that help and
guidance… someone to write the stories and publish for me, I can edit them…
a marketing manager which is both the incubator, teacher, mentor and actual
publishing office… all in one package… not just a persona, it has to be the
whole suite which also manage it all in one place."*

Supersedes the framing behind the Social Approvals Queue (`203082e`, `910d38f`).
That queue survives as one component of five.

Field scan in flight: `docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md` (Quill,
session `ea807b553b05`) settles the publishing-mechanics section. Everything
here that depends on it is marked.

---

## Problem

Clayrune ships continuously and reaches nobody. The operator is a builder who
does not want to become a marketer and will not sustain a practice that requires
him to.

The Social tab built on 2026-09-05 is **an approvals queue with no author behind
it** — the last ten percent of a pipeline, built first. Nothing generates
drafts, nothing remembers what was already said, nothing publishes, nothing
teaches. It has held three pending drafts in one project since it landed.

The gap is not a UI and not a persona. **It is a department: a standing
operation with its own memory, its own clock, its own surfaces, and a
professional opinion Ron can argue with.**

## Who it is for

One operator with several live projects, strong on product and weak on
outreach, who has a clear sense of what he will and will not say but no
sense of what to say first. Not a marketing team. One voice, one veto.

## The one-place requirement

"Manage it all in one place" is the binding constraint, and it rules out the
current shape. The Desk is a **workspace peer to the Floor**, reached from the
sidebar, not a tab inside each project.

This follows from the content itself: a story about Clayrune's scheduler and a
story about the engulfing scanner come from different projects and go out under
the same voice, on the same calendar, against the same audience. Splitting that
across project modals makes the calendar unviewable and the voice incoherent.

The Desk has four surfaces, and everything below lives in one of them:

| Surface | What it answers |
|---|---|
| **Board** | What is happening this month, and why. Campaigns, arcs, the agenda. |
| **Queue** | What needs me right now. Drafts to edit, release, or push back. |
| **Calendar** | What is scheduled and what went out. |
| **Ledger** | What we have said, how it did, and what I keep changing. |

The per-project Social tab survives as a filtered view of the Queue, so a
project modal still shows what is pending for that project.

## What it must do — the four functions

### 1. Publishing office

The Desk publishes. This is the function that makes the rest real, and its
absence is why the current tab is inert.

- On release of an edited draft, the Desk posts to the named account and records
  a receipt: platform, permalink, timestamp, the exact body as published.
- A publish failure is **loud and terminal**. No blind retry. Ron must never be
  left unsure whether something went out.
- The Desk holds the account inventory: which platforms are connected, which are
  authenticated, which have gone stale.
- Nothing publishes without an explicit human release. The Desk cannot widen its
  own permission to post unattended — the learning-system authority guard
  principle applies here verbatim.

**Assumption, stated because it drives the build:** publishing goes through the
**browser pane with named profiles**, not platform APIs. Precedent is already in
this repo — `~/.clayrune/browser_profiles_named/` holds signed-in profiles for
linkedin, facebook, reddit, discord and x, and the vault holds credentials for
all five. Confirm against the field scan before building; the fragility of
browser automation against UI changes is the main risk this carries.

### 2. Incubator

Turns raw work into things worth publishing, over time rather than post by post.

- Harvests **signal** from what Clayrune already sees: commits, backlog items
  closed, features shipped, demo assets produced, agent runs that solved
  something interesting. No new instrumentation in the projects themselves.
- Scores signal into candidate stories and **discards most of it**. Not
  everything shipped is a story.
- Grows surviving candidates into **campaigns** — a launch, a series, a running
  thread — not isolated posts. A campaign has a thesis, a sequence, and a
  finish.
- **A period with nothing worth saying produces nothing.** This is a
  requirement. An operation that posts on schedule regardless of signal is the
  failure mode this design exists to avoid.

### 3. Teacher

The part nobody builds, and the reason Ron asked for a department rather than a
generator.

- **Every draft carries its reasoning**: why this angle, why this platform, why
  now, what it is competing against in the reader's feed. One short block, not
  an essay. Ron reads it while deciding, so it teaches at the moment of
  judgement.
- Every draft links to **the signal it came from**, so Ron can check the claim
  before releasing it. Untraceable claims are the fastest way to lose his trust
  in the whole system.
- The Desk keeps a **running curriculum** on the Board: the small number of
  things Ron is currently getting wrong or has not yet learned, updated from his
  actual edits rather than from a syllabus.

### 4. Mentor

Owns the agenda and has an opinion Ron did not ask for.

- Sets the period's focus and says why, on the Board, before drafts appear.
- **Pushes back.** If Ron kills a good draft or keeps softening the same claim,
  the Desk says so once, plainly, and records the disagreement. It does not
  re-litigate every cycle.
- Reviews the record on a longer clock: what landed, what died, what it would do
  differently. This is a judgement, not a chart.

## State model

Five stores. Four do not exist today, which is the concrete measure of the gap.

### Signal feed (new)

Append-only stream of things that happened. Entry: source project, what
happened, when, links to the artifact, `story_score`. Most entries never become
posts. This is the raw material that separates the Desk from a prompt box, and
it is the one thing Clayrune has that a standalone social tool cannot get.

### Voice profile (new)

What stops the output smelling like marketing. Seeded from Ron's own writing —
commit messages, backlog text, chat — then **maintained by diffing every edit he
makes to a draft.** An edit is the highest-quality training signal in the system
and today it is discarded on save.

Holds: register, banned words and constructions, claims he will not make, how he
refers to the product, and a running list of rewrites with before and after.
The teacher's curriculum is derived from this, not maintained separately.

### Campaign board (new)

Live campaigns, each with a thesis, a sequence of intended posts, a state, and
the agenda note explaining why it is running now. This is what makes the Desk an
incubator rather than a draft generator, and it is what the Board surface
renders.

### Story ledger (new)

Every published post: platform, date, originating signal, the body as
published, and what happened after. Its job is **not** analytics. Its job is to
stop the Desk repeating itself and to let it reference its own earlier posts.
Without it, an autonomous writer re-announces the same feature every month.

### Draft queue (exists)

`social_queue` on the project record, five CRUD routes, edit / release / push
back. Keep the shape. Two changes: a draft carries its originating signal id and
its teaching block, and Release actually publishes.

**Storage constraint:** the four new stores do not go in `DATA_DIR`
(`data/projects/`) unless suffix-excluded in `load_projects()` and
`EXCLUDED_SIDECAR_SUFFIXES`. A stray file there becomes a malformed project and
500s both restart endpoints. The Desk is cross-project, so its state belongs
outside `DATA_DIR` entirely.

## Staffing

**The Desk is staffed from the existing roster. It does not add a marketing
agent.** The standing position of 2026-08-29 declined a separate marketing
expert alongside Posy, and its reasoning holds: the strategy layer already
exists on disk (`docs/LAUNCH_PLAN.md` and beneath it), and a generic marketer's
distinct value is paid acquisition and funnel work, which is out of scope while
Clayrune is free.

- **Posy** (`social-media-strategist`) writes and holds the platform judgement.
  The teaching block is written in her register, because her existing character
  already leads with the verdict on the copy.
- **Quill** (`market-researcher`) supplies the field, on a slow clock.
- **Dave** supplies sequencing where a campaign touches a real launch.

What the Desk adds is not a new persona. It is **the office they work in**: the
state they read and write, the clock they run on, and the surfaces Ron manages
them from. That is the whole distinction between this and what exists.

## Cadence

The Desk runs on the Clayrune scheduler. A run harvests signal, advances live
campaigns, and produces at most a few drafts, then stops. The mentor review runs
on a slower clock than the drafting.

## What it explicitly will not do

- **No paid acquisition, no ad spend, no budget loop.** The Henry reference has
  one; this does not. Clayrune is free by standing decision.
- **No analytics dashboard.** The ledger records outcomes to inform writing.
- **No multi-brand, no team, no approval chain.** One voice, one veto.
- **No publishing without explicit human release**, and no mechanism by which
  the Desk grants itself one.
- **No engagement farming**, reply automation, pods, or astroturf. Posy's
  boundaries hold at the system level, not just in her prompt.
- **It does not become a general campaign manager for other people's products.**
  One operator, his own projects.

## Open

These change the build materially and are not deferrable past v1.

- **Whose account.** Ron the builder, or Clayrune the product. This decides the
  voice profile's foundation and the entire register.
- **Which platforms ship first**, and which the Desk never touches. The vault
  holds five; that is not an argument for using five.
- **Cadence and volume.** What arrives, how often, and how many drafts in a
  batch stops being help and starts being homework.
- **Whether replies come back.** Write-only is far simpler and probably wrong,
  since the mentor function has nothing to review without them.
- **Whether the story score is worth being clever about** before there is any
  published history to learn from. A dumb score plus Ron's veto may beat a
  smart one.
