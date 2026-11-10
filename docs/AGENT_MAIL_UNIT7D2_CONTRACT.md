# MC-1166 unit 7d-2a: delivery-authority implementation contract (option A)

Status: **frozen draft for Dave's review; nothing here is built.** Written against
`master` at `cf8002a0` (7d-1 merged), branch `clayrune/mc1166-u7d2a-contract`.
Doc only: no key generated, no credential created, no deploy, no config change,
no code change. Every "today" claim cites `file:line` at that base.

Inputs: [7d preflight](AGENT_MAIL_UNIT7D_PREFLIGHT.md) (Dave chose **A**, 2026-11-10),
[7d-1 decisions](AGENT_MAIL_UNIT7D1_DECISIONS.md) (remote lifecycle deferred here),
[transport](AGENT_MAIL_TRANSPORT.md). Where the preflight says "proposed", this
document freezes it. Where this document needs a choice the preflight did not
make, it is marked **[D#]**, written against the recommended option, and
listed in section 8. A build unit must not start until Dave has settled the
[D#] items it depends on (dependency column in section 8).

Constants below marked *initial* are tunable without a contract change; every
other value is part of the contract.

---

## 0. Ground truth: what exists today

| Fact | Source |
| --- | --- |
| Delivery-purpose token machinery does not exist. The only mail-purpose ES256 key class is the enrollment-grant key: purpose `clayrune-mail-enrollment`, kid `mail-grant-*`, P-256, no env fallback, no JWKS route. | `control_plane/app/mail_grant_signing.py:12-43` |
| The grant issuer is a pure, injected-reader class that "registers no routes". It cross-checks device, account, tunnel, ingress and DNS before signing a 60 s grant. | `control_plane/app/mail_grants.py:1-4`, `:43-125` (cross-check `:76-109`, claims `:115-120`) |
| The Worker trusts grants only from reviewed config: `jwksUrl === issuer + '/v1/mail-enrollment/jwks'`, never a token-selected URL. That route is not implemented in CP. | `workers/agent-mail/enrollment-grant.js:1-12`; routes list `control_plane/app/main.py:68-71` has no mail router |
| Session JWTs and their JWKS exist and are a different trust set: `GET /v1/jwks` serves every session key; issuer `https://api.<zone>`, audience `clayrune-dashboard`, 30 min. | `control_plane/app/routes_auth.py:71-81`, `sessions.py:66-71`, `:80` |
| CP's ES256 helper is session-specific (`signing_key()` = first active key of one env-loaded set). It must not mint delivery tokens. | `control_plane/app/jwt_es256.py:114-193`, `:216-245` |
| CP has no service-to-service auth. The only machine schemes are the `MC-Device` Ed25519 header and Firebase/operator stubs. | `control_plane/app/auth.py:39-62`, `:84-101` |
| The mail Worker's public surface is signed POST `/v1/mail/send` and `/v1/mail/reconcile` only, and both require `reg.active && reg.owner_verified`. | `workers/agent-mail/worker.js:24-25`, `:36-37` |
| Worker wire signature: Ed25519 over `JSON.stringify(['clayrune-agent-mail', version, direction, key_id, install_id, generation, method, path, timestamp, nonce, digest])`; `direction` is allow-listed to two values; skew 300 s; nonce claim is a durable atomic insert. | `workers/agent-mail/signing.js:25-34`, `:52-77`; `ledger.js:17-21` |
| Replay delivers to `provenRoute(reg)`, which needs `route_verified=1 && route_generation=generation`; a 200 body must carry a durable receipt bound to event, digest, install, generation. Redirects are manual. | `workers/agent-mail/replay.js:7-22`, `:26-47` |
| Enrollment commit creates the registration **inactive**, `route_verified=0`; ownership lease is 300 s; lease/record-version columns exist on `mail_installations`. | `workers/agent-mail/schema.sql:5-12`, `migrations/0001_enrollment.sql:37-70`; `enrollment-grant.js:6` |
| The Worker enrollment core is "deliberately NOT a public HTTP handler"; there is no begin/confirm/complete route. | `workers/agent-mail/enrollment.js:1-2`, `worker.js:24` |
| Flask ingress is a fixed POST route, flag default off, bounded raw read (`MAX_REQUEST_BYTES = 128 KiB`), raw-URI exact match, then `MailIngress.receive`. | `mc/blueprints/agent_mail_routes.py:19`, `:44-72`; `mc/agent_mail_envelope.py:11` |
| `receive` needs an installed pin, verifies the signature with `direction == 'service-to-install'` only, requires current consent, a prebound thread, and a durable nonce claim. | `mc/agent_mail_ingress.py:88-128`, `:28-31`, `:140-147`; `mc/agent_mail_signing.py:63-80` |
| `Enrollment.enable` needs fresh ownership, confirmation, manifest freshness and `Prerequisites.ready()`; `machine_custody` defaults False. It then flips `enabled`, installs the pin, sets stage `active`. `disable` bumps `epoch`. | `mc/agent_mail_enrollment.py:27-33`, `:320-343`, `:345-359` (epoch `:357`) |
| Remote lifecycle is stubbed: every remote op raises `remote_adapter_unavailable`; unresolved revocations are retained; no ack is ever recorded. | `mc/agent_mail_remote.py:17-39`; `mc/agent_mail_recovery.py:127-138` |
| The adapter contract already demands: `cancel_flow` durably fences delayed begin/verify, returns the real assignment, is idempotent even after revocation, and needs **no key/vault access**. | `mc/agent_mail_enrollment_revocations.py:18-28` (vault clause `:24`) |
| Local lifecycle schema is checked as an exact set: every table `LIKE 'mail_enrollment_%'` must equal the 4 expected names, version marker must be `2`. New tables therefore must **not** use the `mail_enrollment_` prefix. | `mc/agent_mail_enrollment_state.py:18-31` |
| A tunneled request reaches Flask over loopback and is exempt from the LAN gate, so the edge, not Flask, is what keeps strangers off every non-mail route. | `mc/core.py:297-303`; preflight "What exists" table |

Two gaps found while reading, both outside the five asked-for items:

* **G1: the human-ceremony transport does not exist.** There is no CP route for
  `MailGrantIssuer.issue`, no `/v1/mail-enrollment/jwks`, no Worker HTTP wrapper
  for begin/confirm/complete, and `begin`/`verify` on the Flask adapter are
  `UnavailableRemote` (`mc/agent_mail_remote.py:30-34`). Sections 1-5 below do
  not depend on it for design or for synthetic tests, but **nothing can go live
  without it**. See **[D5]**.
* **G2: the edge Worker is not in this repo.** It lives in the sibling
  `clayrune-cloud` checkout (preflight "What exists"). This contract therefore
  puts every edge decision in a pure module authored and tested here, with the
  sibling edge reduced to glue. See **[D3]**.

---

## 1. Delivery capability

### 1.1 Trust anchors

| Item | Value |
| --- | --- |
| Issuer (`iss`) | Exactly `sessions.issuer()` (`https://api.<zone>`, `sessions.py:66-67`). Edge and Worker pin it in reviewed config; nothing is read from a token. |
| JWKS origin | `GET <issuer>/v1/mail-delivery/jwks`. A **new route on a new router**, never `/v1/jwks`, so the session trust set and the delivery trust set cannot be confused. Same shape and cache header (`public, max-age=600`) as `routes_auth.py:71-81`. Verifiers still receive the key set from reviewed config/cache; a token cannot name a JWKS URL (same rule as `enrollment-grant.js:10-12`). |
| Signing key | A **new** purpose key class `MailDeliveryKey` in new file `control_plane/app/mail_delivery_signing.py`, modeled on `mail_grant_signing.py:17-43`: P-256 only, kid `^mail-delivery-[A-Za-z0-9_-]{1,100}$`, purpose constant `clayrune-mail-delivery`, **no env fallback, no generation code, no sharing with the grant key or the session keys**. `sign()` refuses claims whose `aud`/`token_use` are not the purpose. JWT header is exactly `{alg:'ES256', typ:'clayrune-mail-delivery+jwt', kid}`. |
| Rotation | Same discipline as `jwt_es256.py:13-21`: publish the new key, wait one 10-minute cache TTL, then sign with it; keep the old key published for one token lifetime (60 s) plus skew, then drop. Because tokens live 60 s, the operational window is 12 minutes end to end. |
| Provisioning (Ron-gated, not done here) | Generating the key, loading it into CP as an explicit secret (`CLAYRUNE_MAIL_DELIVERY_KEYS`, JSON array like the existing one, no ephemeral dev fallback), and publishing the public key to edge/Worker trust config. |

### 1.2 Claim grammar (exact; unknown or missing claim = refuse)

Delivery capability, `aud == token_use == 'clayrune-mail-delivery'`:

| Claim | Rule |
| --- | --- |
| `iss` | `== trust.issuer` |
| `aud`, `token_use` | both `clayrune-mail-delivery` |
| `sub` | CP `user_id` (`^[A-Za-z0-9_-]{1,128}$`); edge checks it against the KV denylist key `u:{sub}` (`control_plane/app/denylist.py:47-48`) |
| `jti` | 32 hex, unique per issuance |
| `iat`, `nbf` | integers, `iat == nbf`, not in the future (no leeway) |
| `exp` | `exp > now`, **`exp - iat <= 60`** (matches the grant ceiling, `enrollment-grant.js:30`), and `exp <= floor(checked_at_ms/1000) + 300` (never outlives the ownership lease, `OWNERSHIP_LEASE_MS`, `enrollment-grant.js:6`) |
| `mode` | `mail` or `proof` (see section 4); fixes the only `x-mail-direction` the edge will accept |
| `install_id`, `generation` | as in the mail registration; `generation` is the decimal string of the registration generation **after** enrollment commit (`expected_generation + 1`, `enrollment.js:139`, `0001_enrollment.sql:66-70`) |
| `device_id` | CP device row id |
| `route_origin` | `'https://' + device.hostname_claim`, **derived by CP from its own device row**, never from the caller (cf. `mail_grants.py:87`) |
| `route_path`, `method` | constants `/api/agent-mail/ingress`, `POST` |
| `req_digest` | lowercase hex SHA-256 of the exact request body; must equal request header `x-mail-digest` |
| `mail_nonce` | 32 hex; must equal request header `x-mail-nonce` |
| `mail_key_id` | the registration key id; must equal `x-mail-key-id` |
| `checked_at_ms` | CP read time, taken **before** I/O (`mail_grants.py:58`); `checked_at_ms <= iat*1000 + 999` and `>= iat*1000 - 999` |
| `record_version` | SHA-256 of the five authority version strings (device, account, tunnel, ingress, dns) as at `mail_grants.py:113-114` |
| `binding_jti` | the enrollment-grant `jti` the binding was created from (audit join) |

A capability is **per request**: issuing one for digest X and nonce N cannot be
used with any other body or nonce, because the edge compares claims to headers
and Flask's own mail signature covers the same digest and nonce.

### 1.3 Audience

One audience, `clayrune-mail-delivery`, held by exactly one verifier role: the
**edge** machine-delivery branch. The Worker and Flask never see a capability
as a credential to trust (Flask never receives it, section 3). No other
service accepts this audience; the enrollment grant (`aud clayrune-mail-enrollment`)
and session JWTs (`aud clayrune-dashboard`) are refused by the verifier on
`aud`, `typ`, and kid pattern, and the delivery key cannot sign them
(`MailDeliveryKey.sign` purpose check).

### 1.4 Issuance preconditions (CP, every call)

CP issues only if **all** hold at one fresh read:

1. The service-authenticated request (section 2) names `install_id` and
   `generation` for which a CP **binding** exists, state `bound`, with
   `generation` equal to the binding's current generation and no revocation
   floor at or above it.
2. Device row: `revoked_at is null`, `provisioning_state == 'active'`, device
   key unchanged; account `suspended is False`; tunnel/ingress/DNS cross-check
   passes **exactly as** `mail_grants.py:76-109`, **and** the derived
   `route_origin`, `tunnel_uuid`, `device_id`, `user_id` equal the binding's.
3. `mode == 'proof'` requires the binding to be in `proof_allowed` (always true
   while bound and not revoked); `mode == 'mail'` additionally requires the
   Worker to have asserted `active` (informational only; CP does not trust it
   for authority, it trusts its own binding and fresh reads).
4. Per-install issuance budget not exceeded: *initial* 600/day (3x the ingress
   cap, `ledger.js:6`), plus a global breaker; refusal code `issue_budget`.

Anything unreadable, stale, mismatched or unavailable refuses with a closed
code and **no fallback** to a dashboard credential (preflight A).

To avoid forking 7a, the cross-check block is **extracted** from
`MailGrantIssuer.issue` into `control_plane/app/mail_authority_check.py` and
called by both; `control_plane/tests/test_mail_grants.py` must pass unchanged
before and after (it is the regression gate for the extraction).

### 1.5 Bindings and replay storage

*CP binding* (new Firestore collection `mail_delivery_bindings/{install_id}`,
accessed only through new `control_plane/app/mail_delivery_store.py`, not
appended to `firestore.py`): `generation`, `state` (`bound|revoked`),
`generation_floor`, `user_id`, `device_id`, `tunnel_uuid`, `route_origin`,
`mail_key_id`, `service_key_id`, `service_public_key_sha256`, `grant_jti`,
`r_sha256`, `bound_at_ms`, `revoked_at_ms`. Writes are compare-and-set on
`generation`/`state`.

Binding creation, `POST /v1/mail-delivery/bind` (service-authenticated):
request carries the **enrollment grant JWT and the transcript `R`**. CP
verifies its own grant (signature, claims, `checked_at_ms + 300000 > now`),
recomputes `sha256(canonical_r(R)) == grant.r_sha256`
(`mail_enrollment_transcript.py:35-58`), requires the request's service key to
equal `R.service_key_id` and its public-key hash to equal
`R.service_public_key_sha256`, sets `generation = R.expected_generation + 1`,
and is idempotent on `grant_jti`. A second different grant for the same
install and generation is `binding_conflict`. This is how CP learns the
authority state without trusting an unverified registration from the caller.

*Replay storage.*

* **Edge: none.** A capability is bound to `(install, generation, nonce, digest)`;
  replaying it requires replaying the request, which Flask rejects through its
  durable nonce claim (`mc/agent_mail_ingress.py:140-147`, 300 s retention via
  `SKEW_MS`). Stateless edge verification is therefore sufficient, and we avoid
  adding Durable Objects to the edge.
* **CP: `mail_delivery_issuances/{install}.{generation}.{nonce}`**, created with
  create-if-absent (precedent: `firestore.nonce_consume`,
  `firestore.py:132-183`); stores `req_digest`, `jti`, `mode`, TTL 10 min. A
  second issuance for the same triple with a different digest is
  `nonce_reused`; same digest re-signs (a retry after a lost response).

---

## 2. Service-to-service auth: mail service to CP issuance

Today there is none (`auth.py:39-62`). **[D1]** Recommended and contracted:
**Ed25519 request signature by the existing mail-service key**, reusing the
Worker's canonical-array scheme (`signing.js:29-34`) with a new `direction`
value.

| Item | Contract |
| --- | --- |
| Caller | The agent-mail Worker only. |
| Endpoints (new router `control_plane/app/routes_mail_delivery.py`, prefix `/v1/mail-delivery`, mounted like `main.py:68-71`, **default off** via `CLAYRUNE_MAIL_DELIVERY_ENABLED`, off = 404) | `GET /jwks` (public), `POST /bind`, `POST /issue`, `POST /lease`, `POST /revoke`. All POSTs: `application/json`, <= 16 KiB, no query. |
| Signature | Headers `x-mail-{version,direction,key-id,install-id,generation,timestamp,nonce,digest,signature}` as in `signing.js:44-45`. `direction = 'service-to-cp'`, `method = 'POST'`, `path` = the exact CP path. Install/generation in the signed array are the install and generation the call is about. |
| Verifier | New `control_plane/app/mail_service_auth.py`: `key_id`/public key must match an entry of config `CLAYRUNE_MAIL_SERVICE_KEYS` (JSON array `[{key_id, public_key_b64u}]`, reviewed deployment config; **never** accepted from the request); skew 300 s; digest of the body; algorithm Ed25519 only. |
| Replay | CP durable nonce claim, create-if-absent doc `mail_service_nonces/{key_id}.{nonce}`, TTL skew+1 s (precedent `firestore.py:132-183`). Claim only after the signature verifies (`signing.js:49-50` rule). |
| Rate | Unauthenticated refusals share one bounded bucket (do not lock out valid callers), as `worker.js:42-47`. |
| What it is not | Not a Cloudflare Access token (app-wide, rejected in the preflight), not a dashboard credential, not the device key. A compromise of the Worker private key already allows forging service-to-install mail, so this adds no new key class; it does add a new **capability** for that key (see risk in section 8). |

CP answers over its pinned HTTPS origin; the Worker pins `issuer` from config
and does not follow redirects.

---

## 3. Edge policy

The edge decision is one pure function, authored here as
`workers/agent-mail/edge-policy.js` (plus `delivery-capability.js` for token
verification), vendored byte-for-byte into the sibling edge Worker **[D3]**.
The edge Worker only (a) selects the machine branch, (b) calls the function,
(c) proxies the result. The cookie/session branch is untouched.

Branch selection: **iff** header `X-Clayrune-Mail-Delivery` is present. If it
is present, the request is machine-branch only: no `cr_session` is read, no
signin redirect is ever issued, and a refusal is a bare JSON 401/403/400. If it
is absent, behaviour is exactly today's.

Allow-list (all must hold, evaluated on the **raw request target** before any
decode or normalisation, mirroring Flask's `RAW_URI` check at
`agent_mail_routes.py:51-53`):

| Dimension | Rule |
| --- | --- |
| Method | `POST` only |
| Host | equals `host(claims.route_origin)`; no port, no userinfo |
| Raw target | byte-identical to `/api/agent-mail/ingress`. Reject any `%`, `?`, `#`, `;`, `\`, `//`, `/./`, `/../`, trailing slash, case variant, or non-ASCII |
| Body | `Content-Length` present, integer, `<= 131072` (`MAX_REQUEST_BYTES`, `mc/agent_mail_envelope.py:11`); no `Transfer-Encoding`; no `Expect` |
| Content type | exactly `application/json` |
| Upgrade | reject `Upgrade`, `Connection: upgrade`, `HTTP/2 CONNECT`, WebSocket handshake headers |
| Headers vs claims | `x-mail-install-id == install_id`, `x-mail-generation == generation`, `x-mail-nonce == mail_nonce`, `x-mail-digest == req_digest`, `x-mail-key-id == mail_key_id`; `x-mail-direction` is `service-to-install` iff `mode == 'mail'`, `service-to-install-proof` iff `mode == 'proof'` |
| Capability | verified per section 1.2/1.3; kid in pinned set; `exp - iat <= 60`; denylist `u:{sub}` absent; otherwise refuse. No entitlement lookup (CP already checked account state at issuance) |
| One credential | cookie flow and capability together: refuse |
| Cross-install / generation | covered by the claim/header equalities and host binding; a capability for install A never admits install B's host |

The edge does **not** hash the body: digest equality is checked
header-to-claim, and Flask checks body-to-header-to-signature
(`agent_mail_signing.py:76-80`). A body swapped under a valid capability fails
there; a header swapped fails at the edge.

Credential stripping before proxy: delete `X-Clayrune-Mail-Delivery`,
`Cookie`, `Authorization`, every `X-Clayrune-*`, every `Cf-Access-*`, and add no
identity header (the existing `forwardRequest` already strips platform cookies
and client `X-Clayrune-*` assertions; the machine branch must strip explicitly
so it does not depend on that). The Flask side never receives or evaluates a
capability.

Responses: status and body pass through, bounded; `Set-Cookie` is dropped; a 3xx
from origin is converted to 502 so a login page can never look like a receipt
(the Worker also treats non-200 or non-receipt bodies as no custody,
`replay.js:34-38`).

**Access-installs dependency (stay pending).** An install still fronted by
Cloudflare Access cannot be reached by this branch: Access answers with a
redirect/login before the edge Worker's logic. Contract: the Worker records
`route_pending` (existing behaviour, `replay.js:23`) and proof mode returns
`edge_unreachable`; `machine_custody` stays False; **no Access change, cutover,
or activation is authorised.** The unblock is the parked JWT/Access cutover
(MC-948) or an independently reviewed bridge, neither of which is in 7d-2.

Reversal: remove the edge branch (header-selected, so removal changes nothing
for the cookie flow) and withdraw the key from trust config; mail stays off.

---

## 4. Proof mode on the fixed ingress route

Purpose: prove machine custody (the edge branch reaches this Flask, the
mail key is held, the manifest-pinned service key is the sender) **before**
`enable()` pins anything, without creating mail, a thread, a wake, or an
activation counter. Today ordinary delivery cannot run first: `receive` needs
an installed pin and a prebound thread (`mc/agent_mail_ingress.py:96-101`,
`:113-118`), and `enable()` needs `machine_custody` first
(`mc/agent_mail_enrollment.py:331-332`).

### 4.1 Wire discrimination

Proof requests use the same URL and POST, and are separated **only** by a
signed header value: `x-mail-direction: service-to-install-proof`. The ordinary
verifier hard-refuses any direction but `service-to-install`
(`agent_mail_signing.py:70`), so a proof request can never be ingested as mail;
and the route dispatch checks direction **before** looking up a pin. This
adds two `direction` values to the Worker allow-list (`signing.js:26`) and a
`verify_proof` in the new Flask module (the ordinary `verify` is not edited).

Full `direction` vocabulary after 7d-2 (each is a distinct signature domain):

| Direction | Signer | Use |
| --- | --- | --- |
| `install-to-service` | install mail key | existing send/reconcile |
| `service-to-install` | service key | existing delivery |
| `service-to-install-proof` | service key | proof challenge delivery |
| `install-to-service-lifecycle` | install mail key | status, renew, proof-arm, activate (section 5) |
| `service-to-install-response` | service key | signed lifecycle responses |
| `service-to-cp` | service key | CP calls (section 2) |

### 4.2 Arm (human-gated, local)

New human endpoint `POST /api/agent-mail/enrollment/proof` on the **existing**
lifecycle blueprint (passcode via `_require_human_passcode`, blocking-sessions
check, same flag, `agent_mail_enrollment_routes.py:44-60`; add `proof` to
`ENABLE_ACTIONS` `:19`). Preconditions: stage `delivery_pending` or `active`
with confirmed review, ownership unexpired, manifest fresh
(`_fresh_candidate`, `agent_mail_enrollment.py:312-318`). Arm writes one row in
new local table `mail_delivery_proof` (prefix chosen to avoid the exact-set
check at `agent_mail_enrollment_state.py:21-31`; own version marker table
`mail_delivery_version`):

`flow, epoch, install, generation, challenge (64 hex), proof_id (32 hex),
manifest_sequence, manifest_digest, service_key_id, service_public_key_sha256,
mail_key_id, route_origin, owner_address_sha256, ownership_expires_ms,
armed_ms, expires_ms, state ('armed'|'proved'|'invalidated'|'consumed'),
receipt_digest`.

`expires_ms = min(armed + 10 min, ownership_expires_ms + 0)`; one armed row per
install+generation (replacing an armed row invalidates it). Arm **never**
installs a pin, sets `enabled`, or changes `stage`. It then tells the Worker
through the lifecycle `proof-arm` call (section 5.3), a mail-key-signed
outbound request that needs no tunnel.

### 4.3 Service sends the proof

Worker (cron or after `proof-arm`), for an install with `owner_verified=1`,
`active=0`:

1. Build body, exact JSON keys:
   `{kind:'enrollment-proof', version:'1', challenge, install_id, generation, manifest_sequence, service_key_id, service_public_key_sha256, mail_key_id, route_origin, owner_address_sha256, service_nonce}`
   (`service_nonce` = 32 random hex chosen by the Worker for freshness).
2. Sign with direction `service-to-install-proof`, path `/api/agent-mail/ingress`.
3. Ask CP `POST /issue {mode:'proof', req_digest, mail_nonce, ...}` (section 2).
4. `fetch(route_origin + path, {redirect:'manual', 10 s timeout})` with
   `X-Clayrune-Mail-Delivery`. The route URL comes from the registration row's
   `route_origin`, but for proof it is **additionally** compared to the
   capability's CP-derived `route_origin` before sending; mismatch aborts
   (`route_mismatch`). (Proof is allowed while `route_verified=0`; this branch
   is the only place that may use an unverified route, and only with a proof
   body.)

### 4.4 Flask validates and commits the receipt

`mc/agent_mail_delivery_proof.py::receive_proof(headers, raw)`:

1. Reuse the same raw-path, size, JSON bounds as the route
   (`agent_mail_routes.py:46-61`).
2. Look up the **armed** row by `install_id`; refuse unless `state` is `armed`
   (or `proved` for an idempotent retry of the same `challenge`),
   `now < expires_ms`, and `epoch`, manifest sequence/digest, service key id and
   key hash still equal current evidence (`_fresh_candidate` semantics).
3. Verify the Ed25519 signature against the **manifest candidate's** service key
   (`candidate_from_evidence`, `agent_mail_enrollment.py:104-106`), **not** an
   installed pin; skew 300 s; body digest equals header.
4. Every body field equals the armed row; unknown keys refuse.
5. In **one** `store._tx()`: claim the nonce (same table and rule as
   `_claim_nonce`, `agent_mail_ingress.py:140-147`), re-run `_fresh_candidate`,
   re-check epoch, write `state='proved'` and `receipt_digest`. A crash before
   commit changes nothing; after commit a retry returns the same receipt.
6. Produce the receipt: mail-key Ed25519 signature (via the seal, vault
   unlocked, `agent_mail_enrollment_keys.py:110-114`) over the canonical array
   `['clayrune-agent-mail-proof','1',install,generation,challenge,service_nonce,route_origin,manifest_sequence,proof_id]`,
   returned as `{custody:'proof', install_id, generation, challenge, proof_id, signature}`.
   Vault locked: respond `503 vault_locked`, commit nothing; the Worker retries
   with backoff. **Never unlock automatically.** **[D6]**
7. It does **not**: create a mail event, bind a thread, call extraction, wake a
   session, touch `registrations.enabled`, install a pin, or consume consent.

### 4.5 Worker verifies and stores

Worker verifies `signature` with `registrations.public_key` (the mail key
committed in enrollment, `0001_enrollment.sql:69`) over the same array,
checks every field, and writes (new migration, table `mail_delivery_proofs`)
`install_id, generation, challenge_sha256, receipt_digest, verified_ms,
expires_ms, state('verified'|'invalidated')`. Its own `expires_ms =
min(verified + 10 min, ownership_lease_until)` is stored, not inferred.

### 4.6 Ack: when Flask treats custody as proven

`Prerequisites.machine_custody` becomes True **only when both** hold at the
moment of the check:

* local `mail_delivery_proof.state == 'proved'`, unexpired, epoch/manifest/
  generation current; **and**
* a **signed Worker status response** (section 5.3) reports
  `proof.state == 'verified'` with `receipt_digest` equal to the local one,
  unexpired.

A bare HTTP 200, edge health, login HTML, redirect, or a Worker "status"
without the service signature never sets it. The projection is computed by a
cached read of those two records and passed as the `prerequisites` callable
(`agent_mail_factory.py:102-108`); it is read **outside** the `enable()`
transaction (the existing `enable()` calls `prerequisites()` inside it,
`agent_mail_enrollment.py:331-332`, so the build unit must change `enable()` to
read the cached projection, never perform network I/O there).

### 4.7 Expiry and revocation

Proof is invalidated (local row and Worker row) on: epoch change (`disable`,
`agent_mail_enrollment.py:357`), identity/owner/key/repin change, manifest
sequence or digest change, generation change, `expires_ms`, or a CP binding
revocation. Local invalidation happens inside the same transaction as the
change that causes it. A consumed proof (see 4.8) cannot be reused.

### 4.8 Activation ordering and crash reconciliation

Order, all keyed by `(install, generation, epoch)`:

1. `proved` + Worker-verified (4.6) -> human `enable()` runs unchanged except
   the `machine_custody` source. Local commit: `enabled=1`, pin installed,
   stage `active`, **and** a `mail_delivery_activation` row
   `(install, generation, epoch, state='pending')` in the same transaction;
   proof row -> `consumed`.
2. Supervisor tick sends lifecycle `activate` (mail-key signed) carrying
   `receipt_digest`. Worker verifies a current verified proof for exactly that
   generation, then in one D1 statement sets `active=1, route_verified=1,
   route_generation=generation` (the fields `provenRoute` and `worker.js:36`
   need) and consumes the proof. Signed response -> local activation row `acked`.

Why Flask first: a pin without Worker activity delivers nothing (harmless);
Worker activity without a pin yields `pin_required` 401s and retained custody
(`agent_mail_ingress.py:100-101`), also harmless but noisy.

Crash matrix:

| Crash point | Recovery |
| --- | --- |
| After local enable, before `activate` call | activation row `pending`; next tick retries (idempotent) |
| Worker activated, ack lost | retry returns the same signed `activated`; Worker never double-applies |
| Flask disabled mid-activation | `disable` queues revocation (`:345-359`); a later `activate` for that generation is refused by the Worker fence (5.2) |
| Worker restart | all state in D1; nothing in memory |
| Local store lost | out of scope here (held store, 7b); Worker stays `active` until revoke by fence token |

No activation counter exists before both proof and consent (preflight).

---

## 5. Remote lifecycle protocol

### 5.1 Auth classes

| Class | Authority | Used for |
| --- | --- | --- |
| **install-auth** | Ed25519 by the install **mail key**, direction `install-to-service-lifecycle`; Worker requires `owner_verified=1`, does **not** require `active` | `status`, `renew`, `proof-arm`, `activate` |
| **fence-auth** | a **fence token** held in the local plain store; Worker holds only its SHA-256 **[D2]** | `cancel`, `revoke`; works with a locked/lost vault, as `revocations.py:24` requires |

Never the device key (preflight rule 3). Requests are bounded JSON (<= 2 KiB),
no query, manual redirects, 10 s timeout, a separate Worker rate bucket
`lifecycle:{install}` (*initial* 500/day, so recovery never starves the 200/day
send/reconcile budget, `worker.js:50`, `ledger.js:6`). Responses are signed by
the service key (`service-to-install-response`, digest of response body, nonce =
request nonce, path = request path) and verified by Flask against the manifest
candidate key; an unsigned or mismatched response is a failure, never an ack.

### 5.2 Worker routes (new file `workers/agent-mail/lifecycle.js`; `worker.js` gets one import and one path table)

All `POST`, exact path, no query, `MAIL_ENABLED` and a new `MAIL_LIFECYCLE_ENABLED` var (default false) both required.

| Path | Body | Behaviour / idempotency key |
| --- | --- | --- |
| `/v1/mail/lifecycle/cancel` | `{flow_id, fence_token}` | fence-auth. If the flow exists, token must hash to its `fence_digest`; if it does not exist, first writer tombstones `(flow_id, sha256(token))`. Writes `mail_lifecycle_fences`; a later `begin`/`confirm`/`complete` for that flow, and the commit trigger itself, refuse (the 0001 trigger is recreated with `AND NOT EXISTS(fence for NEW.flow_id)`, closing the late-request race inside the single D1 statement, cf. `0001_enrollment.sql:37-54`). Returns `{flow_id, state:'cancelled', assignment: null | {install_id, generation}}`; `assignment.generation = expected_generation + 1`. Same key -> same answer forever, including after revoke. |
| `/v1/mail/lifecycle/revoke` | `{install_id, generation, fence_token}` | fence-auth, exact generation. `registration.generation == G`: set `active=0, route_verified=0`, invalidate proofs, record fence, return `revoked`. `registration.generation > G`: `already_superseded` (success). `< G` or none: record a fence so a later commit for that flow refuses; return `fenced`. Never deactivates a newer generation. |
| `/v1/mail/lifecycle/status` | `{install_id, generation}` | install-auth. Read-only signed snapshot: `{registration:{active,owner_verified,route_verified}, lease_until_ms, proof:{state,receipt_digest,expires_ms}, fences:[...], cp_revoke_pending}`. |
| `/v1/mail/lifecycle/renew` | `{install_id, generation}` | install-auth. See 5.4. |
| `/v1/mail/lifecycle/proof-arm` | `{install_id, generation, challenge, expires_ms}` | install-auth. Stores the challenge the Worker will embed (4.3); one per install+generation; replaces and invalidates the previous one. |
| `/v1/mail/lifecycle/activate` | `{install_id, generation, receipt_digest}` | install-auth. See 4.8. |

New Worker tables (migration `0002_lifecycle.sql`, `0003` for the trigger
change if kept separate): `mail_lifecycle_fences`, `mail_delivery_proofs`, and
`fence_digest` on `mail_enrollment_flows` (set at `begin`, which gains the
allow-listed field `fence_digest` in `enrollment.js:37-39`).

### 5.3 Flask client and adapters

* `mc/agent_mail_service_client.py` (new): fixed-origin client (origin from the
  manifest-verified candidate, `agent_mail_enrollment.py:104-106`, never from a
  request or an agent), exact paths, signing through the seal, response
  verification, typed closed error codes. Implements `RevocationAdapter`
  (`cancel_flow`, `revoke`) plus `status`, `renew`, `proof_arm`, `activate`.
* `mc/agent_mail_remote.py` keeps `UnavailableRemote` as the default; the real
  adapter is selected only when its config flag, a verified manifest and
  `MAIL_LIFECYCLE_ENABLED` evidence are all present. An absent or failing
  adapter still raises the closed `remote_adapter_unavailable` and never
  returns success (`agent_mail_remote.py:17-39`; recovery's
  `isinstance(remote, UnavailableRemote)` guard `agent_mail_recovery.py:131-133`
  stays).
* **Fence token (D2).** New local table `mail_lifecycle_fence(flow PRIMARY KEY,
  token TEXT NOT NULL)` (prefix **not** `mail_enrollment_`), created in the
  transaction that creates the flow row and before `remote_started=1`
  (`agent_mail_enrollment.py:210`), so any begin can be cancelled. Token = 32
  random bytes, sent to the Worker once (inside `begin`, as `fence_digest`).
* **Generation mapping.** `PendingIdentity.generation` and the local row
  generation are the **post-commit** registration generation
  (`transcript.expected_generation + 1`). Fixing this removes the 7b ambiguity
  where `revoke(install, generation)` could target the pre-commit number.

### 5.4 Renewal

What renews: the 300 s ownership lease (`ownership_expires_ms`), which gates
`enable()` (`agent_mail_enrollment.py:325-328`) and shows as `ownership_expired`
in recovery (`agent_mail_recovery.py:152`).

1. Supervisor decides (recovery step 3, new `_renew`): stage in
   (`delivery_pending`, `active`), epoch unchanged, confirmed, not disabled,
   store healthy, vault unlocked, remaining lease < 150 s, last attempt
   >= 120 s ago (backoff doubles to 1 h on failure). `delivery_pending` renews
   only within 24 h of the original ownership verify (*initial*), so an
   abandoned pending enrollment cannot renew forever.
2. Flask -> Worker `renew` (install-auth).
3. Worker -> CP `POST /v1/mail-delivery/lease`: CP performs the section 1.4
   authority read against the binding, and answers
   `{checked_at_ms, record_version, lease_until_ms = checked_at_ms + 300000}`.
   Worker updates `mail_installations.ownership_checked_at/lease_until/record_version`
   **forward-only** (`WHERE ownership_checked_at < ?`), never lowering.
4. Worker returns a signed `{install_id, generation, expires_at_ms}`. Flask
   builds the expected `OwnershipReceipt` from its own row exactly as
   `verify()` does (`agent_mail_enrollment.py:296-302`), requires
   `now < expires_at_ms <= now + 300000`, and updates `ownership_expires_ms`
   in a transaction that re-checks epoch, stage and manifest. It never changes
   stage, flow, key, owner, or consent. Failure/expiry leaves the old lease,
   which then expires and blocks sends (7d-1 behaviour).

### 5.5 Ack semantics and lost acks

* The **only** thing that records an acknowledgment locally is a verified,
  service-signed response whose fields equal the request's scope. HTTP status,
  silence, a timeout, or an unsigned/mismatched body records nothing
  (`remote_cancel_retryable`, `remote_cancel_scope_mismatch` stay).
* Every operation is idempotent on its key (`flow` for cancel;
  `install+generation` for revoke/activate/renew/proof-arm). A retry after a
  lost ack returns the **same** result. The local `done`/`resolved` columns
  (`agent_mail_enrollment_revocations.py:43-90`) flip only on that ack.
* Lost `cancel` ack: row stays `done=0`; no new generation may enable
  (preflight recovery step 2). Lost `revoke` ack: same. Lost `activate` ack:
  activation row stays `pending`, retried. Lost `renew` ack: lease not
  extended, retried; Worker-side extension is harmless.
* Worker -> CP revoke on deactivation: Worker deactivates first (this is what
  stops delivery), acks Flask, then calls CP `/revoke {install, generation}`
  with retry from cron (`cp_revoke_pending` in `status`). A lingering CP binding
  is bounded by the 60 s capability life and the denial of Worker delivery.
* Backoff: 30 s doubling to 1 h, per work item; one failing item never blocks
  others (7d-1 supervisor contract).

---

## 6. Local and Worker storage summary

| Store | New objects | Why separate |
| --- | --- | --- |
| Flask SQLite (outside-repo store, 7b) | `mail_delivery_version`, `mail_delivery_proof`, `mail_delivery_activation`, `mail_lifecycle_fence` | avoid `mail_enrollment_%` exact-set check (`agent_mail_enrollment_state.py:21-31`); own version marker; no new sidecar under `data/projects/` |
| Worker D1 | `mail_delivery_proofs`, `mail_lifecycle_fences`, `fence_digest` column, recreated commit trigger | migrations after `0001_enrollment.sql`, never positional edits to transport tables |
| CP Firestore | `mail_delivery_bindings`, `mail_delivery_issuances`, `mail_service_nonces` | one module `mail_delivery_store.py` |

---

## 7. Build split (3 units)

Sizing is an **estimate** of input tokens (files read + written + test runs,
roughly 12 tokens per source line, 2-3 review/fix passes), not a measurement.
Units are sequenced b -> c -> d; the cancel/fence slice of d can start after b.
Run application and CP tests as separate children (their `tests.conftest`
collide). Default-off flags remain off in every unit; synthetic keys/clocks/
SQLite only; no live call.

### Unit 7d-2b: CP issuance + verifier  (~3.5M)

Files:
* `control_plane/app/mail_delivery_signing.py` (new)
* `control_plane/app/mail_authority_check.py` (new, extracted) and `mail_grants.py` (call it; no behaviour change)
* `control_plane/app/mail_delivery_authority.py` (new: bind/issue/lease/revoke)
* `control_plane/app/mail_service_auth.py` (new)
* `control_plane/app/mail_delivery_store.py` (new, Firestore)
* `control_plane/app/routes_mail_delivery.py` (new) + 2-line include in `main.py`
* `control_plane/api_spec.yaml` (new paths; the spec has no mail entries today)
* `workers/agent-mail/delivery-capability.js` (new pure verifier) + golden vectors `workers/agent-mail/test/fixtures/delivery-vectors.json`
* `workers/agent-mail/cp-client.js` (new, Worker -> CP signed calls)

Tests: `control_plane/tests/test_mail_delivery_signing.py`, `_authority.py`,
`_service_auth.py`, `_routes.py`; `workers/agent-mail/test/delivery-capability.test.js`,
`cp-client.test.js`. Must prove: wrong issuer/aud/typ/kid/key purpose, every
claim mutation, 61 s lifetime, lease overrun, wrong host/path/method/digest/
nonce/generation, replay, revoked binding, key rotation window, cross-purpose
signing refusals, the `test_mail_grants.py` suite unchanged, and Python-signed
vectors verified by the JS verifier (the only cross-language gate).

### Unit 7d-2c: edge policy + Flask proof mode + Worker proof sender  (~4.5M)

Files:
* `workers/agent-mail/edge-policy.js` (new) + `test/edge-policy.test.js`
* `workers/agent-mail/signing.js` (add 4 directions), `replay.js` (attach capability on mail mode; no behaviour change otherwise)
* `workers/agent-mail/delivery-proof.js` (new), `migrations/0002_delivery_proof.sql`
* `mc/agent_mail_delivery_proof.py` (new: arm/receive/invalidate/projection), `mc/agent_mail_delivery_schema.py` (new)
* `mc/blueprints/agent_mail_routes.py` (direction dispatch, import + call only), `mc/blueprints/agent_mail_enrollment_routes.py` (`proof` action)
* `mc/agent_mail_enrollment.py` (`enable()` reads cached projection; consume proof; write activation row; `disable`/identity changes invalidate proof)
* **Sibling edge glue** (`clayrune-cloud/edge-worker`): vendor the two pure modules, add the header-selected branch. **Separate dispatch in the `clayrune_cloud` project; deploy is Ron-gated; this unit ships only the vendored files, a hash manifest, and the test harness here.**

Tests: `tests/test_agent_mail_u7d2_proof.py`, `workers/agent-mail/test/delivery-proof.test.js`,
`edge-policy.test.js` (every reject row in section 3 including encoded aliases,
query, upgrade, chunked, oversize, cross-install, both credentials), the
existing ingress/enrollment/enable suites unchanged, plus: proof cannot create a
mail event/thread/wake/pin; challenge replay, expiry, epoch/manifest/generation
change; lost ack converges; vault-locked 503; edge health/login HTML/redirect
never set `machine_custody`; `enable()` does no I/O inside its transaction.

### Unit 7d-2d: remote lifecycle adapters  (~4.5M)

Files:
* `mc/agent_mail_service_client.py` (new), `mc/agent_mail_remote.py` (real adapter selectable; `Unavailable*` stays default)
* `mc/agent_mail_lifecycle_fence.py` (new: fence-token table + queue integration)
* `mc/agent_mail_recovery.py` (`_revocations` real path, new `_renew`, new `_activation`; step order unchanged)
* `workers/agent-mail/lifecycle.js` (new), `worker.js` (one import, one path table), `enrollment.js` (`fence_digest` on `begin`), `migrations/0003_lifecycle.sql` (fences, recreated trigger)
* `workers/agent-mail/response-signing.js` (new, signed lifecycle responses)

Tests: `tests/test_agent_mail_u7d2_lifecycle.py` (extends the 7d-1 recovery
fixtures, `tests/test_agent_mail_u7d1_recovery.py`), `workers/agent-mail/test/lifecycle.test.js`.
Must prove: cancel before begin tombstones and later begin/commit refuse;
cancel idempotent after revoke; revoke exact-generation never touches a newer
generation; commit-vs-cancel race via the D1 trigger; lost-ack replay at every
boundary; unsigned/mismatched response records nothing; vault locked cancel
still works; renewal forward-only, backoff, 24 h pending cap; activation crash
matrix of 4.8; no new generation enables while any revocation `done=0`.

### What none of these units do

No key generation, credential creation, CP/Worker/edge deploy, Access change,
vault unlock, real mail, or flag flip. G1 (ceremony transport) is **not** in any
of these units.

---

## 8. Decisions for Dave

| # | Question | Options (recommendation first) | Blocks |
| --- | --- | --- | --- |
| **D1** | Service-to-CP auth | **(a) Ed25519 by the existing mail-service key, pinned in CP config (contracted above).** Reuses signing code and key custody; adds a new capability to that key, so a Worker-key compromise can also mint capabilities for already-bound installs (bounded by CP binding + 60 s life + per-install budget). (b) Cloudflare Access service token in front of CP: another credential class and rotation path, app-wide scope per the preflight. (c) Cloud Run IAM ID token: strong, but couples CP to a Google identity for a Cloudflare Worker and needs a bridge. | 7d-2b |
| **D2** | Vault-less fence authority for `cancel`/`revoke` | **(a) Fence token in the local plain store, digest at Worker (contracted).** Needs a local table and a Worker column; local-store compromise already controls consent. (b) Mail-key signature: simplest, but a locked/lost vault could then never cancel, contradicting `agent_mail_enrollment_revocations.py:24`. (c) Device key: forbidden by the preflight. | 7d-2d |
| **D3** | How edge logic reaches the sibling repo | **(a) Pure modules authored+tested here, vendored byte-identical with a hash manifest (contracted).** One source of truth, testable without the sibling. (b) Author in the sibling, copy golden vectors back: edge tests live where the edge lives, cross-repo drift risk. (c) Publish an npm package: cleanest, new release/supply-chain surface. | 7d-2c |
| **D5** | G1: ceremony transport (CP grant route + `/v1/mail-enrollment/jwks`, Worker enrollment HTTP, Flask `begin/verify`) | **(a) A separate contract+build unit 7d-2e before any live run (recommended).** It has its own auth surface (flow-secret, human code) and its own review. (b) Fold into 7d-2d: one fewer unit, but 7d-2d exceeds the 5M budget and mixes human-ceremony auth with background lifecycle auth. | live activation only; 2b-2d proceed on synthetic transports |
| **D6** | Proof receipt signing time | **(a) Sign in the request path with the vault unlocked, `503 vault_locked` otherwise (contracted):** the signature covers the Worker's fresh `service_nonce`. (b) Pre-sign at arm time: no vault in the request path, but the receipt proves custody only of Flask's own challenge, a weaker freshness proof. | 7d-2c |

(D4 was settled in-document: separate table prefixes, forced by
`agent_mail_enrollment_state.py:21-31`; it is not a choice.)

---

## 9. Acceptance gates for the contract itself

The three build units are bound to: section 1.2 claim grammar, 3 reject table,
4.8 crash matrix, 5.2 route table, 5.5 ack rules and the per-unit test lists.
A reviewer may reject a unit for any divergence; a divergence that is actually
better goes back to this document first.

Not covered here (carried unchanged from the preflight): the trusted
caller-attribution gate, D1 runtime/upgrade checks, real delivery capacity,
privacy disclosures, root/key provisioning, and exact real-mail continuation
remain independent pre-live gates. 7e owns UI. A synthetic green suite does not
clear them.

## 10. Validation of this document

Source reading only; **no test suite, command against a live system, or
network call was run.** Cited files read at base `cf8002a0`. README,
USER_GUIDE, CHANGELOG, AGENTS and rules unchanged: unadopted internal contract,
no shipped behaviour.
