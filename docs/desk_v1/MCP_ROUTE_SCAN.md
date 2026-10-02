# Desk platform and generation MCP routes

**Checked 2026-10-01. Research, not an integration or architecture decision.**
Verdicts compare the requested Desk operations and customer-owned billing. `No MCP`
means no applicable official action server was verified, not proof that none exists.
`Y/docs`, `Y/local`, and `Y/ads` explicitly do **not** mean a vendor-hosted server
for the Desk's requested operations. DCR = dynamic client registration.

| Platform / engine | Official MCP | Third-party clients | Auth | Billing route | Verdict |
|---|---|---|---|---|---|
| X | Y/hosted | Any; no DCR | Own app, OAuth PKCE | X API; MCP tariff unverified | **REST better**: ordinary post/media parity unverified |
| LinkedIn personal | None found | n/a | REST member OAuth | API access; no plan-credit route verified | **No MCP**: member publishing exists via REST |
| LinkedIn Company Page | None found | n/a | REST OAuth + reviewed scopes | API access; no plan-credit route verified | **No MCP**: organization review still applies |
| Reddit | Y/local developer tools | Any stdio client | Devvit context; REST OAuth separate | Data API agreement, not Premium | **REST better**: MCP is docs/app logs |
| YouTube | None found | n/a | REST Google OAuth | API quota, not YouTube Premium | **No MCP**: upload/comments/analytics use APIs |
| Discord | Y/docs | Public HTTP docs | Anonymous catalog; bot token for actions | Rate limits; no Nitro-credit route | **REST better**: docs MCP cannot post/read messages |
| Facebook Pages | Y/ads; organic unverified | Ads custom-client access unverified | Ads OAuth unverified; REST Page token | Advertiser account; organic tariff unverified | **REST better**: Ads connector is not Page publishing |
| Instagram | Y/Meta ads; organic unverified | As Facebook | Professional-account REST OAuth | Advertiser account; organic tariff unverified | **REST better**: organic scope is separate |
| Threads | None found for organic | n/a | REST Threads OAuth | No subscription-credit route verified | **No MCP**: REST has posting/replies/insights |
| TikTok | Y/ads | Custom agents documented; DCR unverified | Ads sign-in; organic OAuth separate | Advertiser account, not creator subscription | **REST better**: organic posting is a separate API |
| Google Veo (Gemini API) | Y/docs; Cloud sample local | Any for sample; not a hosted render service | API key; Cloud sample ADC | Paid Gemini/Vertex API | **REST better**: sample adds hosting and Cloud setup |
| Gemini image (Gemini API) | Y/docs; Cloud sample local | Same as Veo | API key; Cloud sample ADC | Gemini/Vertex API billing | **REST better**: no consumer-plan MCP verified |
| OpenAI image | Y/docs | Public docs client | API key for generation | OpenAI API metering | **REST better**: docs server does not generate |
| Kling | Y/hosted | Remote clients documented; DCR in CLI docs | User OAuth PKCE | Membership/credits indicated; pool unverified | **MCP better, conditional**: account tools; billing/terms dark |
| Remotion | Y/docs, deprecated | Historical local client | No generation OAuth | Local compute/licence | **No MCP** for vendor-hosted rendering; remains excluded |
| Runway | Y/hosted | Any; DCR advertised | User OAuth PKCE | Web-app credits; no Explore Mode | **MCP better**: documented subscription-credit route |
| Luma AI | Y/local, not vendor-hosted | Any stdio client | Luma API key | Same paid API | **REST better**: local wrapper targets older models |
| Pika | Y/hosted experiment | Several clients; DCR advertised | User OAuth | Pika Agent Wallet; not Create top-ups | **Equal, provisional**: different wallet, parity unverified |
| FLUX / BFL | Y/hosted | Any; DCR advertised | User OAuth, selects org | Same rates as API | **Equal**: easier account auth, no billing advantage |
| Ideogram | Y/hosted | Any/custom; DCR advertised | User OAuth PKCE | Web subscription credits | **MCP better, conditional**: terms conflict needs clarification |
| Sora API | No live generation route verified | n/a | n/a | API retired | **No MCP**: direct Videos API retired 2026-09-24 |
| fal (secondary distributor) | Y/hosted | Any documented | User API key | fal metered usage | **Equal**: API tools, same payer |
| Replicate (secondary distributor) | Y/hosted + local | Multiple clients documented | API token through remote auth / local env | Replicate API usage | **Equal**: wraps same API, not subscription entitlement |

## Evidence boundary and current Desk baseline

Evidence was gathered on **2026-10-01**. Source publication/update
dates are included where the page provides a useful absolute date; otherwise the
date is the observation date, not an invented launch date. Search excerpts are
identified when a full page could not be read. A search miss does not establish
nonexistence. Directory listings and a server's presence in the MCP Registry do
not establish vendor ownership.

No login, OAuth consent, client registration, account read, generation, upload,
purchase, or credential storage was performed. Public documentation and anonymous
OAuth metadata were read. Nothing here establishes a working Clayrune connection,
an account's eligibility, or an authorized `tools/list` result. A registration URL
in metadata establishes **advertised DCR**, not acceptance of a particular redirect
URI or successful third-party registration.

Code inspected at `2bd50f98`:

- `mc/desk_accounts.py:47`: account platforms are X, LinkedIn, and manual blog.
  `LINKEDIN_ORG_POSTING_APPROVED = False` is at line 51; personal-profile publishing
  is not a separate implemented account type.
- `mc/desk_publish.py:120`: X uses `/2`; LinkedIn uses `/rest` at line 127.
  `mc/desk_engagement.py:217` and `:255` read X mentions and metrics. Do not confuse
  a platform's API capability with an implemented Desk reader.
- `mc/desk_engines.py:647`: the current external generation bases are Higgsfield,
  Gemini, and OpenAI REST. This report edits none of them.
- [Generation scan](GENERATION_ENGINES_SCAN.md), secondary-pass section, supplies
  Runway, Kling, Sora, Luma, Pika, BFL and Ideogram. fal and Replicate are included
  because that scan also names them as distribution routes. Models merely named
  inside a distributor's catalog are not additional direct-vendor integrations.

Higgsfield was deliberately excluded because a separate spike owns it. Remotion
remains excluded by the existing product decision; finding a documentation server
does not reopen that decision.

## Social platforms

### X

The [official MCP guide](https://docs.x.com/tools/mcp) documents
`https://api.x.com/mcp`, Streamable HTTP, and any compatible client. User access
uses an owned developer app and OAuth2 PKCE through the local `xurl mcp` stdio
bridge; app-only Bearer is also documented. It explicitly says **no DCR and no
native MCP OAuth discovery**. The bridge's token-file storage is not the Desk
vault contract and cannot be copied into Clayrune unchanged.

Documented tools include post lookup, archive search, user posts/timeline/mentions,
engagement-related lookups, bookmarks, and Article creation/publication. This is
not verification of ordinary `POST /2/tweets`, media upload, replies, or every
private metric needed by the Desk. `https://docs.x.com/mcp` is a different,
documentation-only endpoint. The guide requires the Pay-per-use production
environment for enrollment failures; an MCP-specific tariff or X Premium credit
entitlement is **unverified**. It invites other clients, but no broader exemption
from [developer terms](https://docs.x.com/developer-terms) was verified.

**REST better for the current combined publish/read contract.** MCP read access is
a candidate, not demonstrated REST parity.

### LinkedIn personal profiles

No LinkedIn-operated action MCP endpoint was verified in LinkedIn's developer
documentation. [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin)
(updated 2025-02-05) documents member OAuth and the self-service `w_member_social`
permission. That is a publishing permission, not general permission to read a
member's feed, replies, mentions, or statistics.

[Community Management](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview)
(updated 2026-05-14) describes member analytics, but also says `r_member_social`
is closed to new access requests. Actual read scopes and eligibility require
separate verification; do not promise full personal-account listening from a
successful post authorization. No MCP transport, DCR, or subscription-credit
route can be specified without an official server.

**No MCP verified.** REST is the documented publishing route, subject to the
LinkedIn terms caveat below.

### LinkedIn organization / Company Page

No official action MCP was verified. The same
[Community Management overview](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview)
documents organization posting, comments/reactions, activity and analytics under
a vetted application program. OAuth user consent does not replace product approval
or the user's Page role. The Desk already has an organization publisher but keeps
readiness gated. MCP wrappers cannot confer `w_organization_social` access.

Billing is API-program access, not a verified LinkedIn Premium credit mechanism;
any commercial partner fees are **unverified**. DCR and MCP client eligibility
are not applicable without a verified server.

**No MCP verified.** Personal-profile and organization authorization are different
routes and must remain different rows.

**Terms affecting both LinkedIn rows:** [API Terms of Use](https://www.linkedin.com/legal/l/api-terms-of-use)
(revised 2022-12-13), section 3.1(26), restrict automated posting; 3.1(24) restricts
non-API content acquisition. [Marketing program terms](https://www.linkedin.com/legal/l/marketing-api-terms)
(revised 2025-07-25) require approval. The relationship between those terms, approved
publishing use cases, and a particular human-confirmed Desk workflow is
**unverified**. Do not assert that adding a confirmation button alone settles it.

### Reddit

Reddit does publish [Devvit MCP](https://developers.reddit.com/docs/guides/ai):
`npx -y @devvit/mcp`, local stdio, with examples for multiple clients. It searches
developer documentation and reads Devvit application logs; it is not a hosted
Reddit account publishing, inbox, or analytics service. Log-access authentication
details were not established by the page; no public DCR route was verified.

The separate [Data API reference](https://www.reddit.com/dev/api/) is the target
for submit/comment, inbox/mentions and post engagement fields; exact application
eligibility, OAuth scopes, and richer own-post analytics are **unverified here**.
[Data API Terms](https://redditinc.com/policies/data-api-terms), section 3.1, require
a separate agreement for commercial use. Reddit Premium is not documented as an
API entitlement. API approval and pricing remain dark, not zero-cost assumptions.

**REST better for the requested operations**, subject to access/terms approval;
Devvit MCP has a different purpose.

### YouTube

No Google-operated YouTube action MCP was verified. Google's Analytics MCP is not
YouTube Analytics. REST provides [video upload](https://developers.google.com/youtube/v3/docs/videos/insert),
[comment threads](https://developers.google.com/youtube/v3/docs/commentThreads/list),
and [owner analytics reports](https://developers.google.com/youtube/analytics/reference/reports/query).
Public data and owner-authorized data have different access requirements. OAuth,
API-project quota and any required app verification still apply; a YouTube Premium
subscription does not establish API entitlement. A complete general @mention
inbox was **not verified**.

No official MCP URL, transport, DCR or MCP pricing can be asserted. Other clients
can implement the documented APIs; this scan did not establish that a community
MCP eliminates upload review or other [YouTube API policy](https://developers.google.com/youtube/terms/developer-policies)
requirements. **No MCP verified** for this contract.

### Discord

Discord's [March 2026 developer announcement](https://discord.com/blog/building-on-the-social-layer-of-games-whats-new-from-gdc-2026)
confirms documentation MCP support. Anonymous GET of the
[official endpoint](https://docs.discord.com/mcp) returned a public catalog naming
HTTP transport, documentation search/retrieval and documentation feedback. It
does not expose account messaging. Exact HTTP/SSE protocol interoperability was
not exercised; no user OAuth or DCR contract was established. No feedback was sent.

The [message API](https://docs.discord.com/developers/resources/message) exposes
posting, replies, mentions and reactions, with channel permissions and message
content intent limits. Bot credentials and installation authorization are distinct
from signing in as a personal user; an impression/marketing-statistics equivalent
was **not verified**. [Discord's self-bot policy](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots)
prohibits automating normal user accounts outside permitted OAuth2/bot APIs.
No Nitro credit billing route was verified. **REST better**, with Gateway events
where needed for incoming messages.

### Facebook Pages

An official **advertising** connector exists: Meta's
[Q1 2026 earnings transcript](https://s21.q4cdn.com/399680738/files/doc_financials/2026/q1/META-Q1-2026-Earnings-Call-Transcript.pdf)
announces Ads AI Connectors, and [Meta Blueprint](https://www.facebookblueprint.com/student/activity/715273)
describes the advertising integration. The reported MCP URL is
`https://mcp.facebook.com/ads`; its transport, OAuth registration and arbitrary
client eligibility were **not verified from accessible first-party setup docs**.
The [setup help page](https://www.facebook.com/business/help/1456422242197840)
redirected to login. Treat HTTP/OAuth claims from community projects as leads,
not verified contracts. Advertising spend belongs to the ad account; extra MCP
fees were not verified.

For organic Pages, Meta's [Facebook collection](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api?entity=request-23987686-0b79260c-96bd-49de-875b-6076213785fc)
documents Page-token retrieval and Reels publishing. The current
[Page posts reference](https://developers.facebook.com/docs/pages-api/posts/)
returned HTTP 429; complete current comments/mentions/insights coverage, exact
scopes, review and pricing are **unverified**. **REST better for organic scope**;
no official organic MCP replacement was established.

### Instagram

Meta's Ads connector above must not be counted as verified organic Instagram
publishing. No separate official organic MCP was found. Meta's
[Instagram collection](https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00)
search excerpts describe Professional-account publishing, comments, mentions and
metrics, with Instagram Login and Facebook Login as distinct setups. Its full
page timed out or returned a shell in this run, so exact present-day scope parity
is **unverified**, particularly mentions/tagging differences between login types.
The [publish-Reel request](https://www.postman.com/meta/instagram/request/gabnx7r/publish-reel)
documents the REST `media_publish` action.

OAuth, Professional-account eligibility and any app review remain REST concerns.
No consumer-subscription-credit route or general personal-account publisher was
verified. Ads MCP custom-client, transport, DCR, and terms gaps are the Facebook
gaps, not implied approvals. **REST better for organic scope**; platform terms
and production eligibility still need confirmation.

### Threads

No official organic MCP was verified. Meta's [Threads collection](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)
documents OAuth code exchange and publishing. Its
[mentions request and navigation](https://www.postman.com/meta/threads/request/34203612-fc3f21da-0a53-44ab-80e2-8cd8c376a42a)
identify own mentions, replies and insights. The
[insights reference](https://www.postman.com/meta/threads/request/ndeeu6p/get-post-insights)
search excerpt lists the separate publishing, replies, insights and mentions
permissions; opening it returned “request not found.” Exact current scopes and
metrics therefore need verification, not extrapolation from posting alone.

No hosted MCP transport, DCR, subscription billing, or MCP-specific permission to
use another app was established. REST requires the app and user authorization;
commercial terms and review remain **unverified** in this scan. **No MCP verified**.

### TikTok

The [official Business MCP overview](https://ads.us.tiktok.com/resources/help/article/about-tiktok-for-business-mcp-server?lang=en)
(updated August 2026) gives two remote URLs:
`https://business-api.tiktok.com/open_mcp/tt-ads-mcp-flat` and
`https://business-api.tiktok.com/open_mcp/tt-ads-mcp-layer`. It explicitly includes
custom agency agents, advertising creation, reports and optimization. Exact
HTTP/SSE transport, OAuth registration, DCR and additional fees are **unverified**:
the linked setup guide returned no readable content. This is advertiser-account
access, not evidence of creator-subscription credits or organic API parity.

Organic publishing uses [Content Posting API](https://developers.tiktok.com/docs/en/content-posting-api-get-started)
(updated 2026-08-04); [Display API video fields](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object)
(updated 2026-08-24) include view/like/comment/share counts. General own mentions
and comment-body reading were not verified through that API. The
[content-sharing guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)
exclude private/internal-only uploader utilities and prescribe creator-facing UX.
**REST better for organic scope**, conditional on product review. An ads connector
does not bypass those requirements.

## Generation engines

### Google Veo through the Gemini API

Google's [Gemini Docs MCP](https://ai.google.dev/gemini-api/docs/coding-agents)
(updated 2026-09-24) at `https://gemini-api-docs-mcp.dev` searches documentation;
it is not an authenticated rendering service. No Google-hosted action MCP for the
requested Gemini API route was verified.

There is also Google-published [Genmedia MCP sample code](https://raw.githubusercontent.com/GoogleCloudPlatform/genmedia-creative-studio/main/experiments/mcp-genmedia/README.md):
Veo generation, local stdio or self-hosted Streamable HTTP, ADC, Cloud project and
storage setup. Its disclaimer says it is not an officially supported Google
product. It is a **Vertex/Google Cloud sample**, not evidence that consumer Gemini
subscription credits pay Gemini API calls. No hosted user-OAuth DCR service is
provided by that sample.

The [Gemini billing guide](https://ai.google.dev/gemini-api/docs/billing) ties
API tiers to billing accounts. **REST better** for the current API-key connector;
using the sample would change API product/auth and add infrastructure without a
verified plan-credit benefit. No MCP-specific terms exemption was established.

### Gemini image through the Gemini API

The same Docs MCP and Cloud sample distinctions apply. The Google sample includes
Gemini image generation/editing, but runs under ADC and project billing. Existing
Desk integration uses the Gemini API key. Native REST
[image generation](https://ai.google.dev/gemini-api/docs/image-generation) supports
the requested output; the sample's MCP adds tool discovery, not a verified
consumer-plan entitlement. Complete model/parameter parity was not tested.

No Google-hosted user-OAuth generation MCP URL or DCR route for this Gemini API
product was verified. Third-party clients may run the published sample locally;
that is different from signing into a Google-hosted generation server.
**REST better** for this contract. Terms authorizing a consumer-plan shortcut are
**unverified**, so the billing row stays API billing.

### OpenAI image

[OpenAI Docs MCP](https://developers.openai.com/learn/docs-mcp) explicitly says
`https://developers.openai.com/mcp` is public Streamable HTTP, read-only
documentation, and does not call the API on the user's behalf. It requires no
user OAuth/DCR for that public documentation surface. No official general-purpose
image-generation MCP endpoint was verified.

[Image generation](https://developers.openai.com/api/docs/guides/image-generation)
is available through the Images API and Responses image-generation tool; API
credentials and [API pricing](https://developers.openai.com/api/docs/pricing)
apply. ChatGPT's built-in image tool is not documentation of a public MCP server
that another app can call using ChatGPT subscription entitlement. Third-party
API applications are a documented use; a subscription-credit bridge and its
terms are **unverified**. **REST better** for the implemented connector.

### Kling

Kling's [official tutorial](https://kling.ai/blog/kling-mcp-cinematic-performance-workflow)
(English page dated 2026-08-27) explicitly gives `https://kling.ai/mcp` for
international accounts and invites compatible remote clients. It covers image
and video generation and task tracking. The linked interactive guide rendered
no text. The [published CLI skill](https://raw.githubusercontent.com/klingai-tech/skills/main/SKILL.md)
describes OAuth authorization-code/PKCE/DCR, account membership/credits, task
polling, uploads, Elements and motion control. HTTP is remote; the precise
Streamable-HTTP/SSE behavior of the international endpoint was **not exercised**.

**Conflict to resolve:** that CLI skill insists on CLI-only invocation and
CLI-owned token storage, while the vendor tutorial invites remote clients.
It is evidence of a supported CLI workflow, not by itself a contractual ban on
custom MCP clients. The [user policy](https://kling.ai/docs/user-policy) and the
inaccessible guide did not establish a custom-app exception or exact credit-pool
equivalence. Membership credits are indicated, but web/API pool identity and rates
remain **unverified**. **MCP better, conditional** on those points; no adapter choice
has been made.

### Remotion

The [official MCP page](https://www.remotion.dev/docs/ai/mcp) (updated 2026-09-30)
says its documentation MCP is deprecated and the hosted service will shut down
no earlier than 2026-08-31; that wording does not prove it has already shut down.
The [vendor package](https://www.npmjs.com/package/@remotion/mcp) is a local MCP
client-facing package, historically documentation access, not a vendor-operated
render API. Historical hosted endpoint and current liveness are **unverified**;
no generation OAuth, DCR or plan-credit route was found.

[Studio WebMCP](https://www.remotion.dev/docs/ai/webmcp) is browser control of a
running Studio, not the same service as a remote MCP render endpoint. Local
rendering still needs compute and the applicable
[Remotion licence](https://www.remotion.dev/docs/license/faq).
**No MCP for vendor-hosted rendering.** Nothing here changes the existing decision
to exclude Remotion from Desk engines.

### Runway

The [official connection guide](https://help.runwayml.com/hc/en-us/articles/51931843164691-Connecting-to-Runway-MCP)
states any MCP client can use `https://mcp.runwayml.com/mcp`, **Streamable HTTP
only**, user sign-in, no API key. It generates images/video using models available
to that account. It consumes web-app credits and explicitly excludes Explore Mode.
Public metadata advertises DCR/PKCE (see table below). Exact upload/polling schemas,
limits and model parity remain untested. Runway's separate Dev MCP is not the same
billing finding.

The [API reporting guide](https://docs.dev.runwayml.com/usage/workspace-reporting/)
explicitly separates API and web credit pools. [Terms](https://runway.com/terms-of-use)
restrict resale/hosting except as permitted through APIs and restrict scraping;
the MCP guide expressly invites other clients. Whether a particular hosted Desk
distribution falls within the permitted integration is **not legally resolved**.
**MCP better** for customer web credits, not a claim that credits or generations
are unlimited.

### Luma AI / Dream Machine

Luma's own [luma-api-mcp repository](https://github.com/lumalabs/luma-api-mcp)
documents any MCP client, an API key, Photon image and Ray video generation.
Its [server source](https://raw.githubusercontent.com/lumalabs/luma-api-mcp/main/server.py)
runs local stdio. This is official vendor-authored code, **not vendor-hosted MCP**.
No hosted user-OAuth/DCR route was verified. `mcp.luma.com` belongs to the events
service Luma, not Luma AI, and must not be used as evidence for video generation.

The wrapper targets the older Dream Machine API/model family; current Agents API
coverage is unverified. [API credits](https://docs.lumalabs.ai/reference/getcredits)
belong to the API user, not a proven web subscription bridge.
[Luma API terms](https://lumalabs.ai/legal/api-terms-of-use) explicitly contemplate
customer integrations and downstream users, with obligations. **REST better**:
same API billing without hosting an older intermediary.

### Pika

The [official experimental MCP page](https://pika.art/mcp) gives
`https://experiment-mcp.pika.art/api/mcp`, multiple client examples and media
generation/creative skills. OAuth/DCR is advertised in public metadata. The exact
HTTP/SSE transport variant, custom redirect acceptance and full tool parity are
**unverified**. An installation skill is not a transport requirement for a custom
client unless the server itself requires it.

The page bills creative requests to **Pika Agent Wallet**. The
[Create pricing page](https://pika.art/pricing?interval=year) explicitly excludes
API and MCP from Create top-up credits. Do not silently equate Create, Agent
Wallet and [API Club](https://dev.pika.art/agent) balances.
[Terms](https://pika.art/terms-of-service) require visible attribution for model
integrations unless the plan permits white-labeling; first-party model white-labeling
requires an enterprise agreement. Third-party model terms also apply.
**Equal, provisional**: account-based MCP exists, but neither a billing saving nor
equivalent production controls is established.

### FLUX / Black Forest Labs

The [official help page](https://help.bfl.ai/articles/2286461195-what-is-the-flux-mcp)
documents `https://mcp.bfl.ai`, user OAuth and organization selection, explicitly
**the same image rates as the API**. Public metadata advertises DCR. The
[tool reference](https://docs.bfl.ai/api_integration/mcp_integration) documents
generation, editing, variations, history, credits, virtual try-on and FLUX 3 video
with result polling. Any compatible client is invited; HTTP configuration is
documented, while exact protocol interoperability was not tested.

[Developer terms](https://bfl.ai/legal/developer-terms-of-service) explicitly
contemplate Developer Applications and End Users, subject to service-specific
terms. No exclusive Claude/ChatGPT restriction was found in the cited integration
docs; that does not waive other terms. **Equal**: useful OAuth and account tools,
no verified billing advantage over direct BFL API calls. Distributor FLUX prices
and credits are separate from BFL's selected organization.

### Ideogram

The [official MCP page](https://ideogram.ai/features/mcp/) explicitly documents
custom clients, `https://mcp.ideogram.ai/mcp`, Streamable HTTP and user OAuth.
It says MCP uses the same web subscription and has no separate MCP billing.
Public metadata advertises DCR/PKCE. The documented tools include bulk generation,
editing, upscale/reframe, collections and custom-model training. Complete model
and per-parameter REST parity is **unverified**. Its own guide describes REST as
suited to controlled server-to-server pipelines.

**Terms conflict:** [Terms of Service](https://ideogram.ai/legal/tos/), section
6.2(I), contains a broad automated-access restriction despite the explicit MCP
invitation. Applicability of an MCP exception to a distributed custom app is
**unverified**; obtain vendor clarification rather than treating marketing copy
as a legal override. **MCP better, conditional** for user-authorized subscription
credits, not yet cleared for product integration.

### Sora API (secondary scan item)

[OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) records
the 2026-03-24 announcement and **2026-09-24 removal** of the Videos API and Sora 2
aliases/snapshots, without a recommended replacement. No live official Sora
generation MCP, auth, DCR or billing route was verified. Old community wrappers
and distributor catalog mentions do not overturn that API retirement.
**No MCP** for a verified current direct-vendor route. This does not claim every
third-party Sora-branded catalog entry was independently tested or retired.

### fal (distributor named in the earlier scan)

The [official fal guide](https://fal.ai/learn/tools/how-to-access-flux-3-on-fal)
(dated 2026-08-12) documents `https://mcp.fal.ai/mcp`, HTTP, any compatible client,
and `Authorization: Bearer` with the user's fal API key. It covers model schemas,
prices, uploads and render submission. This route meters model use to the fal
account; it does not spend a separate model vendor's web subscription. DCR is not
needed for the documented API-key route; any OAuth alternative is **unverified**.
No custom-client prohibition is apparent in that invitation; a full distribution
terms review and exact tools/polling parity remain **unverified**. **Equal** for
the examined generation path, with no demonstrated credit advantage.

### Replicate (distributor named in the earlier scan)

The [official server page](https://mcp.replicate.com/) documents hosted
`https://mcp.replicate.com/sse`, **SSE**, plus local `replicate-mcp` stdio.
Its remote consent flow takes the user's **Replicate API token**, rather than
eliminating the need for one. The [launch post](https://replicate.com/blog/remote-mcp-server)
(2025-08-10) describes a server acting on the user's behalf with that token;
multiple third-party clients are supported. DCR specifics are **unverified**.

Tools discover models, create predictions and retrieve API results. Billing is
the account's API use, not ChatGPT/Claude or the model vendor's subscription
allowance. Model licences and commercial terms still apply; no MCP-specific
exemption was verified. **Equal** for the examined contract; exact API parity and
current Streamable HTTP support beyond the documented SSE route are unverified.

## Public authentication metadata observed

Anonymous GETs on 2026-10-01 returned JSON for the following URLs. These are
public endpoints, not credentials. No registration endpoint was called.

| Metadata source | Registration advertised | PKCE / token authentication advertised |
|---|---|---|
| [Runway metadata](https://mcp.runwayml.com/.well-known/oauth-authorization-server) | `https://mcp.runwayml.com/register` | S256; token auth `none`; authorization code + refresh |
| [Ideogram metadata](https://mcp.ideogram.ai/.well-known/oauth-authorization-server) | `https://mcp.ideogram.ai/register` | S256; `client_secret_post` / `client_secret_basic`; code + refresh |
| [BFL metadata](https://mcp.bfl.ai/.well-known/oauth-authorization-server) | Supabase issuer, `/auth/v1/oauth/clients/register` | S256 / plain; basic / post / none; code + refresh |
| [Pika metadata](https://experiment-mcp.pika.art/.well-known/oauth-authorization-server) | Supabase issuer, `/auth/v1/oauth/clients/register` | S256; basic / post / none; code + refresh |

Anonymous root metadata requests at `https://kling.ai/.well-known/oauth-authorization-server`
and `https://mcp.facebook.com/.well-known/oauth-authorization-server` returned 404.
That does **not** establish absence of OAuth or DCR: the issuer can live elsewhere
or use path-specific discovery. Kling's DCR statement above comes from its CLI
documentation, not from a successful metadata probe.

## Community / third-party servers, kept separate

These examples were found in their maintainers' repositories, read on 2026-10-01.
They were not installed, authenticated, security-reviewed or endorsed. A wrapper
using an official API remains an **unofficial MCP**, and a vendor's third-party
model catalog does not make it that model vendor's official server.

| Subject | Example and provenance | What it changes / does not establish |
|---|---|---|
| LinkedIn | [gohyperdev/linkedin-mcp](https://github.com/gohyperdev/linkedin-mcp) | Community member-posting wrapper; no organization review bypass |
| Reddit | [karanb192/reddit-mcp-buddy](https://github.com/karanb192/reddit-mcp-buddy) | Community read/search tool; no Reddit commercial-access grant |
| YouTube | [pauling-ai/youtube-mcp-server](https://github.com/pauling-ai/youtube-mcp-server) | Own Google OAuth; claims upload/analytics/comments; maintainer claims not tested |
| Discord | [cappyeo/discord-mcp](https://github.com/cappyeo/discord-mcp) | Community implementation; not Discord's official docs endpoint |
| Facebook | [IvanBBaev/facebook-mcp](https://github.com/IvanBBaev/facebook-mcp) | Community Graph API Page actions/insights; not Meta's hosted Ads server |
| Instagram | [mcp-dir/instagram-mcp](https://github.com/mcp-dir/instagram-mcp) | Third-party hosted integration; provider terms and billing separate |
| Threads | [Andre-wyap/threads-mcp](https://github.com/Andre-wyap/threads-mcp) | Local stdio planner over official REST; does not establish Meta endorsement |
| TikTok | [HasData/tiktok-mcp](https://github.com/HasData/tiktok-mcp) | Third-party public-data service, not organic publishing or platform approval |
| Veo / Gemini image | [u2n4/gemini-media-mcp](https://github.com/u2n4/gemini-media-mcp) | Community API-key wrappers; paid API remains the upstream route |
| Kling | [aadityasp/mcp-kling](https://github.com/aadityasp/mcp-kling) | Explicitly unaffiliated; distinct from Kling's own endpoint |
| Luma AI | [bobtista/luma-ai-mcp-server](https://github.com/bobtista/luma-ai-mcp-server) | Community Dream Machine wrapper; distinct from `lumalabs/luma-api-mcp` |
| Remotion | [Vidhanvyrs/remotion-mcp](https://github.com/Vidhanvyrs/remotion-mcp) | Community execution/rendering implementation; not a Remotion-hosted service |
| fal | [aryasaatvik/fal-mcp](https://github.com/aryasaatvik/fal-mcp) | Explicitly unofficial; separate from `mcp.fal.ai` |

No community alternative is needed to establish the official Runway, BFL, Pika,
Ideogram, fal or Replicate findings. No exhaustive community inventory was
attempted. No OpenAI image community wrapper was validated in this scan.

## Still dark, and what would close each gap

| Unknown | Evidence/action that would close it |
|---|---|
| Custom-client OAuth actually works for Runway, Ideogram, BFL, Pika | Vendor registration rules plus an explicitly authorized later register/consent/refresh test with Clayrune's redirect. Metadata alone is insufficient. |
| Kling international client contract, credit pool and CLI-only conflict | Readable official MCP guide or vendor response covering custom remote clients, OAuth issuer, allowed redirect URIs, plan/API pool and hosted-app terms. Keep Chinese/international accounts distinct. |
| X ordinary posts/media/replies/private metrics and exact MCP charges | Authorized read-only `tools/list` schema comparison with existing REST calls; vendor MCP billing documentation. No publication needed to inspect schemas. |
| Meta Ads endpoint/transport/client allowlist; organic support | Accessible official Ads AI Connector setup/tool docs or vendor response. Help page required login; organic Page reference returned 429. Do not assume an allowlist or assume unrestricted clients. |
| TikTok Ads auth, DCR and any organic tools | Readable Business MCP setup/tool catalog. Then map independently to Content Posting/Display scopes and audit rules. |
| Instagram login-type differences and current Page/Threads scopes | Fresh readable first-party docs or exported current official Postman collection; failed pages/search excerpts are explicitly lower confidence. |
| LinkedIn member reads and approved organization app behavior | Actual approved product/scopes and current member analytics documentation; posting consent does not establish read access. |
| LinkedIn automation terms, Ideogram automated-access clause | Written vendor clarification/approved integration terms for the concrete human-confirmed product flow; do not infer blanket compliance from OAuth success. |
| Pika wallet equivalence, model parity and attribution | Official Agent Wallet price/credit documentation and tool schemas; written plan entitlement for attribution/white-labeling if wanted. Create top-ups already explicitly exclude MCP. |
| REST vs MCP exact render controls, retries, costs and output retrieval | Later authorized tool-schema capture against the Desk job contract; no need to generate to identify most gaps. Actual billing requires a separately authorized paid canary. |
| Luma current Agents API through official MCP | Updated vendor repository or supported hosted endpoint with current models; old Ray/Photon wrapper does not prove it. |
| Google/OpenAI consumer subscription through own-vendor generation MCP | A vendor-published action endpoint, auth contract and entitlement statement. Docs MCP and SDK support for calling other MCP servers are not that evidence. |
| Remotion hosted MCP liveness | Vendor shutdown issue/status; irrelevant to the excluded rendering architecture because the service is documentation-only. |
| Full commercial terms for each distribution model | Product-specific vendor terms/approval covering customer-owned accounts, hosted deployments, attribution, retention and delegated use. This scan reports observed restrictions, not blanket legal clearance. |

## Implications for the Higgsfield decision pattern

The pattern is real for **Runway and Ideogram**: vendor-hosted, third-party
clients, user authorization, and documented web-plan credits. It is not a general
property of MCP. BFL uses API rates; Luma and Google's sample wrap paid APIs;
Pika uses a distinct wallet; X still needs an owned developer app; Meta/TikTok
advertising connectors do not establish organic publishing support. Kling is a
promising but incompletely verified instance.

No architecture was selected. A next spike has two concrete options: (A) validate
one web-credit connector's authentication/schema contract, or (B) keep extending
REST while those contracts remain open. A offers the billing benefit but adds
OAuth/token lifecycle and tool-schema work; B preserves the existing job adapter
shape but cannot promise web-plan credits. **Recommendation for the deciding
session: Runway first for A**, because its transport, arbitrary clients and
credit treatment are explicit; retain REST fallback and all existing human
approval/spending gates. Ideogram's terms conflict should be resolved before
treating it as equally ready. This is a recommendation for review, not execution
authorization.

## Verification of this research artifact

The table covers ten social rows (personal and organization LinkedIn separated),
eleven direct generation rows, and two named distribution routes. All vendor
claims carry first-party sources or explicit lower-confidence labels. Anonymous
metadata probes distinguish advertised registration from tested authorization.
No application code or user-facing behavior changed; runtime tests and UI smokes
are not applicable. Integration, account eligibility, publishing and rendering
remain **untested by design**.
