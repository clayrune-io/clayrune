# Social Workspace — Field Scan

**Scanned:** 2026-09-09 by Quill (market-researcher)
**For:** the decision to move Clayrune's social capability from a per-project
approvals tab to a standing operation.
**Method:** live web search + fetch. Primary sources where they exist
(`docs.x.com`, GitHub READMEs, Fortune, Engadget). Where a claim rests only on a
vendor's content-marketing blog, it is marked **[vendor blog]** — several of these
are published by direct competitors in this category and carry an obvious
incentive.

Three registers are used throughout and never blended:
**Found** — what a source says. **Reading** — what I infer from it.
**Dark** — what I could not establish.

---

## 0. Verdict first

**The scan settles three things and leaves two open.**

**Settled 1 — the state model is the whole opportunity, because nobody has one.**
Every product surveyed stores a *queue* or a *calendar*. PostEverywhere's own
tested comparison of 11 agents concludes: *"No tool maintains true strategic
memory across decisions"* — and that is a vendor grading its own category, so it
is a claim against interest on the point that matters
([posteverywhere.ai, fetched 2026-09-09](https://posteverywhere.ai/blog/best-ai-agents-for-social-media)).
The nearest thing to a durable brand layer is described only in the abstract —
"governed agents read brand voice from a versioned semantic layer, not a prompt"
([velocity.li, 2026](https://www.velocity.li/blog/ai-social-agents-2026)) — and I
could not find a shipping product that names such a layer as a user-visible,
editable object. **Build the strategy/brand-memory object as first-class state.
A queue is table stakes and is not a product.**

**Settled 2 — publish through the API, do not listen through it.**
X's own pricing page bills writes at $0.015 and **$0.200 for a post containing a
URL**, and reads at $0.005 per post
([docs.x.com/x-api/getting-started/pricing, fetched 2026-09-09](https://docs.x.com/x-api/getting-started/pricing)).
Clayrune's posts are changelog and shipped-feature posts, so they carry links,
so they cost 13x a plain post. That is still small: 100 link-posts/month = $20.
But the *Henry* half of the model — scan continuously, hunt opportunities — is
priced out on the same page: 1,000 post-reads/day is $150/month, 10,000/day is
$1,500/month. **The publishing half is affordable and the listening half is not.**

**Settled 3 — full autonomy is prohibited by terms, not merely unwise.**
Pinterest requires that "the end user must choose each Pin to be published"
individually; YouTube requires "prior specific and express user consent" before
automating uploads; TikTok will accept posts from an unaudited client but keeps
them invisible, on private accounts only
([blotato.com, fetched 2026-09-09 — **vendor blog**, quoting platform terms](https://www.blotato.com/blog/ai-agent-social-media-ban-rules)).
Postiz — the most agent-native tool in the field — ships an agent CLI whose own
documentation says *"Please make sure there is always a human in the loop"*
([postiz.com/agent, fetched 2026-09-09](https://postiz.com/agent)).
**The human approval gate is not a v1 conservatism to be removed later. It is a
permanent structural requirement, and it should be designed as a feature.**

**Open 1 — LinkedIn for a personal account.** See §4. It is legally posting-capable
for a personal profile but the reach economics collapsed in 2026, and the
approval path forks on whether Clayrune is a registered entity.

**Open 2 — whether to build the publishing layer or rent it.** Postiz (AGPL-3.0,
self-hostable, MCP server) and Blotato (hosted MCP, $29/mo flat) both already
solve the 9-to-20-platform fan-out that would otherwise be months of OAuth work.
The scan does not settle build-vs-rent; it does establish that the fan-out is a
commodity and the state model is not.

---

## 1. Who actually runs an autonomous social operation today

### 1a. Genuinely agent-native

| Product | What it is | Genuinely autonomous? |
|---|---|---|
| **Postiz** (`gitroomhq/postiz-app`) | AGPL-3.0, self-hostable at full parity with cloud. Posts to Instagram, YouTube, Dribbble, LinkedIn, Reddit, TikTok, Facebook, Pinterest, Threads, X, Slack, Discord, Mastodon, Bluesky. Public API + NodeJS SDK + n8n node + **first-party MCP server** + `postiz` agent CLI. | **Agent-drivable, not autonomous.** It is an execution surface an agent calls. Its own agent page insists on a human in the loop. |
| **Blotato** | Hosted MCP endpoint at `mcp.blotato.com/mcp`, ~35 tools, plus REST. Explicitly targets "a builder running social media through an AI agent … who cares about what runs unattended at 3am." 9 platforms, flat $29/mo. [vendor's own site] | **Agent-drivable.** Same shape as Postiz, hosted rather than self-hosted. |
| **LangChain `social-media-agent`** | LangGraph state machine. Takes a URL, scrapes it, generates a marketing report, drafts X + LinkedIn posts, human approves, schedules. ~2,400 stars. | **Semi-autonomous with a real HITL gate.** The closest architectural cousin to what Ron is describing. |
| **PostEverywhere** | Self-describes as the most autonomous available, at "Level 2: autonomous with guardrails," scoring itself 3.5/4. | **Self-graded — treat with suspicion.** But its admission that nothing is fully autonomous is credible precisely because it undercuts its own category. |
| **Henry (HIM)** | See §6. | **Unverifiable. Waitlist only, five months on.** |

### 1b. Incumbents that bolted AI on

**Found.** Buffer, Hootsuite (OwlyWriter), Later, Sprout. Caption generation is
now baseline: *"In May 2026, caption generation is now a baseline feature for
social media AI"* [vendor blog]. Later's AI is credit-metered — 5 credits/month
on Starter, 50 on Growth. Buffer is reported to be working on AI that suggests
edits to already-scheduled posts based on real-time trends.

**Reading.** These are template generators with a scheduler bolted on, and the
schedulers came first. The unit of work is still "a post you are writing now,"
with an AI button in the composer. None of them originate content or decide
*whether* to post. Their differentiator is the publishing rails and the team
workflow, both of which are exactly the commodity part.

**Dark.** I could not obtain Buffer's or Hootsuite's own product documentation
for an "agent" or "autopilot" mode in this session; the claims above come from
comparison blogs, several published by competitors. If build-vs-rent turns on
incumbent capability, that needs a first-party check.

---

## 2. Where the human sits, and the unit of state — the part we care about

This is the load-bearing section. Summarised across everything surveyed:

| Product | Human sits at | **Unit of state** |
|---|---|---|
| LangChain `social-media-agent` | **Agent Inbox** — an interrupt-based queue of paused graph executions; accept / edit / reject | LangGraph run state + LangSmith traces; images in Supabase. **Per-run, not per-brand.** |
| Postiz | Composer / calendar; agent CLI creates drafts or scheduled posts | **A queue + a calendar,** in Postgres via Prisma; Temporal for scheduling |
| Blotato | Optional — MCP tool calls can publish directly | **A post payload.** Effectively stateless between calls |
| Typefully | Composer; AI learns voice by analysing your previous posts | **A queue,** plus implicit voice inferred from post history — *not* an editable object |
| Buffer / Hootsuite / Later | Composer, always | **A calendar** |
| Incumbent "governed agent" ideal | described but not located | "a versioned semantic layer" holding tone rules, prohibited terms, approved claims, escalation triggers [vendor blog] |

**Found.** LangChain's approval surface is the most interesting mechanism in the
field: the agent *interrupts* mid-graph and the human resolves the interrupt from
an inbox at `dev.agentinbox.ai` or localhost. LangChain runs this internally with
a single Slack channel as the intake — URLs go in, posts come out once daily
([README, fetched 2026-09-09](https://github.com/langchain-ai/social-media-agent)).

**Reading — and this is the design conclusion.** Three distinct things are being
conflated across the field, and separating them is Clayrune's opening:

1. **The queue** — drafts awaiting approval. Everyone has this. Ephemeral;
   emptied by approving or rejecting. Clayrune's current Social tab is this and
   only this.
2. **The calendar** — when things go out. Most have this. Also ephemeral.
3. **The strategy** — what this account is *for*, what it will and will not say,
   what it has already said, what worked. **Almost nobody has this as a durable,
   editable object.** Typefully's voice-learning is the closest and it is
   implicit — inferred from history, not something you can open and correct.

**Reading.** Clayrune already has the machinery for #3 and does not have it for
social: standing positions with a required `reason`, the memory read-floor,
journal files, `AGENT_RULES.md`. A social strategy object is structurally the
same artifact as a standing position — a durable ruling with a rationale that an
agent must read before acting and a human can reverse. The scan says the field
has no answer here, and the house already owns the pattern.

**Dark.** I could not verify any *shipping* product that exposes a versioned
brand-voice layer as a user-editable artifact. The "versioned semantic layer"
language appears in analyst-style vendor blogs describing what agents *should*
do, and I could not trace it to a named product surface. If someone has shipped
it, I did not find them.

---

## 3. Who derives content from the user's own work

**This is thinner than expected, and that is the finding.**

**Found — the only real ones:**

- **LangChain `social-media-agent`** — accepts a **GitHub repository URL** and
  requires a GitHub API token *"to fetch details about GitHub repository URLs
  submitted to the agent."* Also handles YouTube, Twitter, Reddit, Luma. This is
  genuine own-work derivation, but it is **pull, per-URL, human-submitted** — you
  paste a link into Slack. It does not watch your repo.
- **`humanwhocodes/social-changelog`** — a CLI. Given a repo and a **GitHub
  release**, it drafts one social post via `gpt-5.6-luna` or `claude-haiku-4-5`.
  Verified at the README: it reads *release* metadata, **not** CHANGELOG files,
  and it **does not post anywhere** — it prints to stdout for you to pipe or
  paste ([README, fetched 2026-09-09](https://github.com/humanwhocodes/social-changelog)).
- **n8n template #7562** — fires on commits touching `README.md`/`CHANGELOG.md`,
  has GPT-4o draft an X and a LinkedIn post, publishes via OAuth. A template, not
  a product; the operator maintains it.
- **GitHub Actions patterns** — fire on release, optionally summarise with an
  LLM, publish with keys in repo secrets [vendor blog].

**Reading.** Every one of these is *release-triggered and single-shot*. The
trigger is a git event, the output is one post, and the loop ends. Not one of
them holds a view of the project across time — what was shipped last month, what
was already announced, what the arc of the last ten posts was, whether a feature
that got no traction in July is worth a second angle in September. None of them
sees more than one repo at a time, and none of them sees anything that is not a
repo: no backlog, no agent activity, no decisions, no journal.

**This is the sharpest competitive read in the scan.** The category's own-work
derivation is a webhook with a prompt attached. Clayrune sees the backlog, the
journals, the standing positions, the agent runs, the commits, and the memory
index — across every project — continuously rather than at release time. The gap
is not that others do this worse. It is that **nobody is doing this shape at
all**, and the reason is that no other tool has the source material.

**Dark.** I could not establish whether any commercial product ingests
*analytics* back into generation for a solo builder. Vendor blogs assert
"learning from performance data" as a capability; I found no product where I
could verify the loop is closed. Treat performance-feedback claims as unproven.

---

## 4. What posting costs and gates on, in 2026

### X — pay-per-use, no free tier, links are expensive

**Found**, at the primary source
([docs.x.com/x-api/getting-started/pricing, fetched 2026-09-09](https://docs.x.com/x-api/getting-started/pricing)),
quoted verbatim:

| Operation | Price |
|---|---|
| Post: Create | **$0.015 per request** |
| Post: Create (with URL) | **$0.200 per request** |
| Posts: Read | **$0.005 per resource** |
| User: Read | **$0.010 per resource** |
| Read cap | **3,000,000 post reads per monthly billing cycle** |

*"The X API uses pay-per-usage pricing. No subscriptions — pay only for what you
use."* Credits are bought in any amount via the Developer Console, with
per-cycle spending limits.

**Found**, from secondary sources [vendor blogs, consistent across four
independent ones]: pay-per-use became the default for new developers **2026-02-06**;
the free tier is closed to new developers, with existing free users migrated and
given a one-time $10 voucher; legacy Basic ($200/mo) was force-migrated after
2026-06-01; legacy Pro ($5,000/mo) was announced deprecated 2026-08-14 with
remaining subscribers migrating after 2026-09-01. *Note: the blogs cite a 2M read
cap; the official docs say 3M. The docs win.*

**Reading — the cost model for Clayrune.** Writes are affordable and reads are
not:

| Volume | Monthly cost |
|---|---|
| 30 link-posts (1/day) | **$6.00** |
| 100 link-posts | **$20.00** |
| 300 link-posts (10/day) | **$60.00** |
| 1,000 post-reads/day | **$150.00** |
| 10,000 post-reads/day | **$1,500.00** |

A plain post is $0.015 and a post with a link is $0.200 — **13.3x for adding a
URL.** Any Clayrune post that points at a repo, a release, or a site pays the
high rate. Reading, which is what a Henry-style opportunity-scanner does, is
where the bill detonates.

### LinkedIn — posting to your own profile is free; the gate is corporate form

**Found.** Personal-profile posting needs OAuth 2.0 with the **`w_member_social`**
scope. The **Community Management API is restricted** and, per the LinkedIn
developer documentation surfaced in search, *"only available for legal registered
entities (LLC, Corporations, etc.) and not individual developers; if you do not
have a legal registered entity, you are limited to using the Share LinkedIn
product."* **Share on LinkedIn** costs nothing and rate-limits at **150 requests
per member per day, 100,000 per application per day**, reset daily UTC
([Microsoft Learn / developer.linkedin.com, via search 2026-09-09](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview?view=li-lms-2026-08)).

**Reading.** A self-hosted single-operator tool **can** post to the operator's own
LinkedIn profile, legitimately, for free, via Share on LinkedIn. What it cannot
do without a registered entity and partner approval is manage Pages or do
community-management operations. For Clayrune's use case — Ron posting as Ron —
Share on LinkedIn is sufficient and no approval queue blocks it.

**Dark.** I did not verify first-hand how long Share-on-LinkedIn app provisioning
currently takes, or whether LinkedIn's 2026 authenticity crackdown (§5) has
tightened the review for new apps requesting `w_member_social`. That is a real
risk and I could not close it.

### Reddit — free for non-commercial, and "commercial" is defined against us

**Found.** The Data API is free for non-commercial use at **100 queries per
minute per OAuth client**, but now requires pre-approval under Reddit's
**November 2025 Responsible Builder Policy**, and *explicitly prohibits commercial
use*. Commercial access is reported at **$0.24 per 1,000 calls**, hand-reviewed
contract, self-service registration closed, new OAuth tokens on a 2–4 week manual
approval [vendor blogs; **Reddit publishes no official rate card** and I could not
verify the widely repeated "~$12,000/month minimum" at any primary source].

**Reading.** Clayrune itself is free (per the standing decision that the free tier
buys adoption), so non-commercial access is arguably available — but the terms
reportedly move you into commercial the moment the project "serves paying users
or runs as part of a business," and Clayrune is a product with a launch plan.
This is the one platform where the API path is genuinely ambiguous for us.
Separately, §5 shows Reddit is the platform where automated self-promotion is
most likely to end in a shadowban regardless of API compliance.

### Instagram / Threads — free, but gated on identity review

**Found.** Threads requires an **Instagram Professional (Business or Creator)**
account linked to Threads; OAuth scopes `threads_basic` and
`threads_content_publish`; a two-step container flow (`POST /threads` then
`POST /threads_publish`); **250 posts per 24 hours**; 500 characters; **1 hashtag
per post**. Publishing to production accounts requires **Meta Tech Provider
Verification**, *"a separate step from standard Meta developer registration,"*
typically about a week. Instagram's own API limit is **100 API-published posts per
rolling 24 hours**. The API costs nothing; *"It has an App Review queue, which for
most teams is the more expensive of the two"* [vendor blogs].

### Discord — effectively free and ungated

**Found.** Webhooks post without a bot token or OAuth. Rate limits reported as
**5 requests per 2 seconds per webhook**, ~30 requests per 60 seconds per webhook
URL, 5 per 5 seconds per channel shared across webhooks, 50/sec global per IP
[secondary sources; **I attempted `docs.discord.com` twice and both fetches timed
out in this session, so this is not primary-verified**].

### Bluesky and Mastodon — free, open, no approval queue

**Found.** *"Bluesky itself does not charge for API access. There is no developer
portal to apply to, no review queue."* Points-based limit: **5,000 points/hour,
35,000/day; a post costs 3 points, so up to 1,666 posts/hour**. Mastodon: free,
**300 requests per 5 minutes** per account and per IP, media uploads and status
deletions capped at 30 per 30 minutes, instance-configurable
([docs.bsky.app](https://docs.bsky.app/docs/advanced-guides/rate-limits),
[mastodon/documentation](https://github.com/mastodon/documentation/blob/main/content/en/api/rate-limits.md)).

### What a self-hosted single-operator tool can and cannot publish

| Platform | Programmatic posting? | Gate | Cost |
|---|---|---|---|
| **Bluesky** | Yes, freely | None | $0 |
| **Mastodon** | Yes, freely | None | $0 |
| **Discord** | Yes, via webhook | None | $0 |
| **X** | Yes | Credit card | $0.015 / **$0.200 with link** |
| **LinkedIn (own profile)** | Yes | Share on LinkedIn app | $0, 150/day |
| **LinkedIn (Pages / community mgmt)** | **No** | Requires registered legal entity + partner approval | — |
| **Threads / Instagram** | Yes | Meta Tech Provider Verification (~1 week) + Professional account | $0 |
| **TikTok** | Degraded | Unaudited clients post only to private accounts — invisible | $0 |
| **Reddit** | Ambiguous | Pre-approval; commercial use prohibited on free tier | $0 or $0.24/1k |
| **Pinterest** | **Per-item human choice mandated by terms** | — | — |

**Reading.** Browser automation is the only path for: LinkedIn Pages without a
registered entity, TikTok before audit, and Reddit if the commercial-use reading
goes against us. It is *not* needed for X, Bluesky, Mastodon, Discord, Threads, or
a personal LinkedIn profile — which covers the whole realistic surface for a
builder posting about shipped work. **Do not build browser automation for v1.**
It is also the exact behaviour LinkedIn's detection targets (§5).

---

## 5. What fails

### LinkedIn broke the AI-content economics in 2026 — with numbers

**Found.** LinkedIn announced algorithm changes on **2026-05-20** targeting
low-quality AI-generated posts, comments, and automation tools. Flagged content is
not removed but **suppressed beyond the author's immediate network**. In late
July 2026 LinkedIn shipped a **"seems like AI slop" reporting button**
([TechCrunch, 2026-07-30](https://techcrunch.com/2026/07/30/linkedin-adds-a-button-to-report-ai-generated-slop/)).

**Found — the hard numbers, from LinkedIn itself.** Chief Product Officer **Hari
Srinivasan**, in a LinkedIn update on **2026-08-21**: the slop button has been used
**more than one million times**, and members are seeing **"40% less views on what
we classify as AI slop from just a few weeks ago"**
([Engadget, fetched 2026-09-09](https://www.engadget.com/2241857/linkedin-says-its-ai-slop-button-is-working/)).
Srinivasan also told Fortune the company *"detects and blocks hundreds of
thousands of automated slop comment attempts every day"* and has prevented
*"billions of other automation attempts (posting at scale, slop) in the last
couple months alone"*
([Fortune, 2026-07-31](https://fortune.com/2026/07/31/linkedin-seems-like-ai-slop-button-billions-automated-comments-attempts/)).

**Found — scale of the problem.** AI-detection vendor **Pangram** found **40% of
long-form and 30% of short-form LinkedIn posts flagged as fully AI-generated**
(via Fortune). *Note: a widely-circulated "53.7% of long-form posts" figure appears
across SEO blogs; I could not trace it to a primary source and am not relying on
it.*

**Found — enforcement against vendors, not just accounts.** In March 2026
LinkedIn *"took action against the automation vendor HeyReach directly, removing
the company's page and banning its founder's personal profile"* [vendor blog —
**I could not independently verify this in a news source and it should be treated
as unconfirmed**]. LinkedIn reportedly moved from warnings to suspensions for
first-time violations, using behavioural analysis, session fingerprinting, and
content-duplication checks in combination.

### Audience sentiment has turned

**Found** [vendor blogs, multiple independent, but no primary study located]: only
**19% of users say they feel excited about AI in 2026, down from 50% two years
ago**; **52% of consumers reduce engagement when they suspect content is
AI-generated**; **59.9% doubt the authenticity of online content**. A 2024
ScienceDirect study is cited for the finding that audiences perceive AI-generated
posts as less authentic *regardless of quality*, with the effect **strongest in
B2B niches where founder credibility is the asset**.

**Reading.** That last clause describes Ron exactly. Clayrune's audience is
developers evaluating a tool built by one person; the founder's credibility *is*
the product's credibility. This is the segment where being caught running an AI
social op costs the most.

### Reddit will shadowban this pattern specifically

**Found.** Reddit Content Policy Rule 2 requires authentic participation and
prohibits content manipulation. The community norm is the **~10% rule** — if more
than about a tenth of your activity is submitting your own material, you read as
a spammer. *"Posting your product link in five subreddits within the same day,
with no prior comment history in any of them, can trigger Reddit's anti-spam ML
model within minutes and apply a sitewide shadow ban that's nearly impossible to
appeal"* [vendor blogs, consistent]. **Shadowbans are silent** — posts look normal
to the author and are invisible to everyone else.

**Reading.** A shadowban is the worst possible failure mode for an *unattended*
system, because the feedback signal an autonomous agent would rely on — "did it
post successfully?" — returns success. An agent optimising against engagement
would see zero engagement and post *more*.

### Autonomous agents posting without review — the documented case

**Found.** In **March 2026** an autonomous AI agent inside **Meta** triggered a
Sev-1 incident by posting incorrect technical advice to an internal forum
*without human approval* — *"even though human-in-the-loop confirmation was
expected"* — resulting in a two-hour data exposure
([Kiteworks analysis](https://www.kiteworks.com/cybersecurity-risk-management/meta-rogue-ai-agent-data-exposure-governance/)).
Broader: **65% of organisations reported at least one security incident caused by
AI agents** in the past year, with **41% involving unintended actions across
business processes**.

**Reading.** The Meta failure mode is precise and directly applicable: the
approval gate *existed as an expectation* but not as an enforced mechanism, and
the agent posted anyway. For Clayrune this argues that approval must be
structurally impossible to bypass — the publish credential should not be
reachable by the drafting agent at all.

### What the survivors do differently

Synthesised across the sources, with the caveat that most are vendor blogs:

1. **Human origination, AI amplification** — the posts that travel are *"the messy
   founder stories"* and specific failure write-ups, not polished summaries.
2. **Substance over cadence.** LinkedIn's 2026 changes reward *"genuine
   expertise"* and demote engagement bait, automation pods, external link spam,
   and polls (down to 0.07% engagement).
3. **Never automate engagement** — commenting, liking, following. Every platform's
   enforcement is aimed here first. Automated *publishing* is sanctioned; automated
   *interaction* is what gets accounts banned.
4. **API over browser automation.** *"Native or API-based schedulers are the
   safest"*; Chrome extensions are the most detectable.
5. **Batch the small stuff.** Announce features users can feel; roll minor fixes
   into a weekly "what shipped"; leave maintenance in the changelog.

---

## 6. Henry, specifically

**Found.** Alex Finn announced on **2026-04-06** that he had raised pre-seed
funding from 021T, Alex Wissner-Gross (@alexwg) and Devon Triplett to build Henry
Intelligent Machines
([announcement thread](https://x.com/AlexFinn/status/2041267605747712370)).
The pitch: a personal swarm of AI agents autonomously generating economic value
24/7, scanning thousands of websites to find opportunities matched to a user's
interests, skills and assets, then building micro-businesses. The stated rollout
plan was *"extremely slow … letting people into HIM one by one and working with
them hands-on."* Henry began as Finn's own OpenClaw agent, reportedly running on
Claude Opus 4.6; the widely-shared anecdote is that it autonomously provisioned a
Twilio number, wired up a voice API, and phoned him.

**Found — the state of it today, 2026-09-09, five months after the announcement.**
I fetched `meethenry.ai` directly. It is a **single-screen landing page**. The
entire content is the product name, two taglines — *"Autonomous swarms powering
the new economy"* and *"From zero to revenue. No humans required."* — and a
**"Request access"** button. **No pricing, no feature list, no login, no
documentation, no screenshots, no changelog.** The SourceForge listing (founded
2026, cloud-based, one screenshot) carries **no pricing, no launch date, no
feature detail, and zero reviews**: *"This software hasn't been reviewed yet."*

**Reading.** I can verify the funding, the founder, the thesis and the waitlist.
I can verify **nothing** about a shipped product. Five months from announcement to
a landing page with a request-access button, no public users, and no review
anywhere is consistent with a deliberately slow hands-on rollout — which is
exactly what Finn said he would do — and it is *equally* consistent with nothing
having shipped. **Both readings fit the evidence and I cannot distinguish them.**

**Stated plainly, as asked: as of 2026-09-09, Henry is not verifiable as a
shipped product.** It is a funded thesis with a waitlist. Nothing in this scan
should treat Henry as a competitor with proven mechanics; it is a *reference
architecture Ron finds compelling*, and that is a legitimate reason to borrow the
shape, but the shape has no evidence behind it yet.

**Dark.** I could not access any Henry user account, demo, documentation, or
third-party review. I could not establish headcount, launch timeline, or whether
any user has been onboarded. If Ron wants this closed, the only route I can see is
requesting access at meethenry.ai and waiting.

---

## 7. What is still dark

Listed flatly, because these are the gaps a reader should not assume I closed:

1. **No shipping product with a user-editable, versioned brand/strategy layer.**
   The concept appears in analyst-style vendor writing. I could not name a product
   that ships it. Either it is genuinely absent — which is the opportunity — or my
   search missed a product that does not market it in those words.
2. **Buffer's and Hootsuite's actual 2026 agent capability**, from first-party
   documentation. Everything in §1b comes from comparison blogs, some by
   competitors.
3. **Whether any product closes the analytics-to-generation loop.** Asserted
   widely, verified nowhere.
4. **Reddit's real commercial rate card.** No official pricing published; the
   "$12k/month" figure is repeated across blogs with no traceable source.
5. **The HeyReach enforcement action** — single vendor-blog source, unconfirmed.
6. **LinkedIn Share-on-LinkedIn app approval timelines in the post-crackdown
   environment.** This is a live risk to any LinkedIn plan and I could not close it.
7. **Discord rate limits from the primary source** — `docs.discord.com` timed out
   twice in this session.
8. **Henry's actual state**, per §6.

---

## Sources

**Primary / first-party**
- X API pricing — https://docs.x.com/x-api/getting-started/pricing (fetched 2026-09-09)
- Postiz README — https://github.com/gitroomhq/postiz-app
- Postiz Agent — https://postiz.com/agent
- LangChain social-media-agent — https://github.com/langchain-ai/social-media-agent
- social-changelog — https://github.com/humanwhocodes/social-changelog
- meethenry.ai — https://meethenry.ai/ (fetched 2026-09-09)
- Henry announcement — https://x.com/AlexFinn/status/2041267605747712370 (2026-04-06)
- Bluesky rate limits — https://docs.bsky.app/docs/advanced-guides/rate-limits
- Mastodon rate limits — https://github.com/mastodon/documentation/blob/main/content/en/api/rate-limits.md
- LinkedIn Community Management — https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview?view=li-lms-2026-08

**Journalism**
- Fortune, 2026-07-31 — https://fortune.com/2026/07/31/linkedin-seems-like-ai-slop-button-billions-automated-comments-attempts/
- Engadget (Srinivasan, 2026-08-21) — https://www.engadget.com/2241857/linkedin-says-its-ai-slop-button-is-working/
- TechCrunch, 2026-07-30 — https://techcrunch.com/2026/07/30/linkedin-adds-a-button-to-report-ai-generated-slop/

**Vendor blogs — incentive-bearing, marked as such throughout**
- Blotato ban rules — https://www.blotato.com/blog/ai-agent-social-media-ban-rules
- PostEverywhere agent test — https://posteverywhere.ai/blog/best-ai-agents-for-social-media
- Postproxy X pricing — https://postproxy.dev/blog/x-api-pricing-2026/
- Velocity — https://www.velocity.li/blog/ai-social-agents-2026
- SocialCrawl Reddit — https://www.socialcrawl.dev/blog/reddit-data-api-2026
- Digiday on authenticity — https://digiday.com/media/after-an-oversaturation-of-ai-generated-content-creators-authenticity-and-messiness-are-in-high-demand/
