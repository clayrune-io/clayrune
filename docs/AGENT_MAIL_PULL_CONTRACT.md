# Agent mail pull contract v1

MC-1166, Unit 00. Author: Sol_Tobin. Version **1.0**, 2026-11-11.
Status: **frozen implementation contract; entirely unbuilt unless explicitly
listed EXISTING below**. This document authorizes no implementation, migration
application, deployment, flag/configuration change, credential creation or live
mail. Source baseline: `270fb585` (`52d2fa6b` design merged).

## 1. Authority, versioning and provenance

The [pull design](AGENT_MAIL_PULL_DESIGN.md), Dave's P1-P7 decisions and R1
decision in the main checkout's operator-local journal govern this contract.
The [original spec](AGENT_MAIL_SPEC.md) still governs owner-only mail, exact
conversation continuation, human gates, toolless extraction and privacy. Its
tunnel/CP/free verified-destination transport is superseded. Where a design
paragraph calls an option a candidate, the concrete v1 value below freezes that
implementation detail; builders must not select alternatives.

| Decision | Frozen outcome |
| --- | --- |
| P1 | D1 owns all durable authority, queue, dedupe and receipts; one hibernating DO per install coordinates its socket only. |
| P2 | Signed HTTPS creates a single-use 60-second WSS ticket; reconnect/reauthenticate at 24 hours. |
| P3 | Keep root-signed service manifest, durable rollback floor and human pin review; additionally sign pull responses and service frames. |
| P4 | Content-free dedupe survives through thread expiry + 30 days; bodies do not. |
| P5 | Fresh generation fences old keys, sockets, threads and events; no legacy migration. |
| P6 | Existing global caps; pilot only, no higher quota/spend/capacity claim. |
| P7 | Local human/passcode gate plus hosted old-mailbox-link management session; ordinary mail keys cannot manage owners. |
| R1 | Lost old mailbox: no same-address recovery in pilot. New verified mailbox, unrelated signup/new address, preserved held records, no thread transfer. Recovery-code alternative B is deferred post-pilot. |

Wire version is integer `v:1`, HTTPS path prefix `/v1/pull`. This is distinct
from existing mail HTTP signature version 1 and legacy envelope versions 1/2.
Pull HTTP signature version is **2**; do not accept a legacy signature as pull
proof. New fields, enum values or alternate authentication require a separately
versioned contract; reject unknown fields/types rather than guessing. This is
not a content migration protocol.

### 1.1 Verified source inventory (repository paths)

| Surface | Provenance at baseline; required disposition |
| --- | --- |
| `GET /v1/mail/manifest` | EXISTING `workers/agent-mail/manifest-http.js`, wired by `worker.js`; retain byte contract. |
| `POST /v1/mail/send`, `/v1/mail/reconcile` | EXISTING `worker.js`, `outbound.js`, `replay.js`; legacy protocol only. NEW pull paths below replace their client use. |
| `POST /v1/mail/enrollment/{begin,confirm,complete}` | EXISTING `enrollment-http.js`; CP/destination ceremony, not pull signup. |
| `POST /v1/mail/lifecycle/{cancel,revoke,status,renew,proof-arm,activate}` | EXISTING `lifecycle.js`; legacy proof/CP protocol, not pull lifecycle. |
| Local `/api/agent-mail/enrollment` GET and POST actions | EXISTING `mc/blueprints/agent_mail_enrollment_routes.py`; passcode and human-attribution gates retained, pull actions are NEW adapters. |
| Local public push receiver | EXISTING `mc/blueprints/agent_mail_routes.py`; never used as pull ingress. |
| Manifest trust | EXISTING `mc/agent_mail_manifest_fetch.py`, `agent_mail_service_manifest.py`, `agent_mail_enrollment_manifest.py`; Worker publisher and `tools/agent-mail-manifest-sign.py` exist. Fetcher's old comment saying publisher is unbuilt is stale; publisher source exists. |
| Key/vault/fence | EXISTING `mc/agent_mail_enrollment_keys.py::IdentitySeal`, `mc/secrets_store.py::peek_passphrase_key`, `mc/agent_mail_lifecycle_fence.py`; adapt binding, do not expose seed or broaden fence authority. |
| Local custody and continuation | EXISTING `agent_mail_store`, `agent_mail_ingress_schema`, `agent_mail_wake_schema`, `agent_mail_launder`, `agent_mail_extract`, `agent_mail_wake`, `agent_mail_target`, `agent_mail_continuation`, `agent_mail_pipe` under `mc/`; preserve their safety boundaries. |
| Existing execution route | `/api/project/<project_id>/agent/followup`, called internally by EXISTING `mc/agent_mail_wake.py`; never a service-selected URL or new execution route. |
| Worker ingress and MIME gates | EXISTING `workers/agent-mail/ingress.js`, `auth.js`, `strip.js`, `canonical.js`, `mailbox.js`; reuse authentication/strip logic, change authority/queue adapter. |
| Worker storage | EXISTING `schema.sql` and migrations `0001_enrollment.sql` through `0004_destinations.sql`; do not edit history or reinterpret their rows. |
| All `/v1/pull/*`, hosted `/mail/*`, and WSS frames below | **NEW**, including HTTP signature v2, tickets, owner step-up/revert and paid sending adapter. No current handler implements them. |
| D1/local schemas in sections 8/9 | **NEW** `workers/agent-mail/migrations/0005_pull.sql` and `mc/agent_mail_pull_schema.py`. |
| Other new targets | **NEW** `owner-registry.js`, `verification-mail.js`, `signup-http.js`, `owner-management.js`, `owner-change.js`, `session-auth.js`, `pull-ledger.js`, `pull-mailbox.js`, `email-sending.js`, `pull-sweep.js` under `workers/agent-mail/`; **NEW** `agent_mail_pull_receiver.py`, `agent_mail_pull_client.py`, `agent_mail_signup.py`, `agent_mail_owner_change.py` under `mc/`. |

In pull mode, recognized legacy operational mail routes answer `410
legacy_retired`; other unknown paths answer 404. Manifest remains available.
No legacy route may write pull tables or revive a generation. There is no CP,
tunnel, device-enrollment, Access or verified-destination dependency. The email
handler remains the sole SMTP ingress; there is no public HTTP enqueue route.

## 2. Shared types, bounds and transport

All object fields shown are required unless explicitly `?`; `?` means omission,
not null. Null is permitted only as stated. Exact field sets apply recursively.
UTF-8 only, no BOM, duplicate JSON keys, NaN, infinities, fractional integers,
negative zero, lone surrogates or trailing bytes. Nesting depth <= 8; arrays <=
100 unless a smaller bound is stated. Integer booleans are invalid JSON booleans.

| Type | Exact definition |
| --- | --- |
| `N` | JSON integer 0..9,007,199,254,740,991; arithmetic must remain exact in JS/SQLite. |
| `G` | JSON integer 1..999,999,999,999,999; monotonically increasing generation or epoch, refuse overflow. |
| `T` | `N` UTC Unix milliseconds; all wire and NEW schema times are milliseconds. Existing local REAL timestamps are seconds; adapter converts explicitly. |
| `I` | 32 lowercase hex chars, random 128-bit id. Install, request, operation, ticket-row, connection, lease and receipt ids use this. |
| `ID` | ASCII `[A-Za-z0-9_-]{1,128}`; thread, intent and key ids. New generated ids use `I`. |
| `H` | 64 lowercase hex chars, SHA-256. Digest is over exact specified bytes, never an inferred JSON reserialization. |
| `S` | 64 lowercase hex chars representing 32 random bytes; secret/fence/link/ticket. Store SHA-256 of its 64 ASCII hex bytes, not plaintext; matches EXISTING fence verifier encoding. |
| `B32`, `B64` | Canonical unpadded base64url for exactly 32 public-key / 64 signature bytes; reject alternate encodings. |
| `C` | Closed lowercase reason code from section 7, never remote free text. |
| `origin` | Exact lowercase HTTPS DNS origin accepted by EXISTING `valid_service_origin`; no port/path/query/fragment/userinfo, redirect, IP literal or runtime-selected host. WSS changes scheme only. |
| `mailbox` | One addr-spec accepted by EXISTING `mailbox.js::parseAddrSpec`, <=254 UTF-8 bytes. Domain lowercase; preserve local-part case/dots/plus. No provider equivalence guesses. |
| `MID` | EXISTING strict incoming Message-ID grammar from `mc/agent_mail_envelope.py`, brackets removed, ASCII <=8,192 bytes; no CR/LF/controls. |
| `reason` | `C`, or empty string only where a state has no reason. |

Canonical array bytes `A(...)` mean compact JSON array of **ASCII strings only**,
commas and no whitespace, no escaping alternatives; integer inputs are minimal
decimal strings. These bytes match Python `json.dumps(...,separators=(',',':'))`
and JS `JSON.stringify` for this restricted alphabet. Payload objects need not
have a canonical key order: signatures hash their exact transmitted bytes.

HTTP is same-origin HTTPS, TLS verified, no redirects of any status. No query
strings (including bare `?`) on machine endpoints. No compression. JSON request
cap 128 KiB on `/send`; 8 KiB elsewhere; response cap 32 KiB (manifest also
32 KiB, signed manifest array <=16 KiB). Header cap 8 KiB. Read timeout 10s
(manifest 5s). Set `Cache-Control:no-store`, `X-Content-Type-Options:nosniff`.
Logs/status never contain keys, link/ticket/fence secrets, bodies, raw MIME,
full tokenized reply addresses or provider exception strings.

### 2.1 Pilot limits (not tunable by a request)

| Resource | v1 ceiling / timing |
| --- | --- |
| Normal sends | 20/install/UTC day; 1,000 globally/UTC day. |
| Accepted inbound | 200/install/UTC day; 10,000 globally/UTC day; dedupe retries consume no second acceptance. |
| Cloud pending/held/offered bodies | 100/install, 10,000 global; additionally 2 MiB/install and 200 MiB global decoded stripped text. Both count and byte reservations are atomic. |
| Local active inbox | EXISTING 600/install, 64 KiB body custody ceiling, 16 KiB extracted reply, 8 work attempts. Pull stripped text is at most 16 KiB. |
| MIME / outgoing body / subject | MIME <=64 KiB; stripped inbound/outgoing body <=16 KiB UTF-8; outgoing subject <=200 bytes, nonblank, no controls. |
| Queue / thread / ordinary terminal detail | Up to 30 days absolute from receive, bounded by remaining thread validity / 90 days from send reservation / 30 days after terminal transition. |
| Dedupe | Through thread expiry +30 days, including terminal/ack conflicts; never extend body lifetime. |
| Submitted completion | EXISTING one-day completion timeout; submitted alone does not purge local body. |
| Verification email | 3/install/UTC day AND 3/target mailbox/UTC day, all signup/step-up/recovery/new-owner verification combined; no refund on uncertain send. |
| Completed owner changes | <=1/trailing 7 days AND <=5/trailing 365 days/install, including across disable/key recovery; revert is not charged as a new owner change. |
| Owner installations | <=3 active/verified-or-reserved installations per mailbox; pending reservations count for admission but not activation analytics. |
| Address quarantine | 90 days after release/rename/deletion, never inherit old threads. No custom alias/rename endpoint in v1. |
| Tickets / replay | Ticket 60s single-use; signed-request timestamp skew <=300s; nonce retained until timestamp+300,001ms. |
| Socket | One current connection/install; 24h reauth; first resume within 10s; offer lease 60s. |
| Socket bounds | <=160 KiB/frame encoded; <=112 KiB decoded payload; <=10 outstanding events and <=512 KiB encoded outstanding bytes. No fragmentation accumulation beyond frame/message cap. |
| Authenticated machine HTTP | 200/install/UTC day for send/status/reconcile-status; separate 500/install/day for signup/activation/ticket/fence/key-management operations. Tickets also <=10/install/minute. |
| Unauthenticated traffic | Existing shared 10,000/minute global refusal budget, plus NEW 30/minute/trusted edge source; valid signatures do not charge a forged claimed install. |
| Socket traffic | 120 client frames/minute/current connection; 60 resumes/minute; oversized/invalid frames close immediately. |
| Hosted session | Mailbox links 30 minutes; old-owner revert link 7 days; management cookie 10 minutes, no refresh; browser CSRF state 10 minutes. |
| Sweep / reconnect | One-minute scheduled sweep, <=100 rows per batch; persisted keyset cursor, continue fair passes. Full-jitter exponential 1s..300s; reset only after 60s stable connection; honor longer Retry-After. |

Fixed-template verification/notice mail also consumes the global 1,000/day
outbound reservation, so control mail cannot bypass P6. Normal per-install 20/day
is separate from verification budgets. One notice per committed change and one
per affected recipient on revert, stable idempotency key; retries never allocate a new notice.
Provider uncertainty stays held. Provider quotas/suppression can lower effective
capacity; no automatic cap increase. Frame/HTTP failures and exhausted budgets
remain visible as bounded counters/reasons. Enforce limits before expensive work
where trustworthy scope exists; never allocate unbounded rows for invalid input.

## 3. Cryptographic and authority contract

### 3.1 NEW signed HTTPS v2

Required request headers (all exact ASCII strings): `x-mail-version:2`,
`x-mail-direction:install-to-pull`, `x-mail-key-id:ID`, `x-mail-install-id:I`,
`x-mail-generation:G`, `x-mail-owner-generation:G`, `x-mail-timestamp:T`,
`x-mail-nonce:I`, `x-mail-digest:H`, `x-mail-signature:B64`.
Signature is Ed25519 over:

```text
A("clayrune-agent-mail-pull-http","1","install-to-pull",origin,
  "POST",path,key_id,install_id,generation,owner_generation,timestamp,nonce,
  SHA256(raw_body))
```

Generation in a start/status flow is the candidate generation (1 for fresh
signup); status binds the candidate key registered in that pending flow, not an
unproven arbitrary key. For fresh start only, validate against `public_key` in
its exact body; it proves possession, not a verified owner. Key-loss start is
also candidate-key-signed and gives no rights to the existing registration.
Claim nonce only after valid signature; validate body shape, exact scope and
freshness again inside the mutation transaction. A replay returns 409
`nonce_replayed`; retry the same operation/body with a fresh timestamp/nonce.
Idempotency is operation id + body digest, separate from nonce.

Service JSON responses to valid authenticated requests carry the same headers
with `direction:pull-to-install`, service key id, echoed request nonce and scope.
Sign:

```text
A("clayrune-agent-mail-pull-http","1","pull-to-install",origin,
  "POST",path,service_key_id,install_id,request_generation,request_owner_generation,
  timestamp,request_nonce,decimal_http_status,SHA256(raw_response_body))
```

Response common body is `{v:1,code:C,request_id:I}` plus the exact route result
fields below. `request_id` is the operation id, or request nonce on read/status
operations. Verify service pin, digest, freshness, HTTP status and echoed scope
before treating any response as evidence. A successful generation mutation is
signed in the **request** scope and returns new generation fields in its body.
Unauthenticated refusals are unsigned `{v:1,code:C}` with no private state.
Unsigned/malformed success or a mismatch is `response_unverified`, never custody,
enablement, revoke or owner-change evidence. Signed errors establish only their
stated failure. TLS alone does not replace manifest proof.

### 3.2 Manifest and key custody (EXISTING, retained)

`GET /v1/mail/manifest` returns exactly `{manifest:[11 items],signature:B64}`.
Array: `A("clayrune-mail-service-manifest","1",origin,sequence,issued_ms,
expires_ms,key_id,public_key,activate_ms,retire_ms,sorted_retired_key_ids)` where
the final item is the EXISTING array of sorted unique ASCII ids (the one exception
to scalar-only `A`). Preserve the verifier's exact positive-decimal, validity,
key-retirement and 16-KiB checks, root verification and persistent CAS floor.
Do not automatically repin an unexpected service key or reset a corrupt floor.
No service root private key in Worker/client; offline signer only.

IdentitySeal retains its EXISTING AES-GCM seed+flow-secret format/AAD binding to
flow/install/generation/key-id. Store only a seal reference in NEW local tables;
new generation requires a seal with its new AAD, never relabel ciphertext.
Human generation/unlock remains local; no new vault write API or fallback master
key. On vault lock stop socket/signing, drop in-memory signing material and hold
cloud mail. Fence secrets remain cancellation-only and usable with a locked vault.
Cancellation authorization is a request-body `fence_token:S`; its value is never
returned by status or service responses. Fresh start supplies only its digest.

### 3.3 Tickets, connection epochs and service frames

`POST /v1/pull/ticket` requires active verified current install-key proof. Mint
32 random bytes, store their hash with install/g/og/key-id and expiry. Return
the secret once in a signed response. Exception to ordinary idempotent result
replay: repeated ticket op_id returns 409 `operation_conflict`, never returns
or persists the plaintext ticket again. Lost response uses a new op_id/ticket;
the unused ticket expires. There is no cookie/dashboard token auth.
Upgrade NEW `GET /v1/pull/socket` with `Authorization:MailTicket <S>` and
`Sec-WebSocket-Protocol:clayrune-mail-pull.v1`; no URL/query secret. Reject
Origin header from a browser; this is a server client, not JavaScript in a tab.
Upgrade request has no body. Admission consumes the ticket, rechecks active
generation, increments D1 connection epoch and stores connection id in **one
atomic mutation**. Missing/expired/consumed/foreign ticket ->401
`ticket_invalid`; wrong protocol ->426 `version_unsupported`.

Only 101 after commit; a crash before 101 burns the ticket/epoch harmlessly,
client obtains another ticket. A new epoch fences prior socket operations
immediately, then closes the prior socket 4001. Ordinary reconnect does not
increase registration generation or invalidate durable custody receipts. Both
peers use D1 current epoch checks on offer/ack, including after DO hibernation.
Socket attachments contain only `{v:1,install_id,generation,owner_generation,
key_id,epoch,connection_id,authenticated_ms,reauth_ms}`. They are hints restored
by NEW `pull-mailbox.js`, never authority or queue storage. Use hibernating
`acceptWebSocket`; no per-install poll/timer/long-lived fetch prevents sleep.

Every service frame has exactly:
`{v:1,type:FT,install_id:I,generation:G,owner_generation:G,epoch:G,frame_id:I,
issued_ms:T,key_id:ID,payload:base64url,payload_digest:H,signature:B64}`.
`payload` is base64url of exact UTF-8 JSON object bytes; bound decoded/encoded
length before allocation. Verify signature before parsing data:

```text
A("clayrune-agent-mail-pull-frame","1",origin,type,service_key_id,
  install_id,generation,owner_generation,epoch,frame_id,issued_ms,payload_digest)
```

Accept only current scope/epoch, <=300s issuance skew, unexpired service pin and
valid payload hash. Deduplicate recent frame ids during the current connection;
event/ack ids supply durable replay protection across connections. `hello` is
the signed upgrade/epoch proof and must arrive before the client sends resume.
Client frames need no individual signature inside authenticated TLS/WSS; current
ticket epoch plus durable event/lease checks scope them. They have exact fields
`{v:1,type:CT,install_id:I,generation:G,owner_generation:G,epoch:G,request_id:I,
payload:object}`. No frame can send mail, enable mail, change owner or pick a
project/session/provider/permission. Unknown frame type/field ->4002 close.

## 4. Complete NEW service HTTP route registry

Unless stated, all are POST, JSON, auth per section 3, common signed response
prefix from 3.1. Arrays/objects below are closed shapes, not extensible bags.
`generation`/`owner_generation` body fields appear only where explicitly listed;
otherwise signed headers supply scope. `op_id:I` is stable per durable intent.
All mutations are idempotent: same op+digest returns original committed result,
different digest ->409 `operation_conflict`. Status is read-only except nonce
and rate accounting; no implicit send, consent, activation or key recovery.

| Path | Auth and exact request body | 200 result fields after common prefix |
| --- | --- | --- |
| `/v1/pull/signup/start` | Candidate-key proof; `{op_id:I,flow_id:I,public_key:B32,key_id:ID,owner:mailbox,flow_secret_hash:H,fence_hash:H,consent_version:ID,manifest_sequence:G,manifest_digest:H}`. Header install id is newly generated locally; g=og=1. | `{flow_id:I,install_id:I,generation:G,owner_generation:G,address:mailbox,state:"pending",expires_ms:T,verification_state:VS}` |
| `/v1/pull/signup/status` | Pending candidate key + `{flow_id:I,flow_secret:S}`; service compares flow-secret hash. | `{flow_id:I,install_id:I,generation:G,owner_generation:G,address:mailbox,state:FS,expires_ms:T,verification_state:VS}` |
| `/v1/pull/activate` | Verified current key, local human enable action; `{op_id:I,flow_id:I,flow_secret:S,consent_version:ID}`. No receive proof/CP grant required. | `{install_id:I,generation:G,owner_generation:G,state:"active",active_since_ms:T}` |
| `/v1/pull/status` | Current key even if disabled/frozen; `{}`. | `Status` below. |
| `/v1/pull/ticket` | Active current key; `{op_id:I}`. | `{ticket:S,expires_ms:T,protocol:"clayrune-mail-pull.v1"}` |
| `/v1/pull/send` | Active key; `{op_id:I,intent_id:ID,thread_id:ID,reply_token:32hex,subject:string,body:string,kind:"note"|"question"}`. Bounds in 2.1. | `{intent_id:ID,thread_id:ID,message_id:MID,state:"accepted",provider_id:string}`; provider id ASCII <=256. |
| `/v1/pull/send/status` | Current key; `{intent_id:ID,intent_generation:G}`. Can read old metadata only for same install AND current owner_generation, no body. | `{intent_id:ID,thread_id:ID,message_id:MID,state:IS,provider_id:string}`; empty strings only before allocation/no positive provider evidence. |
| `/v1/pull/fence` | Fence-only proof, not key headers: `{op_id:I,install_id:I,generation:G,flow_id:I,fence_token:S,mode:"cancel"|"revoke",nonce:I,timestamp:T}`. | `{install_id:I,generation:G,state:"fenced"|"already_fenced"|"already_superseded",fence_receipt:I}` |
| `/v1/pull/management/start` | Current or candidate recovery key; `{op_id:I,flow_id:I,purpose:"owner_change"|"key_recovery"|"resume"|"rotate"|"delete",candidate_key_id:ID,candidate_public_key:B32,candidate_signature:B64,flow_secret_hash:H,fence_hash:H}`. Must match current owner; no requested new mailbox here. | `{flow_id:I,state:"pending",expires_ms:T,verification_state:VS}` |
| `/v1/pull/management/status` | Bound starting key; `{flow_id:I,flow_secret:S}`. | `{flow_id:I,state:MS,expires_ms:T,result:MR|null}`; no cookies/capabilities/tokens. |
| `/v1/pull/key/rotate` | Active current-key proof; local human action AND old-mailbox management approval already committed; `{op_id:I,flow_id:I,new_key_id:ID,new_public_key:B32,new_signature:B64,fence_hash:H}`. | `{install_id:I,generation:G,owner_generation:G,key_id:ID,state:"verified"}`. Rotation invalidates active consent for final re-enable. |

`VS = queued|accepted|uncertain|failed`; `FS = pending|verified|active|expired|
cancelled`; `MS = pending|approved|completed|expired|cancelled|reverted`.
`IS = reserved|sending|accepted|uncertain|failed|revoked`.
`MR` exact fields `{install_id:I,generation:G,owner_generation:G,state:
"verified"|"frozen"|"active"|"deleted",change_id:I|null}`. `approved` never exports a
management token to the machine; owner/key mutation requires the hosted session
or a specific durable approval bound to that flow and exact candidate fields.

`Status` fields: `{install_id:I,generation:G,owner_generation:G,state:RS,
address:mailbox|null,owner:mailbox|null,key_id:ID,queue_count:N,queue_bytes:N,
oldest_received_ms:T|null,oldest_expires_ms:T|null,invalid_message_id_holds:N,current_epoch:N,
owner_change:OC|null,last_notice_state:VS|null,reason:reason}`.
`OC={change_id:I,state:"pending"|"completed"|"reverted"|"expired",
created_ms:T,revert_until_ms:T|null}`; no proposed mailbox disclosure outside
its human flow. `RS=pending|verified|active|disabled|frozen|deleted`.

Fence responses use request nonce/install/generation, owner-generation header
value `0` reserved **only for fence responses**, under the service signature
transcript. Fence request has no ordinary signature headers. Failure is unsigned
401. Match fence receipt scope before marking remote revoke complete. The same
fence capability can retry an old generation but can never fence a newer one.
Fence timestamps obey the same 300s skew. Its operation digest is the hash of
`A("clayrune-mail-pull-fence","1",install_id,generation,flow_id,mode,op_id,
SHA256(ASCII(fence_token)))`, excluding retry nonce/time. The nonce has the
normal durable replay claim; an already committed identical operation may return
its original receipt even on duplicate nonce. A fresh retry uses fresh nonce/time.
Cancel before signup allocation creates a durable `(install,flow)` fence tombstone;
subsequent start/confirm for that flow fails. Duplicate fence has the original
receipt, no second revocation. Fence tokens confer no read/send/owner/address-
release rights. Explicit address release/deletion requires the hosted human
management flow, never the cancellation capability.

Candidate proof for management/rotation is Ed25519 over
`A("clayrune-mail-pull-candidate","1",origin,purpose,flow_id,install_id,
expected_generation,expected_owner_generation,candidate_key_id,
candidate_public_key,fence_hash,flow_secret_hash)`; purpose is `owner_change`,
`key_recovery`, `resume`, `rotate` or `delete`.
For owner change/resume/delete the candidate equals current key;
key recovery uses the new key and old-mailbox proof supplies ownership. Its
operation cannot modify old generation before that proof and explicit POST.

`/send` is not idempotent based on text: `(install,g,intent_id)` is the authority.
Hash the exact body for conflict checks. Store outgoing body only locally in
EXISTING sealed outbox; service reserves thread/internal intent key before provider work,
keeps no outgoing body after the request. Lost response after possible provider
acceptance ->`uncertain`; `/send/status` never resubmits. Same intent retry is a
lookup, never another provider call. Service constructs To from current owner,
From from reviewed service sender and structured `replyTo` from address+32hex
reply token. The derived value
`SHA256(UTF8(install_id+":"+generation+":"+intent_id))+"@"+mail_domain`
remains an internal intent/dedupe key (the historical `message_id` field), never
an outbound header or evidence of the delivered RFC Message-ID. Paid structured
Workers `binding.send()` assigns Message-ID; never set `headers.Message-ID`,
put Reply-To in `headers`, or fall back to raw MIME/legacy `EmailMessage`.
No raw MIME/header, `to`, cc/bcc, callback, host or target-selection field.
Keep token only in request/local sealed payload; D1 stores its SHA-256 verifier.
Return the provider's `messageId` unchanged as opaque `provider_id` (nonblank
printable ASCII, <=256 bytes), never assume it equals an RFC Message-ID.
Successful provider id/acceptance means transport acceptance, not inbox delivery.
One provider call, bounded to a 10s wait, with no adapter retry. The structured
binding has no documented abort parameter: timeout cannot establish cancellation.
Known rate/daily quota refusal is retryable `provider_quota`; suppression is
permanent `provider_suppressed`; payload/header/sender/account/recipient policy
refusal is permanent `provider_unavailable`. These are internal adapter outcomes,
not new wire states/codes. Unknown/internal/delivery/transport errors, timeout
after call start or missing/invalid provider id mean `uncertain`, never a second
call. Use thrown `Error.code`, never parse or expose provider exception text.
This amendment follows Dave's 2026-11-12 Message-ID decision and the
[Workers API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/)
and [header restrictions](https://developers.cloudflare.com/email-service/reference/headers/).

### 4.1 Hosted human-only routes (all NEW)

Browser routes share the configured service origin. HTML <=32 KiB, request form
body <=4 KiB, no scripts/third-party assets/analytics, `Referrer-Policy:no-referrer`,
frame denial and restrictive CSP. GET has no registration mutation, token consume
or mail send. Link format is `/mail/link?token=<S>` or
`/mail/revert?token=<S>`; only this exact query is allowed. Redact query from all
access logs; no external redirects. Browser receives fixed text naming purpose,
masked owner, install fingerprint and candidate-key fingerprint, never agent text.
GET creates bounded CSRF browser state/cookie only; page requires explicit POST.

Cookie `__Secure-clayrune-mail` is Secure/HttpOnly/SameSite=Strict, Path=/mail,
no Domain, 10-minute absolute expiry. Session random id 32 bytes; only hash in D1.
CSRF token `S`, hash in that session; POST requires exact Origin plus cookie and
form token. No CORS, token export, URL return-to, agent-facing management tool or
general bearer endpoint. Link token alone cannot submit without the browser
CSRF step; forwarded link possession is not treated as proof of local human
authorization beyond the bound local start flow. Local passcode remains required.

| Method/path | Exact input | Result / effect |
| --- | --- | --- |
| `GET /mail/link?token=S` | Verification or old-owner step-up token. | 200 inert confirmation form + CSRF cookie; 410 if expired; unknown token 404. |
| `POST /mail/link/confirm` | form `{token:S,csrf:S}` + cookie/Origin. | 200 fixed receipt. Signup/new-owner verification consumes bound token; management link promotes that browser session to the exact flow purpose. Duplicate completed confirmation shows same receipt, no new mail/activation. |
| `GET /mail/manage` | Management cookie only. | 200 form for its exact owner-change/key-recovery/resume scope; 401 if absent/expired. |
| `POST /mail/owner-change` | `{csrf:S,op_id:I,new_owner:mailbox}` + approved owner-change session. | 200 fixed pending receipt; reserve new-owner quota and send its fixed verification link. Old owner remains current until new confirmation. |
| `POST /mail/change/cancel` | `{csrf:S,change_id:I}` + matching management session. | 200 cancelled if still pending; committed switch ->409, use revert. No agent route. |
| `POST /mail/key-recovery` | `{csrf:S,op_id:I}` + approved recovery session bound to candidate key. | 200 commits new key/generation, fences old, state verified; machine learns via management/status then human enables. |
| `POST /mail/resume` | `{csrf:S,op_id:I}` + old/current owner's approved resume session. | 200 clears a revert freeze into verified with fresh generation; explicit local enable still required. |
| `POST /mail/delete` | `{csrf:S,op_id:I}` + current owner's approved delete session and explicit deletion confirmation form. | 200 logically deletes registration/private content and releases address into quarantine; no recovery or thread transfer. |
| `GET /mail/revert?token=S` | Old-owner 7-day revert token. | 200 inert freeze-and-revert form + CSRF; no effect from a scanner GET. |
| `POST /mail/revert` | `{token:S,csrf:S}` + bound cookie/Origin. | 200 atomically freezes and reverts exact change; duplicate same receipt. Expired ->410; superseded scope ->409. |
| `POST /mail/logout` | `{csrf:S}` + cookie/Origin. | 200 invalidates this browser session and clears cookie; no registration effects. |

Hosted POST errors are fixed HTML with the same closed codes/statuses as section
7, never reflected mailbox/token/exception. Hosting verification while the local
install is offline works; status/reconnect learns the durable result later.

Token `binding_digest` is
`SHA256(A("clayrune-mail-pull-link","1",purpose,flow_id,change_id_or_empty,
install_id,token_generation,token_owner_generation,target_owner_hash,
candidate_key_id,candidate_public_key,expires_ms))`. Recompute against immutable
flow/change values at consume. Signup/new-owner confirmations consume only their
own flow/change. Revert validates the completed change and still-current switched
owner generation; non-owner key rotation/revoke does not erase its seven-day
authority. Cookie/CSRF use the same ASCII-secret hashing rule. Browser result
replays contain no new token or side effect.

### 4.2 SMTP ingress (EXISTING entry, NEW registry/custody adapter)

Preserve ordered headers and the EXISTING authentication/strip boundary:
exact canonical verified From + secret 32hex reply-token verifier + trusted
Cloudflare DMARC pass and aligned SPF or DKIM. In-Reply-To/References are
non-authoritative threading hints; an outbound MID match is not a possession
proof requirement. Provider id is opaque, not the RFC Message-ID. **Implementation
gap:** `auth.js` still requires the old MID match; removing that gate and testing
the revised ingress proof belongs to separate unit 11b, not the unit 11 adapter.
Sender-submitted Authentication-Results, display names,
Reply-To, subject and envelope claims alone prove nothing. Unknown recipient,
missing/untrusted/failed sender proof, stale generation, loops, bounces and
auto-replies are dropped at edge with bounded content-free rejection metadata,
never queued for any model. No fetching URLs, forward(), attachments, images,
OCR, raw MIME retention or HTML-only fallback; retain the stricter EXISTING
stripper decision, not the original draft's superseded HTML fallback.

Authenticated, correlated but missing/ambiguous incoming Message-ID creates only
a content-free `pull_ingress_holds` row (unique SHA-256 of bounded raw bytes),
reason `invalid_message_id`, 30-day absolute expiry. No text/MIME retained, no
event frame, no local custody ack or follow-up. Expose count/reason in status.
This cannot shadow a later valid MID. It consumes one queue item and accepted
ingress capacity once, zero body bytes; duplicate hold consumes neither again.
Queue exhaustion/D1 failure never reports SMTP durable success. A single valid
MID uses normal event dedupe; raw bytes are discarded after parsing/hashing.
Image-only/stripped empty text with a valid MID may use the content-free held E
descriptor; it is not actionable text. Commands/injection flagged by the EXISTING
toolless extractor stay visibly held; even unflagged text remains quoted untrusted
data and cannot approve a product gate.

SMTP handler returns success only after its queue/hold commit, not after merely
launching an asynchronous notification. Same-auth/body SMTP redelivery resolves
the original event's auth/times; changed signed event fields on WSS are a conflict,
not permission to replace the durable original. Paid Email Sending adapter is
NEW; maintain fixed service From, owner-only recipient and structured tokenized
`replyTo`; Cloudflare assigns Message-ID. Never send the derived internal intent
key as a header. Actual Cloudflare auth provenance and provider support
remain integration/launch gates, never inferred from synthetic input.

## 5. Complete NEW WebSocket frame registry

Frame envelopes are defined in 3.3. Fields below are the exact decoded payloads.
`hello`, `event`, `ack_committed`, `status`, `hold`, `reauth_required` and `error`
are service signed. No unsolicited arbitrary target/command frame exists.

| Direction/type | Payload | Semantics |
| --- | --- | --- |
| S->C `hello` | `{connection_id:I,authenticated_ms:T,reauth_ms:T,max_events:10,max_bytes:524288}` | First frame; verified service proof of current epoch. |
| C->S `resume` | `{after_seq:N,limit:N,available_bytes:N}` | limit 1..10; bytes 0..524288; paging hint only, not acknowledgment. after_seq may be 0. |
| S->C `event` | `{event:E,lease_id:I,lease_until_ms:T}` | Offer current event with 60s lease. |
| C->S `custody_ack` | `{event_id:H,event_digest:H,body_digest:H,lease_id:I,local_receipt_id:I}` | Commit durable local custody first. Ack one exact event, never a seq/high-water range. |
| S->C `ack_committed` | `{request_id:I,event_id:H,event_digest:H,body_digest:H,local_receipt_id:I,receipt_id:I,committed_ms:T}` | Only cloud-body deletion proof. Lost frame causes receipt reconciliation/redelivery, no new follow-up. |
| C->S `hold` | `{event_id:H,event_digest:H,lease_id:I,reason:C,retry_after_ms:T}` | No custody. Keep service body; retry time capped at event expiry, minimum now+60s. Allowed reasons below. |
| S->C `hold` | `{request_id:I,event_id:H,reason:C,retry_after_ms:T}` | Confirms scheduling hold only, never body deletion. |
| S->C `status` | `{request_id:I,next_seq:N,scan_complete:boolean,pending_count:N,oldest_expires_ms:T|null}` | End of bounded resume page; scan_complete is a snapshot, not delivery/turn completion. |
| S->C `reauth_required` | `{reason:"reauth_due"|"service_key_changed",close_at_ms:T}` | Stop offers; client closes/reconnects with new ticket before deadline. |
| S->C `error` | `{request_id:I,code:C,retry_after_ms:T|null}` | One operation refused; 4001/4002/4003 scope/auth errors close. |

`E` exact fields: `{v:1,event_id:H,install_id:I,generation:G,owner_generation:G,
thread_id:ID,seq:N,incoming_message_id:MID,body_digest:H,event_digest:H,
received_ms:T,expires_ms:T,thread_expires_ms:T,auth:Auth,kind:"text"|"held",
text:string|null,reason:reason}`. `seq>=1`. Text kind: nonblank/no NUL text
<=16 KiB, reason empty. Held kind: text=null, reason=`empty_body`, body digest
SHA-256(empty bytes); stripping/authentication failures are not executable held
envelopes. Auth fields exactly `{ingress:"cloudflare",spf:string,dkim:string,
dmarc:string,aligned:"spf"|"dkim"|"spf+dkim"}`; retain EXISTING envelope
result grammar (`pass|fail|softfail|neutral|none|temperror|permerror|missing|other`,
plus-separated), DMARC pass-only, matching aligned pass evidence; <=256 ASCII
bytes/value. No raw authentication headers, sender address or MIME.

Event identity:
`event_id=SHA256(A(install_id,generation,thread_id,incoming_message_id))` where
generation is its decimal ASCII string. Owner generation is separately bound by
the immutable event digest. `body_digest=SHA256(UTF8(text))` (empty for held).
`event_digest=SHA256(A("clayrune-mail-pull-event","1",install_id,generation,
owner_generation,event_id,thread_id,seq,incoming_message_id,body_digest,
received_ms,expires_ms,thread_expires_ms,kind,reason,auth.ingress,auth.spf,
auth.dkim,auth.dmarc,auth.aligned))`. This immutable transcript excludes offer
lease, connection epoch and frame issuance so retries match. Changed canonical
content/auth/timing under the same event identity is a conflict; no overwrite.
Incoming SMTP duplicate compares body digest against existing dedupe first and
returns the original event/metadata, never recalculates new receive/expiry time.

### 5.1 Paging, ordering, lease and ack

Connect/reconnect begins with pending local ack replay, then resume after_seq=0.
`after_seq` scans a bounded page in ascending seq under current install/g/og; it
never excludes earlier unacked events permanently. At scan_complete the next
drain pass resets to zero. A held/not-yet-due head may be skipped for offering;
its seq remains visible for later passes. Active offers are bounded by both
window limits. Notification after enqueue prompts a new bounded pending scan;
no endless idle DB polling. Sweep retries missed notification intents.

Local receiver accepts only a known immutable thread with exact local
project/session/native/permissions mapping and active consent. Preserve service
seq as the ordering key: within a conversation, do not offer a later known
conversation event before an earlier offer has durable custody or terminal
disposition. Worker need not learn the project/session: the client stages offers
in service-seq order and stores all same-conversation work before submitting;
local dispatch orders by service seq, not socket arrival. Held cloud unknown
threads block only their own thread; local immutable conversation mapping orders
other threads sharing a conversation. Resume pages are processed sequentially;
do not dispatch until the current initial scan is complete, avoiding a known
earlier outstanding event being overtaken after reconnect.

Lease expiry permits a new offer with new lease id; it does not expire event or
discard custody. A local committed ack remains valid after its original offer
lease expires: exact stored `event_digest`, local receipt and generation suffice,
even after reconnect under a new epoch. Supplied lease id must match an actual
offer history row for that event/install/g; current-epoch socket is still required.
No valid offer history ->`lease_unknown`. An old socket cannot ack after epoch
replacement. Keep offer history to event's dedupe deadline, content-free.

Ack transaction checks current active g/og (expired/revoked events refuse), exact
event/digests/offer history, then inserts a receipt, changes event to
`local_received`, clears body/lease and notification in the same commit. Repeated
matching local receipt returns original cloud receipt. Conflicting local
receipt/digest ->409 `ack_conflict`. No bulk ack or inferential seq deletion.
An event already acknowledged remains reconcilable from content-free dedupe plus
receipt after its detailed event row is purged. Ack retries may settle that
receipt after queue expiry only when custody had already committed before expiry;
a first ack after expiry fails. A matching previous receipt never reopens work.

The local custody transaction commits EXISTING `events` body/content-free hold,
verified transport/thread mapping, NEW immutable receipt and ack outbox together.
Only then send ack. Unknown thread, queue full, failed commit/fsync, corruption,
expired event or local disabled state ->no custody ack. `hold` reason subset:
`unknown_thread|queue_full|storage_unavailable|local_disabled|vault_locked|
conversation_busy`; these schedule retry, never extend absolute expiry.
Post-custody extraction/provider failure is local held work, no cloud redelivery
request. Loss of local disk after ack is a backup/recovery matter; do not silently
recreate the store or promise cloud recovery of deleted bodies.

Protocol ping/pong only, no JSON heartbeat. Client may send control ping every
60s and close after 30s without pong; use hibernation-compatible handling.
Reauth closes at 24h; on service-key retirement/pin expiry stop receiving and
require reviewed valid pin before a new connection. Close codes: 1000 clean,
1009 message limit, 1013 retryable capacity (jitter), 4001 connection superseded,
4002 protocol/schema failure, 4003 auth/generation/frozen/revoked, 4004 reauth due.
Reason text is just the corresponding closed code, <=40 ASCII bytes. No content.

## 6. Lifecycle and atomic transitions

All authority-changing transactions compare expected g/og and relevant flow
state **inside** the same D1 mutation that commits effects. JS prechecks or DO
memory locks are insufficient. Use transactional D1 batch with guarded SQL or
single-statement triggers; failed condition rolls back every effect, including
quota reservations, notice outbox and token consume. Zero-row guarded update
must not be mistaken for success. No network/provider call inside a DB transaction.

### 6.1 Signup and enable

1. Typed human opt-in passes EXISTING local passcode and human-attribution gates;
   review disclosures including identity association, queue expiry, backups,
   vault availability and pilot caps. Decline/status transmits no owner mailbox.
2. Generate random separate mail install/key identity, sealed seed/flow secret,
   cancellation-only fence and durable flow before network. Reserve an opaque
   address `m` +29 lowercase hex characters at service; +32hex reply token fits
   <=63-character local part. Client never chooses alias or someone else's id.
3. Start atomically reserves canonical owner slot and verification budgets,
   address, pending install, flow and fixed-template outbox. Pending expiry 30m;
   GET/status/resend cannot extend it or enqueue another verification.
4. Explicit hosted confirmation consumes exact single-use request token;
   verifies owner and candidate key binding, commits registration `verified`.
   Initial g=og=1. Confirmation alone cannot enable local execution. Address is
   reserved, not operational, until final enable; no outbound/receive ticket yet.
5. Authenticated status gives signed evidence. Final local human enable durably
   arms consent then calls activate. Activate commits active only for the same
   verified flow/key and unused fence; signed response settles local activation.
   Crash recovery retries that exact authorized activation, never invents consent.
   Offline pending/revoke holds are visible. Owner quota is checked both at
   reservation and activation; reservations expire without counting as active.

For management mutations, flow_secret is generated/sealed at the local human
start and its hash is bound to candidate proof. Final activate accepts that
completed flow's immutable result generation and same candidate key/flow secret;
it never interprets the flow's original expected generation as the new one.
Management flows retain metadata/status access for 30d after completion using
the original bound starting key + secret, but grant no old-key operational rights.
An owner-change POST within the approved session sets pending-flow expiry to the
new-owner link deadline; management mutation completion sets a 30-minute final
enable window. It does not extend an already expired mailbox link or session.
Expired final enable requires a new human/current-owner proof flow. Signup's
original 30-minute activation deadline remains unchanged.

Confirmed-but-not-activated signup reservation expires at original flow expiry;
activation after expiry requires a new human flow and verification. An active
registration is not a five-minute CP lease. The only connection lease is ticket/
epoch/24h reauth; no tunnel heartbeat or periodic ownership-renew endpoint.

### 6.2 Owner change, old-owner revert and seven-day window

1. Human/passcode-gated local action starts management flow. Service sends its
   fixed old-owner step-up link within combined verification budgets. The ordinary
   install key only binds the request, never authorizes a switch. Old link POST
   grants a ten-minute hosted session for exactly this install/g/og/purpose/key.
2. In that session human POSTs new mailbox. Reject same canonical mailbox,
   frequency limit or owner-install cap. Reserve new-owner slot, bind new token
   to change id/old+new owners/g/og/candidate key, expire in 30m. Pending switch
   leaves current owner/active privileges intact. Session expiry after initiating
   does not cancel the new-owner confirmation window.
3. New-owner confirmation POST atomically consumes new token, increments both
   g and og, installs the new verified owner, updates owner slots, retires old
   threads/events/tickets, increments socket epoch and sets state `verified`.
   No old thread is ever reassigned. New local human enable is required; the
   service cannot sign/mail using the old-generation sealed identity. The same
   logical public key may be rebound only after local seal reseal/new human key
   handling; it does not inherit old mail rights.
4. The same transaction inserts exactly one fixed old-owner notice outbox row
   with a seven-day freeze-and-revert token. `revert_until=switch_ms+7 days`.
   Durable retry and pending/failed/uncertain notice status are visible. No
   successful-switch response may omit the notice intent. Expiry is not extended
   if the notice was delayed; show failure rather than claim seven days from
   eventual delivery. No arbitrary body/subject/link in control mail.
5. Revert GET is inert. Explicit old-owner revert POST before `revert_until`
   checks the exact completed change/current og (g may have advanced for key
   rotation/revoke), atomically restores old owner
   under **new** g and og, sets `frozen`, invalidates all current threads/tickets/
   sessions/queued instructions, consumes revert token, transfers quota slot
   back and enqueues fixed revert notices to both affected owners. Never replay
   pre-switch mail. Reserve the old owner's slot through revert deadline so a
   valid security revert cannot fail because that owner added other installations.
   **Exception after deletion (Dave 2026-11-11, unit 05):** if the registration
   was explicitly deleted during the open revert window, the new owner's address
   is already purged (6.3, 8.1) and is NOT retained to address a notice. The revert
   still succeeds; the old-owner notice is queued normally; the new-owner notice
   is recorded as a stable, hash-only intent with NULL envelope in visible
   `failed` state (reason `recipient_deleted`), never silently skipped and never
   retried. No new private retention is introduced for it.
6. Freeze does not automatically thaw when seven days pass. Seven days is the
   revert-link lifetime; frozen mail stays stopped until current restored owner
   proves a fresh hosted `resume` session and local human consent. Resume bumps
   g, retains og, changes frozen->verified, then explicit local enable can activate.
   A revert token cannot send mail, select a third owner or unlock a vault.

Completed change frequency history survives disable/re-enrollment/key recovery
for the same install. A revert does not erase the counted switch. A second
normal change cannot occur while the first seven-day revert is open; comparison
is strict `now >= prior_completed+7 days` and old link is valid only `now <
revert_until`. Used/stale link is idempotent only for its recorded result, never
rolls a later generation back. Owner reinstall does not merge thread history.

### 6.3 Rotation, key loss, lost mailbox, disable and deletion

Normal key rotation: local human gate, old-key request + candidate new-key proof,
old-mailbox management approval bound to exact candidate, atomic g+1 (og same),
new fence, verified state and old-generation fence. No automatic old-thread
migration. Fresh seal/AAD and explicit enable follow. Key loss with old mailbox
available uses the candidate-key management flow + old-mailbox POST; same address
may remain with fresh g/key, no inherited threads. Never accept only a known
install id, healthy manifest, new-address link, local passcode assertion or
unsigned status as old-owner authority.

**R1 pilot recovery:** if old mailbox is unavailable, refuse in-place change/
key recovery with `old_mailbox_required`. Human may restore it externally, or
explicitly start unrelated signup with newly verified mailbox, fresh install id,
new service-assigned address and new consent. Keep old held/uncertain local rows
in the same checked store, with their original registration and disabled execution;
do not rename/rebind their registration or transfer threads. Use available old
fence to retire old service state; if missing/offline retain a durable pending
revocation and do not report success. There is no bypass of existing pending
revocation gates: a blocked install cannot silently loop fresh identities to
resume mail. New signup can finish only after applicable local revoke holds are
settled; otherwise remain held. Old address is not claimed, released or reused
by this operation. Old service data follows its ordinary expiry/retention and
explicit deletion rules. Fresh signup is a loss-of-continuity recovery, not proof
of ownership of the old registration. **B (human-held recovery code) is deferred
post-pilot**, has no v1 route/schema/capability; Dave must revisit before public
launch with P6. No retroactive recovery-code creation or service-admin override.

Disable serializes with local submission/send gates, stops socket/signing and
persists a revoke before network. Service revoke keeps g as the high-water mark,
disables and fences that generation, and removes executable cloud bodies/threads
atomically. Only a new human-authorized recovery/re-enable creates g+1. Local
status says off locally/revocation pending until signed fence receipt. Re-enable
requires fresh human consent and verified current-owner flow; no automatic old
queue drain. Submitted turns/sent mail cannot be recalled; late completions may
settle evidence but cannot authorize another turn.

Explicit hosted human deletion additionally releases address into 90-day
quarantine, removes current private owner address/queued bodies/control-mail
envelopes and disables owner slots; keep content-free fence/dedupe/abuse-suppression
evidence through their deadlines. An already-open seven-day old-owner revert
remains valid across rotation, revoke or deletion: retain only that change's sealed
old-owner address, revert token hash, old-slot reservation and scoped change
record until deadline, disclose this minimal recovery retention. No address can
be reassigned during that window. Revert may restore it only to the old owner,
under a fresh generation and frozen state, never restore deleted message content.
It uses registry's current high-water g+1 and current og+1, not switch g+1.
Local deletion uses EXISTING durable deletion intents and its explicit
human confirmation; never overwrite corrupt/missing state. Logical deletion does
not erase provider backups immediately: preserve disclosed provider backup/Time
Travel windows (design baseline 30 days), no physical-erasure promise.

## 7. Errors, retries and non-success

Only codes below may cross wire/status; internal log details cannot become codes.
JSON errors on verified machine requests include common prefix and no additional
fields except `retry_after_ms:T` for 429/503. `Retry-After` header is integer
seconds, rounded up to the same delay. Before auth failures are uniform 401;
never reveal another install's existence/mailbox. Hosted errors use fixed text.

| HTTP | Closed codes |
| --- | --- |
| 400 | `invalid_request`, `invalid_json`, `invalid_send`, `invalid_message_id`, `invalid_text`, `invalid_hold`, `auth_evidence`, `event_identity`, `text_digest`, `unsupported_frame` |
| 401 | `unauthorized`, `ticket_invalid`, `session_required`, `csrf_failed` |
| 403 | `human_required`, `passcode_required`, `old_mailbox_required`, `consent_required` |
| 404 | `not_found`, `unknown_thread`, `unknown_intent` |
| 408 | `body_timeout` |
| 409 | `operation_conflict`, `intent_conflict`, `message_id_conflict`, `ack_conflict`, `nonce_replayed`, `stale_generation`, `connection_superseded`, `lease_unknown`, `registration_inactive`, `registration_frozen`, `verification_pending`, `owner_limit`, `change_limit`, `change_superseded`, `revocation_pending`, `uncertain`, `fenced`, `revoked`, `already_sent`, `same_owner` |
| 410 | `flow_expired`, `event_expired`, `token_expired`, `legacy_retired` |
| 413 | `body_limit`, `frame_limit` |
| 426 | `version_unsupported` |
| 429 | `request_limit`, `send_limit`, `ingress_limit`, `verification_limit`, `queue_full`, `abuse_limit` |
| 503 | `mail_disabled`, `manifest_not_configured`, `manifest_malformed`, `storage_unavailable`, `provider_unavailable`, `provider_suppressed`, `provider_quota`, `service_unavailable` |

Successful `code` is exactly `ok` except send=`accepted` and fence=`fenced`;
repeat idempotent responses keep original code/result. Lifecycle/local reasons also include
`response_unverified`, `vault_locked`, `store_missing`, `store_corrupt`,
`marker_missing`, `marker_corrupt`, `seal_missing`, `schema_corrupt`,
`local_disabled`, `conversation_busy`, `reauth_due`, `service_key_changed`,
`empty_body`, `queue_expired`, `connection_lost`, `extraction_unavailable`,
`extraction_ambiguous`, `history_missing`, `target_closed`, `provider_uncertain`,
`notice_pending`, `notice_failed`, `notice_uncertain`, `old_mailbox_lost`.
EXISTING extractor/continuation closed reasons stay local and cannot become
arbitrary remote instructions. Fixed-template mail send state VS is not an HTTP
error string. `failed`/`uncertain` send intent state returns 409 with its state
in the `/send` operation result only. `/send/status` itself is a successful 200
read with code `ok` even when the reported state is uncertain/failed; it never
means safe resend with a new intent id.

**`/v1/pull/send` intent-receipt exception (Dave, 2026-11-12, MC-1166 units
14a/21).** This is the only machine error that carries fields beyond the common
prefix. When `/v1/pull/send` resolves to an existing or just-finished intent row
whose state is not `accepted`, the signed 409 body is exactly
`{v:1,code:C,request_id:I,intent_id:ID,thread_id:ID,message_id:MID,state:S,provider_id:string}`
with `code === state` and `S` in `reserved|sending|uncertain|failed|revoked`
(the non-accepted `IS` values). For this route only, `reserved`, `sending` and
`failed` are added to the 409 closed codes. `intent_id` and `thread_id` must
equal the request's; `provider_id` is `""` for `reserved`/`sending`/`failed`
and `""` or retained opaque provider evidence (ASCII <=256) for
`uncertain`/`revoked`, and is never acceptance evidence. Every other `/send`
409 (`intent_conflict`, `registration_inactive`, `stale_generation`,
`nonce_replayed`, a `revoked` raised before any intent row exists, ...) is
common-prefix only. The receipt shape is all-or-nothing: any 409 carrying some
but not all receipt fields, extra fields, `code !== state`, or a mismatched
intent/thread is `response_unverified`. Client meaning: `reserved`/`sending`
= in flight, read `/send/status`, never resubmit; `uncertain` = hold, no
resend; `failed` = terminal for this intent, any retry is a new human-visible
decision; `revoked` = generation hold. Both adapters (Worker unit 14a, Python
unit 21) pin this with shared positive and negative vectors.

401/403, invalid signatures/pin, corrupt store, frozen/revoked generation and
vault lock are holds, not tight retry. 429/503/disconnect retry with bounded
jitter and absolute expiry; respect explicit Retry-After. Unknown provider
acceptance ->uncertain with no resend. Socket receipt is custody only, not
extraction/turn/action completion. Rejected/expired items produce bounded visible
reason/count metadata rather than truncated bodies or fictional delivery success.

### 7.1 Retained authority and expiry details

A retired key may authenticate only an exact previously committed mutation
result lookup in its original flow/operation scope, or bound flow status; it
cannot create a new operation, read a later owner generation, mint tickets or
activate a different flow. This permits lost-response recovery after rotation
without resurrecting the old key. Flow status uses its original bound key and
secret; general status/send-status uses current registry key and owner scope.

`expires_ms=min(received_ms+30 days,thread_expires_ms)` for a deliverable event;
no receive/retry/local custody extends it. Retained missing-MID holds have their
own 30-day metadata deadline, never execution rights. Notice enqueue is atomic
with switch/revert even when today's global send budget is exhausted; provider
handoff waits for a global reservation, within link expiry. A security revert
must not fail because the service cannot send its notice immediately. Control
attempts reserve budget before provider work and expose queued/uncertain status.

Mail registration/activation counts stay identified opt-in metrics separate from
anonymous update ping, never joined or added as unique people/install totals.
No new public analytics route is introduced. Content is not training, promotion,
cross-project memory or audit payload; ordinary resumed-session memory behavior
must be disclosed. Claude-only verified toolless extraction remains the pilot
prerequisite (D8-A); unavailable extraction blocks reply-capable activation and
holds existing replies. No unverified provider/cloud transform fallback.

## 8. NEW D1 schema: `migrations/0005_pull.sql`

This is the complete additive schema target. Apply after EXISTING `schema.sql`
and `0001`..`0004`; no rename, DROP, ALTER or backfill of legacy tables. Prefix
`pull_` is deliberate: no positional old INSERT or legacy activation trigger
can authorize this protocol. Unit 08 adapts transport-independent ledger callers
to this registry; legacy generations never become pull registrations. SQL below
is normative for names/types/nullability/checks/keys/indexes. `TEXT` ids/digests
are further validated against section 2 before every write; checks supplement
rather than replace parsing. Foreign keys must be enabled. No cascade deletes.

```sql
CREATE TABLE pull_version (
  slot INTEGER PRIMARY KEY CHECK(slot=1), version INTEGER NOT NULL CHECK(version=1)
);
INSERT INTO pull_version VALUES(1,1);

CREATE TABLE pull_installs (
  install_id TEXT NOT NULL PRIMARY KEY CHECK(length(install_id)=32),
  generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 999999999999999),
  owner_generation INTEGER NOT NULL CHECK(owner_generation BETWEEN 1 AND 999999999999999),
  state TEXT NOT NULL CHECK(state IN ('pending','verified','active','disabled','frozen','deleted')),
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64), owner_address TEXT,
  address_local TEXT NOT NULL UNIQUE CHECK(length(address_local)=30),
  key_id TEXT NOT NULL, public_key TEXT NOT NULL CHECK(length(public_key)=43),
  consent_version TEXT NOT NULL, active_since_ms INTEGER,
  epoch INTEGER NOT NULL DEFAULT 0 CHECK(epoch>=0), connection_id TEXT,
  next_seq INTEGER NOT NULL DEFAULT 1 CHECK(next_seq>0),
  created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
  CHECK((state='deleted' AND owner_address IS NULL) OR
        (state!='deleted' AND owner_address IS NOT NULL))
);
CREATE INDEX pull_install_owner ON pull_installs(owner_hash,state,install_id);

CREATE TABLE pull_generations (
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  generation INTEGER NOT NULL CHECK(generation>0), owner_generation INTEGER NOT NULL CHECK(owner_generation>0),
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64), key_id TEXT NOT NULL,
  public_key TEXT NOT NULL CHECK(length(public_key)=43),
  fence_hash TEXT NOT NULL CHECK(length(fence_hash)=64), flow_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('current','fenced')),
  created_ms INTEGER NOT NULL, fenced_ms INTEGER,
  PRIMARY KEY(install_id,generation), UNIQUE(install_id,generation,flow_id)
);
CREATE UNIQUE INDEX pull_one_current ON pull_generations(install_id) WHERE state='current';

CREATE TABLE pull_addresses (
  address_local TEXT NOT NULL PRIMARY KEY CHECK(length(address_local)=30),
  install_id TEXT REFERENCES pull_installs(install_id),
  state TEXT NOT NULL CHECK(state IN ('reserved','assigned','quarantined')),
  until_ms INTEGER, created_ms INTEGER NOT NULL,
  CHECK((state='assigned' AND until_ms IS NULL) OR (state!='assigned' AND until_ms IS NOT NULL))
);
CREATE INDEX pull_address_expiry ON pull_addresses(state,until_ms,address_local);

CREATE TABLE pull_owner_slots (
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  role TEXT NOT NULL CHECK(role IN ('pending','active','revert')),
  flow_id TEXT NOT NULL, until_ms INTEGER,
  PRIMARY KEY(owner_hash,install_id,role),
  CHECK((role='active' AND until_ms IS NULL) OR (role!='active' AND until_ms IS NOT NULL))
);
CREATE INDEX pull_slot_expiry ON pull_owner_slots(role,until_ms,owner_hash);

CREATE TABLE pull_flows (
  flow_id TEXT NOT NULL PRIMARY KEY CHECK(length(flow_id)=32),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  purpose TEXT NOT NULL CHECK(purpose IN ('signup','owner_change','key_recovery','resume','rotate','delete')),
  expected_generation INTEGER NOT NULL CHECK(expected_generation>0),
  expected_owner_generation INTEGER NOT NULL CHECK(expected_owner_generation>0),
  candidate_key_id TEXT NOT NULL, candidate_public_key TEXT NOT NULL,
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  flow_secret_hash TEXT NOT NULL CHECK(length(flow_secret_hash)=64),
  fence_hash TEXT NOT NULL CHECK(length(fence_hash)=64),
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('pending','verified','approved','completed','expired','cancelled','reverted')),
  consent_version TEXT NOT NULL, manifest_sequence INTEGER NOT NULL,
  manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
  result_json TEXT, created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  CHECK(expires_ms>created_ms)
);
CREATE UNIQUE INDEX pull_one_flow ON pull_flows(install_id,purpose)
  WHERE state IN ('pending','verified','approved');
CREATE INDEX pull_flow_expiry ON pull_flows(state,expires_ms,flow_id);

CREATE TABLE pull_operations (
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  generation INTEGER NOT NULL, path TEXT NOT NULL, op_id TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  result_json TEXT NOT NULL, status INTEGER NOT NULL CHECK(status BETWEEN 200 AND 599),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  PRIMARY KEY(install_id,generation,path,op_id)
);
CREATE INDEX pull_operation_expiry ON pull_operations(expires_ms);

CREATE TABLE pull_fences (
  install_id TEXT NOT NULL, flow_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
  fence_hash TEXT NOT NULL CHECK(length(fence_hash)=64),
  mode TEXT NOT NULL CHECK(mode IN ('cancel','revoke')),
  receipt_id TEXT NOT NULL UNIQUE, op_id TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64), created_ms INTEGER NOT NULL,
  PRIMARY KEY(install_id,flow_id,generation), UNIQUE(install_id,op_id)
);
CREATE INDEX pull_fence_install ON pull_fences(install_id,generation);

CREATE TABLE pull_tokens (
  token_hash TEXT NOT NULL PRIMARY KEY CHECK(length(token_hash)=64),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL, change_id TEXT,
  purpose TEXT NOT NULL CHECK(purpose IN ('signup','stepup','new_owner','revert')),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  binding_digest TEXT NOT NULL CHECK(length(binding_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('pending','consumed','expired','invalidated')),
  result_json TEXT, created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, consumed_ms INTEGER
);
CREATE INDEX pull_token_expiry ON pull_tokens(state,expires_ms,token_hash);

CREATE TABLE pull_browser_sessions (
  session_hash TEXT NOT NULL PRIMARY KEY CHECK(length(session_hash)=64),
  csrf_hash TEXT NOT NULL CHECK(length(csrf_hash)=64),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL, token_hash TEXT REFERENCES pull_tokens(token_hash),
  purpose TEXT NOT NULL CHECK(purpose IN ('confirmation','owner_change','key_recovery','resume','rotate','delete','revert')),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('csrf','approved','consumed','invalidated')),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL
);
CREATE INDEX pull_browser_expiry ON pull_browser_sessions(expires_ms,session_hash);

CREATE TABLE pull_owner_changes (
  change_id TEXT NOT NULL PRIMARY KEY CHECK(length(change_id)=32),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL UNIQUE REFERENCES pull_flows(flow_id),
  old_owner_hash TEXT NOT NULL CHECK(length(old_owner_hash)=64),
  new_owner_hash TEXT NOT NULL CHECK(length(new_owner_hash)=64),
  old_owner_sealed BLOB, new_owner_sealed BLOB,
  expected_generation INTEGER NOT NULL, expected_owner_generation INTEGER NOT NULL,
  switched_generation INTEGER, switched_owner_generation INTEGER,
  state TEXT NOT NULL CHECK(state IN ('pending','completed','reverted','expired','cancelled')),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  completed_ms INTEGER, revert_until_ms INTEGER, reverted_ms INTEGER,
  CHECK(old_owner_hash!=new_owner_hash)
);
CREATE UNIQUE INDEX pull_one_change ON pull_owner_changes(install_id) WHERE state='pending';
CREATE INDEX pull_change_frequency ON pull_owner_changes(install_id,completed_ms);
CREATE INDEX pull_change_expiry ON pull_owner_changes(state,expires_ms,revert_until_ms);

CREATE TABLE pull_control_outbox (
  outbox_id TEXT NOT NULL PRIMARY KEY CHECK(length(outbox_id)=32),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL, change_id TEXT,
  template TEXT NOT NULL CHECK(template IN ('signup','stepup','new_owner','old_notice','revert_old','revert_new')),
  target_hash TEXT NOT NULL CHECK(length(target_hash)=64),
  idempotency_key TEXT NOT NULL UNIQUE, envelope_sealed BLOB,
  seal_key_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('queued','sending','accepted','uncertain','failed','expired')),
  provider_id TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  lease_id TEXT, lease_until_ms INTEGER, next_attempt_ms INTEGER NOT NULL,
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, finished_ms INTEGER,
  CHECK(state NOT IN ('accepted','expired') OR envelope_sealed IS NULL)
);
CREATE INDEX pull_control_due ON pull_control_outbox(state,next_attempt_ms,outbox_id);
CREATE INDEX pull_control_expiry ON pull_control_outbox(expires_ms,outbox_id);

CREATE TABLE pull_nonces (
  direction TEXT NOT NULL, install_id TEXT NOT NULL, generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, nonce TEXT NOT NULL, expires_ms INTEGER NOT NULL,
  PRIMARY KEY(direction,install_id,generation,key_id,nonce)
);
CREATE INDEX pull_nonce_expiry ON pull_nonces(expires_ms);
CREATE TABLE pull_budgets (
  bucket TEXT NOT NULL PRIMARY KEY, count INTEGER NOT NULL CHECK(count>=0), expires_ms INTEGER NOT NULL
);
CREATE INDEX pull_budget_expiry ON pull_budgets(expires_ms,bucket);

CREATE TABLE pull_tickets (
  ticket_hash TEXT NOT NULL PRIMARY KEY CHECK(length(ticket_hash)=64),
  ticket_id TEXT NOT NULL UNIQUE, op_id TEXT NOT NULL,
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('issued','consumed','invalidated')),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, consumed_ms INTEGER,
  FOREIGN KEY(install_id,generation) REFERENCES pull_generations(install_id,generation),
  UNIQUE(install_id,generation,op_id)
);
CREATE INDEX pull_ticket_expiry ON pull_tickets(expires_ms,ticket_hash);

CREATE TABLE pull_intents (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, intent_id TEXT NOT NULL,
  owner_generation INTEGER NOT NULL, owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64), thread_id TEXT NOT NULL,
  message_id TEXT NOT NULL, provider_id TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK(state IN ('reserved','sending','accepted','uncertain','failed','revoked')),
  created_ms INTEGER NOT NULL, finished_ms INTEGER,
  PRIMARY KEY(install_id,generation,intent_id),
  FOREIGN KEY(install_id,generation) REFERENCES pull_generations(install_id,generation)
);
CREATE TABLE pull_threads (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, thread_id TEXT NOT NULL,
  owner_generation INTEGER NOT NULL, intent_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64), message_id TEXT NOT NULL,
  expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL, revoked_ms INTEGER,
  PRIMARY KEY(install_id,generation,thread_id),
  FOREIGN KEY(install_id,generation,intent_id) REFERENCES pull_intents(install_id,generation,intent_id)
);
CREATE INDEX pull_thread_expiry ON pull_threads(dedupe_until_ms,install_id,generation,thread_id);

CREATE TABLE pull_dedupe (
  event_id TEXT NOT NULL PRIMARY KEY CHECK(length(event_id)=64),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, incoming_hash TEXT NOT NULL CHECK(length(incoming_hash)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64), seq INTEGER NOT NULL,
  disposition TEXT NOT NULL CHECK(disposition IN ('pending','local_received','expired','revoked')),
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,thread_id,incoming_hash), UNIQUE(install_id,seq),
  FOREIGN KEY(install_id,generation,thread_id) REFERENCES pull_threads(install_id,generation,thread_id)
);
CREATE INDEX pull_dedupe_expiry ON pull_dedupe(dedupe_until_ms,event_id);

CREATE TABLE pull_ingress_holds (
  hold_id TEXT NOT NULL PRIMARY KEY CHECK(length(hold_id)=32),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, raw_digest TEXT NOT NULL CHECK(length(raw_digest)=64),
  reason TEXT NOT NULL CHECK(reason='invalid_message_id'),
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,thread_id,raw_digest),
  FOREIGN KEY(install_id,generation,thread_id) REFERENCES pull_threads(install_id,generation,thread_id)
);
CREATE INDEX pull_ingress_hold_expiry ON pull_ingress_holds(expires_ms,hold_id);

CREATE TABLE pull_events (
  event_id TEXT NOT NULL PRIMARY KEY REFERENCES pull_dedupe(event_id),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, seq INTEGER NOT NULL, incoming_message_id TEXT NOT NULL,
  event_kind TEXT NOT NULL CHECK(event_kind IN ('text','held')),
  text TEXT, body_bytes INTEGER NOT NULL CHECK(body_bytes BETWEEN 0 AND 16384),
  auth_json TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK(state IN ('pending','offered','held','local_received','expired','revoked')),
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  next_attempt_ms INTEGER NOT NULL, lease_id TEXT, lease_until_ms INTEGER,
  terminal_ms INTEGER,
  UNIQUE(install_id,seq),
  CHECK((event_kind='held' AND text IS NULL AND body_bytes=0) OR event_kind='text'),
  CHECK(state NOT IN ('local_received','expired','revoked') OR (text IS NULL AND body_bytes=0))
);
CREATE INDEX pull_event_due ON pull_events(install_id,generation,state,next_attempt_ms,seq);
CREATE INDEX pull_event_expiry ON pull_events(state,expires_ms,event_id);
CREATE INDEX pull_event_terminal ON pull_events(terminal_ms,event_id);

CREATE TABLE pull_offers (
  lease_id TEXT NOT NULL PRIMARY KEY CHECK(length(lease_id)=32),
  event_id TEXT NOT NULL REFERENCES pull_dedupe(event_id),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL,
  offered_ms INTEGER NOT NULL, lease_until_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL
);
CREATE INDEX pull_offer_event ON pull_offers(event_id,offered_ms,lease_id);
CREATE INDEX pull_offer_expiry ON pull_offers(dedupe_until_ms,lease_id);

CREATE TABLE pull_receipts (
  event_id TEXT NOT NULL PRIMARY KEY REFERENCES pull_dedupe(event_id),
  receipt_id TEXT NOT NULL UNIQUE, local_receipt_id TEXT NOT NULL,
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  committed_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,local_receipt_id)
);
CREATE INDEX pull_receipt_expiry ON pull_receipts(dedupe_until_ms,event_id);

CREATE TABLE pull_notifications (
  install_id TEXT NOT NULL PRIMARY KEY REFERENCES pull_installs(install_id),
  revision INTEGER NOT NULL CHECK(revision>0), dirty_from_seq INTEGER NOT NULL,
  next_attempt_ms INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  lease_id TEXT, lease_until_ms INTEGER
);
CREATE INDEX pull_notify_due ON pull_notifications(next_attempt_ms,install_id);
CREATE TABLE pull_sweep_cursors (
  task TEXT NOT NULL PRIMARY KEY CHECK(task IN ('notify','expire','control','metadata')),
  cursor_ms INTEGER NOT NULL DEFAULT 0, cursor_id TEXT NOT NULL DEFAULT '', updated_ms INTEGER NOT NULL
);
CREATE TABLE pull_audit (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, install_id TEXT NOT NULL,
  at_ms INTEGER NOT NULL, code TEXT NOT NULL, related_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX pull_audit_time ON pull_audit(at_ms,audit_id);
CREATE TABLE pull_audit_overflow (
  day_ms INTEGER PRIMARY KEY, count INTEGER NOT NULL CHECK(count>=0)
);
```

### 8.1 Additional mandatory D1 invariants and transaction recipes

These guarded multi-row invariants are part of the schema contract even where
SQL CHECK cannot express them. Units 01/02/07 must test them with concurrent
writers; a client-side count then INSERT is not sufficient.

- `owner_hash=SHA256(UTF8(canonical_mailbox))`; neither hash nor install id is
  authenticated proof. Count distinct install ids in unexpired owner slots;
  active slots do not expire. New reservation + budget charge + flow + outbox
  must commit together with <=3 owner slots and exact input idempotency check.
  Signup inserts install then generation/address/flow; all roll back on collision.
- For each pending/verified/active/frozen install exactly one `current` generation
  equals registry g/key/owner hashes; disabled/deleted has only fenced generations
  and retains g as high-water mark. Fence old row before inserting new current row in the same
  transaction; copy fresh immutable snapshot, no overwrites of old key authority.
  Generation mutations invalidate sessions/tokens/tickets/flows bound to old g,
  advance epoch, revoke threads/pending events and purge their text/bytes. Keep
  original change's revert token as the sole explicitly scoped exception.
- Slot release at switch preserves the old owner's `revert` reservation until
  seven-day deadline. Successful revert consumes it into active reservation for
  the restored old owner; freeze retains that slot. A new-owner slot is released.
  Frequency is COUNT completed switches in trailing 365d and MAX completed time,
  including reverted changes; do not delete those records before 365d.
- Claim replay nonce atomically after crypto, before operation side effects;
  unique tuple is the replay fence. Durable fences have no foreign key because
  cancel-before-start is valid; they never expire and block matching future flow.
  Registry stubs/hashes needed for fences survive deletion without private owners.
- Enqueue: check authentic From/token/MID and current active g/og, read candidate
  `next_seq`, compute immutable event digest with that seq, then one guarded
  transaction CAS-increments next_seq, reserves count/byte/global ingress budgets,
  inserts dedupe+event and upserts notification revision. CAS mismatch retries
  computation before SMTP acceptance, never acknowledges an incomplete event.
  Existing dedupe with same body digest resolves first event without new quota;
  conflicting body under same identity records metadata refusal, no replacement.
- Queue byte/count capacity counts all `pending|offered|held` text and held
  descriptors plus unexpired pull_ingress_holds (one item, zero bytes each). Implement guarded
  indexed per-install scans and pilot-bounded global scan or counters guarded in
  the same transaction; no eventual/DO-only capacity authority. D1 batch must
  roll back if any guard fails; use an aborting guard statement/trigger rather
  than assume a zero-row statement aborts a batch.
- Offer: current epoch/g/og + due unexpired event, update lease, insert offer
  history atomically before sending signed frame. Keep at most 64 offer history
  rows/event; prune oldest expired entries only. A pruned lease refuses a first
  ack; re-offer and re-ack the same local receipt under a retained lease, without
  re-laundering or dispatch. Existing committed ack receipt is authoritative
  even if its old offer was compacted. Expired lease alone never destroys mail.
- Ack: current active scope plus exact existing receipt OR valid unexpired event
  + retained offer history; compare all digests/local receipt. Insert receipt,
  set dedupe local_received, clear event body/bytes and lease, terminal timestamp,
  and revise notification in one transaction. Emit ack_committed only after it.
- Owner switch/send: `reserved` send is not yet authorized provider handoff.
  CAS `reserved->sending` only under current active g/og/owner; this is the
  serialized handoff authorization point. Switch/revoke never creates a later
  authorization for old owner; a `sending` attempt already authorized before
  switch may reach provider afterward and cannot be recalled. Record it as
  in-flight, not newly authorized send. Crash after authorization ->uncertain,
  never retry without positive proof it was not submitted. Bound provider calls
  to 10s. Reconcile only positive provider evidence; no API feature is assumed
  to exist in the adapter until tested. All recipient/header construction uses
  immutable owner snapshot from that authorized generation.
- Control mail: tokens are hashed in `pull_tokens`; fixed recipient/template/
  token envelope is encrypted in control outbox, never plaintext. NEW service
  configuration `MAIL_PULL_OUTBOX_KEY_ID`/`MAIL_PULL_OUTBOX_KEY` identifies an
  operator-provisioned 32-byte AES-256-GCM key; it is not an install location or
  end-user recovery credential. No creation is authorized here. Blob format:
  12-byte nonce followed by ciphertext+16-byte tag, AAD `A("clayrune-mail-pull-
  control","1",outbox_id,install_id,flow_id,template,target_hash,expires_ms)`
  (purpose string is the unbroken `clayrune-mail-pull-control`). Decrypted object
  exactly `{to:mailbox,token:S,link_kind:"link"|"revert"}`. No caller content.
  Old/new owner sealed fields use the same key/blob scheme and AAD
  `A("clayrune-mail-pull-owner","1",change_id,"old"|"new",owner_hash)` over
  UTF-8 mailbox bytes. Retain key ids with envelopes; retain decrypt keys only
  for bounded live envelopes during operator rotation, no silent fallback.
  Purge control envelope on accepted/expiry/deletion; uncertain cannot auto-send.
  Remove old/new owner sealed values at revert expiry or cancellation/expiry;
  deletion during an open revert window preserves only the old-owner sealed
  recovery value until that deadline. New-owner sealed value is purged at switch
  once its current registry projection exists, except pending control outbox needs.
- Control `sending` lease loss becomes uncertain, not queued. Retry only errors
  positively known before provider submission. Old notice expiry is revert
  deadline; verification outbox expiry is token expiry. No expired link emails.
  Persistent outbox status exposes notice failure; service may not claim actual
  delivery/read based on provider acceptance.
- Notification worker leases <=60s, reads <=100 due rows; notifier revision is
  cleared only by CAS after bounded DO wake/scan. If another enqueue changes
  revision, retain the dirty row. Offline/backpressure retains a future due intent;
  connect always queries authoritative pending events even if intent was lost.
  No pending work means no per-install periodic poll.
- Cleanup is indexed/keyset-paged with persisted cursor; tasks complete fair
  passes, not permanently the first 100 rows. Event expiry removes text, retains
  metadata 30d; dedupe/receipts/offers survive to their own dedupe deadline.
  Delete receipt/offers, detailed event, dedupe, then thread/intent in FK-safe
  order after all required deadlines. Do not cascade-away replay evidence.
  `pull_operations` lasts 30d or flow/generation replay horizon if later; tickets
  never store their secret in operation result_json (only a consumed marker).
  Send
  idempotency remains in intents through thread expiry+30d. Token hashes/results
  last 30d after expiry for duplicate POST receipts, then purge. Browser sessions
  expire at 10m. Audit <=10,000 detailed rows, purge at 30d; overflow daily counts
  <=30 days, no raw data. Budgets expire only after their enforcement window.

## 9. NEW local schema: `mc/agent_mail_pull_schema.py`

One custody database: EXISTING checked `agent_mail.sqlite3`, outside repository
and `DATA_DIR`, opened by `MailFactory` with marker/seal checks. Do not copy
EXISTING `default_path()` into a new repo location: factory's protected runtime
path is authoritative. No import-time opening, startup, network or key creation.
Base `application_id=0x4D434D4C`, existing ingress `user_version=3` stays intact;
the pull extension has its **own** exact-set marker/version below. No prefix
`mail_enrollment_`, whose existing initializer checks an exact four-table set.
No legacy row is promoted to pull authority.

Fresh explicit authorized initialization may add this extension in the same
checked DB. Existing DB with no `mail_pull_*` objects may receive the whole
extension transactionally. Partial objects, wrong column/index/constraint SQL,
wrong marker/version, lost receipt/ack table, NUL/empty/corrupt file ->fail closed
`schema_corrupt`/store hold, never recreate. Runtime validates all declared SQL
objects, not merely marker presence. No automatic downgrade; future versions
need an explicit checked migration. Use EXISTING write lock + BEGIN IMMEDIATE,
WAL, synchronous FULL; filesystem/commit failure means no ack. No wrapping
network/extractor/provider work in a SQLite transaction.

```sql
CREATE TABLE mail_pull_version (
  slot INTEGER PRIMARY KEY CHECK(slot=1), version INTEGER NOT NULL CHECK(version=1)
);
INSERT INTO mail_pull_version VALUES(1,1);
CREATE TABLE mail_pull_accounts (
  install_id TEXT NOT NULL PRIMARY KEY CHECK(length(install_id)=32),
  generation INTEGER NOT NULL CHECK(generation>0), owner_generation INTEGER NOT NULL CHECK(owner_generation>0),
  service_origin TEXT NOT NULL, address TEXT NOT NULL, owner_address TEXT NOT NULL,
  key_id TEXT NOT NULL, seal_flow_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','verified','active','disabled','frozen','deleted','held')),
  local_enabled INTEGER NOT NULL DEFAULT 0 CHECK(local_enabled IN (0,1)),
  consent_version TEXT NOT NULL, consent_epoch INTEGER NOT NULL CHECK(consent_epoch>0),
  reason TEXT NOT NULL DEFAULT '', updated_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX mail_pull_one_enabled ON mail_pull_accounts(local_enabled) WHERE local_enabled=1;
CREATE TABLE mail_pull_generations (
  install_id TEXT NOT NULL REFERENCES mail_pull_accounts(install_id),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, seal_flow_id TEXT NOT NULL,
  manifest_sequence INTEGER NOT NULL, manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
  service_key_id TEXT NOT NULL, service_public_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('current','fenced','held')),
  created_ms INTEGER NOT NULL, fenced_ms INTEGER,
  PRIMARY KEY(install_id,generation)
);
CREATE UNIQUE INDEX mail_pull_current ON mail_pull_generations(install_id) WHERE state='current';
CREATE TABLE mail_pull_flows (
  flow_id TEXT NOT NULL PRIMARY KEY CHECK(length(flow_id)=32),
  install_id TEXT NOT NULL REFERENCES mail_pull_accounts(install_id),
  purpose TEXT NOT NULL CHECK(purpose IN ('signup','owner_change','key_recovery','resume','rotate','delete')),
  expected_generation INTEGER NOT NULL, expected_owner_generation INTEGER NOT NULL,
  candidate_key_id TEXT NOT NULL, seal_flow_id TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  human_authorized_ms INTEGER NOT NULL, consent_epoch INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','verified','enable_pending','completed','cancelled','expired','held')),
  expires_ms INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
  result_json TEXT
);
CREATE INDEX mail_pull_flow_due ON mail_pull_flows(state,expires_ms,flow_id);
CREATE TABLE mail_pull_threads (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, thread_hash TEXT NOT NULL UNIQUE CHECK(length(thread_hash)=64),
  project_id TEXT NOT NULL, session_id TEXT NOT NULL,
  continuation_digest TEXT NOT NULL CHECK(length(continuation_digest)=64),
  thread_expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','revoked')),
  PRIMARY KEY(install_id,generation,thread_id),
  FOREIGN KEY(install_id,generation) REFERENCES mail_pull_generations(install_id,generation)
);
CREATE INDEX mail_pull_thread_conversation ON mail_pull_threads(project_id,session_id,thread_expires_ms);
CREATE TABLE mail_pull_receipts (
  event_id TEXT NOT NULL PRIMARY KEY CHECK(length(event_id)=64),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, cloud_seq INTEGER NOT NULL CHECK(cloud_seq>0),
  incoming_hash TEXT NOT NULL CHECK(length(incoming_hash)=64),
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  local_receipt_id TEXT NOT NULL UNIQUE, local_event_id TEXT NOT NULL UNIQUE,
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  committed_ms INTEGER NOT NULL, cloud_receipt_id TEXT, cloud_committed_ms INTEGER,
  disposition TEXT NOT NULL CHECK(disposition IN ('local_received','expired','revoked','terminal')),
  UNIQUE(install_id,generation,thread_id,incoming_hash), UNIQUE(install_id,cloud_seq),
  FOREIGN KEY(install_id,generation,thread_id) REFERENCES mail_pull_threads(install_id,generation,thread_id)
);
CREATE INDEX mail_pull_receipt_expiry ON mail_pull_receipts(dedupe_until_ms,event_id);
CREATE INDEX mail_pull_receipt_order ON mail_pull_receipts(install_id,cloud_seq);
CREATE TABLE mail_pull_ack_outbox (
  event_id TEXT NOT NULL PRIMARY KEY REFERENCES mail_pull_receipts(event_id),
  local_receipt_id TEXT NOT NULL UNIQUE, lease_id TEXT NOT NULL,
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('pending','held')),
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_ms INTEGER NOT NULL,
  created_ms INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX mail_pull_ack_due ON mail_pull_ack_outbox(state,next_attempt_ms,event_id);
CREATE TABLE mail_pull_fences (
  op_id TEXT NOT NULL PRIMARY KEY CHECK(length(op_id)=32),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, flow_id TEXT NOT NULL,
  fence_token TEXT NOT NULL CHECK(length(fence_token)=64),
  mode TEXT NOT NULL CHECK(mode IN ('cancel','revoke')),
  state TEXT NOT NULL CHECK(state IN ('pending','acked')),
  receipt_id TEXT, created_ms INTEGER NOT NULL, next_attempt_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,flow_id,mode)
);
CREATE INDEX mail_pull_fence_due ON mail_pull_fences(state,next_attempt_ms,op_id);
CREATE TABLE mail_pull_connections (
  install_id TEXT NOT NULL PRIMARY KEY REFERENCES mail_pull_accounts(install_id),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  epoch INTEGER NOT NULL DEFAULT 0, connection_id TEXT,
  authenticated_ms INTEGER, reauth_ms INTEGER,
  scan_after_seq INTEGER NOT NULL DEFAULT 0, scan_complete INTEGER NOT NULL DEFAULT 0 CHECK(scan_complete IN (0,1)),
  last_activity_ms INTEGER, next_attempt_ms INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE mail_pull_status (
  install_id TEXT NOT NULL PRIMARY KEY REFERENCES mail_pull_accounts(install_id),
  observed_ms INTEGER NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  status_json TEXT NOT NULL, response_digest TEXT NOT NULL CHECK(length(response_digest)=64)
);
```

### 9.1 Mapping to EXISTING storage and execution

`registration_id` in EXISTING `registrations`, `thread_bindings`, `events` and
outbound intents equals pull `install_id`; **the existing field named
`owner_generation` in `thread_bindings` stores combined registration g**, not
the new separate og. New `mail_pull_threads` records both. Reusing the old field
as og would silently let pre-rotation threads execute and is forbidden. Current
generation is adopted only from verified signed service evidence and authorized
human flow, never by blindly calling `register()`/`enable_registration()` until
their locally incremented values happen to match. Unit adapters must set exact
generation under one consent transaction or hold on mismatch. Existing legacy
registrations keep their original ids and disabled held rows; no remapping.

Thread registration/outbound intent/local continuation snapshot is durable before
service send. New map verifies thread_id -> token hash -> EXISTING binding and
continuation snapshot digest (`SHA256(UTF8(snapshot))` over exact stored snapshot).
Keep project generation, session id, native id/history, provider, character,
permissions and question revision in that EXISTING immutable snapshot/outbox;
they are never supplied by the service event. A local thread id collision or
changed snapshot is a hold. Thread mapping is immutable except state revocation.

Receiver uses one transaction for base event + NEW receipt + NEW ack outbox.
It must extract/reuse transaction-safe EXISTING insert logic: calling a helper
that independently commits and then writing ack metadata is forbidden. EXISTING
`events.body_digest` uses its bounded length-prefixed local algorithm; do not
overwrite it with wire SHA-256. `mail_pull_receipts.body_digest` is the wire hash.
NEW receipt input identity prevents replay even after EXISTING base event or
30-day ingress receipt is purged. Dedupe deadline = immutable thread expiry+30d,
not receive time+30d. Matching replay returns original local receipt and requeues
ack, never creates another base event or follow-up. Changed digest ->hold and
no ack, even if text happens to be equal. Held empty-body descriptor creates
content-free base blocked event; it cannot trigger extraction/dispatch.

On ack_committed, verify service frame + event/digests/local receipt; update
receipt cloud proof and remove its ack outbox row transactionally. Restart resets
connection scan state to zero/incomplete; stored epoch does not prove a live
socket. Pending acks replay first, then authoritative scan; a pruned lease triggers
reoffer, replace only outbox lease_id and re-ack same receipt. Never reset event
attempt count, body expiry or uncertain provider state. On terminal event purge
drop body/laundered text under EXISTING policy; keep content-free NEW receipt
until P4 deadline and thread mapping needed for it. Purge ack outbox only after
cloud receipt or explicit terminal generation/expiry disposition recorded locally,
then receipts and thread map in FK order at dedupe deadline. Revocation never
allows old pending acks to delete newly scoped mail.

`Wake`/`agent_followup` stays the sole execution path. Readiness/ordering must join
NEW receipt cloud_seq, not simply base commit order, across threads of the same
local conversation; wait for completed initial scan and all earlier known
unsettled conversation events. Existing `events.seq` alone is insufficient if
offer retries arrived out of order. Uncertain submission requires positive
EXISTING turn/pipe evidence, not a socket ack or an in-memory pending list.
Extraction must use the verified toolless runtime, preserve free-text replies,
quote/forward separation and ambiguity holds; no raw-mail/provider fallback.
Local consent/human gates serialize disable, owner/key changes and submit admission.

`mail_pull_status` is a bounded projection of a verified response, not authority
to repin/enable. Latest observation only; no history of private mailboxes. All
status access is authenticated by existing dashboard policy. Never store WSS
tickets, hosted session cookies, CSRF or mail-link tokens in any local table,
agent context, transcript or URL. Pending flow secret/seed remains sealed;
fence_token plaintext is intentional denial-only capability, not a generic secret
API. No status result includes it. Deleted local account projections remove
owner_address/address content according to explicit deletion; receipt hashes and
held work remain only under disclosed retention or confirmed deletion policy.

## 10. Local HTTP boundary (NEW behavior on EXISTING surfaces)

The EXISTING `/api/agent-mail/enrollment` GET is retained as read-only bounded
status. Pull wire data is not proxied verbatim. Return `{v:1,state:RS|"held",
reason:reason,install_id:I|null,generation:N,owner_generation:N,address:string,
owner:string,connection:"connected"|"offline"|"vault_locked"|"held",
queue_count:N,queue_bytes:N,invalid_message_id_holds:N,oldest_expires_ms:T|null,revocation_pending:boolean,
notice_state:VS|null,observed_ms:T|null}`. Empty owner/address before signup or
after deletion. Status does not create store/identity/flow or send mail.

Every mutating local route retains EXISTING `_require_human_passcode` and
trusted human attribution (no caller-provided `human:true` escape), same CSRF/
dashboard controls as publish. Passcode is accepted only in the local request
body `passcode:string` (<=1,024 bytes), never forwarded to service or logged.
Maximum local body 32 KiB. Agent-facing tools may send bounded mail intents but
cannot invoke these owner/credential/consent actions. A missing production
adapter returns 503 `service_unavailable`, never a mock success.

| NEW POST action at `/api/agent-mail/enrollment/<action>` | Exact body besides passcode | Effect and response |
| --- | --- | --- |
| `start` | `{op_id:I,owner:mailbox,consent_version:ID,manifest:[existing 11-item array],signature:B64}` | Explicit signup only; same current-flow op retries, return local status plus `flow_id:I`. |
| `review` | `{}` | Read verified pending flow for human, local status plus flow_id or null; no mail/enable. |
| `enable` | `{op_id:I,flow_id:I}` | Authorize/persist activation then signed service activate; return local status. |
| `disable` | `{op_id:I}` | Immediate serialized local off + durable remote fence; return local status even if remote pending. |
| `retry-revocations` | `{}` | Replay existing denial-only intents; return local status, no enable. |
| `owner` | `{op_id:I}` | Begin bound old-mailbox owner-change step-up only; local status plus flow_id. Proposed new mailbox is typed in hosted session, never machine-start body. |
| `key` | `{op_id:I,mode:"rotate"|"recover"}` | Begin bound human key flow, local status plus flow_id; no activation or owner replacement before mailbox proof. |
| `resume` | `{op_id:I}` | Begin frozen-owner resume proof only; local status plus flow_id. Background status never calls this mutating action. |
| `fresh-signup` | `{op_id:I,owner:mailbox,consent_version:ID,manifest:[array],signature:B64}` | R1 explicit new identity/new address, preserve disabled old records; refuse revocation_pending. Local status plus new flow_id. |
| `delete` | `{op_id:I}` | Begin current-owner hosted deletion proof after explicit local human confirmation; local off immediately, no address release until hosted POST. Local status plus flow_id. |

Local `owner/key/resume` response includes no hosted token or arbitrary URL;
human opens the service's fixed emailed link. Read-only polling uses GET status
plus bounded authenticated service status inside server supervisor, not a new
management action. Unit 20 owns the owner-change adapter separately from signup.
EXISTING `confirm`, `verify`, `repin`, `proof` legacy actions are not silently
reinterpreted: return 410 `legacy_retired` in pull mode; existing public-key
review/configuration surface remains separately human-gated. No CP proof-arm.

## 11. Implementation ownership and acceptance gates

Build units in the [design's registry](AGENT_MAIL_PULL_DESIGN.md#build-plan-one-concernfile-per-unit)
consume this contract. Units 01-14 own service SQL/registry/verification/signup/
management/change/auth/ledger/DO/ingress/provider/outbound/sweep/wiring; units
15-26 own local schema/key binding/receiver/client/signup/owner adapter/HTTP/
factory/recovery/supervisor/status/outbound. Unit 04a is separate owner management,
not a body added to signup. Registries import modules, not implementations.
No silent move of owner management or queue into a monolith. Follow-up residual
schema/signing/UI/wiring/retirement units remain separate; no module deletion
or destructive data migration is authorized by this contract.

The following are required implementation tests, not assertions of passing
runtime behavior from this document:

1. Cross-language vectors for HTTP v2, frame/event/candidate transcripts and
   existing manifest format: UTF-8, forbidden JSON duplicates, origin/path/status/
   nonce/scope mutation, obsolete signatures, wrong key, rollback and expiry.
2. Signup scanner GET is inert; duplicate POST no second mail; target/install/
   global budgets and three-owner-slot race; verified offline then explicit
   activation; no CP/tunnel/destination lookup; locked vault cannot keep socket.
3. Ticket consume/upgrade crash at each point, wrong protocol, URL token refusal,
   replay, expiry, concurrent clone epochs, hibernate attachment reconstruction,
   current D1 generation checked on every offer/ack and owner-switch race.
4. Enqueue before SMTP success; seq CAS race; notification lost before/after
   commit; bounded fair sweep, expired leases, large-frame/cap backpressure,
   missing wake and service restart recover without duplicate body/custody loss.
5. Local commit/crash before ack; service commit/crash before ack_committed;
   lost ack outbox table refuses; identical replay after base-event purge and
   after 30d terminal cleanup still dedupes until thread+30d; changed digest
   holds; expired first ack refused; committed receipt re-ack succeeds.
6. Conversation ordering across multiple threads/pages/reconnect; an unrelated
   held conversation does not block another; unknown thread cannot select a
   fallback agent; busy/closed/missing native history holds; question revision
   and late-reply behavior stays correct; no second provider write on uncertainty.
7. Old-mailbox step-up cannot be replaced by install-key/passcode claim; new-owner
   confirm, switch+notice atomicity, notice provider uncertainty, 7-day exact
   boundary, quota reserved for old-owner revert, concurrent stale link, freeze
   remains until human resume/enable; no old-thread reassignment/replay.
8. Key rotate/recovery proves new possession and old mailbox, fresh generation/
   seal AAD and no inherited threads. R1 unavailable old mailbox cannot recover
   same address; fresh signup preserves held records and respects pending revoke;
   no recovery-code endpoint, retroactive enrollment or administrator shortcut.
9. Offline disable, revoke/ack/send/owner-switch races, scoped fence with lost
   vault, cancel-before-start, delete quarantine and data purges, no physical
   backup-erasure claim. Existing human-only gates and toolless runtime remain.
10. Synthetic paid provider adapter asserts the exact structured field set, fixed
    From, sole current registered owner recipient, `replyTo` with a 32hex token,
    no caller headers/Message-ID/raw MIME, opaque validated provider id,
    `Error.code` suppression/quota/permanent/uncertain mapping, 10s timeout and
    no retry. Unit 11b separately tests From+token+Cloudflare DMARC/alignment
    proof with absent/mismatched In-Reply-To/References as non-authoritative hints.
    Real sender-provenance
    fixtures and DO hibernation/duration need separately authorized integration
    testing; mocked Node/SQLite tests do not prove those Cloudflare properties.

Every code unit runs focused tests through the installation's reporting launcher;
new/moved `mc/` modules add no pyright basic errors. Run affected smoke/tests
after each merge. Real pilot waits for approved disclosure/provider setup,
separately authorized deployment, validated ingress fixtures, measured empty-idle
hibernation, byte/row bounds, sweep lag and known account limits. Higher load/
spend and recovery-code policy remain post-pilot decisions, not builder discretion.

## 12. Documentation validation and rollback

This Unit 00 changes one document. SQL syntax/foreign-key object references can
be checked in disposable SQLite against the existing baseline schema; this does
not validate D1 transactional guards, provider behavior, WebSocket runtime,
hibernation or deployed state. Check Markdown local links, every EXISTING source
claim, route/frame/table/decision coverage and whitespace before commit. No
runtime tests or live sends are required/authorized for a doc-only contract.

Unit 00 validation at baseline: both SQL blocks compiled in disposable SQLite;
legacy D1 schema/migrations were unchanged; 26 NEW D1 tables/29 explicit indexes
and 10 NEW local tables/8 explicit indexes passed FK preparation/integrity checks
and representative constraint refusals. Checked 29 EXISTING source paths, six
NEW target absences, three local Markdown links/anchor, P1-P7/R1 coverage and
bounded serialization examples (98,666-byte send request, 107,366-byte event
payload, 143,726-byte signed frame). The scratch validator ran through the
reported test launcher; it is not runtime/D1/DO/live-mail verification.
README, USER_GUIDE, CHANGELOG and rules need no changes: no available behavior,
installation instructions or agent authority changed in this doc-only unit.

Implementation rollback keeps all durable pull fences/receipts/generation
tombstones and checked local inbox; disable operational pull paths, never fall
back to legacy CP/tunnel delivery or recreate state. Reverting this document
commit changes no running system. Later migrations must be additive and reviewed;
dropping tables, clearing queues or reassigning addresses requires explicit
authorization independent of deploying code.
