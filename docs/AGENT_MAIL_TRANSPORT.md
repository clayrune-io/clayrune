# Agent mail transport, local receiver and laundering (MC-1166 units 2/4/5)

Branch candidate only. No deployment, account call, enrollment, credentials,
real mail, merge, push, restart or live enablement is part of this unit.
Parent requirements: [AGENT_MAIL_SPEC.md](AGENT_MAIL_SPEC.md). Dave resolved D7's
wire choice as **B, separate mail-only Ed25519 identities**, on 2026-11-09.
This document is the shared contract for the receiver, laundering and enrollment.

## Signing contract v1

Each install generates a dedicated Ed25519 keypair locally. Its private key
stays in its local vault/keystore; only its 32-byte public key reaches the Worker.
The service has a separate Ed25519 keypair: PKCS#8 private material in the Worker
secret `MAIL_SERVICE_PRIVATE_KEY`, public key pinned by each install at enrollment.
Keys are unpadded, canonical base64url; signatures are base64url of 64 raw bytes.
The generation is a positive integer encoded as a decimal ASCII string on wire.
Never reuse tunnel attestation/device keys, dashboard cookies or bearer tokens.

Every request is POST. Headers:

| Header | Value |
| --- | --- |
| `x-mail-version` | `1` |
| `x-mail-direction` | `install-to-service` or `service-to-install` |
| `x-mail-key-id` | Expected pinned/registered key id, `[A-Za-z0-9_-]{1,128}` |
| `x-mail-install-id` | Expected registered install id, same syntax |
| `x-mail-generation` | Current positive generation, decimal, at most 15 digits |
| `x-mail-timestamp` | Epoch milliseconds, exactly 13 decimal digits |
| `x-mail-nonce` | Fresh CSPRNG 16 bytes rendered as 32 lowercase hex characters |
| `x-mail-digest` | Lowercase hex SHA-256 of the exact request-body bytes |
| `x-mail-signature` | Ed25519 signature of the canonical bytes below |

Canonical bytes are UTF-8 of the following JSON array, **all elements strings**,
no whitespace, BOM, newline or trailing characters. Valid values are all ASCII;
no JSON escaping differences are possible. Python uses `json.dumps(values,
separators=(',', ':')).encode('utf-8')`.

```text
["clayrune-agent-mail","1",direction,key_id,install_id,generation,"POST",exact_path,timestamp,nonce,body_sha256]
```

`exact_path` is the literal receiver pathname, with no query/fragment. v1 paths
match `/[A-Za-z0-9/_-]{1,200}`; percent encodings, dots, queries and fragments
are not accepted as receiver routes. The enrolled path must match exactly.
The Worker exposes exactly `/v1/mail/send` and `/v1/mail/reconcile`. Unit 7 stores
unit 4's actual receiver path in its proven binding (example:
`/api/agent-mail/ingress`); the Worker never selects one from a client payload.
JSON body serialization is not canonicalized: hash the transmitted bytes, not
a parsed/re-serialized value. Request content type is `application/json`.

Receivers must check current consent, expected direction, key id, install,
generation, POST and exact path, SHA-256, signature and timestamp within inclusive
300,000 ms of their current clock after the complete body arrives and again after
asynchronous crypto verification. After successful verification, atomically claim
`(direction,install,generation,key_id,nonce)` in a durable store. Retain through
`timestamp+300001` ms; delete only when that deadline is reached. Only the first
claim succeeds, including across processes and restarts. Recheck freshness after
the durable claim completes too: a slow claim racing expiry/cleanup cannot
authorize processing. `verify` accepts a live `clock` callback; its explicit
`now` argument is a fixed-time test compatibility seam. Invalid signatures do
not burn nonces. Storage failure refuses processing visibly; there is no memory
fallback. A lost HTTP response requires a **fresh signature/nonce** around the
same intent/event; stable identity supplies business-level deduplication.

`signing.js` supplies signing/verification; it is not an install key manager.
Unit 4 supplies the equivalent verifier against the enrolled service pin and
the local dedicated durable store. It verifies before interpreting JSON or
accepting custody. Bind envelope install/generation to verified signature fields,
check body text digest, expiry and local thread binding, and deduplicate event id
before acknowledging. Mail cannot change consent, permissions or a human gate.

The canonical-vector test uses body `abc` (SHA-256
`ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad`), timestamp
`1794249600000`, nonce `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`, key `ik1`, install
`install1`, generation `1`, direction `install-to-service`, path `/v1/mail/send`.

## Enrollment and generation ownership (unit 7)

`registrations` is service-authoritative. There is no public management endpoint
in this unit. Enrollment alone writes install id, opaque address local part,
verified exact owner address, active consent, positive generation, install
public key/key id and proven route fields. `schema.sql` contains public keys,
never install private keys or a fleet of symmetric secrets. Address reservations
are never reassigned; preserve registration/revocation tombstones on deletion.

Unit 7 must validate `address_local` as 1..31 lowercase ASCII characters matching
`[a-z0-9][a-z0-9._-]{0,30}`. v1 reply tokens are exactly 32 lowercase hex
characters (16 random bytes), never 26..64 variable-length tokens. The complete
`address_local + '+' + reply_token + '@' + MAIL_DOMAIN` must also pass the same
strict `parseAddrSpec` mailbox parser used for inbound authentication: local part
at most 64 characters, whole address at most 254, dot-atom local and DNS domain.
The 31+1+32 boundary makes a 64-character local part. Length alone is insufficient
(consecutive dots and a long domain can still fail). The registration schema
enforces 1..31 characters; enrollment must enforce syntax and the full mailbox
before activation. Outbound repeats the full validation before reserving an
intent/thread, spending send capacity or calling the provider. Legacy or corrupt
rows fail visibly with HTTP 503 and no send; never silently rename an address.
This undeployed schema candidate is not a migration of an existing service;
unit 7 must check compatibility before applying a schema to any populated D1.
Unit 3's broader token-extraction grammar does not expand this enrollment contract.

The route is an **exact origin plus path**, proof-bound to this install and
generation: `route_origin`, `route_path`, `route_generation`, `route_verified=1`.
Unit 7 must obtain existing tunnel identity/provisioning evidence server-side;
a callback string, tunnel hostname or matching domain suffix is insufficient.
No compatible tunnel/proof means route pending. No automatic provisioning.
Routes must be canonical public HTTPS origins without userinfo, explicit port,
query, fragment or path. Basic IP/local-address checks add a refusal layer but
are not ownership proof. Fetch uses `redirect:'manual'`; no redirect is followed.
No arbitrary route registration API, tunnel-policy change or dashboard-auth
bypass is supplied here. Actual tunnel reachability and ownership remain launch
gates until units 4/7 provide their evidence.

Revocation/disable/owner change/key change advances the generation and removes
active consent under enrollment's serialization. Old generation requests cannot
send or receive newly queued execution. Replay reconciles old events to `revoked`
and purges their content; re-enabling a later generation cannot release them.
Already in-flight delivery may arrive; unit 4's serialized local consent check
is mandatory before custody and again before turn submission. Do not promise
network cancellation retroactively undoes an accepted send or completed turn.
Service-key rotation requires deliberate repinning through trusted enrollment;
unknown key ids fail closed. This unit never remotely replaces an install pin.

## Outbound API

Signed `/v1/mail/send` body contains **only**:

```json
{"intent_id":"opaque-id","thread_id":"opaque-thread","reply_token":"0123456789abcdef0123456789abcdef","subject":"Project / conversation","body":"A note. Reply to continue this conversation.","kind":"note"}
```

`kind` is `note|question`; subject is 1-200 UTF-8 bytes without controls, body is
nonempty plain text at most 16 KiB without NUL. IDs match the signing id syntax.
The HTTP byte bound is 128 KiB to accommodate JSON escaping of a valid body;
it does not increase the decoded-text bound. `request-body.js` bounds the entire
HTTP upload to 10 seconds (not an idle timeout reset by each chunk), returning
408 `body_timeout` or 413 `body_limit`, cancelling the stream without waiting
for an unresponsive producer. These refusals consume no nonce or protected
per-install HTTP allowance. A retry still uses a fresh nonce and the original
immutable intent/event ids.
The install creates a random 128-bit lowercase-hex reply token and durably binds
thread/project/session/native history/question revision **before** calling this
API. Each transport thread has one outbound intent; several such threads can
bind the same local conversation. A new intent cannot mutate an existing thread.
Worker does not receive project/session ids or question bodies as routing data.

Worker derives recipient from the verified owner, From from service config,
Reply-To from opaque address plus token, and Message-ID from SHA-256 of
`install:generation:intent` plus service domain. Subject is UTF-8 base64 encoded
in folded encoded words of at most 75 characters without splitting codepoints;
body is base64 MIME text/plain. No client recipient, CC/BCC, From, raw MIME,
Message-ID, callback or cross-install registration field is accepted.

D1 transaction reserves immutable intent/body digest, hashed token, thread and
outbound Message-ID before any provider write. Exact retries return the existing
result; changed bytes under an existing intent return `intent_conflict`. A
collision rolls back both records. Provider call success yields `accepted`, which
means transport acceptance only. A thrown provider call yields `uncertain`.
A crash/lost durable receipt leaves `sending`, reconciled to `uncertain` after
60 seconds. Neither is automatically resent. The original in-flight attempt
may resolve its own `uncertain` row to `accepted` on a positive later provider
receipt, without resending. Operator review/reconciliation UX belongs to later
units; no retry endpoint can override uncertainty here.
Outbound content and plaintext tokens are not stored at the Worker.

Cloudflare's supported raw `EmailMessage` send binding preserves authoritative
MIME headers. `worker.js` loads it only on the production send seam; synthetic
tests inject a recording function. No test can accidentally invoke Cloudflare.

## Ingress and custody

Email handler requires both feature enablement and `MAIL_INGRESS_VERIFIED=true`.
The latter is a **launch gate**, not proof derived from a setting. Unit 3's
real Cloudflare ingress/authentication fixtures remain unverified. Bound actual
stream bytes to 64 KiB even if `rawSize` lies; bound ordered raw header count and
length. The raw ordered Authentication-Results list must agree with the separate
`message.headers` surface; unit 3 still enforces Cloudflare claimant position,
exact owner, DMARC with aligned SPF or DKIM, token, generation and outbound
Message-ID correlation. Missing/unsupported provenance refuses. Automatic mail,
outgoing copies and null-envelope bounces are suppressed. No forwarding or
automatic replies, no fetching external MIME content.

A unit-3 `lookup_error` still refuses identity, but denotes an operational thread
lookup failure. The ingress adapter propagates it through the existing sanitized
`agent_mail_custody_failed` path, without `setReject`, a rejection audit record
or an event. An unknown token still follows the ordinary permanent refusal path.
This distinction does not promise Cloudflare will retry a failed invocation.

Unit 3 strips MIME before persistence: no HTML fallback, attachments, image or
calendar bytes enter D1 or the install. Command-like plain text remains untrusted;
unit 5, never this transport, performs toolless injection screening. No raw MIME
or sender addresses are copied to rejection logs, events or exception output.

Stable event id = lowercase SHA-256 of compact JSON array
`[install_id,generation,thread_id,incoming_message_id]` (generation is a JSON
number here). Incoming Message-ID is strict and angle brackets removed. D1
deduplicates that authenticated tuple, compares the stripped-text SHA-256, and
keeps the first accepted body on conflicting reuse with visible rejection
metadata in both the prior-row and racing-insert paths. Distinct incoming ids
with equal text remain separate events. A missing
or malformed incoming id yields a non-deliverable `held` event keyed by a raw
digest sentinel; it cannot shadow a valid id. Holds block later receipt order.

Delivery body (the exact persisted JSON bytes are retried): `version` numeric 1,
`event_id`, `install_id`, `generation`, `thread_id`, `incoming_message_id`, `text`,
`body_digest` (SHA-256 of UTF-8 text), bounded unit-3 `auth` evidence,
`received_at` and `expires_at` in epoch milliseconds. There are no callback,
project/session or permission fields supplied by incoming mail.

Body-free held-event variant: `version` numeric **2** replaces `text` with
`reason`, currently only `empty_body`. Every other v1 field remains required;
`body_digest` must be SHA-256 of zero bytes. The exact schema admits no body,
text, reply text or arbitrary additional fields, even if empty or validly signed.
Unknown versions/reasons refuse custody. This is an envelope version; the
Ed25519 signing header/canonical contract remains v1 and binds **every** envelope
field through its exact-byte request digest. `held-envelope.js` owns the Worker
projection; `ingress.js` only selects it after authentication and successful
stripping leave no text (including empty plain/signed, image-only or attachment-only
mail). Failed MIME/HTML extraction still refuses at the edge.

The Worker queues and replays this variant through the ordinary pending/delivery
path; an authenticated empty message spends owner ingress allowance but is not
an SMTP rejection. The local receiver atomically inserts a `blocked` event with
reason `empty_body`, body NULL and length zero before acknowledging custody.
Owner status can read its reason through the existing event metadata APIs; the
owner UI remains unit 7. The hold persists across restart, blocks later submission
in that conversation, and follows existing expiry/revocation/retention rules.
It cannot be released into laundering because no content exists; owner rejection
or eventual expiry can unblock the queue. No model receives this variant. Missing
or invalid incoming Message-IDs retain the existing Worker-only hold policy.

Only the following **HTTP 200** receipt transfers custody:

```json
{"custody":"durable","event_id":"same-event","body_digest":"same-text-digest","install_id":"same-install","generation":1}
```

The HTTPS route is proven and no redirects are followed; unit 4 emits this
receipt only after its authenticated durable insert/deduplication commit. Worker
reads at most 2048 receipt bytes and checks every binding. A generic 2xx, dashboard
HTML, redirect, mismatched receipt, timeout, 5xx or lost acknowledgment retains D1
text. After receipt, state is `local_received` and text is NULL; that is neither
turn submission nor proof the agent read or completed anything.

## Local receiver and enrollment handoff (unit 4)

The branch candidate registers `POST /api/agent-mail/ingress` through
`mc/blueprints/agent_mail_routes.py`. It returns 404 unless
`agent_mail_ingress_enabled` is literal `true`; the absent default is disabled.
There is no new Settings control, consent endpoint, remote pin update, key
creation or live configuration change. Existing application/dashboard auth gates
still run before this blueprint. Unit 7 must prove the actual tunnel can reach
this mail-authenticated route without loosening those gates. Registration alone
is not tunnel readiness.

`mc/agent_mail_signing.py` implements the v1 signature contract with the existing
`cryptography` dependency. `mc/agent_mail_envelope.py` owns the strict delivery
schema; `mc/agent_mail_ingress.py` owns immutable mappings, pins and receive.
`mc/agent_mail_ingress_schema.py` adds schema version 2 to the existing dedicated
unit-1 SQLite database. The original token-based API is preserved. Version-1
inboxes migrate transactionally without guessing transport bindings; version-2
tables and constraints are validated, never silently recreated after damage.
The receiver refuses a missing database rather than starting an empty inbox.
The data file and its WAL/SHM siblings are ignored by Git.

Trusted local enrollment/outbound integration must perform these steps before
calling `/v1/mail/send`:

1. Unit 7 creates/reopens the dedicated store and establishes current human
   consent and generation using the unit-1 registration lifecycle.
2. `MailIngress(store).pin_service(ServicePin(install_id, generation, key_id,
   public_key, route_path))` stores the pinned service **public** key and exact
   receiver pathname. Same-value retries are idempotent; changing a pin/path
   requires revocation and a new generation. A signed Worker request cannot
   create or replace a pin. No private key is stored here.
3. Existing `store.bind_thread(...)` records the authoritative local project and
   session. `MailIngress(store).bind_transport(install_id, generation, thread_id,
   thread_token)` then durably binds the opaque transport thread id to that
   existing token hash. It refuses rebinding and cross-registration/generation
   targets. One transport thread maps to one token hash. Plaintext tokens are
   not retained; incoming mail cannot choose a project or session.
4. Persist the remaining outbound/native-history/question/permission metadata
   required by the parent spec in the owning integration unit, then send. There
   is no reconstruction from subjects, old tokens or a guessed conversation.

Receive first bounds the upload to 128 KiB, checks JSON content type and exact
original pathname (WSGI must supply `RAW_URI` or `REQUEST_URI`), then verifies
the exact bytes against the enrolled pin before parsing JSON. The decoded text
limit remains 16 KiB. Extra/duplicate JSON keys, non-integral generations/times,
wrong event identity, invalid/missing Message-ID, invalid auth evidence, text
digest mismatch and expired events refuse custody. The permitted event lifetime
cannot exceed the local configured queue TTL (provisional default 30 days).
Worker events with missing identifiers stay held at the Worker; they cannot be
promoted through this receiver. Plain text, including command-shaped text, enters
only the unlaundered `local_received` inbox state. This unit never submits it to
a model, transcript, tool, pending question or follow-up.

After crypto, one `BEGIN IMMEDIATE` transaction rechecks current consent,
generation, pin and live thread binding, claims the durable nonce, checks time
again, and inserts the event and immutable delivery receipt. It shares unit-1
revocation's SQLite serialization. A refusal/storage failure rolls back the
nonce and insert together. A revocation that wins prevents custody; one that
follows custody purges the queued content. Clock/expiry checks also run before
and after commit. An expiry or lost response after commit emits no custody
receipt; a fresh signature/nonce for the original event can recover it.

An exact persisted-envelope retry with a fresh nonce returns the same bound
custody receipt after commit and creates no second event. Changed bytes under
an existing event id refuse with `event_conflict` and preserve the first body.
The local unit-1 bounded-body digest and the wire UTF-8 text SHA-256 retain
their separate definitions; the receipt uses the wire digest. Receipt tombstones
last through the original Worker event expiry, including after local event
metadata purge, so replay cannot resurrect a completed turn. Nonce claims last
through `timestamp+300001` ms and survive reopen/process races.

Only success returns the exact HTTP-200 receipt above. Queue full or unavailable
storage returns 503; authentication/schema/binding/replay refusal returns 401;
invalid HTTP framing/path/content type returns 400 and oversize upload 413.
No refusal returns custody. Responses carry `Cache-Control: no-store`, and
refusal logs contain closed reason codes only, never text, owner addresses,
keys, tokens, raw MIME or signed headers. Upload wall-clock timeouts remain the
hosting WSGI/tunnel responsibility and require actual-route verification.

Synthetic coverage lives in `tests/test_agent_mail_ingress.py`: actual Worker
MIME/auth/strip output and Worker signing into Python custody, signature/body
tampering, timestamp boundaries, replay/reopen, concurrent receivers and
revocation, queue full, corrupt schemas, migration rollback and lost receipts.
Units 5/6 own laundering, injection screening, lifecycle wake and exact-session
submission. Unit 7 owns human enrollment, route proof, key/consent lifecycle,
outbound wiring and owner-facing status/recovery/deletion. Service pin/thread
metadata retention/deletion and cleanup scheduling must be wired there; no
enabled service is claimed by these internal Python seams. The Worker shared
breaker P2, real Cloudflare ingress provenance and all live pilot gates remain.

## Recorded-event laundering adapter (unit 5)

`mc/agent_mail_launder.py` exposes `MailLaunder(store, enabled=False)` as a
trusted Python integration seam, with no new route, scheduler or live setting.
Only literal `enabled=True` allows `process_next()` to claim one already recorded
event. Its return contains only event id, state and reason; ready text remains
inside the dedicated inbox. No wake, provider submission, question consumption,
outbound send or enrollment is performed. Units 6/7 own that integration.

`mc/agent_mail_screen.py` conservatively holds command/tool syntax, impersonation,
permission/approval requests, residual HTML, controls and invisible Unicode.
Screening covers the whole input, including quoted/forwarded sections. It never
silently removes or truncates those bytes; ambiguous everyday command words can
therefore require owner review. This detector is not a complete injection defense.

`mc/agent_mail_extract.py` uses the shared `claude_mail_transform()` in
`mc/mail_launder.py`, backed by the verified isolated Claude text transform.
Other providers fail closed. Legacy decision-digest callers keep their schema;
their prompt no longer assumes an installation-specific owner identity. Child
exceptions/errors are sanitized to constant codes, never echoed mail bodies.
No real model calls are part of this unit's synthetic verification.

The new extractor accepts exactly one bounded JSON object, false boolean
ambiguity/failure/truncation/instruction flags, and literal ordered reply/quote/
forward segments covering the entire input. Exactly one fresh reply segment
must match `reply_text`. Excluded quoted/forwarded bytes never enter the ready
envelope. Notes and free-text questions use the same extraction, with **no**
invented yes/no decision. The stored envelope has version, source, event id,
warning, reply text and quote/forward-presence flags; it remains untrusted data,
never approval or authority to change permissions or bypass a human gate.

Unavailable runtime, timeout, malformed/oversize output, ambiguity and flags
become visible durable holds without raw fallback. The call deadline fits within
the persisted lease. Finalization shares the store write transaction with
consent/generation/thread/expiry/lease revalidation; a late result cannot make a
revoked or expired claim ready. Existing crash/backoff recovery is bounded;
explicit hold release does not reset attempts. Body-free v2 holds are never
claimed, sent to a model or submitted.

Dave selected the body-free local-custody variant on 2026-11-09. Cross-language
tests exercise real Worker MIME strip, D1 queue, replay signing and Python
custody for empty plain and multipart/signed messages; tampered held bytes,
validly signed held envelopes containing text, and unknown envelope versions
refuse without recording a nonce or event. The original body-bearing custody
test now surfaces Node child stderr on failure.

Verification from the checkout root (installation launcher path/interpreter may
be supplied absolutely when running from a worktree):

```text
python tools/test-run.py --project mission_control --port 5199 -- python -m pytest -o addopts= tests/test_agent_mail_launder.py tests/test_mail_launder.py tests/test_agent_mail_store.py tests/test_agent_mail_ingress.py tests/test_agent_mail_held.py -q --tb=short
python tools/test-run.py --project mission_control --port 5199 -- pyright mc/agent_mail_screen.py mc/agent_mail_extract.py mc/agent_mail_launder.py mc/mail_launder.py mc/agent_mail_envelope.py mc/agent_mail_ingress.py
```

From `workers/agent-mail`, use the installation's absolute launcher path with
`--project mission_control --port 5199 --shell powershell --command "npm test"`
on Windows (or the corresponding explicit shell on other platforms). The
shell preserves the existing npm script exactly; no guessed test expansion.
The completed candidate passed 330 selected pytest cases, 741 Worker cases and
pyright with zero errors/warnings/informations on all six touched `mc` modules.
Launcher progress capture reported gaps; these counts are framework results,
not a claim of complete dashboard telemetry or live-provider verification.

## D1 ledger, replay, bounds and visibility

Dedicated database; apply `schema.sql` before enabling. No automatic reset or
schema creation on runtime errors. SQL uniqueness, transactional batches and
lease-id compare-and-set protect competing Worker instances. Events progress
`pending -> delivering -> local_received`; side states are `held`, `expired`,
`revoked`. Lease lasts 60 seconds; an expired delivery is safely retried with the
same id because the local durable receiver deduplicates. A late receipt cannot
settle a replacement lease. Retry backoff doubles from 2 seconds to one hour.
Fetch timeout is 10 seconds. Fresh per-delivery time/nonce/signature prevents a
long replay batch from using stale signed timestamps.

Each scheduled pass selects at most 50 earliest eligible installation heads.
Conservatively serialize the entire installation because only unit 4 knows the
local conversation map. A pending/delivering/held earlier event blocks later
mail on that install, preserving conversation order. Blocked heads cannot fill
the 50-row candidate page and starve other installs. An authenticated
`POST /v1/mail/reconcile` with exact body `{}` also requests recovery for that
install; it cannot supply a route or select another install. Install recovery
must call it after reconnect/restart. Configure scheduled replay as well; an
in-memory timer or only the email invocation is insufficient.

Provisional D4 policy (code defaults, **no live enablement**): 30-day queued/held
text; 90-day thread; delete Worker text on local custody; retain terminal metadata
30 days. Intent tombstones live 90+30 days so a thread still in its valid lifetime
cannot be resent after its intent disappears. Expiry changes state/reason and
purges text visibly. Revocation purges content and retains terminal metadata.
Nonce claims expire only after the signature window. Rejections retain time and
constant reason only for 30 days, capped at 10,000 detailed rows with a durable
per-day overflow count (also 30 days). No silent truncation or success claim.

Additional conservative **candidate capacity brakes**, subject to pilot capacity
review: 100 queued/held bodies per install, 10,000 globally; 20 sends/install/day,
1000 sends/day globally; 200 authenticated inbound attempts/install/day;
200 authenticated-endpoint attempts/install/day, shared by send and reconcile.
The inbound `ingress:<install_id>` allowance is reserved only after `decide()`
accepts the owner, provider authentication, token and Message-ID correlation.
Every normal SMTP refusal after a known recipient lookup also charges the separate
`ingress-refused:<install_id>` accounting bucket, capped at 200/day. A saturated
refusal bucket never gates owner mail or changes the refusal reason; the existing
rejection audit/overflow records still apply. Unknown recipients create no
per-install accounting bucket. Authenticated attempts that later fail MIME/text
checks still spend owner allowance; duplicate authenticated deliveries do too.
The unchanged **provisional shared global ingress breaker** (`ingress:global`,
10,000 attempts/day) runs before recipient lookup and authentication. Stranger
and owner traffic share it: exhausting it can still refuse genuine owner mail.
Separating the per-install buckets does not establish global abuse isolation or
pilot capacity. This shared limit must be assessed before enabling real mail.

For HTTP send/reconcile, only a verified signature with a successful durable nonce
claim consumes the protected allowance. Invalid traffic uses a separate global 10,000-refusals/minute
bucket, never a client-supplied install id; beyond it, failed authentication gets
429 `abuse_limit` instead of 401. That bucket is consulted only for failed
authentication, so exhausting it cannot block an authenticated send or recovery.
This bounds refusal accounting, not pre-authentication crypto/D1 work; edge abuse
protection and capacity still require pilot validation. `MAIL_ENABLED=false` is the
global circuit breaker. Limits use atomic D1 reservations; racing attempts cannot
overshoot. A failed reservation may consume rate capacity but cannot send.
Full queue/failed D1 insertion propagates an explicit custody failure and creates
no false receipt. Cloudflare's upstream retry after a failed handler is **not**
claimed lossless; capacity/storage monitoring and the real pilot must cover it.
Verification-abuse quotas belong to unit 7, local model-work quotas to units 4-6.

Read state/reason/terminal timestamps and rejection/overflow counters through the
service's operational D1 tooling for now. No public status/debug endpoint exposes
owner, tokens or body. Owner-facing hold/recovery/deletion controls remain an
enrollment/local-integration deliverable; no enabled user-facing service is
claimed complete by this library/transport unit.

## Synthetic validation and outstanding launch gates

Unit-4 candidate validation (2026-11-09), from the branch worktree root:

```powershell
python tools/test-run.py --project mission_control --port 5199 -- python -m pytest -o addopts= tests/test_agent_mail_store.py tests/test_agent_mail_ingress.py -q
pyright mc/agent_mail_store.py mc/agent_mail_ingress_schema.py mc/agent_mail_signing.py mc/agent_mail_envelope.py mc/agent_mail_ingress.py mc/blueprints/agent_mail_routes.py
```

Result: **151 passed, 0 failed, exit 0** (unit 1's 59 cases and 92 new ingress
cases); pyright **0 errors, 0 warnings, exit 0** across all six changed/new
modules. Whole Worker `npm test`, through the exact launcher command below from
`workers/agent-mail`, also passed **738/738, 0 failed/skipped/cancelled, exit 0**,
covering unit 3 plus transport regressions. An earlier Python verification run
reported unavailable transport/parent/pytest progress capture; final framework
counts and child exit codes are direct test output, not inferred telemetry.
No frontend code changed, so browser smokes are not applicable. No deployed
receiver, signed request through the real tunnel or real mail is claimed.

`test/transport-support.js` uses Node 24.11+ built-in SQLite for actual schema,
constraints, transactions, leases and reopen tests; no new package is needed.
WebCrypto generates disposable synthetic mail keys. Tests cover signed-field
tampering, replay, rotation/pinning, cross-install/generation refusal, owner-only
sends, quotas, provider ambiguity, durable correlation, MIME custody, restart,
leases, lost ack, order, pending routes, redirect refusal, revocation, expiry and
bounded metadata. Run the **whole** existing worker suite through the repository
test launcher, cwd `workers/agent-mail`, with exact shell command `npm test`.

Round-3 candidate validation: 2026-11-09, whole suite **738 passed, 0 failed,
0 skipped/cancelled/todo, exit 0** (724 prior cases + 14 round-3 cases). Exact
command, cwd `workers/agent-mail`:

```powershell
python tools/test-run.py --project mission_control --port 5199 --shell powershell --command 'npm test'
```

The targeted `node --test test/transport-round3.test.js` selection passed 14/14,
exit 0, through the same launcher. Counts are Node cases, not dashboard assertions.
No capture warning appeared in either returned output. Whole-suite tool output
was truncated in the middle; its complete result summary was inspected. No full
stdout artifact is claimed.

`test/transport-round3.test.js` ports Fenn's round-2
`_scratch/mc1166-u2-review-a08e979a/regression-probes.test.mjs:8` attack and adjacent
positive control unchanged in substance: 200 public email-handler submissions,
stranger's own-domain provider-auth fixture, guessed token and outbound id, then
a real fixture owner reply. Imports are relocated and the pre-reply bucket
diagnostic is null-safe because no protected row now exists. Added assertions
check separate refusal counts, no SMTP rejection and one retained event.
Controls verify the owner's 201st authenticated attempt is refused, saturated
refusal accounting cannot gate owner mail or suppress audit, and post-lookup
authentication/correlation/header/loop/MIME/empty/malformed/inactive refusals
are accounted separately. Unknown recipients create no per-install bucket.
These are synthetic handler/SQLite tests; the shared global breaker is unchanged.

Historical round-2 candidate validation: 2026-11-09, whole suite **724 passed, 0 failed,
0 skipped/cancelled/todo, exit 0** (707 baseline + 17 round-2 cases). Exact command,
cwd `workers/agent-mail`:

```powershell
python tools/test-run.py --project mission_control --port 5199 --shell powershell --command 'npm test'
```

The targeted `node --test test/transport-round2.test.js` selection also passed
17/17, exit 0, through the same launcher. Launcher progress
capture reported `transport_unavailable_progress_incomplete` and
`parent_unavailable_progress_incomplete`; these counts are Node's test cases,
not dashboard assertions. No Python/static edits require pyright or boot smoke.

`test/transport-round2.test.js` ports all five Fenn failing probes from the main
checkout's `_scratch/mc1166-u2-review-92849c37/probes.test.mjs`, plus its three
controls. Relative fixture/module imports are relocated to the real test directory.
The quota probe also runs for reconcile and checks no protected bucket was spent;
lookup failure adds sanitized-error/no-rejection/no-event and retry assertions;
conflict adds retained-first-body assertions. The 32-character probe necessarily
changes its expected outcome from a successful send to schema refusal, then
HTTP 503 before reservation/provider when a test-only CHECK bypass models an
old-schema row. Keeping its original unconditional HTTP-200 assertion would
contradict the required registration bound. The 31-character round-trip control
is retained. Delayed-upload/expired-nonce cleanup and forced racing SELECT
interleavings are unchanged; these are deterministic synthetic reproductions,
not live provider evidence. Other cases cover exhausted abuse quota isolation,
authenticated quota sharing, crypto/nonce expiry, stalled/oversize upload
cancellation, malformed dot syntax and whole-address overflow.

Not verified: actual Cloudflare runtime/bindings, account capacity, namespace and
domain configuration, provider ingress headers, receiver through the actual tunnel, enrollment/tunnel
proof, durable local follow-up or completed resumed turn. Unit 7 must wire consent,
deletion and key lifecycle; unit 4 supplies synthetic-tested custody/consent while
recovery-triggered remote reconciliation remains an integration requirement;
units 5/6 own laundering and exact-session submission. Privacy publication,
destination verification, DNS/DKIM, Cloudflare deployment, real mail and a pilot
remain separately human-gated. Disabled template is configuration documentation,
not evidence any account or live configuration was changed.

Primary interfaces checked 2026-11-09:
[send bindings](https://developers.cloudflare.com/email-service/configuration/send-bindings/),
[raw EmailMessage support](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/),
[email handler / ingress results](https://developers.cloudflare.com/email-service/api/route-emails/email-handler/),
[D1 atomic batches](https://developers.cloudflare.com/d1/worker-api/d1-database/).
