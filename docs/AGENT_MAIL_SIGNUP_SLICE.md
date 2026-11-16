# Agent mail: self-serve signup slice (MC-1166)

Status: proposed, 2026-11-16. Author: Sol_Tobin. Doc only; no application,
Cloudflare configuration, deployment or mail changed. Source baseline:
`e8784f34`. Dave must select D1-D3 below before dependent implementation.

Goal: a second, ordinary Clayrune install gets its own `@clayrune.io` address,
verifies a different owner mailbox, sends one note and delivers the reply into
that note's originating chat. No Wrangler, Cloudflare account, SQL or operator
provisioning on the user's machine. This follows the shape of
[the first-install slice](AGENT_MAIL_THIN_SLICE.md), not its historical gaps.

## 0. Finding that shapes the plan

**Most of signup is already built. Finish its adapters; do not rebuild it.**
The local entry is actually **`Signup.start`**, not `Signup.begin`
(`mc/agent_mail_signup.py:291`). It makes the install id, flow id and key id
itself (`:311`); its resume path generates the sealed identity (`:354`).
`MailFactory.signup(authorize=...)` exists (`mc/agent_mail_factory.py:383`),
as does human-only first-store bootstrap (`:317`). The production reviewed
manifest callback is now wired (`:461-475`); the first slice's U1 blocker is gone.

Four boundaries prevent strangers using those pieces:

1. **Local HTTP still selects the legacy enrollment ceremony.** The parent
   registers `enrollment_blueprint()` without its optional factory
   (`mc/blueprints/agent_mail_routes.py:29-30`); the child returns
   `operational_adapter_unavailable` (`agent_mail_enrollment_routes.py:60-61`).
   Its start takes a manifest/signature, and its actions include numeric-code
   verification and tunnel proof (`:68-87`), rather than `Signup.start/resume/enable`.
2. **The Settings controls speak that legacy contract.** Setup posts
   `{owner, manifest, signature}` (`static/js/agent-mail-setup.js:90-101`);
   action selection asks for review/code/proof (`agent-mail-actions-model.js:143-155`);
   rendering contains code/proof controls (`agent-mail-actions.js:110-120`).
   The status model displays tunnel/extraction/custody prerequisites
   (`agent-mail-status-model.js:155-158`). A real pull projection
   already exists but is unwired (`mc/agent_mail_status_view.py:268-286`).
3. **Verification mail is queued but never delivered by the entrypoint.**
   Signup routes and link routing ARE wired (`workers/agent-mail/worker.js:33-36,64-73`).
   `SignupHTTP.start` reserves rows and queues the fixed verification message
   (`signup-http.js:211-240`). The existing `deliverControlMail`
   (`control-mail-delivery.js:25-40`) has durable leasing, provider receipts,
   retry/uncertainty and budgets. But the production cron only calls the pull
   notification sweep (`worker.js:93-100`), which explicitly owns no control
   outbox (`pull-sweep.js:9-13`). No new mail sender is needed.
4. **The deployed send binding admits only one recipient, and hosted signup
   lacks its outbox seal configuration.** Live read-only Worker settings on
   2026-11-16 returned one `MAIL_SEND.allowed_destination_addresses` entry and
   no `MAIL_PULL_OUTBOX_KEY` / `_ID` bindings. The constructor requires both
   (`worker.js:65-66`; `verification-mail.js:121-126`). These are ops gaps,
   not changes a stranger should make.

The destination helper is real but **not on the pull signup path**.
`enrollment-http.js:97,132-135` calls `destinationProviderFor` and `.ensure`;
`enrollment-provider.js:23-30` builds the Cloudflare client using
`MAIL_DESTINATION_TOKEN`. However, the production Worker returns
`410 legacy_retired` for those enrollment paths (`worker.js:51-53`), while
`SignupHTTP.start` never invokes a destination provider (`signup-http.js:186-240`).
Keeping that secret does not make new-owner signup work. This corrects the
historical runbook assumption without deleting its credential.

### Provider fact: Cloudflare verification is optional with sending onboarding

Primary docs checked **2026-11-16**; page update dates below are the dates shown
on the opened canonical pages, not search snippets:

| Source | Current fact | Consequence |
|---|---|---|
| [Limits](https://developers.cloudflare.com/email-service/platform/limits/), updated 2026-10-30 | **200 destination addresses/account**, shared across domains; **200 routing rules/domain**. Before sending-domain onboarding, recipients must be verified destinations; afterward arbitrary recipients are allowed. | A verified-destination pilot is bounded by account capacity; this is not a limit on addresses handled by one Worker catch-all. |
| [Routing addresses](https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/), checked 2026-11-16 | Adding a destination sends Cloudflare's own verification message; the recipient clicks its verification link. | Without sending onboarding, a new owner needs this click **as well as** Clayrune's install-bound confirmation. |
| [Pricing](https://developers.cloudflare.com/email-service/platform/pricing/), updated 2026-06-09 | Arbitrary-recipient sending requires **Workers Paid**; **3,000/month included**, then **$0.35/1,000**. Verified destinations are free and outside that quota. | Paid sending removes the per-owner Routing destination step, but consumes the shared sending allowance. Workers/D1/DO costs are separate. |
| [Sending Workers API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/), updated 2026-10-30 | The same `send_email` binding accepts structured `send({from,to,subject,text,...})`. | Reuse `email-sending.js:41-74` and `control-mail-delivery.js:263-267`; no REST/SMTP replacement. |
| [Domain configuration](https://developers.cloudflare.com/email-service/configuration/domains/), updated 2026-10-30 | Sending-domain onboarding includes provider DNS/domain verification. | One service-operator step on `clayrune.io`, not a Cloudflare signup for every Clayrune user. |

The [binding page](https://developers.cloudflare.com/email-service/configuration/send-bindings/)
(updated 2026-10-15) describes unrestricted bindings in terms of verified
destinations, while the newer Limits page explicitly permits arbitrary
recipients after onboarding. Treat onboarding plus an actual send to an owner
who is **not** a Routing destination as the deployment gate. Removing the
one-address allowlist alone is insufficient. Clayrune's own owner verification
stays mandatory either way; provider recipient permission is a separate check.

**D1 recommendation, not selected:** onboard sending on `clayrune.io` once and
use the existing Clayrune link ceremony. The alternative is viable for two
installs, but needs destination-provider wiring that today's pull path lacks,
two verification interactions and the 200-address ceiling.

### Inbound: read-only inspection is blocked, not a verified catch-all

On **2026-11-16 20:36 UTC**, using the existing Wrangler environment credential:

| Exact read | Result |
|---|---|
| `GET /zones?name=clayrune.io` | 200, one matching zone. |
| `GET /zones/{zone}/email/routing` | **403**, Cloudflare code **10000**, `Authentication error`. |
| `GET /zones/{zone}/email/routing/rules/catch_all` | **403**, code **10000**, `Authentication error`. |
| `GET /zones/{zone}/email/routing/rules?per_page=100` | **403**, code **10000**, `Authentication error`. |
| `GET /accounts/{account}/workers/scripts/{script}/settings` | 200; binding/configuration findings above. Secret names/types only, no values. |
| `GET /zones/{zone}/email/sending/subdomains` (20:37 UTC) | **403**, code **10000**, `Authentication error`; sending onboarding remains unverified. |

An initial attempt with Wrangler's stored OAuth credential failed earlier with
403 / 9109 `Invalid access token`. `wrangler versions list` succeeded using its
existing environment credential; the same credential produced the results
above. No login, token creation, permission change or provider write was used.

Therefore **whether live routing currently uses an exact-address rule or the
catch-all could not be established in this run**. The required journal tail
reports a Worker catch-all and successful replies, plus preserved `hello@`
forwarding; that is historical evidence, not today's API readback. Do not
substitute it for the failed inspection. Operator account ids, private recipient
addresses and raw settings have deliberately not been copied into this public doc.

Self-serve opaque addresses require a catch-all to the mail Worker **unless an
operator adds a route for each signup**. There is no such route-creation call in
`SignupHTTP.start` (`signup-http.js:186-240`). The catch-all is the narrow path:
one shared setting, no per-user rules or additional Cloudflare secret. Preserve
`hello@` exactly. [Cloudflare's inbound lifecycle](https://developers.cloudflare.com/email-service/concepts/email-lifecycle/)
and [routing guide](https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/)
describe specific-rule processing and catch-all fallback. Ops O1 must prove the
actual setting before declaring a second install ready; **do not change it in this task**.

## 1. End-to-end path, step by step

This is the proposed path **if Dave selects D1A/D2A/D3A below**. EXISTS means
source traced, not live-tested here. MISSING means an adapter/ops gap anchored
to the current caller or constructor; suggested labels are proposed UI copy.

| # | User/system step | Source and state |
|---|---|---|
| S1 | User opens Settings > Connectivity > Agent mail, clicks **Set up agent mail**. A decline sends nothing. | Existing setup button/decline handlers: `static/js/agent-mail-setup.js:68-77,107-116`. **Missing:** useful first-install status while flag/store are absent; current HTTP gate returns 404 or adapter 503 (`mc/blueprints/agent_mail_enrollment_routes.py:46-61`). U1. |
| S2 | User enters owner email, sees the fixed disclosure/service, then types the dashboard passcode. A missing/locked vault directs the user to its existing setup/unlock UI. | Existing passcode gate: `agent_mail_enrollment_routes.py:48-57`; explicit unattended refusal precedent: `agent_mail_deployment_routes.py:64-72`. Vault/seal checks: `mc/agent_mail_enrollment_keys.py:172-199`. **Missing:** pull start body/consent and released public service defaults. U1/U2. No agent creates or unlocks a vault. |
| S3 | Authorized start saves the public service root/origin only when unset, bootstraps local mail state and calls `factory.signup(authorize=...).start(owner, consent_version)`. | Bootstrap/schema/floor: `mc/agent_mail_factory.py:317-335`; reviewed callback: `:461-475`; verified fetch: `mc/agent_mail_reviewed_service.py:61-79`. **Missing:** HTTP composition and gated durable config update. U1. Preserve custom origins/roots and all existing accounts. |
| S4 | Local signup creates its own random install identity and pending receipt, seals the key, signs `/v1/pull/signup/start`. | **EXISTS:** `mc/agent_mail_signup.py:291-324,349-389`. The pull path gets install identity from its account row (`mc/agent_mail_factory.py:371-378`); no operator `agent_mail_install_id` or provisioning SQL is necessary. |
| S5 | Worker atomically reserves an opaque address, owner slot, immutable flow, token and fixed verification outbox entry. | **EXISTS + wired:** `workers/agent-mail/worker.js:33-36,64-73`; `signup-http.js:186-240`; `verification-mail.js:223-258`. **Missing:** small public-signup admission gate U6 and seal-key ops O3. No mail-text or chosen-address field. |
| S6 | A cron tick sends the fixed verification link to that proposed owner. Clayrune shows **Check your email**, queued/accepted/failed/uncertain status and a **Check verification** button. | Fixed template: `verification-mail.js:85-101`; durable consumer: `control-mail-delivery.js:25-40,94-158,250-280`. **Missing:** production consumer call (`worker.js:93-100`) U7, and pull UI U3-U5. With D1A this is the only mailbox-verification email. |
| S7 | Owner opens link, then explicitly confirms in its hosted page. Scanner GET does not verify. | **EXISTS:** inert confirmation and CSRF/session setup `signup-http.js:275-298`; bound POST and atomic verified transition `:300-334`; token/flow checks `verification-mail.js:265-303`. GET may create browser-session metadata, but grants no mail authority. Link expires after **30 minutes** (`verification-mail.js:9`). |
| S8 | User returns to Clayrune and clicks **Check verification**; authenticated resume records the signed verified state. | **EXISTS:** `mc/agent_mail_signup.py:326-328,369-408`; signed Worker status `signup-http.js:249-272`. Local GET remains passive (`agent_mail_signup.py:276-288`); it must not secretly send a status request. **Missing:** UI action/route U1/U3/U4. |
| S9 | User clicks **Enable agent mail**, with fresh passcode proof. Server activates and then starts its one pull supervisor. | **EXISTS:** `Signup.enable` delegates (`mc/agent_mail_signup.py:136-139`); `Activation.enable` authorizes and verifies before enabling (`agent_mail_activation.py:98-196`); Worker validates verified flow and activates (`pull-activate-http.js:145-181`). **Missing:** human route, durable rollout flags and idempotent `start_default` call U1. Supervisor entry exists (`agent_mail_supervisor.py:453-466`). Never enable from link GET, passive status or a failed activation. |
| S10 | Agent in a fresh chat sends a note to this install's verified owner. | **EXISTS:** note route `mc/blueprints/agent_mail_note_routes.py:39-54`; `Outbound.prepare` (`mc/agent_mail_outbound.py:223`) binds the originating chat; signed transport reaches `worker.js:27,75-76`. Owner-only provider selection and plain Reply-To/References token: `workers/agent-mail/email-sending.js:51-74`. **Missing:** shared binding/onboarding ops O2. No recipient parameter is added. |
| S11 | Owner replies. Domain routing sends it to Worker; Worker authenticates owner plus this email's token, durably queues and wakes the correct install. | **EXISTS:** `worker.js:82-87`; owner/DMARC/token checks `auth.js:78-115`; durable custody/wake `ingress-pull.js:62-83`. **Unverified ops:** catch-all O1. Token is in References/In-Reply-To; legacy +token remains supported. A new unsolicited email cannot create a chat (`auth.js:83`). |
| S12 | Install takes signed custody, acknowledges, strips quotes and delivers the reply to exactly the sending chat. | **EXISTS:** `mc/agent_mail_pull_receiver.py:243` (receive); deterministic extraction `mc/agent_mail_extract.py:46`; target binding and follow-up `mc/agent_mail_wake.py:189-229`. This is the shipped verified-owner path; do not restore the removed model laundering/content screen from older plans. |

## 2. Build units

**Seven production targets, one file plus its focused test per unit.** No new
signup state machine, provider adapter, schema, mail transport or store is
proposed. Existing routes/models are the units they already own. Estimated
sizes are changed production lines, not commitments or measured test results.
Owner tiers are suggestions for Dave, not dispatches made by this plan.

| Unit | One production file | Change and acceptance | Test / suggested owner tier |
|---|---|---|---|
| **U1** | `mc/blueprints/agent_mail_enrollment_routes.py` | Replace the unavailable default legacy adapter with pull signup composition; retain injected test seam. Passive GET returns explicit pull protocol discriminator + existing `pull_status` and `Signup.status` data; pristine absent store is `not_enrolled`, held store stays held. Only human start bootstraps. Validate owner/consent; use release-reviewed public root/origin defaults when unset, persist through the existing atomic serialized config pattern (`agent_mail_deployment_routes.py:62-85`); never fetch a new trust root from an untrusted server or overwrite custom deployment values. Start persists enrollment availability only; outbound/ingress/supervisor opt-in follows signed activation and durable config success. Call existing `start_default(current_app)` idempotently; repeat enable repairs a failed local flag save without a second activation. Keep passcode, unattended and conservative blocking-session gates. No owner/key/recovery endpoints added. About 180-250 lines. | `tests/test_agent_mail_enrollment_routes.py`: fresh install without provisioning/config edits; GET/decline no identity/network/consent; custom service retained; locked/corrupt stores refused; unauthorized/unattended calls refused; resume/restart; enable lost response/flag-save failure; supervisor starts once only after activation. **Sol_Tobin / Codex**, authority + persistence. |
| **U2** | `static/js/agent-mail-setup.js` | Keep existing Settings/first-run mounts. Show verified-owner disclosure, released service identity and vault prerequisite. Post only owner + fixed consent version to the human route; stop using the legacy manifest payload. Do not remove first-run setup when mail is merely unconfigured. Keep decline local. About 30-60 lines. | `tools/smoke/agent-mail-setup.mjs`: new and already-enrolled installs, click/decline, failed/cancelled passcode, no email on mount. **Tobin / Sonnet**, UI wiring. |
| **U3** | `static/js/agent-mail-actions-model.js` | Recognize explicit pull result; map pending -> check verification, verified -> enable, active -> no signup action, refusal/expired -> clear reason. No code/proof/review buttons for pull. Keep unrelated legacy handling isolated if still needed by tests; no fake proof fields. About 40-70 lines. | New `tools/smoke/agent-mail-actions-model.mjs` (pure Node test, no browser): pending/queued/failed/uncertain/verified/enable_pending/active/expired, malformed data fail closed. **Tobin / Sonnet**. |
| **U4** | `static/js/agent-mail-actions.js` | Render link instructions and Check verification using resume; turn-on action keeps the existing typed-passcode prompt. Use U3's pull controls, display refused results as failure even when HTTP 200 carries a held state. No hosted token enters local agent context. About 30-60 lines. | `tools/smoke/agent-mail-actions.mjs`: pending through active, no code/tunnel-proof interaction, rejected activation never announces success. **Tobin / Sonnet**. |
| **U5** | `static/js/agent-mail-status-model.js` | Describe existing pull projection: owner/address, local enablement, actual socket/vault state and separately labelled local/service queues. Unknown remains unknown. Preserve `describe()` output shape so existing status renderer needs no edit (`agent-mail-status.js:34-45`). About 50-90 lines. | `tools/smoke/agent-mail-status.mjs`: pristine/verified/active/offline/locked/held; no tunnel or extraction-model prerequisite. **Tobin / Sonnet**. |
| **U6** | `workers/agent-mail/signup-http.js` | Add operator-controlled fresh-signup admission, default refused unless enabled by U7. After exact durable replay resolution and before new token/outbox issuance, reserve a small aggregate fresh-signup/day budget and enforce a live pending-signup ceiling in the **same existing D1 batch**. Proposed pilot: **10 new signups/UTC day, 10 live pending flows**; values await D2. Replays/status/confirmation of admitted requests continue while new admission is off. Reuse `pull_budgets`, registry guards and existing verification quotas; no migration. About 40-70 lines. | `workers/agent-mail/test/signup-http.test.js`: races across new ids/owners/IPs cannot exceed cap; no provider call/outbox on refusal; replay free; disabling admission preserves in-flight completion. **Sol_Tobin / Codex**, atomic shared-account admission. |
| **U7** | `workers/agent-mail/worker.js` | Supply the reviewed signup-admission option only to signup; wire existing `deliverControlMail` into scheduled handling with `VerificationMail`, seal config and `MAIL_SEND`. Preserve the pull sweep, including when signup config is absent/fails; one failed job must not starve the other. Keep glue here, delivery bodies in the existing module. No owner-change/recovery work. About 25-45 lines. | `workers/agent-mail/test/worker-pull-routing.test.js`: real entry start -> queued outbox -> cron -> fake binding -> hosted POST -> signed status/activate; consumer failure cannot suppress notification sweep, absent signup seal does not break already-provisioned installs. **Tobin / Sonnet**, Dave reviews. |

U6 precedes U7; U1 defines the small pull DTO before U2-U5. Work sequentially
unless Dave explicitly approves concurrent spend. Existing
`tests/test_agent_mail_signup.py`, `test_agent_mail_activation.py` and
`test_agent_mail_slice_e2e.py` are regression/interop coverage, not more production
build units. Extend the focused U1/U7 tests to exercise the real composition,
rather than proving only individually injected adapters.

Use the installation's `tools/test-run.py` launcher with the exact selected
pytest/Node/smoke argv; package test runner is `node --test`, not Vitest. Run
mail/route tests and the four touched smokes after each merge. Link the main
checkout's existing smoke dependencies if needed; do not install another browser.
New Python code must add no pyright errors. This doc run executed no tests.

### Existing caps and the minimum spend gate

| Existing control | Effective limit and source | What it does / does not stop |
|---|---|---|
| Normal outbound | **20/install/day, 1,000 globally/day** (`ledger.js:6-8`; atomic pull charge `ledger-pull.js:200-221`). | Charge before provider handoff. Recipient is the active verified owner, not caller input. Random unverified installs cannot send notes. |
| Verification | **3/install/day, 3/target/day**, sharing the same **1,000/day** global bucket (`owner-registry.js:58-67`; issuance `verification-mail.js:249-258`). | Fixed template only. Fresh install ids evade install limits, but not the target/global counters. These are UTC-day buckets, not rolling 24-hour windows. |
| Owner slots / expiry | **3 active or live-reserved installs/owner** (`owner-registry.js:75-84`), **30-minute** signup expiry (`verification-mail.js:9`). | Prevent one verified mailbox accumulating unlimited active installations. Not Sybil identity or a global pending-signup cap. |
| Public HTTP | **30/source/minute** (`signup-http.js:137-140`), **500 management requests/install/day** (`:165-175`). | Trusted edge source only. A different source/new identity can bypass per-source/per-install limits; signatures prove key possession, not a human. |
| Reply custody | **100 pending events/holds + 2 MiB/install**, **10,000 + 200 MiB globally**; **16 KiB text/event**, **200 ingress/install/day, 10,000 globally/day** (`pull-ledger.js:143-148,178-187`; no-body holds `ingress-pull.js:31-39`). | Durable queue/storage backpressure. These are **reply** limits; they do not bound signup verification envelopes. |
| Verification delivery | Fixed sealed template, leased one-shot delivery, uncertain outcomes not blindly retried, retries spend global budget (`control-mail-delivery.js:94-158,250-280`). | Reuse this consumer. Do not call `MAIL_SEND` directly from a new local/signup action. |

**Minimum pilot gate proposed in U6:** leave the shared 1,000/day cap intact and
bound fresh signup admission to 10/day and 10 pending, transactionally before
token issuance. The local passcode is a human product gate, **not** a shared
service abuse defense: modified clients can create keys and sign starts.
Per-target limits alone do not stop someone targeting many mailboxes. U6 limits
that verification stream and prevents it exhausting the normal-send allowance.
It is a pilot circuit breaker, not bot resistance: a stranger can still consume
all 10 signup slots and cause temporary admission denial. Do not claim otherwise.
Turnstile/invites introduce another dependency and are not required for the
second-install test; open public scale is a later choice.

At the existing 1,000 total reservations/day, a 30-day month can authorize at
most 30,000 paid messages: **$9.45** email overage after 3,000 included, assuming
the whole account allowance is available and all sends are billable. Verified
destinations are free. This is an email-usage bound, not an account invoice or
protection from HTTP/Workers/D1 traffic. Keep provider quota refusals visible;
Cloudflare publishes no single guaranteed initial daily quota. Do not raise
global caps or change retention for this slice.

## 3. Ops that need Ron, separately from build work

No step below was performed. Dave owns the decision record; Ron authorizes
deployment, credential, DNS, billing and outward-facing changes.

| # | Operator action / evidence | Requirement |
|---|---|---|
| **O1** | With an already-authorized credential containing Email Routing read permission, repeat the three routing GETs above; read sending subdomains. Confirm active catch-all -> deployed mail Worker, preserve `hello@` exact forwarding. | **Blocked readback today (403)**. Ron can inspect the dashboard or supply authorized read access; do not silently create/expand credentials. If catch-all is absent, request the exact provider change before making it. No per-signup routing rules. |
| **O2** | If D1A approved: confirm Workers Paid and onboard **sending** for `clayrune.io`, including required DNS/provider verification. Change `MAIL_SEND` from the current single-recipient allowlist to a binding capable of sending to arbitrary owners (optionally sender-restricted), then prove a non-Routing-destination send. | Billing/DNS/provider/binding/deploy approval. One-time service ops, never per-user. Keep owner-only app checks and References token carrier. Domain onboarding status is not verified in this run. |
| **O3** | Human creates/publishes `MAIL_PULL_OUTBOX_KEY` (32-byte seal key) and `_ID` through the existing approved secret workflow. Keep the service signing key, D1 and DO bindings. | Hosted constructor requires them (`worker.js:65-66`). No key values in config/source/transcript. Do not delete `MAIL_DESTINATION_TOKEN` as part of this slice; secret retirement is outside scope. |
| **O4** | Approve/release U1-U7; deploy Worker with explicit signup admission and its pilot values; retain existing minute cron, `MAIL_ENABLED` and reviewed `MAIL_INGRESS_VERIFIED`. Publish the verified public service origin/root in the client release default used by U1. | This plan does not deploy, renew keys, change trust anchors or enable anything. No CP/tunnel/custom-domain service, no new migrations, no per-install SQL. Confirm the manifest is current before live activation; renewal only if needed under the existing runbook. |
| **O5** | Load the client release on the test install, hard-reload UI, user sets passcode and initializes/unlocks its own vault, then consents through signup. Approve controlled verification/note/reply mail. | No manual runtime config edits on the second install; that would invalidate the self-serve test. Rollback: close **new** signup admission and roll back code/config deployment while retaining committed accounts/receipts. Do not delete user data or regenerate keys. |

If Dave instead chooses D1B, O2 becomes: unrestricted verified-destination binding,
capacity readback, retained destination credential and a reviewed pull-path
adapter invoking the existing provisioning helper. Its existing limits are
3/device/day, 20/account/day and a 190-address admission stop
(`workers/agent-mail/destination-provisioning.js:49-58`); those limits currently
belong to the retired ceremony and must not be claimed as active pull protection.
That option needs a revised build delta before dispatch, not an improvised use
of the retired CP endpoint.

## 4. Live test: a genuinely second install

Preferred target is the existing **Windows 11 test VM**, with a clean Clayrune
user profile, a different consenting owner mailbox **B**, and two fresh chats
**T** and **control**. The VM does not need Cloudflare/Wrangler credentials.
Use a mailbox not already registered as a Routing destination for D1A. Operator
readback confirms that prerequisite without adding it. No test was run here.

1. Install the proposed client release; configure only the normal dashboard
   passcode and user-owned vault. No operator provisioning script, copied
   database, identity seal, SQL, install id or mail config file. Record clean
   pre-state and build ids.
2. Open Agent mail. GET and **No thanks** send no email and create no signup
   identity. Reopen, choose signup, enter B, review disclosure and type passcode.
   Expect one stable install/flow, unique `m...@clayrune.io`, and one fixed
   verification email. Compare the assigned address with the existing install:
   distinct, with no exposure of either owner's private mailbox in reports.
3. Open the hosted link once (GET only): still pending. Explicitly confirm;
   return to Clayrune, Check verification -> verified. Still no enabled
   outbound/socket privileges. Duplicate confirmation must not resend mail.
4. Enable with fresh passcode. Expect signed active receipt, local enabled
   state, one supervisor/socket and truthful Settings status. Restart the VM
   client once and confirm the same account reconnects without re-provisioning.
5. From **T**, send `signup-slice-1` through the existing note route or
   `send_mail.py --project <project> --session <T>`. Require **agent-mail
   accepted**, with no Gmail fallback; fallback makes this test fail. Inspect
   plain per-user Reply-To and References carrier. B receives exactly one note.
6. B replies with a unique harmless marker. Require remote enqueue -> local
   custody -> ack/body retirement -> one completed turn in **T**, containing that
   marker. **control** and the original install receive zero new turns.
7. Negative checks, under the same separately approved mail test: an unconfirmed
   account cannot send/activate; another sender replying with the token is
   rejected; B sending a fresh unthreaded message cannot start a chat. Offline
   unit tests cover bad-token/race/budget cases without spending real mail.
   Verify `hello@` forwarding remains intact by readback; any extra real-mail
   forwarding probe needs its own consenting sender.

A second local process/data directory is an alternative only if **all** mail
store/marker/seal/vault/config identities are isolated: default mail state is
home-scoped (`mc/agent_mail_factory.py:75-94`), so another repo `data/` directory
alone is not another install. Do not start another process on port 5199, share
the live mail identity or call a fake-backed test a second-install live test.

## 5. Decisions for Dave — recommendations, none selected here

| Decision | Options / recommendation | Cost / reversal |
|---|---|---|
| **D1: recipient permission** | **Recommend A:** onboard Email Sending once and keep only Clayrune owner-link verification. B: automatically provision each owner as a Routing destination, wait for Cloudflare verification, then run Clayrune confirmation. | A: one shared Paid/domain/DNS/binding ops step; 3,000/month then $0.35/1,000; no extra transport code. B: avoids paid-message usage, adds pull destination wiring + second email click and 200/account ceiling (helper stops at 190). If A onboarding/entitlement cannot be obtained, stop and revise for B; do not silently substitute. Reverse A by stopping new admission and reviewing destination enrollment for existing users. |
| **D2: minimum admission** | **Recommend A:** retain existing caps plus 10 fresh signups/day and 10 live pending for the second-install pilot. B: expose signup under existing 1,000/day total only. C: invitation/Turnstile gate now. | A: one atomic U6 edit, bounded verification spend; can deny new users when slots fill. B: least code but one attacker can exhaust the shared send budget across targets. C: stronger admission friction, adds hosted challenge/invitation workflow and secrets/config; unnecessary for the controlled second install. Reverse A by separately reviewed cap change, not removing send/owner checks. |
| **D3: local UX/trust bootstrap** | **Recommend A:** release-reviewed public hosted-service defaults and existing Settings setup/check/enable flow; existing overrides stay intact, typed passcode and vault gates remain. B: require users to copy public root/origin into the deployment card before setup. | A: U1 persists only public deployment/rollout config after human consent; U2-U5 adapt existing UI. B: less defaulting logic, extra setup instructions and copying, still no per-user Cloudflare access. Neither permits silently changing an established trust anchor. Reverse A by removing defaults for new installs; preserve existing pins and accounts. |

Unresolved evidence, not a design choice: O1 routing readback and sending-domain
status failed with explicit 403s. Dave needs authorized readback before ops/live
readiness can be claimed. The historical readiness/runbook CP/tunnel, numeric
code, model-extraction and +token-first instructions are not prerequisites for
this pull signup slice. Key rotation/recovery, delete, owner change, service
review, retention changes and multi-account per install remain out of scope.

Validation of this planning artifact: source/constructor/caller trace, public
Cloudflare docs and the read-only calls listed above; `git diff --check` and
file:line citation existence check before commit. No functional tests, live
signup, mail, configuration edit, deploy, merge or push is claimed.
