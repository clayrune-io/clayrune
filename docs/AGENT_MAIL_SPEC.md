# Per-install agent email

> **2026-11-11 transport update:** Ron selected outbound-connection pull mail.
> See [AGENT_MAIL_PULL_DESIGN.md](AGENT_MAIL_PULL_DESIGN.md) for the proposed
> replacement of tunnel/CP delivery and signup; the binding owner/address-change
> abuse controls below remain in force.

Status: **Draft for Dave's decisions; not approved for implementation or deployment.**
Author: Marlow. Date: 2026-11-08.

This document records Ron's requirements and the existing implementation seams.
Sections marked **Required** follow those requirements or preserve existing safety
boundaries. **D1-D8 are open**: alternatives and recommendations are proposals,
not selections. A builder must not silently select an alternative.

## Problem and intended user

A person running Clayrune needs notes and questions from their agents while the
dashboard is closed. Replying to either kind of email must deliver the reply to
the agent conversation that sent it and cause that conversation to run a
follow-up turn. Opening the dashboard must not be necessary for normal delivery.

The unit of enrollment is an **installation**, not an agent or project. Each
opted-in installation receives an `@clayrune.io` address, associated with its own
verified owner email. Every outgoing thread records its originating project and
agent session. Two agents on one installation must never receive each other's
replies, even if their subjects and character names match.

## Fixed scope

**Required by Ron, 2026-11-08:**

1. Offer enrollment during installation/setup. The user can decline and complete
   installation, enable later, or turn the feature off later. No registration or
   owner-address transmission occurs before explicit opt-in.
2. Register an opted-in installation with the Clayrune Worker. Registration may
   contribute an activated-install metric alongside the existing anonymous update
   ping. It is opt-in and therefore undercounts installations. It links an
   installation to a real email address: privacy policy and disclosure must change
   before launch.
3. A reply to a note or question must wake the **exact originating session** and
   deliver readable reply content as a follow-up turn it can act on, within that
   session's existing permissions. Use the existing
   `POST /api/project/<project_id>/agent/followup` path. No parallel mail-only
   process launcher, resume command, or agent-dispatch substitute is permitted.
4. Outgoing mail goes **only to that installation's verified owner**. Inbound mail
   enters through a Cloudflare Email Worker and reaches the user's installation
   over its tunnel. Bulk and promotional mail stay outside this channel. This is
   the adopted standing position, not an open sending-policy question.

**Required safety and reliability boundaries:**

- The Worker enforces the installation-to-owner relationship. A client cannot
  nominate another recipient, send CC/BCC, override transport headers, or exploit
  another installation's verified destination. Verification alone is not a
  cross-install allowlist.
- The authenticated local agent context supplies the project/session binding;
  message text and caller-provided names cannot choose the target conversation.
- An unknown token, unauthenticated sender, revoked enrollment, deleted project,
  or unavailable original conversation must never fall back to an arbitrary live
  agent, the project's default character, or another installation.
- Raw inbound content never enters a tool-bearing session. Reuse the toolless
  laundering boundary in `mc/mail_launder.py`, preserve its failure-closed behavior,
  and keep the resulting envelope labeled as untrusted content.
- A verified email reply is a user communication channel, not a new authority
  source. It cannot bypass passcodes, human-only publish/credential gates,
  ownership restrictions, or the session's tool/execution policy.

## Explicit non-goals

- Mail to third parties, shared mailboxes with several owners, forwarding rules,
  campaigns, promotional email, contact lists, or an SMTP relay.
- A general-purpose inbox, unsolicited instructions sent to the base install
  address, or creating agent sessions from unthreaded mail.
- A replacement for the existing chat, `mc:question` forms, session lifecycle,
  provider resume machinery, or the operator's legacy SMTP configuration.
- Attachment execution, automatic URL fetching, calendar invitations, or a new
  browser/mail-MCP route for reading raw replies. Attachment support is deferred.
- Resolving the long-term effect on the private tunnel product. That question is
  deferred by Ron; list it only under deferred questions below.

## Verified baseline and gaps

Repository inspection was performed on branch `clayrune/agent/455a025f1d00`, based
on `646b94b5`. These are code observations, not live end-to-end mail verification.

| Existing component | What it does today | Required extension or constraint |
| --- | --- | --- |
| `mc/question_channel.py` | After an attendance grace window, emails parsed `mc:question` requests through `tools/night-review/send_mail.py`; polls the operator's IMAP inbox. | Reuse question rendering/attendance behavior, but add the registered-owner transport. Do not depend on operator Gmail credentials on stranger installs. |
| `question_channel.handle_reply()` | Matches subject qid plus our outbound Message-ID in `In-Reply-To`/`References`; checks From as an unauthenticated extra filter. Option-bearing questions accept a matching first line; free-text questions deliberately remain open. | Preserve correlation checks. Add authenticated, laundered free text and notes through a shared reply adapter. A subject or 8-hex qid is not authentication. |
| `_outbox`, `_delivered`, `_answered` | In-memory only. `_answered` can be marked before `_answer()` succeeds. | Durable thread, delivery and deduplication state must replace these as the authority for the new transport. Failed follow-up must not consume the reply. |
| `mc/mail_launder.py` | `launder_mail_digest()` uses the verified Claude toolless transform seam; returns a warning-labeled decision digest, including `reply_text`, `decision` and `ambiguous_or_mixed`. Current prompt assumes Ron and a decision request. | Generalize extraction for each install's owner and arbitrary reply text without inventing approval. Preserve existing decision-digest callers. No raw fallback if the runtime is unavailable. |
| `mc/blueprints/agent_routes.py::agent_followup` | Handles ordinary follow-ups; resolves conversation ownership; revives missing Claude sessions from agent log. Non-Claude cold resume exists for Codex/Qwen/Gemini when a native session id was captured; other cases may start cold under the same MC id. | Add an internal exact-conversation constraint before any provider submission. A successful HTTP response, or the same MC id, does not establish preserved conversation history. |
| Same route's delivery acknowledgments | Some Mode B writes support `_durable_delivery_ack`; Mode A may enqueue only in memory. Respawns/provider errors do not uniformly yield durable turn acceptance. | Reuse and extend this submission contract; do not claim an existing universal exactly-once/read receipt. |
| `mc/delegation_delivery.py` and guarded submission in agent routes | SQLite-backed inbox/outbox, leases, durable state and explicit uncertain-delivery handling already exist for child notifications. | Reuse their durable-acceptance/uncertainty conventions and suitable helpers. Mail must have its own payload/state ownership, not masquerade as a delegation completion. |
| `mc/update_ping.py`; `workers/update-ping/{worker.js,schema.sql}` | Sends exactly `{install_id, version, os}` after disclosure, on by default; settings/env opt-out wins. Receiver stores first/last seen and responds with latest version. | Do not add email, thread data, or mail consent to this payload. Mail registration has a separate identified record and explicit consent. |
| `static/js/first-run.js`; `static/js/setup-update-ping-step.js` | First-run UI owns setup choices; ping disclosure is its own step module. | Add a separate mail-enrollment step/module. Consent to the anonymous ping does not opt into mail. Declining mail must not block installation. |

The deployment journal `docs/_journal/update-ping-deploy.md` in the main checkout
records a deployed and exercised update-ping receiver on 2026-11-05. The
`workers/update-ping/README.md` and worker header still say not deployed. Treat
that status text as stale; this spec did not inspect the live Cloudflare account
or change that separate feature. The deployment journal is operator-local and
is not a tracked link a stranger checkout must resolve.

## Enrollment, verification and controls

**Required state model:** declined/off -> pending verification -> active ->
disabled/revoking -> revoked. Verification errors, delivery blocked, and deletion
pending are visible conditions, not synonyms for active.

The setup offer explains, before collecting the address:

> Optional agent email gives this installation an @clayrune.io address. Agents
> can email only your verified address. Your replies return to the conversation
> that sent the email and can start a new turn there. Clayrune and Cloudflare
> process your owner address, installation registration, routing data and mail
> content to provide this service, including temporary storage while your machine
> is offline. This is identified data, separate from the anonymous update check.
> You can skip this and turn it off later in Settings.

Final wording must name the selected retention limits and model-processing
dependency from D4/D8. The feature may not be described as anonymous or as
collecting nothing. The optional registration count must be disclosed too.

Activation requires explicit consent, proof of control of the owner mailbox,
provider-confirmed destination verification, and a tunnel route bound to this
installation. A pending request does not receive operational sending privileges.
Enrollment must remain resumable without a dashboard tab left open. A failed
verification or unavailable tunnel leaves a visible pending/blocked state.

Cloudflare verification and installation pairing are separate checks. A scanner
opening a verification link must not activate another installation or alter its
owner. Bind the pending challenge to the originating human enrollment flow,
expire it, and make confirmation idempotent. D1 specifies provisioning options.

Settings must show owner, assigned address, verification and tunnel/delivery
status, a way to resume verification, mail on/off, and pending/rejected reply
status. Changes of owner require verification of the replacement and revocation
of the previous binding; existing threads cannot be reassigned to a new owner.
The old recipient must not remain an allowed recipient after the switch.

### Owner and address change: abuse controls (Ron 2026-11-11, binding)

Ron: "make sure no one can abuse this service by changing emails somehow." An
owner change is the one action that redirects every future agent email, so it
is treated as a credential change. Each rule below closes a named abuse:

| Abuse | Control |
|---|---|
| Someone with the dashboard (stolen phone, open tunnel session) redirects mail to themselves | Change is human-only and passcode-gated, same gate as publish. Agent-facing mail operations cannot start, confirm or cancel a change (no route, no tool). |
| Silent takeover | The new address activates only after its own single-use link is clicked. At the same moment the OLD address gets a fixed-text notice with a "this wasn't me" link that freezes mail and reverts to the old owner. Revert stays valid 7 days. |
| Using verification mail to spam strangers | Verification and notice emails are fixed templates; no user- or agent-supplied text, subject or links. Limits per install: 3 verification emails per day, 1 completed owner change per 7 days, 5 per year. Per target address: 3 verification emails per day across all installs. |
| Hijack by a scanner or forwarded link | Links are single-use, expire in 30 minutes, are bound to the pending request, and need a click on a confirm page (POST), so a link prefetch cannot confirm. Confirming twice is a no-op. |
| Old threads leaking to the new owner, or old owner still replying | Each thread is bound to the owner at send time. After a switch, replies from the old address are rejected as unverified, and old threads are never reassigned. |
| Grabbing a released @clayrune.io name to receive someone's mail | A renamed or deleted name is held 90 days and cannot be claimed by anyone else. A reused name never inherits old threads. |
| One victim address enrolled on many installs | One owner address may own at most 3 active installs; each needs its own confirmation click from that mailbox. |

While a change is pending, mail keeps going to the current verified owner; the
pending address has no sending or reply privileges. All limits are enforced by
the mail service, not only in the local UI, so a modified install cannot skip
them. Every change, revert and rate-limit hit is logged on the service and shown
in Settings.

Turning mail off immediately blocks local sends and automatic mail follow-ups.
Worker revocation is retried durably if offline; the UI must distinguish local
off from completed server revocation. The Worker rechecks enrollment at both
receipt and replay. Already-sent mail cannot be recalled. Re-enabling cannot
silently release old queued instructions. Remote/local deletion behavior and
whether a disabled address is reserved depend on D4/D2. Uninstall instructions
must explain remote data removal; deleting local files alone is insufficient.

The existing anonymous-ping settings and `DO_NOT_TRACK`/`CLAYRUNE_NO_PING` behavior
remain intact. Mail consent is separate and cannot turn ping back on. D3 covers
counting and cross-dataset linkage; mail service traffic is not an anonymous ping.

## Outbound notes and questions

Both kinds use one registered-owner delivery interface:

- A note is ordinary session-authored text with no pending question requirement.
  Its reply must still wake the originating conversation.
- A question retains its question id, all question/option definitions and question
  revision at send time. Preserve the existing attendance grace for automatic
  question email; do not send both transports for the same notification.

The interface accepts bounded subject/body and an intent/idempotency key. It
derives owner, From, Reply-To, Message-ID and session binding server-side. The
worker constructs authoritative recipient headers rather than relaying arbitrary
MIME from the client. Per-install and global quotas must include verification,
sends, inbound processing, storage and model work. A user-supplied `to`, CC/BCC,
header injection, or cross-install thread reference is rejected before sending.

Before transport, durably record the sending intent and immutable thread binding:
registration/generation, original owner generation, project/generation, MC session
id, provider/native conversation id when available, character identity, message
kind, outbound Message-ID, thread-token verifier, and question/revision if any.
The local binding is authoritative for the target; inbound MIME cannot supply it.
Persist correlation at the Worker before a message can leave, so an immediate
reply cannot race thread registration. Use the same outbound intent key on retry.

Every thread uses a cryptographically random, unguessable reply token, with at
least 128 bits of entropy. The token is not a qid, session id, email address, or
the anonymous install id. Store a verifier rather than plaintext where lookup
allows; redact tokens and full reply addresses in logs. Token expiry/revocation
must be explicit. D2 selects address syntax and lifecycle. Message-ID correlation
remains an independent check; a token alone does not establish sender identity.

An accepted send means accepted by the transport, not delivered to the owner's
inbox or read. A transport timeout after possible acceptance enters an uncertain
state; SMTP is not transactionally exactly-once. Never resend blindly or invent
delivery success. Failures remain visible without failing unrelated agent turns.
The mail body identifies project/conversation and says what replying does.

## Inbound trust and correlation

The Email Worker's `email()` handler is the sole public mail ingress for this
feature. Its sequence must be:

1. Bound MIME size/work, check registered envelope recipient/token and active
   owner/enrollment generation. Reject unknown, expired or revoked targets.
2. Parse one unambiguous RFC mailbox From value and compare the exact registered
   owner identity. Do not use substring matching, display names, a submitted
   `Authentication-Results` header, or Reply-To as proof of sender identity.
3. Use Cloudflare's own ingress authentication result: the prepended result with
   authserv-id `mx.cloudflare.net`, with parsed SPF/DKIM/DMARC alignment details.
   D6 selects the acceptance policy. DMARC authenticates a domain, not a person
   or a specific mailbox; combine it with the verified owner match and secret
   token. Unsupported forwarding/alias cases require a visible hold, not weaker
   identity checks.
4. Require `In-Reply-To` or `References` to name an outbound Message-ID registered
   to this thread. A changed subject is not a routing failure; a subject alone
   can never establish routing. Reject cross-thread/cross-install references.
5. Suppress outgoing copies, bounces/delivery reports, auto-replies and loop mail
   without launching an agent. Bound `References`; do not fetch external content
   while parsing. Do not generate automatic replies to unauthenticated senders.
6. Atomically persist the authenticated event and deduplication evidence before
   scheduling delivery. A spoofed/rejected event must not consume a genuine
   owner's later reply or close the question.

Cloudflare currently documents the ingress authentication result through
[`ForwardableEmailMessage.headers`](https://developers.cloudflare.com/email-service/api/route-emails/email-handler/).
That documented boundary must be verified with real provider fixtures before
launch; arbitrary MIME headers are not a substitute.

Worker-to-install delivery carries an integrity-protected envelope binding event
id, installation/generation, thread, authentication verdict, body digest,
receipt time and replay expiry. The install verifies it before accepting data;
mail headers never stand in for Worker authentication. D7 selects the transport
authentication mechanism. Protect against request replay and cross-tenant access.

The only permitted destination is the installation's proven Clayrune tunnel
route. A registration request cannot make the Worker call a supplied arbitrary
URL, loopback address, private network, redirect target, or another user's tunnel.
Reuse existing tunnel identity/provisioning evidence and scope the new receiver
to mail delivery; do not disable dashboard authentication or distribute a
Cloudflare management token to users. If no compatible tunnel exists, explain
the dependency and leave mail pending. Automatic provisioning is D1's open scope.

## Offline queue, replay and local durable inbox

Cloudflare mail routing alone is not a durable queue to an offline installation.
The implementation must supply persistence and replay; D4 selects the storage
mechanism and operating limits.

- After authenticated ingress is durably accepted, network failure, tunnel outage,
  local restart or a 5xx leaves the event queued. Replay the same event id with
  bounded backoff. A local connectivity recovery requests reconciliation; do not
  rely on an email handler invocation or an in-memory timer remaining alive.
- The local receiver authenticates and durably inserts an event **before**
  acknowledging custody. The Worker can discard its content after that receipt
  is durable under the chosen retention policy. Local custody is distinct from
  a provider accepting a turn and from the agent reading/completing it.
- The inbox rechecks local consent, owner generation and project/session binding
  after every restart and before submission. Local durable state lives outside
  `data/projects/`; use the existing atomic-write/database conventions. No
  mail sidecar may become a malformed project or silently restart from empty
  after corruption. Stop delivery and expose storage errors.
- Use unique event keys, transactional claims and leases. Duplicate webhook
  requests/replay cannot create duplicate accepted follow-ups. Different valid
  replies in one thread are separate events, not a single `_answered` bit.
  SMTP redelivery must resolve to the first accepted event, not allocate a new
  random id each time. Scope deduplication to the authenticated registration,
  thread and incoming Message-ID, with a bounded-content digest to detect
  conflicting reuse. Missing/ambiguous identifiers require a visible hold;
  equal reply text in two distinct messages does not make them duplicates.
- Preserve accepted receipt order within a conversation. If it is busy, hold in
  the durable mail inbox until the existing lifecycle can accept a turn. An
  in-memory `pending_followups` list is not durable custody or submission proof.
- Queue exhaustion and expiry must produce a visible terminal/held outcome,
  never silent truncation, success, or indefinite unbounded retention. An outage
  longer than the disclosed retention window cannot be promised lossless delivery.
  Revocation cancels queued execution rather than replaying after re-enable.

Suggested event vocabulary, expressing required distinctions rather than a
selected database schema:

`worker_pending -> local_received -> laundering -> ready -> submitting -> submitted -> turn_completed`

Side states: `retryable`, `blocked`, `uncertain`, `rejected`, `expired`, `revoked`.
`submitted` requires positive provider/turn acceptance evidence; `turn_completed`
requires the corresponding lifecycle event. Completion does not claim the agent
carried out every request. A 2xx from an enqueue-only or provider-error branch
must not be interpreted as either state.

A crash after a possible provider write but before durable acknowledgment is
**uncertain**. Reconcile against existing turn/provider evidence; if it cannot
be proved, require review instead of replaying automatically. This preserves the
existing durable-delivery posture without promising impossible exactly-once
effects for arbitrary agent actions. D4's retention rules must cover holds too.

## Laundering and reply semantics

After ingress authentication and local custody, a toolless extractor sees bounded
mail content and trusted thread metadata. Its schema must support `reply_text`,
quote/forward separation, ambiguity and extraction failure for a note or free-text
question. A general reply must not be forced into approved/declined: “use the
second file and wait until Friday” is reply text, not a yes/no verdict.

Retain the existing decision-digest contract for its callers. The adapter for
this feature must remove Ron-specific assumptions, preserve the user's wording,
and keep quoted/forwarded/HTML-hidden text out of actionable reply text. HTML is
inert input; scripts, attachments and links are never executed or fetched.
Unsupported content, oversize text and extraction truncation are explicit holds,
not silent partial instructions. Laundering is a capability boundary, not proof
that the extracted content is benign or authorized.

If the extractor errors, times out, returns malformed output, cannot separate
fresh text, or reports `ambiguous_or_mixed`, hold and expose the reason. Do not
fall back to raw body/subject, mail MCP, IMAP, a different unverified runtime, or
a first-line heuristic. Retry only a recorded event, with bounded model work.
The existing implementation verifies toolless operation only for Claude; D8
must be resolved before promising replies on a Codex/Gemini-only installation.

For a note, deliver the extracted text and warning-labeled provenance as a normal
follow-up. For a question, validate against the stored question/revision:

- An unambiguous option response may consume only its own still-pending question.
  Multi-question/multi-select answers must retain question boundaries; never
  flatten every option into one positional list.
- A free-text answer is delivered as text. A reply outside the offered options
  must not be relabeled as a choice the user never made. It can be delivered as
  a clarification follow-up without inventing an approved/declined result.
- A reply arriving after an answer in chat is a later message to the same
  conversation, marked as late. It must not clear a newer unrelated question or
  plan-approval gate. Two distinct owner replies must remain distinguishable.

The existing follow-up route clears all waiting question/plan flags before
dispatch. Mail integration must add question/revision-aware behavior there;
otherwise even a plain-note reply can accidentally consume an unrelated gate.
The mail relay must not translate an email into a product approval-button click.

## Exact session wake-up and continuation

Required sequence:

1. Resolve the persisted originating project/session and conversation identity;
   validate generation, current consent, owner generation and lifecycle holds.
2. Claim the local event and call the existing follow-up route with an internal
   authenticated mail-origin envelope, event id and exact-conversation constraint.
   These are proposed extensions to that route, not fields claimed to exist today.
3. The normal route/lifecycle performs provider submission and any permitted
   revival. Preserve project, character, worktree, execution/permission context,
   incognito handling and conversation history. Reuse existing duplicate-process
   prevention. A restarted OS process is acceptable; an unrelated conversation
   under the same character or MC id is not.
4. Persist provider acceptance evidence against the event and turn. Display the
   mail source in the conversation. Only matching lifecycle evidence completes
   the event. Test that the resumed turn reads the reply, not merely that an HTTP
   handler returned 200.

`agent_followup` already invokes `_revive_from_agent_log` and
`_revive_non_claude_from_agent_log`. Extend these existing paths only where
needed. They can also select cold/fresh or auto-fresh behavior, and provider
branches can return success after exceptions; exact-session enforcement and
receipt quality must be checked **before** the new reply is submitted. Inspecting
`fresh: true` after the turn has started is too late.

If the recorded conversation already has a live canonical owner, resolve it only
with trusted proof that it is the same conversation. Do not use a character name
or a message-supplied session id as an alias. Automatic rollovers need an explicit
durable lineage contract under D5; no broad “same agent” fallback is acceptable.

Deleted project/session, explicit closure/cancellation, missing native transcript,
incognito non-persistence, invalid project generation, revoked permission, quota
block, provider failure and missing persona/worktree are separate visible holds.
An email must not silently undo intentional deletion/closure or recovery review.
Provider/quota holds retain the event without switching providers or spending
unbounded retries. Session fallback policy is D5; the ordinary idle/ended session
with valid continuation evidence must be covered by acceptance testing.

Mail-origin continuation must not re-arm a dispatch's spawner/workflow completion
notification merely because a human replied. Preserve the existing follow-up
callback ownership semantics. Any special callback behavior would need its own
explicit requirement, not a mail transport side effect.

## Registration and activation metrics

**Required:** call this measure “verified mail-enabled installations,” with the
qualifier “opt-in; undercounts installs.” A pending verification is not activated.
Separate ever-activated, currently-enabled and recently-seen installations; the
same owner may operate several installations, so none is a unique-person count.
Re-registering the same installation must not increment activation repeatedly.

The existing ping is an anonymous random-install-id observation, not proof of a
person, a successful onboarding, or mail enrollment. Mail registration ties an
install to an owner address and is therefore identified data. A hash of the
owner address does not make the registration anonymous.

D3 determines whether these datasets remain separate or gain an explicitly
consented linkage. Until that decision, publish separate measures: **never add
ping count and registration count** as a distinct-install total; their overlap
is unknown. Do not extend `/api/ping`, retroactively join existing ping ids to
addresses, or treat disabling ping as consent to an identified activation ping.
No email address is necessary in an aggregate metrics response or public log.

## Data, privacy and deletion contract

Minimum logical records, independent of D4's physical storage choice:

| Record | Required data and exposure |
| --- | --- |
| Registration | Independent registration identity, assigned address, verified owner email/status, consent/disclosure version and time, installation/tunnel proof, enrollment generation, enable/revoke state; first/last service activity if D3 permits. Identified service data. |
| Thread | Install/owner generations, opaque thread identity/token verifier, outbound Message-IDs, kind, expiry/revocation. Local authoritative project/session/conversation binding; send only routing metadata the Worker needs. |
| Worker reply event | Immutable event id, registration/thread generation, trusted authentication evidence, bounded MIME/body content and digest, receipt/expiry, retry/custody state. Raw content is not ordinary application logging. |
| Local inbox/receipt | Event id, authoritative binding, laundered envelope, extraction/version status, deduplication evidence, turn/submission receipts, retry/uncertainty/terminal state. |
| Activation aggregate | Counts with precise population/window and opt-in caveat; address, content and reply tokens excluded. Cross-dataset joining is D3, not implied. |

Before enabling this feature for users, update the published privacy policy,
README “Does it phone home?” disclosure, setup/Settings copy and user guide.
The repository's `docs/play-store/PRIVACY_POLICY.md` contains an Android-specific
“collects nothing” claim. Review its actual scope and published destination;
do not reuse that claim for identified server-side agent mail or imply every
Android request uploads mail. Document Cloudflare and the configured model
provider as processors, operator access, purpose, access control, content
handling, international processing where applicable, exact retention and
deletion process. This spec does not publish or approve policy text.

Thread/reply content must never be reused as analytics, training artifacts,
project memory in another project, or promotional contacts. Ordinary memory
behavior within the resumed session must be disclosed. Raw content cannot reach
the tool-bearing session, its transcript, or a broad error log. Store credentials
in the existing vault/approved secret storage outside the repo; redact credentials,
owner addresses and tokenized Reply-To values from normal logs.

Only a human-authenticated setup/Settings action may enable registration or
change owner/credentials. Agent-facing mail operations cannot create or broaden
their own enrollment, recipient, credential or permission scope. Cloudflare
account credentials remain in the service environment, never on user installs.
D7 covers per-install trust establishment without adding an agent vault-write API.

Deletion must remove service content and routing bindings under the selected
policy, while explicitly describing minimal revocation/deduplication tombstones,
aggregate retention and provider/back-up deletion lag. Local transcripts already
created are governed by existing project deletion controls. Turning off,
unregistering and deleting are distinct controls; neither a new owner nor an
address reused by a stranger may receive the previous installation's mail.

## Open decisions for Dave

No option below is adopted by this document. Recommendations are for Dave to
accept, replace or escalate if they would change Ron's fixed requirements.

| ID | Decision and alternatives | Marlow's recommendation | Cost / consequence |
| --- | --- | --- | --- |
| D1 | Enrollment and prerequisites: A. Managed Worker registration provisions Cloudflare destination verification and proves/pairs an available tunnel, with explicit human confirmation; B. operator manually verifies/enables each install; C. require users to configure their own Cloudflare account. Within A, provision a missing compatible tunnel or leave enrollment pending until one exists. | A, initially pending when no compatible tunnel exists; offer explicit setup, not silent tunnel provisioning. | A needs service-side scoped Cloudflare management access, verification-abuse limits and a recoverable enrollment flow. B adds an operational gate; C changes the proposed service experience. Dave must confirm whether mail enrollment includes tunnel setup. |
| D2 | Address and token lifecycle: A. generated opaque install address with per-thread tokenized Reply-To, never reassigned across owners; B. user-chosen install alias with the same secret thread tokens; C. per-session addresses beneath an install identity. | A; reserve/revoke identity rather than reassign it. | A avoids alias namesquatting and identity leakage but is less memorable. B needs reservation/moderation/recovery. C expands address lifecycle. Choose token lifetime/rotation with D4; unknown/base-address mail never starts sessions. |
| D3 | Activation measurement: A. separate mail registration id/count and anonymous ping, no join; B. disclose and obtain explicit consent to join this install's ping id to its owner registration for deduplication; C. omit mail activation analytics entirely. Decide whether service opt-in includes disclosed aggregate registration counting or asks separately. | A, with registration aggregates explicitly disclosed in service consent, no separate tracking heartbeat. | A preserves ping separation but cannot produce a deduplicated combined total. B identifies the linked ping history and needs stronger privacy/disclosure changes. C loses the supplementary activation measure. |
| D4 | Durable storage and operating policy: A. dedicated D1 reply ledger with scheduled replay; B. Cloudflare Queues plus a durable correlation/receipt ledger and dead-letter handling. Local store: dedicated SQLite vs extension of a suitable existing store. Also select queue TTL, thread TTL, raw-content retention, dedupe retention, limits and deletion timelines. | A plus a dedicated local SQLite store following delegation-delivery conventions; proposed starting values: 30-day offline queue, 90-day threads, delete Worker bodies on local-custody ack, 30-day dedupe/terminal metadata, 64 KiB inbound MIME, 16 KiB extracted reply, 20 sends/install/day and a global circuit breaker. | A requires replay scheduling/indexing/leases; B adds queue limits and another custody boundary. Proposed numbers are unapproved and need a capacity/cost check, including full-queue and model-work limits. Bound local blocked/uncertain content too; do not silently drop it at expiry. |
| D5 | Continuation when the target ended: A. revive the recorded conversation only when native history/identity survives; otherwise hold for recovery; B. allow an explicitly disclosed successor with a handoff when cold resume fails; C. never revive ended sessions. Clarify proven automatic-rollover lineage and explicit close semantics. | A, preserve closure/cancellation holds, and do not equate a successor with the exact session. | A requires provider-by-provider continuation proof and durable binding retention. B changes Ron's exact-session requirement and needs his explicit acceptance through Dave. C leaves ordinary late replies unable to fulfill the requirement. |
| D6 | Sender-authentication compatibility: A. exact verified From + token + thread match + Cloudflare DMARC pass with an aligned SPF **or** DKIM result; B. additionally require aligned DKIM pass for every automatic continuation. | A, hold missing/untrusted/failed results; log parsed SPF/DKIM evidence without content. | A supports normal DMARC-valid SPF-only senders. B is stricter but rejects more legitimate owners. Neither proves personal authorship or permits forwarding-based owner substitutions. Validate actual Cloudflare metadata and mailbox-provider fixtures. |
| D7 | Worker/install authentication: A. per-install signed requests with a separate scoped service credential; B. use the existing tunnel/session authorization mechanism if it can issue a narrowly scoped mail capability. | B only after its scope, replay and rotation contract is proved; otherwise A. | B avoids a parallel identity system but dashboard credentials must not become a mail bypass. A adds credential issuance/rotation/recovery and human enrollment UX. Both require tunnel-ownership proof, receiver authentication, revocation and body-bound replay protection. No implementation may pick conditionally without Dave resolving the evidence. |
| D8 | Toolless extraction on installs without Claude: A. v1 requires the existing verified Claude transform; B. verify toolless isolation for additional installed providers through the existing transform seam before general availability. | A for a clearly labeled limited pilot; B before claiming provider-independent availability. | A adds a disclosed Claude installation/authentication/cost prerequisite and must block activation as reply-capable without it. B expands verification work; never substitute an unverified provider or a cloud fallback silently. Both need a general free-text schema compatible with existing digest callers. |

### Dave's calls (2026-11-08, pending Ron's review)

DECIDED WITHOUT RON; each is reversible until build starts.

- **D1: A.** Managed enrollment. With no tunnel, enrollment stays pending and
  offers explicit tunnel setup. Mail never provisions a tunnel silently.
- **D2: A.** Opaque install address, per-thread tokenized Reply-To, never reassigned.
- **D3: A.** Mail registration count stays separate from the anonymous ping. No join.
- **D4: A.** D1 reply ledger plus a dedicated local SQLite store. Marlow's
  starting numbers stand as provisional until a capacity check.
- **D5: A.** Revive the exact conversation only; otherwise hold. A successor
  session would change Ron's exact-session requirement, so it needs his explicit yes.
- **D6: A.** DMARC pass with aligned SPF or DKIM.
- **D7: A.** Separate per-install scoped mail credential. The dashboard
  session credential is not reused as a mail capability, which removes the
  conditional and the bypass risk.
- **D8: A.** Claude-only labelled pilot; other providers verified before GA.

**Scale ceiling for Ron:** Cloudflare limits page (updated 2026-10-30,
re-checked 2026-11-08) caps verified destination addresses at 200 per
account. Automatic signup refuses new destination creates at **190 account
addresses**, leaving ten slots of headroom; unrelated destinations count too.
Durable unobserved create claims also reserve capacity. Existing addresses are
never re-created. Signup therefore supports fewer than 190 new mail-enabled
installs when the account has other destinations. Going past that means onboarding clayrune.io as a sending domain
(paid monthly quota; owner-only still enforced in our Worker) or a limit
increase. Both spend money or change providers, so both are Ron's call. The
pilot still requires the real account-capacity check. Device-signed begin plus
CP preflight precedes provisioning; pending attempts last seven days, address
replacement waits 24 hours, and create budgets are 3/device and 20 globally per
rolling day. Cloudflare verification precedes the Clayrune code. See the
[automatic signup contract](AGENT_MAIL_AUTO_DESTINATION.md).

### Inbound hardening (Ron, 2026-11-08) - fixed requirements

1. **Owner-only inbound, enforced at the Worker edge.** Mail that fails the D6
   check (exact verified owner From, valid thread token, DMARC pass) is dropped
   at the Worker. It is never forwarded to the install, never queued in the
   local store and never shown to any model. Only a metadata-only rejection
   record (time, reason code, no body) is kept. A failed check means drop, not hold.
2. **Strip everything but plain text before custody.** The Worker removes every
   attachment, inline/embedded image, remote image reference, calendar part and
   HTML part (HTML is converted to plain text only as a fallback when there is
   no text/plain part). Stripped parts are never stored, OCR'd or passed to a
   model. Images can carry injected text, so v1 treats them as a closed door.
   Adding image or attachment support later requires its own review and must
   route through the same toolless transform. It may never reach a tool-enabled agent.
3. **Screen for commands and injection.** The toolless extraction step also
   flags instruction-like content (tool or command syntax, "ignore previous",
   requests to change permissions, run, install, send or approve). A flagged
   reply is held visibly for the owner and is not delivered as actionable text.
   An unflagged reply is still delivered as quoted, untrusted data, never as an
   instruction. A mail reply can never grant an approval, unlock a human-only
   gate or expand an agent's permissions.

Tests: a non-owner sender, a spoofed From with failed DMARC, an image-only
reply, an attachment carrying instructions, an HTML-hidden payload and a
command-shaped plain-text reply. Each must be dropped or held with no body
reaching the install's tools, transcripts or the agent context.

### Cloudflare capacity and deployment gates

Current [send bindings documentation](https://developers.cloudflare.com/email-service/configuration/send-bindings/)
supports a binding restricted to verified destinations. That still requires
per-install owner enforcement inside the Worker. Account-wide verified status
does not authorize every installation to send to every verified address.

The [routing address documentation](https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/)
requires recipient verification. The [limits documentation](https://developers.cloudflare.com/email-service/platform/limits/)
currently lists **200 destination addresses per account** and **200 routing rules
per domain**. These are verified documentation observations as of 2026-11-08,
not evidence that this account has a higher limit or any capacity allocated.
The same limits page describes free verified-destination sending; this spec
does not select or budget a broader sending product.

Use a Worker-dispatched namespace rather than assuming one routing rule per
install. The exact routing rules must coexist with current domain mail and
not capture or disrupt established non-agent addresses. A namespacing prototype,
actual account-capacity check, DKIM/SPF/DMARC setup, verification flow and
authenticated tunnel delivery must precede a pilot. Do not claim arbitrary
install scale or silently shard accounts/buy a different sending service.
At 190 account destinations (pending or verified), refuse new creates with
`destination_capacity` before code issuance; a limit increase or alternative
infrastructure returns to Dave. The
owner-only restriction remains fixed regardless of available sending products.

### Deferred question (do not resolve here)

What is the long-term effect of providing mail's tunnel delivery dependency on
the private tunnel product? Ron deferred this. No pricing, entitlement or product
strategy decision is part of this spec.

## Implementation boundaries and build gates

Unit 2's separate mail-only Ed25519 wire contract (Dave's D7 B resolution,
2026-11-09), Worker APIs, D1 schema, custody receipts and unit 4/7 handoff are in
[AGENT_MAIL_TRANSPORT.md](AGENT_MAIL_TRANSPORT.md). This is a synthetic-tested
branch candidate, not evidence of deployment or live readiness. Unit 3's later
no-HTML-fallback decision remains stricter than this draft's fallback wording.

This is a specification-only change. No implementation, API registration,
Cloudflare deployment, enrollment, test mail, privacy publication, migration,
live config change or tunnel provisioning is authorized by this document.

After D1-D8 are resolved, use separate units for enrollment, durable thread/store,
Worker transport, inbound authentication/parsing, local ingress, laundering
adapter and follow-up integration. Candidate source locations are new modules
under `mc/`, a dedicated blueprint, a dedicated setup-step module and
`workers/agent-mail/`; these are proposed locations, not existing files/APIs.
Wire them through existing registries/blueprints. Do not grow the agent-routes
monolith with queue, MIME, verification or transport implementations.

The new transport must share the existing question interface and follow-up path.
Existing SMTP/IMAP remains an operator-configured legacy channel, without copying
operator credentials into new installs. Mail registration and ping records must
remain separate. Build specs/install packages must include new modules/assets
and exclude operator data/credentials. New `mc/` modules must pass pyright basic.

Feature enablement depends on migrations for Worker and local durable stores,
recovery/cleanup at startup, a scoped authenticated receiver through the actual
tunnel, approved privacy/disclosure updates, and a visible status/recovery path.
A flag or registered route alone is not readiness. No auto-launch from migrated
legacy pending mail: pre-existing in-memory thread tokens/bindings cannot be
reconstructed by guessing a qid or searching a subject.

## Acceptance contract

Each case requires positive evidence; tests use synthetic fixtures/fake transport
unless a separate live pilot is explicitly authorized. Normal tests must not
send email, create destinations, sign in, provision tunnels or spend cloud money.

| Case | Pass condition |
| --- | --- |
| Decline / later enrollment | Fresh setup can decline and finish; no registration/address transmission occurs. Later human opt-in resumes verification; mail refusal does not change anonymous-ping choice. |
| Verification / recovery | Pending and failed verification cannot send or count as active. Retry, restart and repeated confirmation produce one enrollment; unrelated link-openers cannot pair another installation. |
| Recipient isolation | A registered install sends only to its bound verified owner; another verified destination, crafted MIME, CC/BCC, altered From/Reply-To and cross-install ids are rejected. |
| Notes and free text | Reply to a plain note and to a no-option question produces a follow-up in the exact original conversation, with fresh text represented accurately, not reduced to a yes/no decision. |
| Multiple agents / threads | Two sessions with the same persona and subject receive only their own replies. Subject changes do not matter; valid token with wrong Message-ID fails. |
| Options / late replies | Number/label/multi-question/multi-select answers retain question identity. Invalid or ambiguous choices do not become approvals. A late note/question reply cannot clear another pending question/plan gate. |
| Sender spoofing | Forged From, substring mailbox matches, sender-injected auth results, DMARC failure, mismatched owner, untrusted ingress and guessed/expired/revoked tokens cannot wake a session. Accepted DMARC/DKIM/SPF combinations follow the resolved D6 policy. |
| Laundering failure | Injection in quoted/HTML/forwarded text, malformed digest, timeout, unavailable runtime, mixed authorship and oversize content cause visible hold; no raw text or fallback fetch reaches tools/transcripts. |
| Restart durability | Restart after send, Worker receipt, local custody, extraction and before submission preserves thread correlation and reply; rejected events never mark a question answered. Corrupt stores do not reset silently. |
| Offline / replay | With the machine/tunnel down, persist the reply; after recovery within retention, replay and run the same conversation without opening the dashboard. Repeated event delivery yields one accepted follow-up. Expiry is explicit. |
| Lost acknowledgment | Crash after a possible provider write or lost HTTP ack does not blindly replay; available turn receipts reconcile, otherwise the event becomes uncertain and visible. A 2xx/queued result alone is insufficient. |
| Ended session | Normal ended/purged sessions resume with native history, identity, permissions and project/worktree intact for each supported provider. Missing history or explicit closure/deletion stays held according to D5; no cold/default-agent substitution. |
| Busy / quota / gates | Concurrent replies serialize durably. Busy sessions and provider limits hold without losing/reordering mail or switching provider. Email cannot click a human-only approval or re-arm old spawner callbacks. |
| Consent revocation | Turn off while online/offline and during replay: no new local send/turn starts after the serialized disable point. Remote revocation eventually completes; stale generations cannot execute after re-enable or owner change. |
| Metrics / privacy | Activation counts verified installations once, labels opt-in undercount, and separates enabled/recent populations. Independent ping schema/env opt-out survives; no combined distinct total without approved linkage. Logs/exports redact owner/token/content; deletion and retention match disclosure. |
| Tunnel and capacity | Real tunnel rejects unsigned/replayed/wrong-install envelopes while ordinary dashboard authentication stays intact. Arbitrary callback URLs/redirects fail. Capacity limits, namespace coexistence, full queue and a revoked destination fail visibly without data loss or relay behavior. |

A separately approved pilot must exercise owner verification, one note reply and
one free-text-question reply from a real mailbox, offline/restart replay, and a
completed resumed turn demonstrating it read the delivered text. Use the reported
test launcher for repository suites. Test every affected provider path; do not
infer readiness from one healthy HTTP endpoint or a different provider's success.

## Draft validation and handoff record

- Scope: documentation only, on the assigned branch; no merge or push.
- Sources: local files listed above, adopted owner-only memory position,
  operator-local update-ping deployment journal, and linked primary Cloudflare
  documentation read 2026-11-08. No live mail/provider/account verification.
- Work sequence: inspect existing seams; verify Cloudflare contracts; draft fixed
  requirements and open alternatives; check local references, scope and coverage;
  commit only this session's documentation; hand D1-D8 to Dave.
- Document validation: all 11 checked local source paths exist; all eight open
  decision rows and 16 acceptance cases are present; no copied operator identity,
  machine path or account id; whitespace validation passed. No runtime tests were
  run for this documentation-only change, and no behavior is claimed verified.
- Plan-tab persistence was attempted in the configured user plans directory. The
  unattended PreToolUse fence blocked writes under `.claude` as human-owned supply-chain
  state. No retry or workaround writes there were made; this record preserves
  the work sequence. Dave need not unblock that surface to review this spec.
- README, USER_GUIDE, published policy and runtime instructions are not changed
  by this draft: they must describe available behavior, which has not changed.
  Policy/disclosure publication remains a launch gate, not a completed task.
