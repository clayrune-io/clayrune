# MC-1166 unit 7d: delivery authority and recovery preflight

Status: **documentation only; decision requested from Dave**. No option below
is adopted. Base `2f7ac7feaeb1d1770b684256cb3867d449665d0a` includes 7a/7b/7c;
branch `clayrune/mc1166-u7d-preflight`. No production code is changed.

**Recommendation A:** specify a separate, narrowly scoped machine-delivery
capability at the edge, issued through the existing control-plane authority.
This requires new protocol/authentication surfaces and a reviewed edge change;
it does not work with the current deployed-contract assumptions. Keep mail
inactive until those dependencies and the preactivation proof protocol pass.
Dave must choose before a 7d implementation begins.

Contracts: [unit 7 split](AGENT_MAIL_UNIT7_SPLIT.md),
[7a grants](AGENT_MAIL_UNIT7A_GRANTS.md),
[7b lifecycle](AGENT_MAIL_UNIT7B_LIFECYCLE.md),
[7c outbound](AGENT_MAIL_UNIT7C_OUTBOUND.md),
[unit 6 continuation](AGENT_MAIL_UNIT6_HANDOFF.md), and
[transport](AGENT_MAIL_TRANSPORT.md). The MAIN item journal records Dave's
2026-11-09 choice of 7a authority A and expressly leaves machine-delivery auth
undecided. Choosing A here is a separate decision, not implied by that choice.

## What exists, and why ownership cannot activate delivery

| Source at this base | Actual boundary |
| --- | --- |
| `control_plane/app/mail_grants.py`, `mail_grant_signing.py`; `workers/agent-mail/enrollment.js` | Injected ownership authority and dedicated grant verifier. Successful completion records inactive, unverified delivery state. No public production grant/renewal adapter. |
| `mc/agent_mail_enrollment.py::enable` | Requires fresh ownership, confirmation, manifest and trusted tunnel/extraction/custody probes; then atomically enables and pins. All three prerequisite defaults are false. |
| `mc/blueprints/agent_mail_routes.py::ingress` | Fixed POST `/api/agent-mail/ingress`, default off, exact raw path, bounded JSON, no dashboard-auth exemption. Still opens the old receiver data-dir path; the shared outside-repo factory is absent. |
| `mc/agent_mail_ingress.py::receive` | Requires installed pin, service signature, current enabled generation and prebound local thread; persists before returning the event/digest/install/generation receipt. |
| `workers/agent-mail/replay.js::replay` | Uses only the service mail signature on POST, manual redirects, 10-second timeout and bounded receipt validation. No Access service token, browser cookie or machine edge capability. |
| `workers/agent-mail/worker.js::fetch` | Only signed send and empty-object reconcile are public HTTP operations. Reconcile triggers inbound replay; it is neither an outbound send-receipt query nor a pull inbox. |
| `mc_remote_iface/provider.py` | `ProviderStatus` and lifecycle methods expose status/hostname, not authenticated installation evidence or service-delivery credentials. Unsupported providers must remain pending. |
| `control_plane/app/cloudflare.py` | Access service-token creation/deletion and a `non_identity` service-auth policy on an existing Access app exist for mobile pairing; the policy is app-wide with zero cached-session duration. |
| `control_plane/app/routes_auth.py`, `sessions.py` | Browser/mobile session JWTs and refresh exist. JWT claims identify an account/username/entitlement/session, not mail install/generation/path/body authority. |
| `mc/blueprints/local_auth.py::_local_auth_exempt` | Loopback bypasses the LAN gate. A tunneled request reaches this boundary over loopback; upstream edge/Access auth must do its job. Forging a forwarding header is not an auth solution. |

The sibling `clayrune-cloud` source `edge-worker/src/index.js` was read locally,
not fetched from production. Its `fetch` reads `cr_session`, checks ES256 against
configured issuer/audience/JWKS, binds `u` to the requested subdomain, checks
denylist and entitlement, then proxies. It has no machine-delivery branch.
`forwardRequest` strips platform cookies and client `X-Clayrune-*` assertions.
Those headers cannot authorize a request before the edge gate. Commit
`837d6d76` introduced the CP session side; neither that commit nor the sibling's
comments proves the parked Access/JWT cutover shipped. The sibling is a separate
checkout, not part of this base or this commit; no edits were made there.
Its inspected checkout HEAD was `18832e81d14f3747264f5f206928b4eab5908814`;
the inspected edge source had no tracked diff against that HEAD.

Existing `/__cr/health` answers at the edge without exercising Flask storage,
pin, consent or thread admission. An online tunnel, HTTP 200, login HTML, owner
session or signed ownership grant is not machine custody. No live account,
workflow, tunnel, Cloudflare, JWKS or deployment state was inspected here.

## Alternatives for Dave

Costs are engineering/operating costs, not priced estimates or measured bills.
Every alternative needs new integration. None may use client callback URLs,
remove dashboard auth, borrow a browser session, or reuse the tunnel/device key
for ongoing mail. A delivery credential never replaces mail-envelope verification.

| Option | Cost and release coupling | What it exposes | What Ron must provision/authorize |
| --- | --- | --- | --- |
| **A. New mail-only edge capability, CP authority (recommended)** | New purpose-specific issuance/renewal contract, edge verifier, Worker transport adapter and cross-repo tests. Requires authorized CP/edge deployment under parked MC-948, or an independently reviewed backport. | Only the fixed ingress POST for the authoritative install/generation and bounded request; no dashboard navigation or other API. New authority/protocol surface, not an existing endpoint. | Dedicated delivery-purpose signing/trust material and authenticated service-to-CP mechanism; reviewed public-key publication/rotation, Worker secret/binding custody and CP/edge rollout. Existing 7a keys/roots remain separately required. |
| **B. Dedicated Access service token per install** | Reuses existing CF token/policy primitives on the Access-based baseline; add protected Worker credential lookup, provisioning/rotation/revocation and replay adapters. Operate per-install policies/tokens and provider limits. Not compatible with the JWT-only source gate without another change. | Existing app-wide policy grants the token holder broad dashboard reach, even though mail ingress still checks its own signature. No path/method scope is provided by the current helper. | New per-install token, protected service-side storage and a policy on the correct authoritative Access app, rotation/revocation procedures and capacity review; any narrow path policy is additional reviewed work. Never reuse a phone token. |
| **C. Authenticated install-initiated pull/ack** | New Worker bounded fetch/ack protocol, local client and delivery-proof semantics; adapt replay ordering/claims/retention and test lost fetch/ack. Avoids incoming edge credential but materially changes the push-through-tunnel contract. | Service returns only that mail installation's signed events to the matching mail key; no dashboard access. New mailbox retrieval surface and install liveness/proof metadata. | Approve the transport/spec change and service deployment; existing mail-service keys/root/7a authority still needed. No dashboard bearer token or new per-install shared Access secret. |

### A: authorize one delivery rather than one dashboard

Build on the CP's existing authoritative device/account/tunnel checks and ES256
infrastructure, with a **different delivery token purpose/audience and verifier**.
The issuance call must be authenticated as the mail service through a reviewed
service-to-service mechanism. No such production mechanism is wired today.
It must join fresh authoritative route/ownership evidence to the mail service's
exact current install, generation, consent and revocation state; accepting an
arbitrary origin or an unverified registration supplied by a caller is forbidden.
The ownership-grant JWT cannot simply be relabeled as delivery permission.

Proposed capability constraints: service identity, install/generation, exact
authority-derived origin, POST, exact `/api/agent-mail/ingress`, short expiry
bounded by ownership evidence, and binding to the signed request digest/nonce.
The mail-only service Ed25519 proof remains mandatory. Purpose-specific keys,
issuer/JWKS origin, claim grammar, audience, maximum lifetime, replay storage,
renewal and revocation freshness must be frozen in the implementation contract
before coding; they are not implied by dashboard token TTLs. Unknown/stale keys
or unavailable authoritative state refuse delivery without a dashboard fallback.

The edge authenticates this separately from the cookie flow, permits only the
exact supported request, and strips the machine credential before proxying.
It must reject other paths, methods, origins, encoded aliases, query strings,
stream upgrades and cross-install/generation use. Flask still authenticates the
mail signature and consent. Existing dashboard cookie checks, user controls and
human passcode gates stay intact. This is a proposed authenticated route policy,
never a blanket path exemption or an added callback registration operation.

On an install still behind Access, a new edge capability does not magically
pass Access. An authorized migration or separately reviewed equivalently scoped
Access-compatible bridge is a dependency; until then keep delivery pending.
No automatic cutover, Access deletion or activation is authorized by this choice.
Reversing A means withdrawing its edge policy/capabilities and leaving mail off;
it does not require altering dashboard login behavior.

### B: existing primitive, broader authority

`create_service_token`, `add_service_token_policy`, `delete_access_policy` and
`delete_service_token` are concrete CP methods, not proof of a deployed mail
provisioner. The current policy admits the service token to the entire existing
Access app. Zero session duration avoids cached-session revocation lag; it does
not narrow scope. A token leak could reach dashboard APIs beyond mail, many of
which have no separate per-request human passcode proof.

Therefore B is **not acceptable as-is for the mail-only authority boundary**.
A truly path-scoped, method-restricted deployment needs new reviewed enforcement
and real precedence testing; do not silently add a bypass policy or assert that
an overlapping Access app already provides it. B is an explicit alternative if
Dave revisits this tradeoff, not a workaround authorized by this preflight.
Reversal disables delivery and revokes only its dedicated policy/token, keeping
owner login and phone credentials intact. Provisioning/deletion needs Ron's
authorization; none was performed.

Giving the Worker a `cr_session`/refresh token instead is also rejected: current
JWT authorization covers the user's dashboard, and browser refresh custody and
revocation differ from mail consent. A new `sid` label does not create path scope.
Putting a mail JWT in the browser-cookie slot would either fail its audience
check or require weakening the existing verifier.

### C: remove inbound dashboard traversal, change the contract explicitly

The existing install-to-service mail signature and D1 custody ledger are useful
primitives for a pull design. Current `/v1/mail/reconcile` only asks the service
to push; it cannot be presented as already fetching events. New fetch/ack
operations would need generation-bound claims, immutable bytes, fresh service
signatures and an acknowledgment tied to a committed local event/digest receipt.
Local custody must precede ack; lost ack refetches the same event, never creates
another follow-up. Preserve holds, per-conversation ordering and offline TTLs.

A signed pull/ack proves mail-key possession and local persistence, **not** that
the authoritative tunnel ingress is reachable. C therefore cannot satisfy the
current custody-probe wording by substitution. Dave must explicitly amend that
contract (and the owner-only inbound-via-tunnel position) if choosing C; ownership
and all human controls still apply. It has the largest transport redesign and
changes an adopted delivery direction, so A is recommended. Reversal restores
push only after its auth/proof gates pass; pending events remain held meanwhile.

## Shared prerequisite: preactivation proof without circular activation

There is a second missing contract independent of A/B/C. 7b checks machine custody
*before* enabling and pinning; ordinary `MailIngress.receive` requires that active
pin plus an existing thread binding. 7a's registration is inactive and its route
unverified, so normal Worker replay will not attempt that route either. Neither
an invented message/thread nor briefly enabling ordinary delivery can solve this
without crossing the consent boundary.

For A (or a narrowed B), include an explicit **non-mail enrollment proof mode on
the already fixed ingress route**, subject to separate contract review. This is
new protocol behavior, not an endpoint claimed to exist. Proposed proof rules:

1. Only the originating human-confirmed pending flow may arm a bounded challenge;
   use its validated manifest candidate without installing an active service pin.
2. Derive the origin/path from fresh authoritative evidence. Bind challenge,
   flow/epoch/install/generation, both key identities, ownership expiry and
   manifest sequence/digest. Authenticate both the outer machine request and
   the mail-service proof. No arbitrary destination, body, subject or thread.
3. Commit a dedicated proof receipt locally before returning a matching bounded
   acknowledgment; prove mail-key custody in the receipt. It must not create a
   mail event, bind an agent, invoke extraction, wake a session or enable anything.
4. Store/reconcile the same proof at the service, tolerate lost acknowledgments,
   expire/revoke it on identity/epoch/manifest changes, and let the later explicit
   human enable consume only current matching evidence. Define local/service
   activation ordering and crash reconciliation before either can send ordinary
   traffic. No activation counter before both proof and consent are established.

This is part of A's recommended scope for Dave to approve, not a selected wire
format. Ownership renewal and exact-generation remote activation/cancellation
also lack production adapters. A boolean from HTTP, worker status or a cache
without these scoped receipts must never become `machine_custody=True`.

## Conditional 7d build plan

No build starts from this document until Dave chooses the transport authority
and accepts the proof scope. Maintain default-off behavior throughout; installing
a supervisor/factory is not enrollment, consent, key creation or activation.

### Modules and narrow integration hooks

| New unit/file responsibility (proposed names) | Integration boundary |
| --- | --- |
| `mc/agent_mail_service_client.py` | Bounded fixed-origin service calls; exact paths, signatures, typed constant errors, manual redirects/timeouts; no credential/URL input from agents. |
| `mc/agent_mail_delivery_proof.py` | Pending-flow proof receipts, freshness/epoch validation and cached trusted prerequisite projection; no ordinary mail or consent mutation. |
| `mc/agent_mail_factory.py` | One shared checked store outside the repository, seal, enrollment, ingress, outbound, laundering and wake composition. Separate read-only ownership access from enabled send paths. |
| `mc/agent_mail_recovery.py` | Bounded durable recovery steps, ownership-lease renewal within existing consent, flow cancellation/revocation and Worker replay reconciliation. |
| `mc/agent_mail_supervisor.py` | One server-owned start/stop loop, bounded fair work per tick, backoff, last success/attempt and closed health reasons; no browser dependency. |
| `mc/agent_mail_deletion.py` | Project/session/native-alias revocation helpers before destructive route side effects; no module bodies in route monoliths. |
| `mc/question_channel_retention.py` | Joint ownership/cache/timer/selection-lock retirement, only through the question channel's synchronization contract. |
| Separate delivery-capability/proof modules in CP/Worker/edge | Purpose-specific auth, service proof and activation/revocation persistence; new migrations/adapters reviewed explicitly, never appended to registries as implementation bodies. |

Names are boundaries, not claims these files/APIs exist. Reuse the existing
blueprint registry and supervisor startup/shutdown conventions; `server.py`,
`agent_routes.py` and `project_routes.py` get only imports/calls. No broad refactor.
The ingress factory must stop using the receiver's old data-dir default. Resolve
the shared store through 7b's outside-repository location. Record an external
enrollment/store marker so deletion of an enrolled inbox cannot recreate empty
authority; missing/corrupt/version-mismatched store or seal stays visibly held.
Do not auto-migrate an existing inbox or erase rollback floors. No new sidecar
JSON under `data/projects/`. A legacy inbox migration needs its own reviewed plan.

### Recovery order and limits

1. Open/validate retained state and proofs; install read-only question ownership
   recovery before any notification can choose a channel. Reconcile local
   revocations/expiry/receipts without creating identities, roots or trust pins.
   A locked vault blocks signing/extraction-dependent work, not local closure or
   readable ownership tombstones. Never unlock it automatically.
2. Process durable flow cancellation and exact-generation revocation first.
   Existing `Revocations.retry` resolves the service assignment before revoke;
   failed responses/writes retain that work. Implement the absent remote fence,
   not just local retry calls. No new-generation enable until old revocation ack.
3. Recover already human-authorized pending work by exact flow/epoch. Do not
   replay human start/confirm/code/enable/owner/key/repin calls through a background
   path. Separate safe idempotent status/resolution and bounded renewal from new
   ceremony authority. Fresh account/tunnel checks and reviewed mail-key renewal
   must keep the same consent; failure/expiry blocks new sends. No device-key
   signature outside the original human enrollment ceremony.
4. Reconcile the Worker's existing replay queue with bounded signed requests and
   fair/backed-off scheduling, inside its current 200/day per-install authenticated
   HTTP budget (send and reconcile share it). Never poll at a guessed rapid
   interval. Network/signing/model work holds neither manager/global question
   locks nor SQLite transactions; prerequisites read already validated bounded
   proof state, since current `enable` invokes the probe inside a transaction.
5. Run store lease recovery, `MailLaunder.process_next`, `MailWake.process_next(app)`
   and `MailWake.reconcile` in bounded passes. Preserve manager -> store ordering,
   exact native conversation/form, claim fencing and per-conversation receipt
   order. Holds and uncertainty block later work for that conversation, not all
   projects. Stale laundering can retry; submitting/uncertain cannot auto-resend.
6. Run outbound cleanup, inbound terminal retention and receipt retirement even
   when sending is off. Existing `Outbound.reconcile` is enable-gated: separate
   safe cleanup from send permission instead of temporarily enabling it. Existing
   reconcile HTTP does **not** resolve outbound sending/uncertain receipts. Add a
   separately reviewed read-only receipt query or leave visible uncertainty;
   absence of a positive receipt never authorizes another provider write.
7. Run joint question retirement described below. Publish bounded metadata for
   7e: local off vs remote pending/revoked, last worker failure, proof/lease expiry,
   holds, uncertainty and deletion-pending. Never log bodies, mailbox codes,
   secrets, signed credentials or arbitrary remote exception text.

Each tick catches failures per work item with closed observable reason codes and
bounded backoff, then remains schedulable. A store failure refuses admissions,
does not reset storage, and cannot look healthy because the parent loop is alive.
Startup/shutdown are idempotent; overlapping ticks/processes obey durable claims.
Register any spawned process through existing Process Manager integration. A
thread owned by the server has explicit startup/shutdown/health ownership; no
orphan shell job. Shutdown never marks an in-flight send accepted or retryable.

Deletion hooks must call `MailStore.revoke_session` for exact session/native
aliases, including already-absent runtime rows, before removing sessions/history.
Project deletion enumerates its mail bindings and fences that project before
files disappear; do not revoke the installation and unrelated projects. No
`MailStore.revoke_project` method exists at this base. Use a dedicated transactional
helper over existing revocation logic and project-generation checks. Lost
revocation writes must refuse destructive completion or persist a deletion intent
first, never silently continue. Late callbacks preserve positive evidence but
cannot reactivate targets. Registration deletion/disable uses existing enrollment
disable plus durable flow cancellation; no network wait under the manager lock.

### Three carried 7c requirements

**1. Cache loss with outbound off.** At this base `on_question_raised` invokes
durable selection only when eligible or a session choice survives. Losing
`_delivered`, `_outbox` and `_offline_question_channels` while retaining the old
form and SQLite receipt can therefore select legacy with the flag off. Recover
existing ownership **before eligibility**, through a no-send/no-schema-creation
lookup available while outbound/enrollment are off. It must distinguish an
authoritative miss from unavailable/corrupt storage; the latter holds, never
falls back. An existing agent-mail receipt wins without starting a timer/send.
An existing legacy owner remains qid-wide; agent-mail ownership remains scoped
to the full revision/project/session. New questions with no ownership retain
legacy behavior when off. Do not rehydrate old legacy forms or invent unit-6
identity snapshots. Preserve the original failing `[False]` cache-loss probe as
a regression through the real notification entry point, not just `selected()`.

**2. Factory re-entry contract.** `question_channel._selection_locks[qid]` is
non-reentrant. A factory or ownership lookup must **never synchronously call
`on_question_raised` for that same qid**, directly or through startup/recovery,
including before returning a singleton. Construct/read dependencies only;
enqueue any later notification after selection has released the lock. Keep
factory/receipt I/O outside the global `_lock`; do not replace the per-qid lock
with an RLock to conceal recursion. Test the real operational factory call graph
and bounded failure behavior, without leaving blocked test threads behind.

**3. Joint bounded retirement.** `_selection_locks`, `_delivered` and `_outbox`
are all unbounded at this base. Do not prune only one or check `lock.locked()`
then delete it: an unacquired waiter can still hold that lock reference. Track
selection users/waiters under the global mutex before releasing its reference;
retire an entry only with zero references and no live timer, parser/reply reader,
pending form, delivery/reconciliation or deduplication obligation. Account for
the window after selection releases and before `_outbox`/timer publication.
Group qid-wide legacy and revision mail keys; retire their three caches together
with a synchronized tombstone policy, not three unrelated TTL passes.

Durable channel receipt retirement must obey the same live-form exclusion:
current outbound cleanup deletes receipts after thread expiry plus terminal
retention even if a retained form can reappear. Preserve a bounded retired-owner
tombstone or prove that form is irrevocably retired before dropping authority;
otherwise pruning recreates the flag-off fallback gap. Apply backpressure/hold
on capacity rather than evicting live ownership or replacing a waiter's lock.
Exact capacity/retention values need a measured implementation proposal, not an
invented policy here. Keep legacy IMAP matching for its live `_outbox` records.

## Verification plan and completion gates

Use synthetic transports/keys/clocks/SQLite and toolless-runtime doubles only.
No model call, real SMTP/IMAP, account/credential/Cloudflare action, tunnel startup
or production provider import may escape the fixture boundary. Run application
and CP tests as separate children (their `tests.conftest` modules collide).

| Area | Required acceptance evidence |
| --- | --- |
| Capability/proof | Wrong issuer/audience/key, host/path/method/query, bytes/nonce, install/generation/epoch, stale ownership, replay, key rotation and account/revoke races refuse; login HTML/redirect/edge health never satisfy proof. Pending proof cannot admit real mail or activate; crash/lost ack converges. |
| Restart/storage | Missing store marker/database/WAL, corrupt schemas/seals/manifests, locked/replaced vault, restart at every remote/local write boundary, two processes/live leases, lost assignment/cancel/enable receipts; no new identity or generation adoption. |
| Supervision | Offline/backoff/budget fairness, row failure vs next healthy project, repeated startup/stop, slow network/extraction/signing, no hidden worker death, feature flags off, cleanup while disabled; no busy loop or lock-held I/O. |
| Delivery/uncertainty | Existing end-to-end synthetic Worker -> ingress -> extraction -> exact resume/receipt path; no fresh session fallback, duplicate write, raw-body tool delivery, reordered held reply or automatic uncertain replay. |
| Questions | Original all-cache-loss/flag-off regression; legacy and full-revision ownership, miss vs storage error, unavailable factory/store, no hidden credential read, same-qid concurrent waiters, no synchronous factory re-entry, timer-publication/retirement races and bounded three-cache cleanup with live owners retained. |
| Deletion | Live/missing sessions and native aliases, two projects sharing one install, delete racing admission/late completion/restart, persistence failure before deletion side effects, local off with durable remote cancellation pending. |

Implementation verification must use the installation `tools/test-run.py`
launcher and project interpreter with explicit ordered manifests/argv/cwd/env;
commands and real child exits go in the MAIN journal. Preserve credential-removal
fixture overlays and initialize test doubles before application imports (the
7c review once accidentally started live attestation). Link existing MAIN
dependencies only if needed, remove junctions nonrecursively, never install a
second toolchain. Run all affected mail/question suites, separate CP and full
Worker tests; add sibling edge tests if A. New/moved `mc/` modules require
Pyright basic with no new errors. Re-run relevant checks after each merge.

7e owns desktop/mobile status/setup and recovery UI; browser/boot and installer
asset tests apply when that code changes. 7d must expose enough metadata for it,
without adding an agent-facing enable/approve surface. Existing human controls,
trusted caller-attribution gate, D1 runtime/upgrade checks, real delivery capacity,
privacy disclosures, root/key provisioning and exact real-mail continuation
remain independent pre-live gates. A synthetic green suite does not clear them.

## This preflight's validation and handoff

Source inspection and documentation checks only; **no test suite was run**.
All six relative document links resolve. `git diff --check` passed; a scoped
comparison of `mc/`, `workers/` and `control_plane/` against `2f7ac7fe` exited
0 with no production changes. Staged/range whitespace and named-path checks
are recorded with the local commit in the MAIN journal.
Pyright, browser/boot/mobile/installer checks are N/A: no executable or UI edits.
No new dependency. README, USER_GUIDE, CHANGELOG, AGENTS and rules are unchanged:
this is an unadopted internal proposal with no available behavior or conduct rule.
The sibling edge source was inspected read-only, without claiming deployed state.

The full plan is this document. The optional global PLAN-tab artifact was refused
by the unattended fence as out-of-project configuration; it was not retried.
The MAIN ignored journal records the checkpoint so worktree cleanup cannot lose
it. Only this named document belongs in the local commit. No merge, push, restart,
deployment, real key generation, credentials, external provider calls or mail.

**Decision for Dave:** A (recommended, narrow new CP/edge capability and proof
contract), B (existing Access token with unacceptable app-wide scope unless
separately narrowed/reconsidered), or C (new authenticated pull/ack and explicit
delivery-contract change). All require new reviewed surfaces. Until selected,
implementation is stopped and mail stays inactive.
