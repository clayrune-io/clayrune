# MC-1166 unit 7d-2e: human-ceremony transport (implementation contract)

Status: **draft contract, doc only.** Nothing here is built. Dave picked option (a) for D5
in `docs/AGENT_MAIL_UNIT7D2_CONTRACT.md` (separate unit before any live run). Author: Tobin,
2026-11-10, on `clayrune/mc1166-u7d2e-contract` (off `f6f67a20`, which contains 7d-2r `8753e45f`).

This document closes gap **G1** of the 7d-2 contract: the begin/confirm/complete ceremony that
turns a local enrollment flow into a Worker-side ownership record has cores on both sides and
**no transport between them**. It does not touch the delivery capability (7d-2b), the proof mode
(7d-2c) or the lifecycle routes (7d-2d), all merged.

Everything marked "(verified)" was read in this worktree while writing; every file:line below
was opened. Where something does **not** exist it says so (section 0.2).

---

## 0. What exists, what does not

### 0.1 Verified present

| Fact | Where |
| --- | --- |
| Worker `Enrollment` core: `begin(value, secret)`, `confirm(flowId, secret, code)`, `complete({flowId, secret, token, deviceSignature, mailSignature})`. Header says "deliberately NOT a public HTTP handler". Flow auth is `sha256(flow_secret)` stored at begin (`human_flow_required` otherwise). | `workers/agent-mail/enrollment.js:1-2`, `:30-36`, `:38`, `:93`, `:111` |
| `begin` takes exactly 11 fields (+ optional `fence_digest`), checks the service pin, reserves an identity (capacity 200, 15 min TTL), stores `R`, returns `{transcript, code, state}`. The comment says the adapter must send `code` to the stored owner only. A re-begin of an identical flow returns `{transcript, state}` and **never reissues the code**. | `enrollment.js:38-91`; `enrollment-reservations.js:6-28`; `enrollment-challenge.js:5` |
| `confirm`: 32-hex code, HMAC digest with a 32-byte pepper, 5 failures invalidate the flow in one conditional UPDATE. `complete`: verifies both Ed25519 proofs over `R`, requires `code_confirmed`, verifies the grant JWT, calls the injected read-only `provider(owner)`, commits in one INSERT guarded by D1 triggers. An exact retry (same token digest) returns the original result. | `enrollment.js:93-110`, `:111-152`; `migrations/0001_enrollment.sql:37-79` |
| `complete` returns `{install_id, generation: expected+1, state:'ownership_recorded_delivery_pending', active:false, route_verified:false}`. It does **not** return the ownership lease end. | `enrollment.js:153-154` |
| Grant verifier: ES256, header exactly `{alg,kid,typ:'clayrune-mail-enrollment+jwt'}`, `aud = token_use = 'clayrune-mail-enrollment'`, lifetime <= 60 s, `checked_at_ms` within the `iat` second, 300 s lease, `r_sha256` equals the stored `R`, claims equal `R` fields. Trust object must carry `jwksUrl === issuer + '/v1/mail-enrollment/jwks'`; keys come from reviewed config, never from the token. | `workers/agent-mail/enrollment-grant.js:8-41` |
| Transcript `R`: 20 ASCII fields in a fixed order, canonical JSON array bytes; keys are 32-byte base64url (unpadded, strict round-trip). | `enrollment-transcript.js:3-39` |
| CP grant issuer `MailGrantIssuer.issue(r, device_signature, mail_signature) -> jwt`: default off, checks `service_origin`, `expires_ms` within 900 s, **both** Ed25519 proofs over the canonical `R`, then `check_authority` (7d-2r-backed), then signs with `MailGrantKey`. | `control_plane/app/mail_grants.py:21-69` |
| `check_authority` (shared by grant and delivery) cross-checks device, account, tunnel, ingress, DNS and returns `user_id` + `record_version`. | `control_plane/app/mail_authority_check.py:51-100` |
| `MailGrantKey` (P-256, kid `^mail-grant-...$`, purpose `clayrune-mail-enrollment`, refuses other claims). | `control_plane/app/mail_grant_signing.py:14-43` |
| CP delivery router pattern to copy: flag-default-off `require_enabled` -> 404, `get_deps()` from env, JWKS route with `Cache-Control: public, max-age=600`, closed `_STATUS` code table, bounded body, `RefusalBucket`. Mounted in `main.py:74`. | `control_plane/app/routes_mail_delivery.py:1-175`; `control_plane/app/main.py:29`, `:74` |
| Service-to-CP signed-call verifier and the in-process refusal bucket. | `control_plane/app/mail_service_auth.py:119-165`, `:140` |
| Worker signing grammar: `generation` must match `[1-9][0-9]{0,14}`, six allow-listed directions, ASCII canonical array. `signedReply` signs only 200 bodies under `service-to-install-response`, bound to path and request nonce. | `workers/agent-mail/signing.js:29-41`; `response-signing.js:10-16` |
| Python response verifier `verify_response(response, candidate, install, generation, path, nonce, clock_ms)`, the real `HttpsTransport` (POST only, no redirects, bounded read), and the lifecycle `ServiceClient`. | `mc/agent_mail_service_client.py:68`, `:110`, `:131` |
| `ServiceClient.begin/verify` raise `remote_adapter_unavailable`; so do `UnavailableRemote.begin/verify` (`agent_mail_remote.py:30-34`). | `mc/agent_mail_service_client.py:330-334`; `mc/agent_mail_remote.py:28-34` |
| `Enrollment` calls the adapter in exactly two places: `resume()` -> `adapter.begin(row, flow_secret)` and `verify(code)` -> `adapter.verify(row, flow_secret, code)`, and it demands `PendingIdentity`/`OwnershipReceipt` dataclasses equal to values it derives itself (`pending_scope_mismatch`, `ownership_scope_mismatch`), plus `0 < expires - now <= 300000`. | `mc/agent_mail_enrollment.py:39-68`, `:210-250`, `:291-320` |
| `IdentitySeal.prove(flow, install, generation, key_id, transcript) -> (signature, flow_secret)` and `flow_secret(...)` exist (vault-bound). | `mc/agent_mail_enrollment_keys.py:106-114` |
| `LifecycleFences.digest_of(token)` and `token_for(flow)` exist (7d-2d); `Enrollment.start` creates the token in the flow's transaction. | `mc/agent_mail_lifecycle_fence.py:29-75`; `mc/agent_mail_enrollment.py:174-175` |
| Local keystore holds `device_id`, `device_pub_b64`, `hostname`, and `device_keys.sign(msg)` signs with the device key. It is **proprietary**; `mc/` imports it only lazily inside functions. | `mc_remote/device_keys.py:58-71`, `:306-318`; `mc_remote/config.py:96-108`; `mc_remote/PROPRIETARY.md`; `mc/blueprints/remote_routes.py:720` |
| The CP records `hostname_claim` and `cf_tunnel_uuid` per device. | `control_plane/app/routes_account.py:967-968` |
| Local lifecycle schema is an exact set of `mail_enrollment_%` tables; new local tables must not use that prefix. | `mc/agent_mail_enrollment_state.py:18-31` |

### 0.2 Verified ABSENT (the 7d-2a lesson: do not cite what is not there)

| Missing thing | Evidence | Consequence in this contract |
| --- | --- | --- |
| **CP route for `MailGrantIssuer.issue`.** | `main.py` mounts only `mail_delivery_router` for mail; `grep` for `MailGrantIssuer` outside `mail_grants.py` and tests finds nothing. | New router, section 1. |
| **`GET /v1/mail-enrollment/jwks`.** The Worker verifier *requires* this exact `jwksUrl`. | `enrollment-grant.js:11`; no such route in `control_plane/app`. | Section 1.2. |
| **A grant keyring/loader.** `MailGrantKey` exists, but there is no `parse_grant_keys`/`MailGrantKeyring` (delivery has `parse_delivery_keys`, `mail_delivery_signing.py`). `CLAYRUNE_MAIL_GRANT_JWKS` is only a *verifier-side copy* read by `bind` (`routes_mail_delivery.py:89`). | Section 1.1. |
| **Worker HTTP handler for enrollment.** `worker.js` dispatches only `/v1/mail/send`, `/v1/mail/reconcile` and the lifecycle table. No `Enrollment` is constructed anywhere in `workers/agent-mail/*.js` outside `test/`. | `worker.js:20-61` | Section 2. |
| **A production `provider`** (the read-only "destination is verified" check `complete` needs). Only test doubles exist. | `grep provider workers/agent-mail/*.js` -> only `enrollment.js`; tests inject `async (a) => ({address:a, verified:true})` (`test/enrollment-support.js:60`). | Decision **E4**. |
| **A code-mail sender.** `begin` returns the code to its *caller*; nothing mails it. `outbound.js` only builds registered-owner mail for active installs. | `enrollment.js:89-91`; `outbound.js:33-48` | Section 2.4. |
| **A local source for `tunnel_uuid`.** `R` requires it; the local keystore has `device_id`, `device_pub_b64`, `hostname` but no tunnel id. The attestation reply carries only a tunnel *token*, and the CP can return a `PLACEHOLDER_TOKEN_...` there. | `mc_remote/config.py:96-108`; `control_plane/app/routes_attest.py:209-239` | Decision **E1**. |
| **Production key material.** Factory wires `material=unavailable_material`; a real generation ceremony is a human gate. | `mc/agent_mail_factory.py:108-112`; `mc/agent_mail_remote.py:75-78` | Out of scope, section 7. |
| **Gate on abuse at `begin`.** `begin` is reachable by anyone who can reach the route; `reserveIdentity` admits any `device_id` string until 200 reservations exist. | `enrollment-reservations.js:19-28` | Decision **E2**. |
| **Ownership-lease end in `complete`'s reply**, which `OwnershipReceipt.expires_at_ms` needs. | `enrollment.js:153-154` vs `agent_mail_enrollment.py:49-56`, `:310-311` | Section 2.5 (additive reply field). |

### 0.3 Three traps found while reading (the first two are contracted below; the third is decision E6)

* **Std-base64 vs base64url.** The local keystore stores `device_pub_b64` in *standard* base64 (CP compares `base64.b64decode`, `mail_authority_check.py:73-77`), but `R` requires strict *unpadded base64url* (`enrollment-transcript.js:35`). The Flask side must convert; the mail key is already urlsafe (`mc/agent_mail_enrollment_keys.py:27-28`).
* **Wrong-format codes burn strikes.** The Worker accepts only `^[a-f0-9]{32}$`; anything else is a counted failure and 5 invalidate the flow (`enrollment.js:98-108`). Flask's `verify(code)` accepts `[A-Za-z0-9_-]{1,256}` (`agent_mail_enrollment.py:296`). The adapter must refuse malformed codes locally, before sending.
* **An abandoned post-`begin` flow wedges local revocation (found by scratch test, not yet fixed).** After `begin` the local row is `assigned=1` with the Worker's install and expected generation (`mc/agent_mail_enrollment.py:248`), and `Revocations.queue` copies both into the revocation row (`mc/agent_mail_enrollment_revocations.py:35-41`). The Worker's `cancel` for a flow with **no commit** answers `assignment: null` (`workers/agent-mail/lifecycle.js:82-83`; install/generation are non-null only when a commit exists), but `Revocations.retry` then requires the answer to equal `CancelledAssignment(row.install, row.generation)` and raises `remote_cancel_scope_mismatch` (`mc/agent_mail_enrollment_revocations.py:69-70`). Result: `start -> begin -> disable/abandon -> retry_revocations` never resolves. Reproduced in `_scratch/test_gen_drift.py` with a Worker-shaped fake (cancel of an uncommitted flow returns `None`); not reproduced against the real Worker (no live run). It surfaces only once `begin` is real, i.e. in this unit. Contracted fix: decision **E6**.

---

## 1. Control plane (unit 7d-2e-1)

New files only; no edit to `mail_grants.py`, `mail_delivery_*`, `routes_mail_delivery.py`.

### 1.1 Grant keyring

`control_plane/app/mail_grant_keyring.py` (new), modeled on `mail_delivery_signing.py` (`DeliveryKeyring`, `parse_delivery_keys`, `jwks()`):

* Env `CLAYRUNE_MAIL_GRANT_KEYS`: JSON array of private keys; **no dev fallback, no generation code**, unset/invalid -> `not_configured`. One new name; nothing shares the delivery or session key sets.
* `MailGrantKeyring.signing_key()` returns the single active `MailGrantKey`; `jwks()` returns `{keys:[public_jwk...]}` for every non-retired key (rotation as in 7d-2a 1.1: publish, wait one 10-minute cache TTL, sign; keep the old key published one token lifetime plus skew).
* Existing `CLAYRUNE_MAIL_GRANT_JWKS` (used by `bind`) stays as is. Provisioning step for Ron's checklist, not done here: keep the two in step (a test asserts the keyring's `jwks()` is a superset of what `bind` accepts).

### 1.2 Routes

`control_plane/app/routes_mail_enrollment.py` (new), mounted in `main.py` with **one added import and one `include_router`** line at prefix `/v1/mail-enrollment`. Flag `CLAYRUNE_MAIL_ENROLLMENT_ENABLED` (default off): every route answers **404** and reads nothing when off (same `require_enabled` shape as `routes_mail_delivery.py:105-108`). A second, independent flag is *not* added for the reader: it reuses `CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED` via `authority_reader_from_env(suspended_absent_is_false=True)` exactly as `get_deps()` does (`routes_mail_delivery.py:97-99`); reader absent -> grant answers `503 authority_unavailable`.

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /v1/mail-enrollment/jwks` | none (public) | The key set the Worker pins. `Cache-Control: public, max-age=600`, `503 not_configured` if no keyring. Never `/v1/jwks`, never `/v1/mail-delivery/jwks`: three separate trust sets. |
| `POST /v1/mail-enrollment/grant` | **possession proof only** (below) | `MailGrantIssuer.issue`. |
| `POST /v1/mail-enrollment/preflight` | Worker service signature (`service-to-cp`) | Only if **E2(a)** is chosen: the Worker asks "is this a real, active, non-suspended device on a live tunnel?" before it mails anything. |

**`/grant` request** (JSON, `Content-Type: application/json`, `Content-Length` required, <= 8 KiB, no query string):

```json
{ "r": ["...20 strings in FIELDS order..."], "device_signature": "<b64url 64 bytes>", "mail_signature": "<b64url 64 bytes>" }
```

**Response 200:** `{ "grant": "<ES256 JWT>" }`. Anything else is a closed code `{code, message: code, request_id}` (same envelope as `_err`, `routes_mail_delivery.py:122-124`).

**Auth surface, stated exactly.** There is no session, cookie, bearer or `MC-Device` header on this route. The caller proves *possession of the device private key and the mail private key* by signing the canonical `R`; the issuer already verifies both (`mail_grants.py:43-48`) and then forces the device key to equal the CP device row's key (`device_key_mismatch`, `mail_authority_check.py:73-77`). Knowing a device id, its public key and its hostname (all non-secret) is not enough. `R` is bound to a Worker-issued challenge (`challenge_id`, `challenge_nonce`, `install_id`, `expected_generation`), and the Worker accepts a token only if `r_sha256` equals the `R` it stored, so a grant minted for a fabricated `R` is inert. What this route does *not* authenticate is the human or the mailbox: that is the Worker's code confirmation and provider check (section 2). Documented so nobody mistakes the CP grant for ownership proof.

**Rate and replay.**
* Per-device budget, durable, in a new Firestore collection `mail_enrollment_grant_budget` via a new `control_plane/app/mail_enrollment_store.py` (transaction shape copied from `DeliveryStore._mutate`, `mail_delivery_store.py:46`): initial 20 grants / device / hour, `429 grant_budget`. Charged only after both signatures verify and before `check_authority`, so a stranger cannot spend a real device's budget.
* Unauthenticated refusals (bad JSON/transcript/signature) share one `RefusalBucket` (`mail_service_auth.py:140`) -> `429 abuse_limit`, charged on refusal only.
* **Not idempotent by design:** each call mints a fresh `jti` and a fresh 60 s token. Safety is the Worker's `jti` PK + `flow_id` UNIQUE + `token_digest` match in `mail_enrollment_commits` (`0001_enrollment.sql:37-44`, `enrollment.js:119-123`). The CP stores no grant (no new state besides the budget).

**Failure codes -> HTTP** (additions to the shape of `_STATUS`; every `GrantRefused` reason already exists in `mail_grants.py`/`mail_authority_check.py`):

| Code | Status | Source |
| --- | --- | --- |
| `invalid_json`, `bad_request`, `bad_transcript` | 400 | route; `canonical_r` `ValueError("bad_transcript")` is **not** a `GrantRefused` and must be mapped here (`mail_grants.py:34`) |
| `key_proof_required`, `authority_refused`, `device_unavailable`, `device_key_unavailable`, `device_key_mismatch`, `provisioning_mismatch`, `cloudflare_mapping_mismatch` | 403 | `GrantRefused` |
| `challenge_unavailable` (wrong `service_origin` or `expires_ms` outside (now, now+900 s]) | 409 | `GrantRefused` |
| `grant_budget`, `abuse_limit` | 429 | route |
| `authority_unavailable`, `authority_version_required`, `authority_check_expired`, `mail_grant_disabled`, `not_configured`, `storage_or_configuration_failure` | 503 | `GrantRefused` / route |

`GrantRefused` text is a constant; no row, token or key is ever in a response or log (same rule as `_run`, `routes_mail_delivery.py:160-166`).

**Reuse.** `MailGrantIssuer` and `check_authority` unchanged; `authority_reader_from_env` (7d-2r) unchanged; `RefusalBucket`, `verify_service_call` (preflight only) unchanged. The issuer is constructed with `issuer=sessions.issuer()` and `service_origin` from reviewed config `CLAYRUNE_MAIL_SERVICE_ORIGIN`, `expected_service="http://localhost:5199"` as today.

### 1.3 Tests (CP)

`control_plane/tests/test_mail_enrollment_routes.py`, `test_mail_grant_keyring.py`:
flag off -> 404 on all routes and the reader is never touched; JWKS shape/cache header/no private member; keyring refuses missing env, wrong kid shape, non-P-256, wrong purpose, duplicate kid; `/grant` happy path verified **by the Worker's own `verifyGrant`** (cross-language vector, `test_mail_delivery_crosslang.py` pattern); every `GrantRefused` code maps to its status; `canonical_r` failure -> 400 not 500; one flipped byte in each of device/mail signature -> `key_proof_required`; device key != row -> `device_key_mismatch`; suspended user; expired/long `expires_ms`; budget exhausted; refusals do not spend the device budget; body > 8 KiB / missing length / query string; `test_mail_grants.py` (7a) passes unchanged.

---

## 2. Worker (unit 7d-2e-2)

New files: `workers/agent-mail/enrollment-http.js`, `workers/agent-mail/code-mail.js`, `workers/agent-mail/enrollment-provider.js`. Edits: `worker.js` (one import, one dispatch, mirroring `lifecycle.js`' `isLifecyclePath` hook) and `enrollment.js` (additive, section 2.5 only).

### 2.1 Surface

Flag `MAIL_ENROLLMENT_ENABLED === 'true'` (own flag; `MAIL_ENABLED` must also be true, as for every other path, `worker.js:24`). Off -> `503 {code:'enrollment_disabled'}`.

`POST /v1/mail/enrollment/begin | confirm | complete`, POST only, no query string, JSON, `Content-Length`-bounded (limit 4096 bytes through `readRequestBody`, `request-body.js:3`).

Request headers: `x-mail-nonce` (32 hex, client-chosen, echoed into the response signature). **No `x-mail-install-id`**: no install key exists yet (this is how it gets one).

Construction of the core from reviewed config (all absent -> `503 enrollment_config_required`, never a default):

| `Enrollment` arg | Source |
| --- | --- |
| `serviceOrigin` | `env.MAIL_SERVICE_ORIGIN` |
| `servicePin` `{manifest_sequence, service_key_id, service_public_key_sha256}` | `env.MAIL_SERVICE_PIN` (JSON). Must move in lockstep with the manifest installs trust (`candidate.sequence/key_id/fingerprint`, `agent_mail_service_manifest.py:114`). |
| `trust` `{issuer, serviceOrigin, jwksUrl, jwks}` | `env.MAIL_GRANT_ISSUER`; `jwksUrl` is *derived* as `issuer + '/v1/mail-enrollment/jwks'` (the verifier rejects anything else); `jwks` per **E5** |
| `codeKey` (32-byte pepper) | secret `env.MAIL_CODE_KEY`, base64url, exactly 32 bytes (`enrollment-challenge.js:8`) |
| `provider` | `enrollment-provider.js`, per **E4** |
| `capacity` | existing default 200 (`enrollment.js:12`) |
| `enabled` | `env.MAIL_ENROLLMENT_ENABLED === 'true'` |

### 2.2 Auth surface

* **`begin`:** unauthenticated at the HTTP layer by necessity (no key exists). The whole defence is *what begin is allowed to cause*: a flow row, a reservation, and one code mail to `owner_address`. See **E2** for the abuse control; this contract does not pick it.
* **`confirm`, `complete`:** the **flow secret** (64 lowercase hex, `enrollment-challenge.js:13-16`) in the body. The Worker stores only `sha256(secret)`; a wrong or missing secret -> `human_flow_required` -> HTTP 401 `unauthorized`, charged to the shared refusal bucket, never to an install. `confirm` additionally needs the human code; `complete` additionally needs the dual proofs and the CP grant.
* No dashboard cookie/bearer is read (as `worker.js:49`).

### 2.3 Requests and replies

All replies are **service-signed** with `signedReply` (`response-signing.js:10`): direction `service-to-install-response`, path = the request path, nonce = the request nonce. Ceremony replies have no install yet, so the signed envelope headers carry **`install_id := flow_id`** and **`generation := "1"`** (a constant; `signing.js:31` rejects `"0"`). The true scope rides in the signed body. The Flask side passes the same two values to the unchanged `verify_response`. Only 200 is signed; every other status is an unsigned closed code and is **never** an acknowledgment (`agent_mail_service_client.py:68-73`).

`begin`: `{"value": {<11 fields + optional fence_digest>}, "flow_secret": "<64 hex>"}` ->
`200 {"flow_id", "install_id", "expected_generation", "transcript": [20], "state":"pending", "code_sent": true}`.
**The reply never contains `code`.** The code leaves the Worker only inside the mail in 2.4; returning it would make the mailbox proof void.

`confirm`: `{"flow_id", "flow_secret", "code"}` -> `200 {"flow_id", "confirmed": true|false, "state"}`. `confirmed:false` is a normal 200 (a counted wrong code); the 5th wrong code returns `confirmed:false, state:'invalidated'`.

`complete`: `{"flow_id", "flow_secret", "token", "device_signature", "mail_signature"}` -> `200 {"flow_id", "install_id", "generation", "state":"ownership_recorded_delivery_pending", "active":false, "route_verified":false, "ownership_expires_ms": <int>}`.

### 2.4 Code mail (`code-mail.js`)

After `Enrollment.begin` returns a fresh `code` (state `pending`, `code` present), the wrapper builds an owner-only text mail in the shape of `constructMail` (`outbound.js:33-48`: no client-chosen From/To/body; `From = config.from`, `To = stored owner_address`, base64 body, `Auto-Submitted: auto-generated`, `X-Clayrune-Agent-Mail: 1`) and sends it through the existing `sender(env, mail)` seam (`worker.js:15-21`). Body is fixed template text + the code; the code appears nowhere else (not in logs, not in the HTTP reply, not in the D1 row beyond its HMAC).

Ordering and failure (**pinned**):
1. `begin()` inserts the flow, then the wrapper sends. If `send` throws (including ambiguous acceptance) the wrapper marks the flow `state='invalidated', code_digest=''` and answers `503 code_mail_failed`. A retry then begins a fresh flow. It never retries a send (same rule as `outbound.js:73-75`).
2. A re-`begin` of an identical pending flow returns the transcript with `code_sent:false` and sends **nothing** (the core never reissues, `enrollment.js:79-83`).
3. Send budget: `ledger.limit('enroll-mail:global', ...)` and `ledger.limit('enroll-mail:owner:'+sha256(owner), ...)` (initial 3/day per owner, 200/day global) before the send; exhaustion -> `429 mail_limit`, flow invalidated. `ledger.limit` is the existing helper (`worker.js:41`).
4. A Worker crash between insert and send leaves a `pending` flow with no mail; it expires in 15 minutes or is cleared by the existing fence-auth `cancel` (`lifecycle.js:43-68`).

### 2.5 Change to the core (additive, the only edit to `enrollment.js`)

* `result(f)` returns `ownership_expires_ms` read from the committed row's `lease_until` (column exists, `0001_enrollment.sql:41`). Without this, `OwnershipReceipt.expires_at_ms` has nothing authentic to come from.
* Retry semantics of `complete` per **E3**.
* Nothing else: begin/confirm/complete semantics, the trigger, the pin check and the grant verifier are untouched.

### 2.6 Failure codes (Worker)

| Code | Status | Cause |
| --- | --- | --- |
| `enrollment_disabled` / `enrollment_config_required` | 503 | flag off / any config absent |
| `invalid_request`, `bad_enrollment`, `bad_transcript`, `bad_grant`, `bad_flow` | 400 | grammar |
| `unauthorized` | 401 | `human_flow_required` (wrong/absent flow secret), unauth bucket |
| `key_proof_required`, `bad_grant_*`, `grant_trust_required`, `unknown_grant_key` | 403 | proofs / grant verification |
| `flow_fenced` | 409 | cancel tombstone (7d-2d) |
| `flow_conflict`, `grant_conflict`, `challenge_unavailable`, `mailbox_confirmation_required`, `service_pin_mismatch` | 409 | core |
| `destination_unavailable`, `destination_unverified` | 409 | provider |
| `enrollment_capacity` | 503 | reservations full |
| `request_limit`, `mail_limit`, `abuse_limit` | 429 | budgets |
| `code_mail_failed`, `storage_or_configuration_failure` | 503 | send / D1 |
| `preflight_refused` | 403 | CP `/preflight` says the install is not real/active (added 7d-2e-2) |
| `preflight_unavailable` | 503 | CP `/preflight` unreachable or malformed (added 7d-2e-2) |
| `body_timeout` 408, `body_limit` 413 | | `readRequestBody` |

Core error messages are mapped to this closed table; an exception string is never put in a reply.

### 2.7 Replay and idempotency

* `begin`: identical re-begin returns the stored transcript, no new code; any field differing -> `flow_conflict`; a fenced flow -> `flow_fenced` (the INSERT carries the fence check, `enrollment.js:78-86`).
* `confirm`: a used or exhausted code cannot be retried, and a correct answer after the 5th failure still loses (single conditional UPDATE, `enrollment.js:98-108`).
* `complete`: exact retry only (E3).
* Request nonces: ceremony requests are not signed by an install key, so there is no nonce ledger; the `x-mail-nonce` only binds the *reply* to this request. Replaying a captured request is harmless: `begin` is idempotent, `confirm` needs the one-time code, `complete` needs a still-valid grant.

### 2.8 Tests (Worker)

`workers/agent-mail/test/enrollment-http.test.js`, `code-mail.test.js`, `enrollment-provider.test.js`. Reuse `test/enrollment-support.js` (ephemeral keys + SQLite D1).
Must prove: flag off / each missing config -> 503 and **no D1 call and no send**; unknown path/GET/query -> 404; the `begin` reply and every log line **never contain the code** (scan the full reply, headers and captured `console.error`); the mail has From/To from config/row only and the code exactly once; send failure invalidates the flow and a retry works; ambiguous send failure never retries; mail budgets (owner and global); wrong flow secret on confirm/complete -> 401 and does not burn an install's budget; 5 wrong codes invalidate; a second `confirm` on an already-confirmed flow answers `confirmed:true` and changes no counter; **the Python-built grant (cross-language vector from 7d-2e-1) passes `verifyGrant`**; `complete` reply carries `ownership_expires_ms == lease_until`; signed replies verify under the *Python* `verify_response` with `install=flow_id, generation=1` (vector file `workers/agent-mail/test/fixtures/ceremony-vectors.json`, Python-built, same pattern as `lifecycle-vectors.json`); 200-only signing (a 4xx/5xx is never signed); capacity 200 reached; existing `enrollment.test.js`, `enrollment-policy.test.js`, `lifecycle.test.js` unchanged and green.

---

## 3. Flask adapter (unit 7d-2e-3)

New modules (each one concern, per `AGENT_RULES.md` "one unit, one file"); no growth of `agent_mail_service_client.py` or `agent_mail_remote.py` beyond what is listed.

### 3.1 Modules

| New file | Job |
| --- | --- |
| `mc/agent_mail_ceremony_client.py` | `CeremonyClient.begin(context, flow_secret) -> PendingIdentity` and `.verify(context, flow_secret, code) -> OwnershipReceipt`. Reuses `HttpsTransport` and `verify_response` from `agent_mail_service_client.py` unchanged. |
| `mc/agent_mail_ceremony_device.py` | The device-identity seam (below). The only place that may touch `mc_remote`, via a lazy import inside a function, as `remote_routes.py:720` does. |
| `mc/agent_mail_ceremony_remote.py` | `CompositeRemote(lifecycle, ceremony)`: `cancel_flow`/`revoke` -> the existing `ServiceClient`; `begin`/`verify` -> `CeremonyClient`. `Enrollment` takes one adapter (`agent_mail_enrollment.py:72`), so composition is required. Plus `select_ceremony(config, ...)`. |

Edits: `mc/agent_mail_service_client.py`: **none** (its `begin`/`verify` stubs keep refusing; the composite routes around them). `mc/agent_mail_remote.py`: `UnavailableRemote` stays the default and is untouched; `select_remote` is untouched; the composite is chosen in `mc/agent_mail_factory.py` only (a few lines in `MailFactory.__init__`/`enrollment()`), still `UnavailableRemote()` when no flag/evidence.

**`mc/agent_mail_ceremony_device.py` seam:**

```python
class DeviceIdentity(Protocol):
    device_id: str
    device_public_key: str   # base64url, unpadded (converted from device_pub_b64)
    tunnel_uuid: str         # source per E1
    route_origin: str        # 'https://' + hostname
    def sign(self, message: bytes) -> bytes: ...   # Ed25519, 64 bytes
```

Default provider wraps `mc_remote.device_keys.load_identity()` / `.sign()`; on `ImportError`, `KeystoreUnavailable` or not enrolled it raises closed `device_identity_unavailable`. Tests inject a fake with an ephemeral key; the repo never needs `mc_remote`.

### 3.2 Flags

`agent_mail_ceremony_enabled` (default absent = off) **and** the existing `agent_mail_enrollment_enabled`. Both must be exactly `True`. With the flag off, `MailFactory.remote` is whatever it was (`UnavailableRemote`), byte-for-byte today's behaviour. No evidence probe is required for the ceremony (it *is* the first contact), but the manifest-fresh check that `Enrollment._fresh_candidate` already makes stays in force.

### 3.3 `begin(context, flow_secret)`

1. Pre-checks, all local, nothing sent on failure: `flow_secret` is 32 bytes -> `.hex()` (64 chars; matches `enrollment-challenge.js:13`); service candidate from `candidate_from_evidence(context, root, origin)` (`agent_mail_service_client.py:155`) with a **fresh** manifest floor (same check as `_candidate(fresh=True)`), else `manifest_stale`.
2. Build `value` exactly (11 fields):

| Field | From |
| --- | --- |
| `flow_id` | `context['flow']` |
| `owner_address` | `context['owner']` |
| `device_id`, `tunnel_uuid`, `route_origin` | `DeviceIdentity` |
| `device_public_key` | `DeviceIdentity.device_public_key` (b64url, see 0.3) |
| `mail_key_id`, `mail_public_key` | `context['key_id']`, `context['public_key']` |
| `manifest_sequence` (string), `service_key_id`, `service_public_key_sha256` | candidate `.sequence`, `.key_id`, `.fingerprint` |
| `fence_digest` (only when `LifecycleFences` is enabled; as built 7d-2e-3 the factory enables fences whenever the ceremony is on, so every ceremony flow is cancellable) | `LifecycleFences.digest_of(token_for(flow))` |

3. POST `/v1/mail/enrollment/begin` with a fresh `x-mail-nonce`; `verify_response(..., install=flow, generation=1, path=..., nonce=...)` or raise closed `remote_not_acknowledged`/`remote_response_unsigned`.
4. Validate the signed body against what Flask already knows: `transcript[FIELDS]` equals the `value` it sent for every field it chose (the Worker may only fill `challenge_*`, `install_id`, `expected_generation`, `expires_ms`, `owner_address_sha256`, `service_origin`, `route_path`); `owner_address_sha256 == sha256(owner)`; `service_origin == self.origin`; `expected_generation` is a non-negative int.
5. Return `PendingIdentity(flow, install_id, expected_generation + 1, owner, public_key, candidate.fingerprint)`. Generation mapping: the local row's `generation` is the post-commit registration generation (`enrollment.js:153`); `Enrollment.resume` rejects any other (`agent_mail_enrollment.py:238-241`). **A test must pin both a fresh install and a previously established install** (`expected_generation >= 1`), because local `start` seeds `generation=1` for a new registration (`agent_mail_enrollment.py:162-168`).

`begin` is idempotent end to end (Worker re-begin returns the same transcript, no second mail), so `resume()`'s crash-before/after-call behaviour (`remote_started=1` before the call, `agent_mail_enrollment.py:218-221`) needs no new state.

### 3.4 `verify(context, flow_secret, code)`

Sequence (each step a closed-code exit; **nothing is persisted between steps**, per E3):

1. Normalise `code`: strip, lowercase; refuse locally unless `^[a-f0-9]{32}$` (`invalid_code`; 0.3).
2. POST `confirm`. `confirmed:false` -> raise `code_rejected` (Flask's `verify` converts to `remote_verify_retryable`; the human sees a wrong-code result). **Never retry a rejected code.**
3. Rebuild `R`: call `begin` again (idempotent, returns the stored transcript). Verify it is the same `R` as step 3.4.
4. Sign `canonical(R)` (`json.dumps(R)` ASCII, identical to `enrollment-transcript.js:36`): device signature via `DeviceIdentity.sign`; mail signature via `IdentitySeal.prove(flow, seal_identity, seal_generation, key_id, transcript)` (`agent_mail_enrollment_keys.py:110`). **Vault locked -> `vault_locked_or_unconfigured`, and nothing has been sent to the CP.**
5. POST CP `/v1/mail-enrollment/grant` (origin from reviewed config key `agent_mail_grant_origin`; absent or non-https -> ceremony stays off, HTTPS, no redirect, 10 s, bounded) -> `{grant}`. This is the last step before the 60 s token clock starts, so steps 1-4 happen first.
6. (E3, as built 7d-2e-3) first POST one **tokenless** `complete`: a lost-reply retry finds its commit and returns the receipt; a Worker with no commit refuses it before writing, and only then is step 5's grant minted and presented. A fresh token on a retry would be `grant_conflict`. Then POST `complete` with the token immediately. Verified signed 200 -> `OwnershipReceipt(flow, install_id, generation, owner, public_key, candidate.fingerprint, ownership_expires_ms)`; `Enrollment.verify` then enforces `now < expires <= now + 300000` (`agent_mail_enrollment.py:310-311`). A receipt that arrives late is `ownership_expired`; recovery is the existing 7d-2d `renew`.

A CP or Worker 4xx/5xx/timeout at any step raises a closed code and records nothing; there is no partial success. The remote text never reaches an exception or log (`ServiceRefused` rule, `agent_mail_service_client.py:59-60`).

### 3.5 `Enrollment` is not changed

`resume()` and `verify()` already demand exact-equal dataclasses and a bounded expiry, so a lying remote cannot mint ownership. The adapter's job ends at producing authentic values; the checks in `agent_mail_enrollment.py:228-232`, `:305-311` stay the gate. **No edit to `mc/agent_mail_enrollment.py`.**

### 3.6 Failure codes (Flask side, closed set)

`ceremony_disabled`, `device_identity_unavailable`, `manifest_stale`, `invalid_code`, `code_rejected`, `vault_locked_or_unconfigured` (existing), `remote_unreachable`, `remote_not_acknowledged`, `remote_response_invalid`, `remote_response_unsigned`, `ceremony_scope_mismatch` (transcript/receipt does not equal what Flask sent), `ceremony_scope_mismatch` also covers two local pre-send refusals (flow secret not 32 bytes, oversize body). Worker `preflight_refused` (403) / `preflight_unavailable` (503) and every other Worker non-200 map to `remote_not_acknowledged`: non-200 replies are unsigned and `HttpsTransport` drops the body, so a finer code would mean trusting an unsigned body. `grant_refused` (any CP non-200; the CP code is kept only on the exception object for logs, never surfaced). `Enrollment` already collapses adapter exceptions to `remote_begin_retryable` / `remote_verify_retryable` (`agent_mail_enrollment.py:224-225`, `:302-303`).

### 3.7 Tests (Flask)

`tests/test_agent_mail_u7d2e_ceremony.py` (extends the 7d-1/7d-2d fixtures). Must prove: flag off -> exact `UnavailableRemote` behaviour and no import of `mc_remote`; every pre-check refuses with **zero** transport calls; device key std-b64 -> b64url conversion vector; `value` has exactly the 11 (12) fields and equals the transcript the Worker returns for them; mutated transcript/`owner_address_sha256`/`service_origin` -> `ceremony_scope_mismatch`; unsigned, wrong-nonce, wrong-path, wrong-install (`flow` vs real install) replies refused; generation mapping for fresh and established installs; malformed codes refused locally with no send and no strike (wrong-case/padded hex is normalised per 3.4 step 1 and sent lowercased, not refused); wrong code -> `code_rejected` and no CP call; vault locked -> no CP call; CP 4xx -> no token-bearing request reaches the Worker (exactly one tokenless `complete` probe precedes the grant, per 3.4 step 6); receipt expiry bounds; `Enrollment.start -> resume -> confirm -> verify -> enable` end to end over an in-process fake of the Worker (Python-built vectors from 7d-2e-2) -> stage `delivery_pending` with a real `ownership_expires_ms`; crash matrix of 4.x below; all `tests/test_agent_mail*.py` (944 pass at 7d-2d) unchanged.

---

## 4. Ceremony sequence and crash matrix

```mermaid
sequenceDiagram
  participant H as Human (Flask UI)
  participant F as Flask Enrollment + CeremonyClient
  participant W as Worker /v1/mail/enrollment/*
  participant O as Owner mailbox
  participant C as CP /v1/mail-enrollment/*
  H->>F: start(owner, manifest) ; resume
  F->>W: begin(value, flow_secret)
  W-->>O: code mail (owner only)
  W-->>F: signed {transcript, install_id, expected_generation} (no code)
  H->>F: confirm review ; verify(code)
  F->>W: confirm(code)
  W-->>F: signed {confirmed}
  F->>F: sign R (device key + mail key via vault)
  F->>C: grant(R, device_sig, mail_sig)
  C-->>F: {grant JWT, 60 s}
  F->>W: complete(grant, sigs)
  W->>W: verifyGrant, provider(owner), D1 commit
  W-->>F: signed {install_id, generation, ownership_expires_ms}
  F->>F: OwnershipReceipt -> stage delivery_pending
```

| Crash / loss point | State after | Recovery |
| --- | --- | --- |
| Before `begin` sent | `remote_started=1` set, no Worker row | `resume()` re-sends; if user cancelled, 7d-2d `cancel_flow` tombstones the flow id first |
| `begin` committed, reply lost | Worker has pending flow + code mail sent | `resume()` re-begin returns the same transcript (no second mail) |
| Mail send failed in Worker | flow invalidated, 503 | new flow (user restarts); cancel not needed |
| After `confirm`, reply lost | code spent or counted | `verify()` again: on an already-confirmed flow the core's `UPDATE` matches no row (`code_confirmed=0` is in its WHERE) and `confirm` returns `state==='pending' && code_confirmed===1`, i.e. **true** (`enrollment.js:104-110`), so a re-confirm answers `confirmed:true` without spending or counting anything; the adapter treats `confirmed:true` as success whether or not this call spent the code, and the Worker test list (2.8) pins it |
| After CP grant, before `complete` | token (60 s) unused | re-run `verify` flow from step 3; a new grant is minted; no state |
| `complete` committed, reply lost | Worker registration inactive, lease running | **E3** |
| Vault locked at step 4 | nothing sent past `confirm` | unlock; re-run |
| Flask crash after receipt, before DB write | local stage still `pending` | `verify` again; E3 makes the Worker answer replay-safe |

---

## 5. Default-off summary

| Surface | Flag | Off behaviour |
| --- | --- | --- |
| CP | `CLAYRUNE_MAIL_ENROLLMENT_ENABLED` | all three routes 404, nothing read |
| CP | `CLAYRUNE_MAIL_GRANT_KEYS` unset | `503 not_configured`; no ephemeral dev key |
| Worker | `MAIL_ENROLLMENT_ENABLED` (+ `MAIL_ENABLED`) | `503 enrollment_disabled`; no D1/mail call |
| Worker | config absent | `503 enrollment_config_required` |
| Flask | `agent_mail_ceremony_enabled` + `agent_mail_enrollment_enabled` | `UnavailableRemote` (current behaviour) |

No flag is flipped by any unit. Nothing is deployed.

---

## 6. Sizing and build units

Reference: 7d-2b..7d-2d were each budgeted ~4.5M input tokens (`AGENT_MAIL_UNIT7D2_CONTRACT.md` section 7). This surface spans three languages/stacks (FastAPI, Workers JS with D1 triggers, Flask with a vault) plus cross-language vectors; estimated together it is roughly **12-14M** (one unit ~4-5M, by the 7d-2 precedent), which is over the ~5M single-session ceiling, so it is split. These are estimates from precedent, not measurements.

| Unit | Scope | Files | Est. |
| --- | --- | --- | --- |
| **7d-2e-1 CP** | grant keyring, `/jwks`, `/grant`, budget store, (if E2a) `/preflight`, cross-language grant vector | `mail_grant_keyring.py`, `routes_mail_enrollment.py`, `mail_enrollment_store.py`, one `main.py` line, tests | ~3.5-4.5M |
| **7d-2e-2 Worker** | enrollment HTTP wrapper, code mail, provider seam, `result()` lease field, E3 change, ceremony signing vectors | `enrollment-http.js`, `code-mail.js`, `enrollment-provider.js`, `worker.js` (dispatch), `enrollment.js` (additive), tests, fixtures | ~4.5-5M |
| **7d-2e-3 Flask** | ceremony client, device seam, composite remote, factory wiring, end-to-end over fakes | `agent_mail_ceremony_client.py`, `_device.py`, `_remote.py`, factory lines, `test_agent_mail_u7d2e_ceremony.py` | ~4.5-5M |

Order: **1 and 2 are independent** (they meet only at the grant vector, owned by unit 1); **3 depends on both** (it consumes their vectors). Per the burn-rate preference ("default to one, sequentially") the recommended order is 1, 2, 3. The vector hand-offs make each unit's acceptance test the previous unit's output, not a mock.

---

## 7. Not in this unit (and why)

* **Key material generation** (`unavailable_material`, `agent_mail_factory.py:108-112`): a human ceremony, Ron-gated. Without it `begin` can never run live.
* Provisioning of `CLAYRUNE_MAIL_GRANT_KEYS`, `MAIL_CODE_KEY`, `MAIL_GRANT_JWKS`, `MAIL_SERVICE_PIN`, the CF provider credential (if E4a), Worker/CP deploys, DNS/DKIM, Cloudflare destination verification, any real mail: Ron-gated, not done here.
* Edge Worker glue (`clayrune-cloud`, G2 of the 7d-2 contract): the ceremony routes sit on the **mail Worker's own origin**, not on the user tunnel, so the edge Worker is not involved.
* 7e UI (the code-entry field already exists behind `agent_mail_enrollment_routes.py`); no UI work here.
* The pre-live gates of 7d-2 section 9 are unchanged and still open.

---

## 8. Decisions for Dave (options + recommendation; none are picked here)

| # | Question | Options (recommendation first) | Blocks |
| --- | --- | --- | --- |
| **E1** | Where does the install learn `tunnel_uuid` for `R`? It has no stored copy (0.2). A wrong value fails closed at the CP (`provisioning_mismatch`), so this is about reliability, not safety. | **(a) The CP attestation reply gains a non-secret `tunnel_uuid` field and the supervisor persists it next to `hostname` (recommended).** Needs an edit in the *proprietary* `mc_remote` (private repo move pending) and `routes_attest.py`. (b) Decode the tunnel token's `t` claim: no CP change, but `routes_attest.py:232` can return a `PLACEHOLDER_TOKEN_...`, so it is unreliable until real tokens ship. (c) A new device-authenticated CP read of the caller's own `tunnel_uuid`/`hostname_claim`: no `mc_remote` edit, but it needs a new device-auth scheme for a GET (the existing `MC-Device` flow needs a nonce handshake). | 7d-2e-3 live use; not synthetic tests |
| **E2** | `begin` is unauthenticated and causes a flow row, a reservation and one mail to a caller-chosen address. Anyone can fill the 200-slot reservation table with random `device_id`s for 15 minutes each (`enrollment-reservations.js:19-28`), blocking every real enrollment. | **(a) CP preflight: the Worker asks the CP (service-signed, `service-to-cp`) whether `device_id`+`device_public_key`+`tunnel_uuid`+`route_origin` is a real, active, non-suspended device before it reserves or mails (recommended).** Strongest; adds one CP route and one read per begin. (b) Budgets only (global/per-owner mail, per-IP-bucket): no CP coupling, but leaves the capacity-exhaustion DoS and lets anyone mail-bomb a victim up to the budget. (c) Self-proof of both keys at begin: proves key possession only, not enrollment; weak against bots that can generate keys. | 7d-2e-1 and -2 scope (adds `/preflight`) |
| **E3** | `complete` retry after a lost reply. The core's "exact retry" needs the *same token digest* (`enrollment.js:116-121`), but the CP mints a new token every call, so a retry today answers `grant_conflict` after the commit already succeeded. | **(a) Worker: when a commit exists for the flow, accept `complete` with no `token`, still requiring the flow secret and both valid proofs, and return the original result (recommended).** Small additive core change; no token ever stored client-side. (b) Persist the bearer token and signatures locally between the CP call and `complete`: new local table (must avoid the `mail_enrollment_` prefix), a 60 s-lived secret at rest. (c) Re-fetch via a new read route: another public route. | 7d-2e-2 and -3 |
| **E4** | Production `provider` (the "is this exact address a verified destination" check `complete` requires). None exists. | **(a) Read Cloudflare Email Routing destination addresses with a read-only token stored as a Worker secret (recommended):** it checks the same set `send_email` is already restricted to. Needs a credential (Ron-gated). (b) A D1 allowlist table Ron fills by hand: no credential, but a second source of truth that drifts. (c) Drop the provider check and rely on the code alone: removes the independent evidence `complete` was designed around; not recommended. | live activation only |
| **E5** | How the Worker gets the grant JWKS. | **(a) Reviewed config copy updated at rotation (recommended):** matches the verifier's "no fetching token-selected URLs" rule and adds no runtime CP dependency; rotation is a redeploy with the 7d-2a overlap window. (b) Fetch the pinned `/v1/mail-enrollment/jwks` with a 10-minute cache: rotation without redeploy, but a runtime dependency on the CP for every `complete` and a fetch surface (bounded by the pinned URL). | 7d-2e-2 |
| **E6** | The wedge in 0.3 (third trap): a flow abandoned after `begin` never finishes revocation. | **(a) In `Revocations.retry`, when the Worker answers `assignment: null`, accept it and resolve the row with no remote revoke; keep the strict equality check when the Worker returns a non-null assignment (recommended).** Safe because null means no commit exists and the fence the cancel just wrote makes any later commit impossible (`lifecycle.js:63-68`). Touches a merged guard (7d-1), so the existing revocation tests must pass unchanged plus a new test for this case. (b) Flask adapter maps `null` to `CancelledAssignment(row.install, row.generation)`: leaves the merged guard alone, but fabricates an assignment the Worker never reported, and the follow-on `revoke(install, generation)` targets a flow with no commit. (c) Stop queuing install/generation on the revocation row before a commit exists (change `queue`): removes the mismatch at the source but alters the 7d-1 row contract. | 7d-2e-3 (any live `begin`) |

---

## 9. Acceptance gates for this contract

Each unit is bound to: section 0.2's absent-items table (build them, do not assume them), the code tables in 1.2, 2.6 and 3.6, the replay rules in 2.7, the crash matrix in 4, and the per-unit test lists (1.3, 2.8, 3.7). A reviewer may reject a unit for any divergence; a divergence that is actually better goes back to this document first. E1-E6 must be decided before the unit they block starts; none of them is decided by this document.
