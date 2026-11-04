# Desk channel impact — MC-1152

Status: **D1 decided (A); cleared for P1 implementation**. Specification only.
Author: Marlow, 2026-11-04. Backlog item `e4796e7b`.

## 1. Problem and user

A campaign owner needs to compare channels before moving effort between them.
The Desk already reads some post metrics, but it has no conversion reader and
its legacy tracked link has no consumer. An impression, a platform link click,
a site visit and a completed signup must remain distinct measurements.

Done means a campaign has one conversion definition, newly generated links carry
stable attribution identifiers, and the campaign screen compares only the data
each source actually supplies. Missing coverage must be visible. A table must
not manufacture channel conversions from repository growth.

## 2. Binding decisions and release boundary

Dave's decisions, recorded in the main checkout's journal
`docs/_journal/e4796e7b-desk-channel-impact.md`, and the dispatch settle:

- Desk-generated UTM links replace newly inserted `?ref=desk-<id>` links.
  Existing posts, their bodies and their tags remain untouched.
- One conversion definition per campaign; conversion sources are pluggable.
- GitHub is the first source: stars, release downloads and referrer observations.
  Site analytics APIs (Plausible, Umami, GA4) form the second source family.
- Platform clicks occupy a separate column and are never conversions.
- No Clayrune-hosted redirect or tracking pixel; no paid AI-visibility tracker in v1.

`docs/THE_DESK_SIMPLIFICATION_PLAN.md` section 4 governs release: only a human
approves a campaign, approval is bounded, and widening a bound invalidates it.
Measurement must not approve a campaign, change destinations, increase cadence
or spend, generate a send, or resume publishing. The later email channel's own
approval policy belongs to MC-1153, not this feature.

### Open D1: what does the GitHub-first release expose?

GitHub exposes repository traffic/referrers, star timestamps and asset download
counters. Its documented responses do not join a star or download to a Desk UTM,
post, channel or campaign. **Inference:** UTM generation alone cannot make GitHub
stars or downloads attributable per channel. Repository-wide observations may
overlap several concurrent campaigns. Dividing them by clicks or post counts
would invent attribution.

| Option for Dave | User-visible behavior | Cost / limitation |
|---|---|---|
| **A — recommended: contextual GitHub release** | Show per-channel post metrics; show GitHub outcomes separately as repository context; channel conversions say `Not attributable by this source`. | Requires separate contextual and attributed presentation. Channel conversion comparison remains unavailable until a suitable analytics adapter works. |
| **B — hold the comparison view** | Build UTM and GitHub collection first; keep the channel-impact view dark until the site-analytics phase passes attribution tests. | Delays visible benefit and adds an analytics connection prerequisite. GitHub collection can still preserve history meanwhile. |

Neither option changes the decided source order. Neither permits estimated
channel conversions. **DECIDED (Dave, 2026-11-04): A.** Users see per-channel post
metrics now; GitHub outcomes appear as repository context, and channel conversion
cells read `Not attributable by this source` until a site-analytics adapter proves
attribution. Rationale and reversal: `docs/_journal/e4796e7b-desk-channel-impact.md`. The remaining sections specify the
shared contract; references to the contextual view are conditional on A.

## 3. Current implementation constraints

Source inspection on this branch, 2026-11-04:

- `mc/desk_engagement.py` defines `Reader`, API/pane selection, X metric names,
  and coverage/error semantics. `XReader(include_nonpublic=False)` leaves X
  link-click reads off. `LinkedInReader` itself remains gap-only.
- Actual pane dispatch also uses `mc/desk_engagement_pane_digest.py`, including
  platforms beyond X. It accepts exact counters only. Do not infer overall
  LinkedIn coverage solely from the gap-only class; selected account/read route
  and the generic pane reader determine actual coverage.
- `mc/desk_engagement_poll.py` writes post outcomes through
  `desk.record_feed_outcome`. It enforces read consent, daily metric checks,
  a recent-post window and paid-read budget reservations. Reuse these persisted
  outcomes; opening a comparison must not cause extra platform reads.
- `mc/desk_engagement_targets.py` resolves multiple accounts on one platform.
  Its first-account key may move when an account is removed. Persist account IDs,
  not a bare platform key, as attribution identity. Coverage must match the account.
- `mc/desk_engagement_account.py` merges presence read settings with saved account
  metadata. This feature must not bypass the selected route or human read gates.
- `static/js/desk.js` contains the legacy `deskQueueInsertLink` tag insertion.
  Find all live composer/link-insertion paths before implementation; changing
  this legacy function alone does not establish Desk v1 coverage.
- `static/js/desk-v1-results.js` already owns the campaign Goal screen and manual
  goal measurements. Results is a legacy alias for Goal. Do not create a second
  Results destination or silently replace an existing manual goal.

## 4. Measurement semantics

All stored times use UTC. Requested ranges are half-open `[start, end)` and carry
the source's timezone/granularity when it cannot answer exactly in UTC.

Each measurement declares its unit, scope, time basis and coverage:

| Measurement | Permitted interpretation |
|---|---|
| Post impressions/views and engagement | Latest observed cumulative counter per published receipt, separately named; never add repeated snapshots of the same counter. |
| Platform clicks | Exact outbound clicks if the selected existing reader supplies them. Missing stays unknown. No new API permission is implied. |
| Attributed conversions | Only events/aggregates the source can filter by the campaign/channel identifiers under its documented attribution semantics. |
| GitHub stars/downloads | Repository or asset outcomes in an observed interval, without channel credit. A net star change is not gross new stars. Downloads are not unique people or installs. |
| GitHub referrers | Repository-level referral observations, not star/download conversions and not a UTM-to-outcome join. |

A successful, fully covered zero is `0`. An unsupported field, missing baseline,
partial pagination, missing day, expired credential, failed request, or unknown
attribution is `null` plus a reason. Retain last-good values with their original
timestamps and a separate failed-attempt status; never relabel stale data as fresh.

Do not sum lifetime post metrics into an in-period acquisition figure. Label them
`Latest totals for posts published in this period`. Do not sum unique users across
days or channels, add overlapping rolling windows, or sum both a platform subtotal
and its constituent accounts. No winner, causal lift, ROI or automatic effort
reallocation is derived from these observations in v1.

## 5. Data model and persistence contract

The existing Desk state is `data/desk.json`, outside `DATA_DIR` (`data/projects/`).
The proposed impact store follows that boundary in **`data/desk_impact/`**. It must
be runtime user data, gitignored and absent from installers/build bundles. No new
JSON file enters `data/projects/`; no project-loader suffix exclusion is needed
for this location. Tests must pin that it cannot become a project record.

`mc/desk_impact_store.py` owns persistence, locking, validation and versioning.
Use the existing atomic-write/fsync convention for replaceable records; preserve
observability for failed state I/O. Separate adapter logic from storage bodies.

| Record | Location / required fields |
|---|---|
| Conversion definition and its revisions | `data/desk_impact/<project_id>/definitions.json`, keyed by campaign ID: `id`, `revision`, `label`, `source_id`, `connection_id`, `metric`, `unit`, validated source-specific `target`, `effective_at`, `created_at`. Exactly one active revision per campaign; no definition is also valid. |
| Link manifest | Same project directory, `links.json`: `link_id`, campaign ID, account/channel ID, post/version ID, original destination, final URL, exact UTM map, creation time. Multi-channel posts have separate link records per destination version. |
| Source snapshots | Same project directory, `snapshots.jsonl`: schema version, unique snapshot ID, source/connection/target identity, definition revision where relevant, request window, observed/effective timestamps, metric/unit/value, counter kind, scope, attribution dimensions, coverage and provenance. |
| Read-attempt status | Same project directory, `coverage.json`: source/target, last attempt, last success, safe error code/detail, retry-after, historical gaps and incomplete-page status. Failure does not overwrite successful observations. |

Resolve project/campaign/account ownership server-side and validate directory IDs;
never interpolate a user-supplied path. Credential values and raw user-level
analytics/stargazer records must not enter these files, logs or responses. Store
only vault references in connection metadata, using the existing connection system.

The impact definition is the source-of-truth measurement binding for the campaign,
not a second campaign goal. The existing Goal screen displays/edits that binding
alongside its target. Preserve manual entries and existing goal values; attaching
or changing a source must not reinterpret them. A new definition revision starts
a distinct series. Prior revisions remain inspectable and are not silently summed.
Until a tested binding exists, automatic goal progress stays dark.

Snapshot retries are idempotent for source/target/metric/window/dimensions/revision.
Source corrections replace the effective value for that key in derived queries,
while preserving the prior observation as history. A truncated final JSONL line
after a crash is reported and excluded; corruption inside a file is not swallowed.
No silent retention cap. Existing campaign archive/delete behavior must not gain
an implicit impact-data purge.

## 6. Link generation

`mc/desk_impact_links.py` owns deterministic URL generation and reverse lookup.
Use stable internal IDs, not titles, account names or mutable slugs:

- `utm_source`: platform/listing source slug.
- `utm_medium`: `social`, `email` or `agent` as supplied by the channel adapter.
- `utm_campaign`: stable campaign ID.
- `utm_content`: stable link ID resolving to the post/version and account.

The manifest provides account-level distinction when two accounts share a source.
Generate these URLs only for an explicit Desk tracked-link insertion/generation;
do not rewrite arbitrary pasted URLs, historic drafts, released payloads or receipts.
Preserve unrelated query arguments and fragments with a URL parser. Signed or
otherwise non-editable URLs must be refused as tracked links with an explanation.
If a proposed destination already has conflicting UTM values, expose the conflict
for an explicit edit; never silently erase someone else's attribution.

Freeze link identity with the outbound version/receipt. Preview must show the URL
that the publisher receives. Reopening a draft must not create another identity.
UTM tags do not assert that the destination collects them. If its source cannot
consume them, show that coverage limit. The legacy `ref` tag is not dual-emitted.

## 7. Conversion-source interface

New modules: `mc/desk_impact_sources/base.py`, `registry.py`, `github.py`, and later
one module per analytics provider. The registry imports adapters and holds data,
not their implementations. This is separate from the platform engagement Reader:
repository/site outcomes are not post engagement.

Required operations (names are the implementation contract):

| Operation | Contract |
|---|---|
| `capabilities(connection)` | Metadata only: supported metrics, units, target schema, attribution dimensions, time granularity, history limits, credential/gate requirements. No network or secret dispensing. |
| `validate_definition(definition, connection)` | Reject unsupported metrics/targets, wrong-project connections and incompatible units. One selected metric, not a sum of stars plus downloads. |
| `collect(request, transport, clock)` | Bounded, injectable read-only call returning normalized observations, coverage, safe errors, request count and retry-after. Request includes project/campaign/definition revision, connection reference, target and exact window. |
| `normalize(raw, request)` | Deterministic validation: finite nonnegative counts where appropriate, exact timestamps/dimensions, explicit cumulative/interval/window semantics. Reject malformed rows; report partial coverage. |

Adapters must declare whether campaign/channel/post attribution is supported.
The aggregation layer must not upgrade repository scope into campaign scope.
Collection deduplicates shared repository/connection reads across campaigns and
joins observations by reference, rather than counting the same downloads twice.

Use existing vault resolution and project/unattended scope checks. A saved GitHub
token's existence is not evidence of sufficient repository permissions; report
403/404/expired-scope failures explicitly. Never create a token or weaken a gate.
Rate limits and failed attempts are not zero conversions. Analytics endpoints must
use existing connection/network policy and validated service URLs, not arbitrary
URLs from a model or response. No credential-bearing cross-host redirect.

## 8. First adapter: GitHub

Use authenticated, read-only GitHub API calls for the configured repository.
Resolve the user's chosen GitHub vault reference server-side; no operator-specific
token name belongs in source defaults. A token is not created by the adapter.

Daily collection while Clayrune runs preserves the available series. Startup may
backfill what the API still supplies; it must mark older gaps. No public endpoint
or machine-always-on assumption is introduced. A manual refresh respects the same
deduplication and rate limits. Importing a module must perform no collection.

- Traffic views/referrers: preserve their native daily/rolling semantics. GitHub
  documents a 14-day window and top-ten referrers; overlapping referrer snapshots
  cannot be added or subtracted into an exact arbitrary-range series. Traffic
  permissions are distinct from public repository access.
  [GitHub traffic API](https://docs.github.com/en/rest/metrics/traffic).
- Stars: the timestamp representation is available via
  `application/vnd.github.star+json`. Complete pagination is required for complete
  interval counts. The docs announce admin/collaborator restrictions and also
  describe public access, so a successful scoped live read is a release check;
  do not assume either entitlement. Retained star observations are not proof of
  every star/unstar event.
  [GitHub starring API](https://docs.github.com/en/rest/activity/starring).
- Downloads: preserve each release asset's stable ID and observed `download_count`.
  [GitHub asset response](https://docs.github.com/en/rest/releases/assets).
  **Derived rule:** only two compatible counter observations establish an interval
  delta. First observation is a baseline, not new campaign downloads. Asset
  replacement, deletion, a decreasing counter or missing endpoint observation
  breaks continuity; never clamp a negative delta into a successful zero.

GitHub observations cannot establish channel conversion rates. Referrer hosts
alone also cannot distinguish two Desk campaigns, two accounts or organic traffic
from a Desk post. See D1 before exposing the comparison release.

## 9. Second source family: site analytics

Keep Plausible, Umami and GA4 behind the same interface; selecting the first
provider within this family is deferred until that build phase, not chosen here.
Quill's scan section 2 establishes Plausible UTM dimensions; Umami's exact UTM
breakdown and GA4 desktop authorization remain unverified.

A provider is not selectable until fixtures and an authorized live read demonstrate
the configured event/goal, campaign filter, channel/post breakdown, window semantics
and permissions. Site visits may be the campaign's explicit conversion definition;
they must not be relabelled signups. A site download event is distinct from a
GitHub asset download. Provider-attributed site events do not prove downstream
GitHub stars or downloads without a documented join, which v1 does not supply.

Never silently substitute a different event, account, date range or provider.
Unknown/direct traffic remains outside channel credit. Expose the provider's
attribution semantics; no Desk-invented multi-touch model or session identity.

## 10. Campaign comparison view

Mount **Channel impact** inside the existing campaign **Goal** screen. Its own
module is `static/js/desk-v1-channel-impact.js`, with its own CSS file and backend
projection `mc/desk_impact_compare.py`. Add only mount/registry seams to existing
screen files. A new route group belongs in
`mc/blueprints/desk_impact_routes.py`; proposed operations are definition read/write,
cached comparison read, and explicit refresh. These are new contracts, not existing
endpoints. GET comparison is local-only and never starts network collection.

Each campaign channel/account row shows: channel identity, published-post count,
impressions/views with their exact label, engagement breakdown, platform clicks,
selected conversion count, and coverage/last-read time. Conversion column uses the
single definition's label/unit. Removed historical accounts retain labelled rows;
the view cannot add destinations. All plan accounts remain visible even with no
posts. Missing values explain their cause rather than displaying zero.

For option A, a separate `Repository context — not attributed to this campaign`
block displays GitHub outcomes/referrers with target/window/provenance. It must not
repeat the repository total in each channel row or present it as campaign lift.
For option B, collection/status remains available to the backend but this comparison
surface is not exposed before the analytics gate passes. D1 owns that choice.

V1 shows absolute counts and coverage, without conversion-rate rankings, statistical
winner badges or a pooled conversion total across overlapping channels. Channel
rows can expose underlying receipts and source status. The existing Goal target
and receipt-outcome view remain accessible. The refresh action never requests new
platform permissions or turns API clicks on.

At narrow widths, show channel rows as readable cards with the same labels and
coverage; no controls fall outside the viewport. Keyboard navigation, source/status
text, and errors must work without hover or color alone.

## 11. Later channel integrations

**MC-1151 agent channel:** provide a stable channel/listing ID and medium `agent`
to link generation. Listing completion, public install counters, mentions and
citations are distinct observations with declared scope, not conversions by
default. A bot fetching documentation is not a human visit or install. Registry
traffic and assistant referrers only get credit when the conversion source supports
it; do not map all AI referrals to a particular listing. No prompt-probe runner,
paid tracker, consumer-chat automation or agent-only content is introduced here.

**MC-1153 email channel:** provide a stable provider/account/broadcast identity and
medium `email`; freeze generated link IDs in the approved broadcast payload.
Delivery, opens, provider clicks, unsubscribe counts and subscriber growth keep
their own units. Opens/clicks are not the campaign conversion. A signup can count
only if selected as the one definition and supplied by an adapter with sufficient
attribution. Subscriber records/list contents never enter this store. Email-send
approval, opt-in handling, Kit/Resend sending and provider link wrapping belong to
MC-1153. Link wrapping must be verified to preserve attribution before claiming it.

## 12. Phased build order and evidence

**Stop gate:** Dave resolves D1 before implementation dispatch. Each phase is a
separate reviewable unit; no phase is declared live from fixture tests alone.

| Phase | Deliverable | Required tests / release evidence |
|---|---|---|
| P1 | Store, schema, source interface and definition-to-Goal binding, dark | No-definition/manual-goal compatibility; single active definition; revision isolation; wrong-project/target/path refusal; atomic-save/crash recovery; correction/retry idempotency; no secret persistence; project discovery unaffected; new `mc/` modules pass pyright basic. |
| P2 | UTM generator and every live tracked-link insertion seam, dark | URL query/fragment preservation; conflicting tags refused; stable reopen/rename; distinct campaign/account/version IDs; composer preview equals prepared payload; no mutation of existing drafts/posts/receipts; no duplicate legacy ref emission; existing release/bounds tests unchanged. |
| P3 | GitHub adapter, collection and cached comparison projection, dark | Injected transport/clock fixtures for traffic windows, top-ten referrers, paginated stars, cumulative baselines, deleted/reset assets, 403/404/429/timeouts and partial responses; overlapping campaigns deduplicate; downtime gaps survive; retries do not double count; negative changes never become new conversions; existing paid engagement reads/consent untouched. |
| P4 | Channel-impact module and D1 release behavior | Source/row/null/zero/stale states; multiple accounts and removed-account history; no attributed GitHub conversions; lifetime-vs-period labels; manual Goal preserved; keyboard/mobile smoke at 390px and desktop; real cached backend data rather than fixture-only success; frontend boot and relevant campaign/Goal smokes after integration. |
| P5 | First site analytics adapter, then additional providers | Dave selects the provider at phase entry. Fixture and scoped live evidence for authentication, actual event/filter/dimensions, provider timezone, unknown traffic, duplicates/overlap, range errors and revoked access. Only then expose supported attributed conversion cells. Umami/GA4 capabilities stay dark until individually proven. |
| P6 | MC-1151 / MC-1153 adapter wiring | Stable channel IDs; no invented reach for listings; provider-wrapped email URLs preserve tags; send payload retains its approved bytes; each channel's human-only gates remain enforced. No activation just because a channel record exists. |

Before any live adapter verification, use an existing authorized read connection;
record only safe metadata and results, never credential values. A missing permission
is a failed release check, not authorization to create/expand credentials. Builders
must document actual live limitations and run touched smokes after each merge.

## 13. What stays dark / out of scope

- All implementation and flags in this spec-only task. D1 is unresolved.
- Attributed GitHub stars/downloads, inferred channel credit, automatic reallocation,
  causal lift, multi-touch attribution, revenue/ROI and conversion-rate winners.
- Additional platform scraping or new engagement API entitlements; X non-public
  clicks and LinkedIn click reads remain off/unavailable until separately verified
  under the user's existing consent and budget.
- Analytics adapters not individually verified; historical UTM reconstruction,
  edits to old posts, redirects/pixels, shortener dependencies and hosted ingestion.
- A generic scheduler system, any automatic outbound publication, new email send
  behavior, individual audience tracking, and paid AI-visibility integrations.

## 14. Source and handoff record

Required sources read: live backlog item `e4796e7b` (2026-11-04), Quill's
`docs/desk_v1/FIELD_SCAN_AGENT_MEASURE_EMAIL.md` section 2, Dave's main-checkout
journal, all five `mc/desk_engagement*.py` modules, and
`docs/THE_DESK_SIMPLIFICATION_PLAN.md` release policy. Current Goal/Desk store and
legacy link code were also inspected. GitHub API documentation linked above was
read on 2026-11-04; no credential was dispensed and no live GitHub read was made.

This document does not settle D1. The recommendation and cost comparison in
section 2 are the handoff to Dave. No code, backlog notes, email, merge or push
belongs to this task.
