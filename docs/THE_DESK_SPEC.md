# The Desk — an in-house marketing department

**Status:** v0 draft, 2026-09-09. Written from Ron's own framing over four
messages: *"I personally suck at marketing and outreach, so I need that help and
guidance… someone to write the stories and publish for me, I can edit them…
a marketing manager which is both the incubator, teacher, mentor and actual
publishing office… all in one package… not just a persona, it has to be the
whole suite which also manage it all in one place."*

Supersedes the framing behind the Social Approvals Queue (`203082e`, `910d38f`).
That queue survives as one component of five.

Field scan landed: `docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md` (Quill,
session `ea807b553b05`). Its corrections are folded in below and marked.

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

**Corrected 2026-09-09 by the field scan** (`docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md`).
The v0 draft of this spec assumed browser-pane automation for publishing. That
was wrong, and the correction is cheap enough to make the browser path
indefensible:

- **X publishes through the API.** Pay-per-use since 2026-02-06, no subscription.
  Verified directly at `docs.x.com/x-api/getting-started/pricing`, not taken from
  the scan: **$0.015 a post, $0.200 if it contains a URL, $0.005 a read.**
- **LinkedIn publishes through Share on LinkedIn** (`w_member_social`), which is
  free and rate-limited at 150 requests per member per day. It needs no
  registered entity. The Community Management API does, and we do not need it,
  because the `clayrune` voice posts from a personal profile by the decision
  below.

Clayrune's posts point at releases and repos, so nearly all of them pay X's link
rate. **100 link-posts a month is $20.** That is the real number and it is not
worth engineering around.

**The Desk still reads through the browser pane, never through the API.** Reads
are $0.005 each, so watching replies at 1,000/day is $150/month while the pane
does it for nothing on a profile that is already signed in. This is the split
that makes the mentor function affordable, and it is why the pane infrastructure
stays load-bearing even though it no longer publishes.

**Build, do not rent.** Postiz (AGPL, self-hostable, MCP server) and Blotato
($29/month, hosted MCP) both solve fan-out across nine to twenty platforms. We
publish to two. The fan-out is the commodity part and we do not need it.

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

**BUILT 2026-09-09 (`adbc563`).** All four now exist, with 44 tests:

| Store | Where | Notes |
|---|---|---|
| Signal feed | `data/desk_signals.jsonl` | Append-only. Consumption is a *later line*, never a rewrite. |
| Voice profiles | `data/desk.json` | `record_edit()` learns from every human edit; `voice_brief()` renders it for the drafting agent. |
| Campaign board | `data/desk.json` | A campaign without a thesis is refused — it would be a folder. |
| Story ledger | `data/desk.json` | `similar_published()` is the repetition guard. |
| Draft queue | project record | Unchanged, as specced. |

Code: `mc/desk.py`, `mc/blueprints/desk_routes.py` (`/api/desk/*`), wired in
`server.py`. Both files are **siblings** of `DATA_DIR`, never members — a stray
`*.json` under `data/projects/` becomes a malformed project and 500s both
restart endpoints. Nothing in either module publishes; two tests assert that.

**Still to build:** the four surfaces (Board / Queue / Calendar / Ledger), the
signal *producers* that feed the feed from commits/backlog/journals, and the
drafting agent that turns a signal into a queued draft.

### Signal feed (new)

Append-only stream of things that happened. Entry: source project, what
happened, when, links to the artifact, `story_score`. Most entries never become
posts. This is the raw material that separates the Desk from a prompt box, and
it is the one thing Clayrune has that a standalone social tool cannot get.

### Voice profiles (new) — two of them

What stops the output smelling like marketing. Seeded from Ron's own writing —
commit messages, backlog text, chat — then **maintained by diffing every edit he
makes to a draft.** An edit is the highest-quality training signal in the system
and today it is discarded on save.

Each profile holds: register, banned words and constructions, claims that voice
will not make, how it refers to the product, and a running list of rewrites with
before and after.

Per the decision below there are two, and they are not variants of one another:

- **`ron`** — first person, a builder saying what he built and what it cost him.
  Owns X.
- **`clayrune`** — the product speaking about itself. Owns LinkedIn.

Both feed **one** teacher's curriculum. Ron is learning to market, not learning
two jobs, and the lessons that matter (specificity over adjectives, claims that
survive checking) are register-independent.

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

## Decided (Ron, 2026-09-09)

### Identity: both, split by platform

**X carries `ron`. LinkedIn carries `clayrune`.** A story that runs on both
platforms is written twice from the same signal, never cross-posted, and the two
drafts may make different claims because the two voices have different standing
to make them.

Consequence the Desk must enforce: `ron` may say "I got this wrong for three
weeks"; `clayrune` may not say it in the first person. The voice profile's
banned-construction list is the mechanism, not a style note in a prompt.

### Platforms: X and LinkedIn, v1

Reddit, Discord and Facebook are out of v1 even though the vault holds
credentials for all three. Reddit in particular is not a publishing target —
it rewards participation and punishes broadcast, so it needs a different
function than the one specified here.

### Finding — "Clayrune on LinkedIn" has two implementations, and the obvious one is wrong

A LinkedIn **Company Page** is the literal reading, and it starts at zero
followers reaching nobody. That is precisely the failure this design exists to
avoid, and it would make the Desk look broken for months for reasons that have
nothing to do with the writing.

**v1 posts the `clayrune` voice from Ron's existing personal LinkedIn profile**
— product register, product subject matter, his distribution. The vault's
LinkedIn entry already authenticates that profile, so nothing new is needed.

This is reversible: a Company Page can be created later and the `clayrune`
profile re-pointed at it once there is an audience worth moving. Creating the
page is outward-facing, so it is Ron's call, not the Desk's, and it is not on
the v1 path.

## The constraint that outranks everything else: being detected

The field scan turned up one finding that reshapes the design rather than
informing it.

**LinkedIn suppresses content it classifies as AI slop.** Its Chief Product
Officer said on 2026-08-21 that the report button had been used over a million
times and that flagged content was getting **40% fewer views** than a few weeks
earlier. Detection vendor Pangram measured 40% of long-form LinkedIn posts as
fully AI-generated. Audiences perceive AI-written posts as less authentic
*regardless of quality*, and the effect is strongest in B2B niches where founder
credibility is the asset.

That last clause describes this exact case. Clayrune's audience is developers
evaluating a tool built by one person, so the founder's credibility **is** the
product's credibility. Being caught running an AI social operation costs more
here than in almost any other segment.

Three consequences, all of them build requirements:

1. **Ron's edit is the detection defense, not a courtesy.** A draft released
   unedited is the single highest-risk artifact the system can produce. The
   voice profile exists to make his edits smaller over time, not to make them
   unnecessary.
2. **The Desk tracks its own edit rate and says something when it collapses.**
   If Ron starts releasing drafts unchanged, that is the failure mode, not the
   success condition. Precedent is already in this repo: the learning system's
   human promotion gate ran 80 promoted against 2 rejected and was never a
   quality gate at all. Assume the same drift here and instrument for it.
3. **Volume is a risk, not a goal.** Every additional post per week raises
   detection exposure against an audience that is already suspicious. This
   settles the cadence question below in one direction: start low.

**Silent failure is the specific danger.** Reddit shadowbans return success to
the poster, and suppressed LinkedIn posts look normal to their author. An
unattended system optimising on "did it post" sees green in both cases. Publish
receipts must record reach, not just acceptance, or the Desk cannot tell the
difference between working and being invisible.

## Open

- **Cadence and volume.** What arrives, how often, and how many drafts in a
  batch stops being help and starts being homework. Constrained above: start
  low, and treat volume as exposure.
- **Whether the story score is worth being clever about** before there is any
  published history to learn from. A dumb score plus Ron's veto may beat a
  smart one.
- **Whether `clayrune` posting from a personal profile confuses readers**, and
  what the tell is if it does. Watch for it; do not pre-solve it.
- **How long Share-on-LinkedIn app provisioning takes for a new
  `w_member_social` app**, and whether the 2026 authenticity crackdown has
  tightened that review. The scan could not close this and it gates LinkedIn v1.

## Resolved by the field scan

- **Replies come back, through the pane.** The mentor function needs them and
  the API price made write-only look inevitable; the pane makes it a non-issue.
- **Reddit stays out**, now for a second and better reason than "different
  function": its API terms define commercial use against a product with a launch
  plan, and its anti-spam model shadowbans exactly this pattern silently.
- **The approval gate is permanent.** Pinterest requires per-item human choice,
  YouTube requires express consent, and Postiz's own agent documentation asks
  for a human in the loop. This was already a principle here; it is also a term
  of service. Design it as a feature rather than a stage to outgrow.
- **Henry is not verifiable as shipped.** Announced and funded 2026-04-06, but
  five months on `meethenry.ai` is a landing page with a request-access button,
  no pricing, no docs, no reviews. Treat the announcement as a shape worth
  borrowing and nothing more; do not benchmark against a product nobody has
  used.
