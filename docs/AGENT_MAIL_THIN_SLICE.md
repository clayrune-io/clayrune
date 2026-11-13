# Agent mail: thin slice for one install (MC-1166)

Status: plan, 2026-11-13. Doc only; no code, Cloudflare or mail touched.
Scope (Dave, 2026-11-13): get Ron's install sending one note and waking the
exact session on the reply. Hardening for other users comes later.
Architecture is unchanged from `docs/AGENT_MAIL_PULL_DESIGN.md`: no tunnel, the
install pulls over one outbound WebSocket, outbound goes only to the verified
owner, the reply is laundered without tools and wakes the originating session.

## 0. Finding that shapes the plan

The brief assumes most of the path is missing. In fact, reading the code shows
**nearly every step already exists and is wired behind config flags**. Both
sides use the same Ed25519 signed-v2 contract, and the shared test vectors
cover it. The slice is blocked by four things:

1. **No production `reviewed_service`.** `agent_mail_factory.get()`
   (`mc/agent_mail_factory.py:457`) builds `MailFactory()` with
   `reviewed_service=None`, so `MailFactory.reviewed_service()`
   (`:347-359`) always raises `service_key_changed`. Every pull, send and
   signup path calls it, so nothing can run.
2. **No way to provision an install without the signup ceremony.** The only
   writers of `mail_pull_accounts` / `mail_pull_generations` are
   `Signup.begin` (`mc/agent_mail_signup.py:314-318`) and `Activation`
   (`mc/agent_mail_activation.py:130-195`). The Worker-side rows
   (`pull_installs`, `pull_generations`, `pull_addresses`, `pull_owner_slots`)
   are written only by `signup-http.js` and `pull-activate-http.js`.
3. **The question-channel adapter is never set.**
   `agent_mail_outbound_questions.factory` is `None` (`:13`) and nothing
   assigns it (grep: no assignment in `mc/` or `server.py`), so
   `deliver()` always returns `operational_adapter_unavailable`.
4. **Agent notes have no outbound trigger.** `Outbound.prepare(kind='note')`
   exists (`mc/agent_mail_outbound.py:189`). Its only caller is
   `outbound_questions.deliver` (`kind='question'`).

Also: **nothing has ever run the Python client against the real Worker.**
Cross-language tests cover the send-signing vectors
(`tests/test_agent_mail_pull_send_conformance.py`), receiver frames
(`tests/test_agent_mail_pull_receiver.py`) and signup HTTP
(`tests/test_agent_mail_signup.py`). No test runs ticket → socket → frame →
ack → send between `PullClient` and `worker.js`. The first live run should
not be the first time those two meet, so unit U7 closes that gap locally.

### Auth decision: keep the existing Ed25519 signed-v2, do not add a shared secret

Replacing it with HMAC/bearer would mean rewriting the ticket verifier
(`session-auth.js`, 260 ln), the send verifier (`pull-send-http.js`, 277 ln),
`PullClient._ticket` and its response verification
(`mc/agent_mail_pull_client.py:258-326`) and `ServiceClient`'s v2 checks
(`mc/agent_mail_service_client.py:153-209`). That is about 1k lines across
both languages, and the new code would never have been interop-tested. The
signing itself is already built and vector-tested. Only its bootstrap is
missing: install key creation (`IdentitySeal.generate_pull`,
`mc/agent_mail_enrollment_keys.py:199`) and the service pin. Both are small.
A shared secret would be heavier, not lighter.

## 1. End-to-end path, step by step

Legend: **EXISTS** = code present and unit-tested. **WIRED** = reachable
from server boot or a Worker entry once flags are on. **MISSING** = a gap
this slice must close.

### Setup (one time, human)

| # | Step | Existing code | State |
|---|------|---------------|-------|
| S1 | Generate root + service Ed25519 keys offline | `tools/agent-mail-keygen.py` | EXISTS |
| S2 | Sign the service manifest with the root key | `tools/agent-mail-manifest-sign.py` | EXISTS |
| S3 | Create local mail store, marker and pull schema | `MailFactory.bootstrap(authorize=…)` (`agent_mail_factory.py:316`) → `agent_mail_pull_schema.initialize` | EXISTS, no caller outside signup |
| S4 | Create the install's Ed25519 identity in the sealed vault-keyed file | `IdentitySeal.generate_pull` (`agent_mail_enrollment_keys.py:199`) | EXISTS |
| S5 | Fetch and verify the manifest; produce `(ServiceKeyCandidate, manifest_digest)` | `fetch_verified_manifest` (`agent_mail_manifest_fetch.py:100`), `verify_service_manifest`, `ManifestCAS` floor (`agent_mail_enrollment_state.py:59`) | Pieces EXIST; **MISSING** the production callable → **U1** |
| S6 | Write local account/generation/flow/registration rows as `active`, `local_enabled=1`, `consent_epoch=1` | Row shapes in `Signup.begin` (`signup.py:314-318`), `Activation` (`activation.py:130-195`) | **MISSING** non-ceremony writer → **U2** |
| S7 | Insert Worker rows: `pull_installs` (active), `pull_generations` (current), `pull_addresses` (assigned), `pull_owner_slots` (active). `owner_hash = sha256(owner address)` (`owner-registry.js:28`) | Schema `migrations/0005_pull.sql:44-90`; read by `CURRENT` (`ledger-pull.js:14`), `SessionAuth.active` (`session-auth.js:76-83`) | **MISSING** → SQL emitted by **U2**, applied by ops step O6 |
| S8 | Config keys `agent_mail_install_id`, `agent_mail_root_public_key`, `agent_mail_service_origin`, and flags | `MailFactory._deployment` (`factory.py:307`), flags read across modules | EXISTS (human edits config) |

### Outbound: agent note → Ron's inbox

| # | Step | Existing code | State |
|---|------|---------------|-------|
| O1 | Agent asks to send a note for its own `(project_id, session_id)` | none for notes; questions use `question_channel.on_question_raised` → `outbound_questions.deliver` (`agent_mail_outbound_questions.py:33`) | Note: **MISSING** → **U3**. Question: **MISSING** factory → **W2** |
| O2 | Durable intent; bind reply token → exact session + continuation snapshot | `Outbound.prepare` (`agent_mail_outbound.py:189-262`) → `MailStore.bind_thread`, `MailWake.bind`, `mail_pull_threads` | EXISTS |
| O3 | Authority check: one active account, pin matches the reviewed manifest, seal ready | `Outbound._authority` (`:129-175`) | EXISTS, needs U1 + U2 |
| O4 | Signed POST `/v1/pull/send` | `Outbound.send` (`:288`) → `ServiceClient` (`agent_mail_service_client.py:277`) → `HttpsTransport` | EXISTS |
| O5 | Worker verifies, reserves intent + thread, sends via `MAIL_SEND` to `owner_address` only, with `Reply-To: <address_local>+<token>@clayrune.io` | `worker.js` HTTP_ROUTES → `PullSendHTTP` (`pull-send-http.js`) → `pullOutbound`/`constructMail` (`outbound.js:28-38`) → `cloudflareEmailSending` (`email-sending.js`) | EXISTS + WIRED (`76ed8053`) |

### Inbound: Ron's reply → exact session

| # | Step | Existing code | State |
|---|------|---------------|-------|
| I1 | Email Routing catch-all on clayrune.io → Worker `email()`; `hello@` exact rule keeps priority | `worker.js` `email()` | EXISTS; routing is ops (O8) |
| I2 | Recipient `m<29hex>+<token>@domain` → registration lookup → owner/DKIM/SPF alignment → thread correlation → strip to plain text | `ingress.js:57-110`, `auth.js decide`, `strip.js` | EXISTS + WIRED. Requires `MAIL_INGRESS_VERIFIED=true` |
| I3 | Durable enqueue in D1 `pull_events`; best-effort wake of the install's DO | `persistPullIngress` (`ingress-pull.js:57-86`), `wake` in `worker.js` | EXISTS + WIRED |
| I4 | Install holds the socket: signed ticket → `/v1/pull/socket` → DO admits → pushes frames | `PullClient._ticket/_session` (`agent_mail_pull_client.py:258,571`); `PullSocketRoute` (`pull-socket-route.js`), `PullMailbox` (`pull-mailbox.js`) | EXISTS + WIRED, never run together → **U7** |
| I5 | Durable local custody, then signed ack; only `ack_committed` retires | `PullReceiver.receive` (`agent_mail_pull_receiver.py:243`), `PullClient._committed` | EXISTS |
| I6 | Supervisor owns client start/stop and runs recovery passes | `server.py:3827` → `agent_mail_supervisor.start_default` → `_drive_client` (`:205`) | EXISTS + WIRED (flag `agent_mail_supervisor_enabled`) |
| I7 | Toolless laundering of the reply (haiku, no tools/MCP) | `Recovery._inbound` → `MailLaunder.process_next` (`agent_mail_launder.py:64`) → `extract_reply` (`agent_mail_extract.py:103`) → `mail_launder.claude_mail_transform` | EXISTS + WIRED (flag `agent_mail_ingress_enabled`) |
| I8 | Resolve the bound continuation to the exact native session; refuse aliases or rollovers | `MailWake.process_next` (`agent_mail_wake.py:187`), `target.resolve` (`agent_mail_target.py:125`), `Continuation` | EXISTS + WIRED |
| I9 | Inject the reply with `POST /api/project/<pid>/agent/followup {session_id}` | `MailWake.process_next` calls `agent_routes.agent_followup` in a request context (`agent_mail_wake.py:226-229`); 10 s ack (`agent_mail_pipe.py`) | EXISTS + WIRED |

## 2. Build units

Each unit owns exactly one file plus its own test file. Sizes are estimates
in lines of source / test.

| Unit | Owns (one file) | What | Depends on | Parallel? | Size |
|------|-----------------|------|-----------|-----------|------|
| **U1** | `mc/agent_mail_reviewed_service.py` (new) | `build(factory) -> Callable[[], tuple[ServiceKeyCandidate, str]]`: fetch and verify the manifest from `agent_mail_service_origin` with `agent_mail_root_public_key`, CAS the rollback floor through `ManifestCAS`, return the candidate and `manifest_digest`. Cache the verified result in memory until `min(expires_at_ms, now+10 min)`, so the per-call authority checks do not refetch over HTTP. Fail closed, with no fallback to cached account keys. Before freezing, confirm how the digest is computed against `Signup._service` and its tests. | none | **Yes** | 100 / 150 |
| **U2** | `tools/agent-mail-provision-install.py` (new) | Human-run operator script for setup steps S3–S7. It prompts for a typed confirmation (the `authorize` callable), runs `bootstrap`, creates install/flow/key ids, `address_local = 'm' + 29 hex`, runs `generate_pull`, calls U1 for pin and digest, and writes the local rows in exactly the column order of `signup.py:314-318` and `activation.py:130-195` (state `active`, `local_enabled=1`, `consent_epoch=1`, `registrations` enabled, `mail_pull_status`). It writes the Worker D1 SQL (`pull_installs/generations/addresses/owner_slots`, `fence_hash = sha256(fence_token)`) to a file under `~/.clayrune/`. Prints the config keys to set. Takes the owner address as an argument and never writes it into the repo. Refuses if any account already exists. The test must prove `PullClient._account`, `Outbound._authority` and `validate_pull` accept the result. | U1 interface (signature above; can stub) | **Yes** | 250 / 180 |
| **U3** | `mc/blueprints/agent_mail_note_routes.py` (new) | `POST /api/project/<pid>/agent/mail/note {session_id, request_id, subject, body}` → `agent_mail_factory.get().outbound().prepare(kind='note')` then `.send()`. Returns metadata only. 404 unless `agent_mail_outbound_enabled`. Owner-only is already enforced by `Outbound` and the Worker; the route adds no recipient field. | none | **Yes** | 80 / 120 |
| **W1** | `mc/agent_mail_factory.py` (edit `get()` only) | `MailFactory(reviewed_service=<lazy U1 build>)`. About 8 lines. | U1 merged | Yes (write now, merge after U1) | 8 / 40 |
| **W2** | `mc/agent_mail_outbound_questions.py` (edit) | Default `factory` to `lambda: agent_mail_factory.get().outbound()`. Delivery stays gated by `agent_mail_outbound_enabled` exactly as now. About 5 lines. | none | **Yes** | 5 / 40 |
| **W3** | `mc/blueprints/agent_mail_routes.py` (edit) | Register U3's blueprint beside the existing child blueprints (`:29-33`). About 3 lines. | U3 merged | Yes (merge after U3) | 3 / 20 |
| **U7** | `tests/test_agent_mail_slice_e2e.py` (new) | Local interop proof, no network. Runs the real `worker.js` + `PullSocketMailbox` under Miniflare (already a devDependency) with D1 and all migrations, inline `node --input-type=module -e` as existing tests do. Provisions through U2's functions. Then: `PullClient` gets a ticket, opens the socket, gets one event injected through `email()` with a fixture message, custodies it and acks (D1 event retired). `Outbound.prepare/send` reaches `/v1/pull/send` against a fake `MAIL_SEND` that records the message: correct `to`, `Reply-To`, no other recipient. Launder and wake run with a stubbed extractor and a fake follow-up, asserting the exact `session_id`. | U1, U2 (W1 for the factory path) | After U1+U2 | 300 |

**Schedule (3–4 builders, under a day):**

- Wave 1, all parallel: U1, U2 (against U1's frozen signature), U3, W2.
- Wave 2, after U1/U3 merge: W1, W3 (minutes each; one builder can do
  both, as two commits), and U7.
- Run the smokes after each merge (CLAUDE.md, 2026-09-10).

**Not built (cut for the slice):** signup/enrollment ceremony, verification
mail, key rotation/recovery, owner change, account delete/disable UI, service
review (W05), retention passes (W03), multi-user UI, fence/cancel flows. Their
code stays as is; nothing in the slice deletes or weakens it.

## 3. Minimum Cloudflare ops

Live state below is **journal-reported, not verified from code**
(`docs/_journal/a841feef-agent-mail.md` lines 3541-3542, 3650-3657). Step O1
re-reads it before anything changes. Every outward change needs Ron's
go-ahead.

| # | Step | Needed? | Why / note |
|---|------|---------|-----------|
| O1 | Read-only inventory: Worker `clayrune-agent-mail` versions, vars, bindings; D1 list; Email Routing rules; verified destinations | Yes | Journal says the Worker was deployed (older code), the catch-all → Worker was set by Ron, the `hello@` exact rule wins (probe), and Ron's Gmail is a verified destination. Confirm all of it |
| O2 | Run S1 keygen + S2 manifest sign offline | Yes | The Worker refuses the socket without `MAIL_SERVICE_MANIFEST` (`pull-socket-config.js:12-33`); the client verifies it against the root key |
| O3 | **D1** `agent-mail`: create if absent; apply `schema.sql`, then `migrations/0001`–`0006` in order | Yes | All pull state lives there. Apply every migration: the template comment still lists only 0001-0004 (stale) |
| O4 | **Durable Objects** binding `MAIL_PULL_MAILBOX` / `PullSocketMailbox` (SQLite class, migration tag `pull-mailbox-v1`) | Yes | Holds the socket and receives the ingress wake. Confirm plan entitlement in O1 |
| O5 | **Secrets:** `MAIL_SERVICE_PRIVATE_KEY` only | Yes | `MAIL_PULL_OUTBOX_KEY(_ID)` serves only the hosted signup routes; `MAIL_DESTINATION_TOKEN` serves only destination provisioning. **Drop both** |
| O6 | Apply U2's D1 provisioning SQL (`wrangler d1 execute --remote --file`) | Yes | One install row set |
| O7 | **Worker deploy** of current master `worker.js`/`production.js` with vars `MAIL_ENABLED=true`, `MAIL_DOMAIN=clayrune.io`, `MAIL_FROM=<agent sender>@clayrune.io`, `MAIL_SERVICE_ORIGIN` (the `workers.dev` URL is enough), `MAIL_SERVICE_KEY_ID`, `MAIL_SERVICE_PIN_EXPIRES_MS`, `MAIL_SERVICE_MANIFEST`; bindings D1, `send_email MAIL_SEND`, DO; cron `* * * * *` | Yes | The cron runs `pull-sweep.js`, the backstop for a missed best-effort wake (free) |
| O8 | **Email Routing:** keep `hello@clayrune.io` exact rule untouched; catch-all → Worker | Yes, likely already done | Verify only. Do not edit `hello@` |
| O9 | `MAIL_INGRESS_VERIFIED=true` after one controlled reply: inspect `wrangler tail` for `ingress_header_mismatch` / auth reason codes | Yes | `ingress.js:68` rejects everything while false |
| O10 | **Email Sending onboarding** for clayrune.io (DNS records) | **Only if** the first send fails with `E_SENDER_NOT_VERIFIED` / `E_SENDER_DOMAIN_NOT_AVAILABLE` | The slice sends only to Ron's Gmail, which the journal reports as already a verified destination. Try the binding first. Onboarding is a DNS change and needs Ron |
| — | Custom domain for the service origin, KV, Access, CP/tunnel | **No** | Not used by the pull path |

## 4. Live test

Preconditions: U1–U3, W1–W3 and U7 merged and green. Ops O1–O9 done. Config
set: `agent_mail_install_id`, `agent_mail_root_public_key`,
`agent_mail_service_origin`, `agent_mail_supervisor_enabled`,
`agent_mail_enrollment_enabled`, `agent_mail_outbound_enabled` and
`agent_mail_ingress_enabled` all on. Vault unlocked: the seal needs the
passphrase key, otherwise the client halts with `vault_locked`. Server
restarted (Dave has standing approval).

1. **Connect.** Agent-mail status (`agent_mail_status_view`) shows the client
   `running`, with `pull` step `ok` / `scan_complete`. `wrangler tail` shows
   one ticket and one socket admission.
2. **One note out.** Start a fresh, short test session **T** in
   `mission_control`. T calls
   `POST /api/project/mission_control/agent/mail/note` with its own
   `session_id`, `request_id: slice-1`, subject `Slice test`, body `ping`.
   Expect `state` → accepted, one mail in Ron's Gmail from `MAIL_FROM`, and
   `Reply-To: m…+<32hex>@clayrune.io`.
3. **One reply back.** Ron replies from the owner Gmail with `pong 1`.
   Expect: the Worker `email()` returns `queued`; the event is pushed, held
   locally and acked (the D1 `pull_events` row moves to acked); the event is
   laundered and then `turn_completed` in the local store.
4. **Which session wakes: exactly T.** T gets one new turn beginning
   `[Owner mail reply; event <id>]`, then the untrusted-mail warning, then
   `pong 1`. No other session in any project receives a turn. If T's native
   conversation changed (rollover/resume to a new CLI session), the event is
   **held**, not rerouted (`target.resolve`, `continuation.reject_rollover`).
   That counts as a correct outcome but a failed live test: keep T short.
5. **Negative checks, same run:** a reply from a different address to the
   same `Reply-To` is refused (`owner_inactive` / auth reason in tail, no
   local event). A mail to `hello@clayrune.io` still lands in Gmail and never
   reaches the Worker.
6. Optional: an unattended `mc:question` in a scheduled run goes out through
   W2 and a `Q1: <label>` reply answers it.

## 5. Decisions for Dave

- **D1, auth.** (A) Keep Ed25519 signed-v2 + root-signed manifest, adding
  only U1/U2 bootstrap. (B) Replace with a vault shared secret.
  **Recommend A**: B rewrites about 1k lines on both sides and throws away
  tested interop.
- **D2, note trigger.** (A) Local route U3, which any agent can curl with its
  own session id. (B) An MCP tool. (C) No note path; the slice tests only the
  question channel (W2), which saves U3/W3. **Recommend A**: the live test
  asks for a note, and the route has no recipient field, so its worst misuse
  is a note to Ron from a session id the caller names.
- **D3, sending.** (A) Try the existing verified-destination binding first
  and onboard domain sending only if it refuses. (B) Onboard clayrune.io
  sending up front. **Recommend A.** Either path's DNS change goes to Ron.
