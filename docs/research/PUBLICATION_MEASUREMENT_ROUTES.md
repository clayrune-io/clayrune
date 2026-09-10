# Publication measurement routes: every way to tell whether a Desk post worked

**Status:** research, 2026-09-10. Read-only. No production code written, no command
run that posts, publishes, or writes to a platform or the vault.
**For:** `docs/THE_DESK_SPEC.md` §1 (receipts must record reach, not acceptance) and
§4 (the mentor reviews what landed and what died).
**Precedent this builds on:** `docs/_campaign/attribution_findings_2026-08-05.md`.

**Evidence labels used throughout.** *READ* = I opened the file. *MEASURED* = I ran
a read-only command on this machine today and am quoting its output. *FETCHED* =
I fetched the public page today and am quoting it. *INFERRED* = my reasoning; not
checked. Anything without a label in a claim that matters is called out in §9.

---

## 0. Recommendation

**The smallest honest v1 is three numbers per post plus one baseline, and it
needs almost no new machinery.** Most of the pieces already exist and are simply
not connected: the ledger has a `url` field nobody fills (MEASURED: 0 rows in
`data/desk.json`), `record_outcome()` has no caller, and a daily scheduled pass
already reads per-post LinkedIn numbers through the browser pane and writes them
as prose into a 165 KB journal in a different repo where no code can use them.

1. **A receipt with the permalink.** `mc/desk.record_published(url=…)` on release.
   Nothing else is possible without this. Effort: hours.
2. **Platform-side reach and intent per post, snapshotted at 48 h and 7 d**, into
   `record_outcome()` as an additive history rather than a single overwrite.
   - LinkedIn: from the pane, exactly as the "Social standings" schedule already
     does (out-of-network share, impressions, reactions, comments, link clicks,
     profile views). Reroute its output from prose to the ledger route.
   - X: from the API's **Owned Reads at $0.001 per post** (FETCHED, §2.A). This
     is not the $0.005 read the standing position priced. Reading 20 posts daily
     is under a dollar a month, and `url_link_clicks` + `user_profile_clicks`
     are the only per-post intent numbers X offers to anyone.
3. **One click count per post on our own domain**: a per-post redirect path
   (`clayrune.io/l/<post-id>` → the real destination), counted by the existing
   `tools/_campaign/cf_funnel.py` bot/browser split and snapshotted daily,
   because the free Cloudflare zone dataset forgets everything older than
   **8 days** (MEASURED). This is the fix for the 08-04 failure: it names the
   post at the first hop, which is where the referrer chain currently loses it.
4. **A written baseline** for site and repo on a no-post day (the standings
   journal already recorded one for 09-09), so a post's site effect is stated as
   a delta and labelled *correlation*, never *cause*.

**What v1 cannot claim:** that a post caused an install, a star, or a user; where
any visitor came from unless they hit the redirect or carried a referrer; that an
impression was a reader; anything from the RUM referrer panel below its 1:10
sampling floor; anything about clones (MEASURED: 188 unique cloners on 08-31 with
one page view); X non-public metrics for posts older than 30 days (FETCHED);
and any "best time to post" from a sample this small.

**Standing position, answered.** The "listen through the pane, never the API"
reason ($0.005 a read, $150/month at 1,000/day) is about *listening at volume*.
It does not bind reading your own posts' metrics: X bills those at $0.001 each
and the volume is the number of posts, not the number of replies. **For X, own-post
metrics should go through the API**, and the pane should not be used for X at all:
the schedule that has been running since 08-26 carries the line *"X: NEVER log in.
It got Ron's account temporarily blocked once"* (READ, `data/schedules.json`,
schedule `2b9d5c60`). **For LinkedIn the position reopens on cost and closes on
the gate**: the analytics API is free of charge but restricted to registered legal
organizations with a business-domain email and a Page-verified app (FETCHED,
primary source, §2.D). Unless Ron has or forms an entity, the pane stays the
LinkedIn read path, with its measured failure mode (a signed-out profile has
produced two dark cycles and blocked a dated ship slot as of today).

**Ranked (value to a solo builder ÷ effort), highest first:**

| # | Route | Attributes a post? | Effort | Verdict |
|---|---|---|---|---|
| 1 | Receipt with `url` in the ledger | prerequisite | hours | build first |
| 2 | LinkedIn per-post numbers → `record_outcome`, 48 h / 7 d | yes (platform-side) | small; reroute existing pass | build |
| 3 | X own-post metrics via Owned Reads API | yes (platform-side, incl. link clicks) | small once publish OAuth exists | build |
| 4 | Per-post redirect on clayrune.io + daily CF snapshot | yes (our side, click) | small–medium | build |
| 5 | Comment / reply text into the brief | qualitative | medium | build for LinkedIn only |
| 6 | GitHub traffic snapshot (`gh_traffic.py`) | host-level only | already running | keep, do not extend |
| 7 | Cloudflare RUM referrer panel | host-level, sampled 1:10 | read by hand, monthly | do not build on |
| 8 | LinkedIn `memberCreatorPostAnalytics` API | yes, richer than pane | apply + 1–4 wk review | only if an entity exists |
| 9 | Release-asset and install-click counts | no | exists | context line only |
| 10 | Timestamp-vs-traffic correlation | no | — | a sentence in the mentor review, not a feature |
| – | UTM-only tagging | no (nothing reads it) | tiny | do not build alone |
| – | Analytics dashboard, posting-time recommender, clone-based install counts, X pane reading, Facebook/Reddit/Discord measurement | — | — | do not build |

---

## 1. What exists on this machine today (verified)

| Artifact | What I found |
|---|---|
| `mc/desk.py` `record_published()` (lines 642–665) | READ. Stamps `platform, voice, body, signal_id, campaign_id, project_id, url, published_at, outcome: None`. Docstring: "NOT a publish path." |
| `mc/desk.py` `record_outcome()` (line 680) | READ. Overwrites `row['outcome']` with the dict given. Only callers: the route below and `tests/test_desk.py:298`. |
| `mc/blueprints/desk_routes.py:252` | READ. `POST /api/desk/ledger/<post_id>/outcome` → `_desk.record_outcome`. Nothing in `mc/`, `static/`, or `server.py` calls it (MEASURED: grep, main tree only; the earlier hit under `.clayrune/agents/` is a worktree copy). |
| `data/desk.json` | MEASURED. `ledger: []`, zero rows. 498 lines in `data/desk_signals.jsonl`. |
| `mc/desk_brief.py` `build_brief()` | READ. Injects signal, campaign thesis, `voice_brief()` (verbatim edits), `similar_published()` overlap, and `PLATFORM_NOTES` (X link cost, LinkedIn demotion). No outcome data of any kind. |
| `mc/blueprints/browser_routes.py` | READ (index). Routes: launch, stream, input, `selection` (line 833, `Runtime.evaluate` on `window.getSelection()`), stop, status, profiles. No read-page route. |
| `tools/_campaign/gh_traffic.py` | READ. `gh api` for views/clones/referrers/paths; merges into `history.json`; `REPO = "ronle/clayrune"`. MEASURED: `gh api repos/ronle/clayrune` redirects to `clayrune-io/clayrune`, 15 stars. |
| `tools/_campaign/history.json` | MEASURED. 50 daily rows 2026-07-22 → 2026-09-09; 24 snapshots 08-05 → 09-10T15:01Z. |
| `tools/_campaign/cf_funnel.py` | READ + RAN (`14`). Zone GraphQL, drops own IP and tunnel hosts, splits BROWSER/BOT by UA, counts `/e/*` beacons. **Does not persist anything.** |
| `data/schedules.json` `2b9d5c60` "[Social standings]" | READ. Daily 08:00, project `clayrune_website`, enabled, last run 2026-09-10T15:00Z. Runs the two scripts above, then drives the signed-in `linkedin` profile over raw CDP and records per-post impressions, out-of-network %, reached, reactions, comments, reposts, profile viewers, followers. Writes to `docs/_journal/social-standings.md`. |
| `C:\Users\levir\MissionControl\clayrune_website\docs\_journal\social-standings.md` | MEASURED. 165,324 bytes, 2,877 lines, daily entries since 08-26. Holds real per-post numbers as prose. |
| `data/skills/_proposed/clayrune_website/2026-08-26…`, `…08-29…`, `…09-04…` `EXPLORATION.md` | READ. Three distilled sessions that attached raw CDP to the LinkedIn pane and read the analytics panel. |
| Vault (`GET /api/secrets`, metadata only) | MEASURED. Entries `x.com`, `linkedin`, `facebook`, `github`, `github-token`, `reddit`, `discord`, `ycombinator`, `alternativeto`, `google`. No Cloudflare entry; `CLOUDFLARE_API_TOKEN` is in this shell's environment. |
| Live `https://clayrune.io/` | MEASURED (curl, three UAs). 22,956 bytes. Scripts: `/cdn-cgi/…/email-decode.min.js` and `app.js?v=10`. `app.js` lines 126–142: `crBeacon()` fires `navigator.sendBeacon('/e/' + evt)`. **No `cloudflareinsights` tag, no `URLSearchParams`, `location.search`, `utm_`, or `document.referrer` anywhere in the site source** (MEASURED: grep of the website repo; `git log -S cloudflareinsights` returns nothing). |

---

## 2. The avenues, one by one

### A. X per-post analytics through the API

**FETCHED** (`docs.x.com/x-api/getting-started/pricing`, today):
- "Posts: Read | $0.005 per resource", "Charged per resource returned in the response."
- "Owned Reads are requests made by your own developer app for your own data (posts, bookmarks, followers, likes, lists, and more). These endpoints are priced at **$0.001 per resource**", and the list includes "GET /2/users/{id}/tweets | Your own posts".
- "Post: Create $0.015", "Post: Create (with URL) $0.200". No subscription, no minimum.

**FETCHED** (`docs.x.com/x-api/fundamentals/metrics`): `public_metrics` = retweets,
replies, likes, quotes, bookmarks, impressions (any auth). `non_public_metrics` =
`url_link_clicks`, `user_profile_clicks`, `engagements`, `impression_count`, user
context on owned posts only, and "only available for posts created within the
last 30 days". `organic_metrics` adds the same click fields.

- **Can tell you:** per post, impressions, replies, bookmarks (a save), link
  clicks and profile clicks. The last two are the only intent-adjacent numbers X
  exposes, and they name the post.
- **Cannot:** anything after 30 days for the non-public fields; who clicked; what
  they did on the site.
- **Cost:** N posts × $0.001 per daily read via `/2/users/{id}/tweets`. 20 live
  posts read daily for a month ≈ $0.60. Even at the $0.005 lookup rate it is $3.
- **Gate:** OAuth 2.0 user context for the `@RanLevi15` account. The publish path
  needs the same token, so this is not an extra credential. *Unverified:* whether
  pay-per-use accounts receive `non_public_metrics` (historically a paid-tier
  field); the pricing page does not itemise metrics fields.
- **Effort:** small once publishing exists. One scheduled call, one `record_outcome`.
- **Attribution class:** post → platform-side outcome, exact.

### B. X per-post analytics through the pane

The author's post page shows Views and the analytics view shows the rest. But the
standings schedule states, verbatim: *"X: NEVER log in. It got Ron's account
temporarily blocked once."* (READ.) With A costing under a dollar a month, there
is no case for the pane on X. **Do not build.**

### C. LinkedIn per-post analytics through the pane (exists, undirected)

**READ, the schedule and journal.** The daily pass already reads, for every
Clayrune post in the last 30 days: impressions, **out-of-network %** (its stated
primary metric: "a post that never left the network never had its copy tested"),
members reached, reactions, comments, reposts, profile viewers per post, total
followers. Benchmarks it carries: 08-05 = 3,395 impressions / 82 % out-of-network;
08-19 = 107 / 23 %; 08-26 = 54 / 7 %. Later entries: 08-29
(`urn:li:activity:7499591415874658304`) 325 / 29 % at 10 d; 09-03
(`urn:li:activity:7501392665280720896`) 82 / 11 % at 17 h, 175 / 22 % at 120 h.

- **Can tell you:** the fullest per-post picture available anywhere for LinkedIn,
  including link clicks (the 08-29 exploration read "6 clicks" for the 08-05
  post) and profile views from the post.
- **Cannot:** survive a signed-out profile. MEASURED in the journal: 09-09 and
  09-10 entries both read "LinkedIn: still no data… Profile signed out… Needs a
  human", and a dated ship slot (`e121e0e7`) is blocked on it today.
- **Cost:** none in money. Its method is raw CDP against the pane's Chromium
  (the pane has no read endpoint; the schedule documents finding the port via
  `Win32_Process`). `DESK_ACCOUNT_LAYER_PROPOSAL.md` §3.3 already specifies the
  narrow sanctioned `POST /api/browser/text` that would replace this.
- **Gate:** the `linkedin` named profile being signed in; a human re-login when
  it is not.
- **Effort to make it useful:** small. The reader already runs; it writes prose
  to a journal that code cannot consume. Have it POST the numbers to
  `/api/desk/ledger/<id>/outcome` keyed by the permalink. The 09-04 exploration's
  rule applies: "engagement signal too sparse… requires ≥48 h for stable
  metrics", so snapshot at 48 h and 7 d rather than daily-forever.
- **Attribution class:** post → platform-side outcome, exact.

### D. LinkedIn `memberCreatorPostAnalytics` through the API (the reconciliation)

**FETCHED**, all Microsoft Learn, today:

- *Member Post Statistics* (updated 2026-05-15): `GET /rest/memberCreatorPostAnalytics`,
  permission `r_member_postAnalytics`, "for the authenticated member". Metrics
  from version 202604: IMPRESSION, MEMBERS_REACHED, RESHARE, REACTION, COMMENT,
  **POST_SAVE, POST_SEND, LINK_CLICKS, PREMIUM_CTA_CLICKS,
  FOLLOWER_GAINED_FROM_CONTENT, PROFILE_VIEW_FROM_CONTENT**. One `queryType` per
  call; `aggregation=DAILY` available for most.
- *Increasing Access* (updated 2026-08-17): Community Management API is "open
  to all approved developers — apply through the standard process";
  `r_member_postAnalytics` is in it "(supported only in API versions starting
  from 202506)". Development tier is "the default tier provisioned": **500 calls
  per app per 24 h, 100 per member per 24 h**, and "You must complete your
  integration and testing within twelve months". Standard tier needs a separate
  form and screencast.
- *Community Management App Review* (updated 2026-02-11), the sentence the
  field scan could not source: **"At this time, our Community Management APIs
  are only available to registered legal organizations for commercial use cases
  only."** Also: "Be prepared to share your business email address and your
  organization's legal name, registered address, website, and privacy policy…
  **Personal email addresses won't pass the vetting process**", and "Ensure a
  super admin of the LinkedIn Page associated with your organization has
  verified your application." Development-tier review checks: approved use case,
  verified business email, verified organization, verified website/domain,
  Page-verified app. A rejected app cannot re-apply; you create a new one.
- Postiz issue #1680 (FETCHED, 2026-07-06): "third-party vendors can apply for
  access at no cost via a form". No comments, no report of an individual being
  approved. The other "individuals excluded" pages surfaced by search are a
  consultancy selling application help and vendor docs; none is primary.

**Reconciled:** both claims are true and do not conflict. *Free* means no fee is
charged anywhere in the documentation. *Registered legal entity* is the vetting
criterion for the same form. So the API is free of charge and gated on
incorporation, a business-domain email, and a LinkedIn Page that verifies the
app. Whether a sole proprietorship with a domain email and a Page passes is
"at LinkedIn's discretion" and I found no first-hand report either way.

- **Can tell you:** everything the pane reads, plus saves, sends, and followers
  gained per post, without a browser session to lose.
- **Cannot:** be obtained by Ron-as-individual with `leviran1@gmail.com` (the
  vault's LinkedIn username, MEASURED). Note `w_member_social` publishing is
  *not* gated this way; the spec's publish decision stands.
- **Cost:** $0. Volume: 100 calls/member/day covers ~9 posts × 11 metrics, or
  more posts with fewer metrics.
- **Gate:** entity + business email + Page + 1–4 week hand review (vendor
  figure, unverified); 12-month clock to Standard tier.
- **Effort:** the form, then small code.
- **Verdict:** this *does* reopen the position's cost reasoning for LinkedIn, but
  the gate decides it. If Ron has or intends an entity (this repo has a
  clayrune_cloud sibling with investor material; I did not check whether an
  entity exists), apply now because the review clock is the long pole. If not,
  C is the v1 path.

### E. Cloudflare zone analytics (`cf_funnel.py`)

**MEASURED today (`python tools/_campaign/cf_funnel.py 14`):** the free zone
refuses anything older than **"1w1d"** (six of fourteen days returned
"cannot request data older than 1w1d"). Last 8 days, real browsers: 1,728
requests, 0 × 5xx; per-day uniques 31–68; NL `45.148.x.x` addresses are 25 % of
"browser" traffic on their own; beacons `/e/project-open` 9, `/e/demo-open` 9,
`/e/install-click` 5, `/e/demo-launch` 3, `/e/conv-switch` 3.

- **Can tell you:** daily unique browser IPs, per path, per country, own-domain
  beacon counts, with the bot split that three earlier cycles got wrong without.
- **Cannot:** referrer (the docstring and `gh_traffic.py:9` both record that the
  free zone dataset lacks `clientRefererHost`); anything older than 8 days; any
  per-visitor join (beacons are bodyless, cookie-less counts).
- **Cost:** none. **Gate:** the API token this shell already has.
- **The hole:** nothing snapshots it. `gh_traffic.py` solved the identical
  problem for GitHub with `history.json`; the zone reader needs the same
  `cf_history.json` or the post-day traffic for any post is gone eight days later.
- **Attribution class:** correlation only, unless combined with I.

### F. Cloudflare Web Analytics (RUM) referrers, the panel nobody had read

I read it today. **MEASURED** via GraphQL at *account* scope (it is not a zone
field: `rumPageloadEventsAdaptiveGroups` returns "unknown field" under
`zones{}`), `siteTag cc04b12d…`:

| date | visits (sum) | referrers |
|---|---|---|
| 08-04 | 10 | none |
| 08-05 | 30 | none |
| 08-11 | 70 | facebook.com 20, l.facebook.com 20, github.com 20, none 10 |
| 09-07 | 10 | www.facebook.com 10 |
| 09-09 | 20 | lm.facebook.com 10, none 10 |

Every figure is a multiple of ten because **`avg{sampleInterval}` = 10 on every
row** (MEASURED). Retention: a query wider than "13w2d" is refused, so roughly
three months. Also present: `ronl.clayrune.io` (the operator's tunnel host)
emits RUM events, so this dataset needs the same host filter as `cf_funnel.py`.

- **What it settles:** the 08-04 record day shows **10 sampled visits with no
  referrer**; the panel does not explain 08-04 either. What it does confirm is
  a Facebook-referred wave on 08-11 that matches the GitHub referrer table
  (below), and the standings journal's own explanation: "ONE Hebrew-language
  Facebook group post out-referred the entire Show HN launch 2:1 on GitHub
  referrers" (READ). Facebook is out of v1 by decision; this is recorded as a
  fact for Ron, not a recommendation.
- **Cannot:** see a post that sends fewer than ~10 visitors, which is every
  LinkedIn post to date (the 3,395-impression post produced 6 link clicks).
- **Inconsistency to flag:** the 08-05 findings say `index.html` ships the
  beacon with token `7f120cfe…`. Today the live HTML under three user agents
  carries no `cloudflareinsights` tag, the website repo has never contained one
  (`git log -S` is empty), and the dataset's site tag is `cc04b12d…`. Events are
  nonetheless arriving through 09-10. I could not determine the injection path
  (INFERRED: a Cloudflare-side automatic injection not visible to curl). The
  `rum/site_info` REST call returned 403 with this token.
- **Cost:** none. **Effort:** none to read by hand; not worth code.
- **Attribution class:** host-level correlation, heavily sampled.

### G. GitHub repo traffic (`gh_traffic.py`)

**MEASURED from `history.json`:** latest snapshot 2026-09-10T15:01Z, 15 stars,
14-day views 33 / 24 unique, clones 1,081 / 413 unique. Referrers now:
github.com 4, Bing 2, clayrune.io 2, reddit.com 2. Union of every referrer host
seen across all 24 snapshots: facebook.com 26, news.ycombinator.com 20,
m.facebook.com 16, clayrune.io 12, github.com 4, kagi.com 4, and singletons.
**`t.co`, `lnkd.in`, and `linkedin.com` have never appeared in any snapshot.**

- **Can tell you:** daily views/uniques/clones (only if snapshotted; GitHub keeps
  14 days), top-10 referrer *hosts*, top-10 paths. Stars, forks. The 09-10
  standings entry notes first-ever hits on `/releases` and `/issues`, which are
  deliberate acts by an evaluator.
- **Cannot:** name a post. A host is the finest grain. Five LinkedIn posts,
  one of them with 3,395 impressions, have produced zero attributable repo
  visits, because the link goes to clayrune.io first and the second hop shows
  up as referrer `clayrune.io` (8–12 uniques), which carries no memory of which
  post started it.
- **Clones are not installs.** 08-31: 337 clones / 188 unique cloners against 1
  page view. 09-09: 289 / 43. The 08-05 findings' discriminator (install-script
  fetches vs cloners) is still unsettled, and cannot be settled retroactively
  because of E's 8-day window.
- **Cost:** none. **Gate:** `gh` is authenticated as `ronle` (MEASURED).
- **Effort:** none; already daily. Note `REPO` still says `ronle/clayrune` and
  relies on the redirect; the paths table now lists both `/ronle/clayrune` and
  `/clayrune-io/clayrune`.
- **Attribution class:** host-level correlation.

### H. UTM tags on the links we post

- **Cost on X:** zero *incremental*. The $0.200 rate is for containing a URL at
  all (FETCHED); a query string on a URL that is already there adds nothing. A
  post with no link has nothing to tag. So UTM never changes the X bill; the
  decision to include a link does, and `PLATFORM_NOTES` already tells the writer
  that.
- **Cost on LinkedIn:** zero incremental; the demotion is for the presence of an
  external link, not its parameters (READ, `PLATFORM_NOTES`; the 08-29
  exploration's body-link vs comment-link comparison). The first-comment link
  can carry the tag.
- **What it buys today: nothing.** Nothing on clayrune.io reads the query string
  (MEASURED, site source and live `app.js`). The zone dataset dimension
  `cf_funnel.py` uses is `clientRequestPath`, which excludes the query
  (*unverified whether the free zone exposes a query dimension*). The RUM
  dataset has no UTM dimension that I know of (unverified). So a tagged link is
  invisible to every reader we have.
- **To make it visible:** either five lines in `app.js` that read `?src=` and
  fire `/e/src-<tag>` (a path, which E already counts), or route I below, which
  is strictly better.
- **Verdict:** do not build UTM alone. The tag is free; the reader is the work,
  and the redirect is the better reader.

### I. A redirect path we control (`clayrune.io/l/<post-id>`)

- **Shape:** every link the Desk publishes points at `clayrune.io/l/<ledger-id>`,
  which 302s to the real target (release, repo, docs). Cloudflare Pages
  supports this with a `_redirects` file (INFERRED from general Pages knowledge;
  not fetched today). No JavaScript, no cookies, works in in-app browsers with
  referrers stripped, works for GitHub-bound links, and the hit lands in E as a
  path that `cf_funnel.py` can bot-split. `MARKETING_PATHS` in that script is a
  fixed set; it needs a `/l/` prefix rule like the existing `/e/` one.
- **Can tell you:** clicks per post from real browsers, by day and country,
  regardless of destination. This is the number the 08-04 incident lacked.
- **Cannot:** follow a click to a demo open or install (no per-visitor join
  without cookies, and adding one is a privacy posture change the site has
  deliberately avoided: `app.js` line 126 "No cookies, no PII").
- **Cost:** none. X still charges $0.200 for the link itself.
- **Gate:** a website-project deploy, which is outside steward-reversible scope
  per the 08-05 findings; and E's snapshotting, or the count evaporates in 8 days.
- **Effort:** small–medium (redirect file, ledger-id in the link, `cf_funnel.py`
  prefix, `cf_history.json`).
- **Attribution class:** post → click, exact, our side.

### J. Correlating post timestamps against traffic with no attribution

Post dates on record (READ, `data/coordination/clayrune_website/events.jsonl`
and the journals): LinkedIn 08-05, 08-19, 08-26, 08-29 22:28Z, 09-03 ~21:20Z;
Discord 08-31 01:11Z; a Hebrew Facebook group post around 08-04/08-10
(date not recorded precisely). Against `history.json` (MEASURED):

| event | repo views next day | repo uniq cloners | site (E) |
|---|---|---|---|
| LinkedIn 08-29 | 0 | 4 | out of retention |
| Discord 08-31 | 1 | **188** | out of retention |
| LinkedIn 09-03 | 7 / 4 uniq | 41 | 68 uniq browsers vs ~45 baseline, but NL bot-like IPs dominate |
| nothing (09-09) | 11 / 6 uniq | 43 | 58 uniq, 10 demo opens, 11 downloads |

The best repo day of the last month (09-09) had no post; the 188-cloner day had
one page view. **This method cannot distinguish "the Discord post drove 188
installs" from "a scanner ran"**, and nothing we hold can. The 09-10 standings
entry reaches the same conclusion independently ("unattributable, and this time
I could run every check").

- **Verdict:** keep it as one sentence in the mentor's long-clock review
  ("traffic moved / did not move in the 48 h after this post"), always labelled
  correlation. Never a feature, never a posting-time recommender; the sample is
  five posts.

### K. Install and download counts

- **Release assets** (MEASURED, `gh api …/releases`): v2.2.0 Windows 3, macOS 2;
  v2.0.2 Windows 7, macOS 1; v2.0.0 macOS 8; v1.5.1 macOS 1. **22 downloads
  across all releases.** Too small to attribute anything and too small to move.
- **`/e/install-click`** (E): 5 in 8 days. Same.
- **Installers do not phone home** (MEASURED, `installer/install.sh` and
  `install.ps1`: only fetch nvm, Claude's installer, and clone the repo). Adding
  a ping is a product decision with privacy weight; not recommended for this.
- **Verdict:** a context line in the mentor review. Nothing to build.

### L. Stars, forks, issues, releases-page views

Stars 9 → 15 over 36 days (MEASURED, snapshots). The 09-07 standings entry:
"Stars moved 13 → 15 on a day nothing was published… Unattributable, and worth
more per unit than any clone". Deliberate acts, host-level at best. Context only.

### M. Comments and replies as text

The intent signal the feature scan asks to carry, and the raw material for the
next brief (a reader's own words). LinkedIn: readable through the pane on the
post page. X: `reply_count` is in `public_metrics` (A); reply *text* is a
mentions/search read at $0.005–$0.001 per resource and is the volume the
standing position priced, so it stays out of v1 on X. Effort medium; value high
for the writer, nil for a dashboard. **Build for LinkedIn only, via the narrow
`POST /api/browser/text` in the account-layer proposal, never via arbitrary
`Runtime.evaluate`.**

### N. Hand-entered numbers

`DESK_ACCOUNT_LAYER_PROPOSAL.md` §3.2 Tier A: four number fields on the ledger
row posting to the existing outcome route. This is the fallback every automated
route degrades to, costs an hour, and is what makes the profile-signed-out
failure mode survivable. Include it in v1 regardless of the rest.

---

## 3. Attribution or correlation, precisely

| Route | Names the post | Names the outcome | Class |
|---|---|---|---|
| A. X API own-post metrics | yes | impressions, replies, bookmarks, **link clicks, profile clicks** | attribution, platform-side |
| C. LinkedIn pane | yes | impressions, OON %, reached, reactions, comments, **link clicks, profile views** | attribution, platform-side |
| D. LinkedIn API | yes | C plus saves, sends, followers gained | attribution, platform-side |
| I. Per-post redirect | yes | a click reached our domain | attribution, our side |
| H. UTM alone | yes, in the URL | nothing (no reader) | none until I or a beacon exists |
| G. GitHub referrers | host only | a repo view | correlation |
| F. RUM referrers | host only, sampled 1:10 | a page view | correlation |
| E. Zone analytics / beacons | no | site activity by day | correlation |
| J. Timestamps | no | a delta | correlation |
| K, L. Downloads, stars | no | a delta | correlation |

**The 08-04 case, re-run against this table.** Every route that existed then
(E, G, and F unread) is in the correlation half. The only routes that would have
named the cause are A/C (the platform's own click count on the post) and I (a
click on our domain carrying the post id). Both are in v1 above.

---

## 4. The UTM / shortlink question, answered

Tagging outbound links is **not** the highest-leverage fix on its own, because
nothing reads the tag. The highest-leverage fix is the *reader*, and the best
reader is a per-post redirect path on clayrune.io (I), which makes tagging
unnecessary.

The X link price does not change the recommendation; it changes the framing.
A link costs $0.200 whether or not it is measurable, so **every link we pay
for should be a measurable one**: a redirect path costs nothing more. The
Desk's existing rule (include a link only when the link is the point) stays,
and the redirect makes each paid link report back.

LinkedIn's demotion likewise does not change it: the link already lives in the
first comment by the team's own measured practice (08-29 exploration), and a
redirect URL there is no more demoted than a bare one. One caveat, INFERRED:
readers can perceive an own-domain short link as tracking; keep the path
readable (`clayrune.io/l/scheduler-post`) rather than opaque.

---

## 5. The smallest honest v1

Ron's framing: *"reactions can be measured in different ways, posting time, site
traffic, unique visitors, correlation between post to traffic etc."* Each of
those exists in some form on this machine today; what is missing is the join.

**Build:**

1. Ledger receipt with `url` and `published_at` on release (exists in code; give
   it a caller). A "Mark as posted" paste box for hand-posted permalinks.
2. `record_outcome` made additive (`history[]` with `at`, `source`, `url_seen`;
   `latest` recomputed), per the account-layer proposal §3.4. Two readings per
   post: 48 h and 7 d. Sources: LinkedIn pane (C), X Owned Reads (A), hand (N).
3. Per-post redirect (I) plus `cf_history.json` snapshotting in `cf_funnel.py`
   so the click count outlives the 8-day window.
4. A baseline record: for the 7 days before each post, median daily unique
   browsers, demo opens, download hits, repo uniques. The post's site line is
   "vs baseline", written as correlation.

**The number worth acting on:** out-of-network share and link clicks per post,
side by side with the structural facts of the post (link placement, image,
ending, length). That is what the three explorations were reaching for by hand.

**What v1 must say it cannot claim** (put this text on the Ledger surface, not
in a doc): no causal claim about installs, stars, or users; no origin for
visitors who did not hit the redirect; impressions are not readers; reactions are
not intent; clones are not people; a post older than 30 days has no X
non-public metrics; the RUM panel is blind below ten visitors; and no timing
recommendation from fewer than roughly thirty posts.

---

## 6. What feeds the next brief, and what stays out

`build_brief()` already injects verbatim edits and the overlap check. Add one
block, **"How the last five posts in this voice did"**, with facts only:

**In:**
- Per post: published date, platform, out-of-network % (LinkedIn) or
  impressions (X) *as a distribution diagnostic* (did it get shown at all, which
  is also the suppression tell the spec's "being detected" section demands).
- Comments count and the **verbatim comment or reply text**, truncated. This is
  the intent signal and the only reader language the writer will ever see.
- Link clicks and profile clicks / profile views from the post.
- The structural facts of each post, stated flatly: link in body or in comment,
  image or not, ended on a question or not, length. Facts, not rules.
- The reading's age ("48 h reading" vs "7 d reading"), so the writer does not
  compare a 17-hour number with a ten-day one (the 09-04 exploration's mistake).

**Out, because optimising for them makes the writing worse:**
- Reactions and impressions as a *target* or a *score*. The feature scan's
  reason stands: a story score trained on likes learns to write bait.
- Any "what works" rule derived inside the brief from n ≤ 10. The 08-26
  exploration derived three rules from three posts; carry the facts and let the
  mentor's slower review draw rules, once.
- Site traffic deltas, clone counts, stars, followers. Correlation belongs to
  the mentor review, not to the moment of drafting.
- Posting-time recommendations.
- Anything that reads as an instruction to maximise. The brief's job is to
  make the next post more specific and less repetitive, not more popular.

---

## 7. What I would not build, plainly

- **UTM tagging without a reader.** Free and invisible.
- **A dashboard or charts.** The spec says no analytics dashboard; the Ledger
  row shows `latest` and "last read 2 d ago" and stops.
- **A posting-time or correlation engine.** Five posts, sampled referrers,
  8-day retention. It would produce confident nonsense.
- **Clone-based install counts.** 188 cloners on a one-view day.
- **X through the pane.** Account was blocked once; the API is cheaper anyway.
- **Reply listening on X at volume.** The standing position's reason is intact
  for that use and it stays out of v1.
- **Facebook, Reddit, Discord measurement.** Out of v1 by decision. Recorded
  for Ron: Facebook is the only social host that has ever moved the GitHub
  referrer table (26 + 16 uniques in the 08-13 snapshot), so if the platform
  decision is ever revisited, that is the datum.
- **An installer phone-home.** Privacy weight, tiny signal.
- **Anything on the RUM panel.** Read it by hand if a big day needs a host name.

---

## 8. Standing-position check

- **"Publish through the API, listen through the pane, never the reverse."**
  The reason is X's $0.005 per read at listening volume. FETCHED today: that
  rate is real, and a cheaper $0.001 Owned Reads rate exists for your own posts.
  Own-post metrics are not listening; they are N reads for N posts. **The reason
  does not bind own-post metrics on X.** For reply listening on X it still binds.
- **X-specific?** Yes. LinkedIn's analytics API is free of charge (no fee stated
  on any Microsoft Learn page fetched, and Postiz reports "at no cost"). **The
  cost reason does not apply to LinkedIn; the entity gate does.** So the
  position for LinkedIn should be restated as "pane unless an entity exists, then
  API", not "pane because reads cost money".
- **Pane as the read path** has a measured single point of failure: session
  cookies. Two dark cycles this week and a blocked ship slot. N (hand entry) is
  the mitigation, not a nicety.

---

## 9. What I could not verify

- Whether X pay-per-use apps receive `non_public_metrics` / `organic_metrics`
  at all (docs describe user-context auth but not tier); and whether
  `GET /2/tweets?ids=` lookups bill at the Owned Reads rate or the $0.005 rate.
- Whether LinkedIn approves a sole proprietor with a domain email and a Page for
  Community Management Development tier; whether Ron has a registered entity.
- The review time ("one to four weeks") is a consultancy's figure.
- How RUM events reach Cloudflare for clayrune.io with no beacon tag in the
  served HTML or the repo history, and why the site tag differs from the one the
  08-05 findings quoted. The `rum/site_info` REST call was 403 with this token.
- Whether the free zone dataset exposes a query-string dimension (matters only
  for UTM, which I do not recommend anyway).
- Cloudflare Pages `_redirects` behaviour, from memory not from the docs.
- LinkedIn's `lnkd.in` and X's `t.co` preserving query strings on redirect
  (general knowledge; not tested).
- The exact date of the Hebrew Facebook group post; the journal names it, the
  events log does not date it.
- I did not open `docs/_social/CAMPAIGN-2026-09.md` or the earlier campaign
  docs beyond the attribution findings; post dates come from
  `events.jsonl` and the two journals.

---

## 10. What was read, run, or fetched

**Read:** `docs/THE_DESK_SPEC.md`; `docs/research/SOCIAL_PRESENCE_FEATURE_SCAN.md`
§3; `docs/research/DESK_ACCOUNT_LAYER_PROPOSAL.md` §3;
`docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md` lines 228–275;
`docs/_campaign/attribution_findings_2026-08-05.md`; `mc/desk.py` 636–695;
`mc/blueprints/desk_routes.py` 250–258; `mc/desk_brief.py` in full;
`mc/blueprints/browser_routes.py` (route index); `tools/_campaign/cf_funnel.py`,
`gh_traffic.py`, `_dl_paths.py`; `data/schedules.json` (schedules `2b9d5c60`,
`0566ec5b`, `486c367c`); `data/coordination/clayrune_website/events.jsonl`;
`installer/install.sh`, `install.ps1` (network calls); the three
`EXPLORATION.md` files under `data/skills/_proposed/clayrune_website/`;
`C:\Users\levir\MissionControl\clayrune_website\docs\_journal\social-standings.md`
(tail 130 lines and headings) and `social-watch.md` (grep); website `app.js`.

**Ran (read-only):** `python tools/_campaign/cf_funnel.py 14`; Cloudflare
GraphQL `rumPageloadEventsAdaptiveGroups` at account scope for 07-30→09-10 with
`refererHost`, `requestHost`, `requestPath`, `siteTag`, `sampleInterval`;
`gh api repos/ronle/clayrune`, `…/releases`; `gh auth status`;
`curl localhost:5199/api/secrets` (metadata); curl of `clayrune.io/`,
`download.html`, `blog.html`, `docs.html`, `demo/`, `app.js`; Python over
`history.json` and `desk.json`; grep over the repo and the website repo,
`git log -S cloudflareinsights` there.

**Fetched:** `docs.x.com/x-api/getting-started/pricing`;
`docs.x.com/x-api/fundamentals/metrics`; Microsoft Learn *Member Post
Statistics* (li-lms-2025-11 view, content through 2026-08), *Increasing Access*
(2026-01 view, updated 2026-08-17), *Community Management Overview* (2026-08),
*Community Management App Review* (2023-11 URL, content updated 2026-02-11);
`developer.linkedin.com/product-catalog/marketing/community-management-api`;
`github.com/gitroomhq/postiz-app/issues/1680`;
`singhamandeep.com/linkedin-community-management-api-access/` (consultancy).
`docs.mixpost.app` timed out.
