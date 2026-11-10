# MC-1166 unit 7: split proposal for Dave

Status: proposal only, stopped at the dispatch's first-step scope gate.
Base: `fa6c12999abb8242ff4f6b19d02f4315586271cd`.
No implementation or decision below is adopted by this document.

## Why this is more than one reviewable change

The transport contract explicitly assigns unit 7 the service-authoritative
registration lifecycle, installation/tunnel proof, key management, outbound
wiring, local recovery scheduling and owner controls. These cross distinct trust
and persistence boundaries; they are not just a local enrollment form.

At this base, `workers/agent-mail/worker.js` accepts only signed send/reconcile
requests. The transport handoff says there is no public enrollment management
endpoint. Registration rows contain the already-verified owner, public key and
proven route; creating those assertions safely remains work. The unit-4 receiver
retains dashboard authentication, while unit 6 provides a default-disabled
controller without a startup loop. A new UI alone cannot establish readiness.

The main journal records D7's final mail-only Ed25519 contract and the merged
global-breaker repair. This proposal preserves both; it does not reopen D1-D8.

## Recommended sequence

Each sub-unit is a separate disabled review candidate, with synthetic tests and
its own handoff. Integration must not activate partial enrollment capabilities.
Proposed filenames are ownership boundaries, not new APIs already available.

| Order | Sub-unit and ownership | What its review must prove |
| --- | --- | --- |
| 7a | Service enrollment and trust contract; separate enrollment/challenge/provider-adapter modules under `workers/agent-mail/`, schema migrations and tests. | Bind an expiring human challenge, provider-confirmed mailbox and install public key to one durable registration. Retry/restart/link scans cannot create duplicate installs or pair another install. Prove the existing tunnel server-side, otherwise stay pending. Bound verification abuse and capacity; serialize generations, revocation/deletion and activation accounting without ping linkage. Specify trusted service-pin bootstrap and rotation. |
| 7b | Local enrollment lifecycle; separate `mc/agent_mail_enrollment*.py` modules and a dedicated human-only enrollment blueprint. | Human consent alone creates/changes enrollment and mail-only key storage outside the repository; agents cannot enroll, verify, enable, repin or change owner. Reopen pending work after restart without creating a second identity. A missing tunnel or Claude extraction prerequisite blocks activation. Disable immediately gates local sends/follow-ups, then durably retries remote revocation; owner/key changes cannot reuse old threads. |
| 7c | Outbound notes and question adapter; separate `mc/agent_mail_outbound*.py`, narrow hooks into the existing question interface. | Bind the local thread, unit-6 snapshot and transport mapping before sending. Persist immutable intent/correlation; retries use the same intent and fresh signatures. Derive the sole recipient server-side, reject recipient/header overrides, preserve question revision and attendance grace, and avoid duplicate legacy/new notifications. Show accepted versus uncertain without blind resend. Synthetic immediate replies reach only the recorded conversation. |
| 7d | Durable lifecycle integration and recovery; separate supervisor/recovery modules, narrow startup/deletion hooks. | Resume enrollment/revocation, reconcile Worker replay, run extraction/wake/receipt reconciliation and retention without an open tab. Restart/offline/racing disable/deletion cannot release stale generations or lose ordering. Holds and uncertainty remain durable and visible; storage/worker failures log closed reasons and cannot silently kill the loop. Project/session deletion reaches the existing revocation operations. |
| 7e | Human setup and status/recovery UI; separate first-run step and status modules under `static/js/`, registration hooks only in existing screens. | Decline completes setup with no address transmission or ping change. Display resumable verification, tunnel/Claude prerequisites, owner/address, holds, uncertainty, local-off versus remote-revoked and deletion-pending accurately. Human proof protects every mutation. No raw reply is rendered as an instruction or approval. Cover desktop/mobile setup, later enrollment, restart and recovery with browser smokes. Include installer asset checks and draft privacy/README/user-guide disclosures; publication remains separately gated. |

Dependencies: 7a -> 7b -> 7c -> 7d -> 7e. Unit 7e can use synthetic status fixtures
for isolated rendering checks, but final acceptance must exercise the actual
local blueprint and lifecycle. None of these sub-units authorizes deployment,
real destination verification, real mail, DNS changes, account access or a pilot.

## Decision requested from Dave

- **A, recommended:** approve the five sequential sub-units above. Cost: five
  focused build/review cycles and explicit handoffs; trust and revocation can be
  reviewed before outbound integration or UI makes the service appear usable.
- **B:** keep one unit containing all five concerns. Cost: one larger review
  across Worker, Flask, SQLite, key storage, runtime and browser behavior;
  failures have a wider integration surface. No code has been started for B.

Before dispatching 7a, its brief must settle the exact service enrollment trust
mechanism: authenticated tunnel ownership evidence, human challenge binding and
trusted service-public-key bootstrap. The unit-2 journal explicitly records that
a hostname or `ProviderStatus` is not a Worker-verifiable ownership proof. A
builder must return actual alternatives to Dave if the existing control-plane
contract cannot supply that evidence; it must not invent a callback-registration
endpoint or relax dashboard auth. This preflight has not selected that design.

## Validation and remaining gates

This change is documentation only: no tests executed (count/exit code N/A), and
pyright is N/A because no Python module changed. `git diff --check` is the
appropriate check for this proposal. Existing suite counts in the journal are
prior-session evidence, not results from this session.

README, USER_GUIDE, CHANGELOG and rules need no edits for a proposal with no
user-visible behavior. Each implementation unit must run the exact affected
suites through the installation test launcher; new `mc/` modules require pyright
basic with zero errors, and frontend changes require boot/feature smokes.

Everything functional remains synthetic-only or unverified here. Real Cloudflare
verification, D1 behavior (including cross-statement `changes()`), actual tunnel
ownership/reachability, privacy publication and a completed real-mail resumed turn
remain separate launch gates. No merge, push, restart or deployment is authorized.

## Unit 7a preflight: enrollment evidence and service pin (2026-11-09)

**Design only; recommendation B below awaits Dave's decision.** Dave approved
the five-unit split (option A of the earlier section), not an enrollment trust
mechanism. The A/B/C labels below are new alternatives within 7a. No endpoint,
credential, schema, deployment or tunnel-policy change is implemented here.

### What the existing code can actually establish

- `control_plane/app/routes_account.py::_do_enroll_after_auth` provisions a
  named tunnel, ingress and DNS, then records `devices/{device_id}` with
  `user_id`, `device_pub_b64`, `hostname_claim`, `cf_tunnel_uuid`,
  `cf_dns_record_id`, `provisioning_state` and `revoked_at`. These are the
  authoritative relationship to check; a client hostname is only a lookup hint.
  The recorded June source (`d34584f`) also writes the public key, hostname,
  tunnel UUID, DNS record id and legacy Access app id. Actual deployed schema
  compatibility has not been inspected against a real account.
- `mc_remote/device_keys.py` and `mc_remote/attestation.py` supply the existing
  device identity and signing seam. `control_plane/app/verify.py` checks device
  signatures, registry membership/revocation, an enrollment-token hash, a
  platform signature, a single-use nonce and the hostname claim. This is tunnel
  attestation, not an issued mail enrollment assertion. `ProviderStatus` in
  `mc_remote_iface/provider.py` is a local snapshot with no verifiable proof.
- Commit `837d6d763eb1b4f3c9c05952221c4482adab9615` added ES256 JWKS and
  `/v1/session/start|refresh|logout`. `sessions.mint_access_jwt` emits issuer,
  audience, subject, username, plan, entitlement and session id, with issuance
  and expiry supplied by the signer. It does **not** bind a device id, tunnel
  UUID, mail public key, enrollment challenge or consent generation. Refresh
  re-reads the live account; logout prevents renewal, not immediate invalidation
  of an issued access JWT. Neither a dashboard JWT nor Firebase account login
  alone is an install enrollment capability.
- `GET /v1/devices` omits the authoritative public key and tunnel UUID;
  `is_this_device` is derived from a caller-supplied hint. Forwarding that JSON
  from an install would not turn it into proof. Relaying `/v1/attest` is also
  rejected as a shortcut: its input contains an enrollment secret and its
  output contains a reusable tunnel token. `challenge_response` is presently
  reserved by the local builder and not given mail semantics by the verifier.
  The mail Worker must not become custodian of those tunnel credentials.
- The MAIN journal's unit-2 preflight and Dave's final D7 call require separate
  mail-only Ed25519 identities. An existing device key can prove its identity
  **once during a human enrollment ceremony**, with a separate purpose string;
  it must never sign mail send, delivery, reconciliation or ordinary renewal
  requests. That enrollment use itself needs explicit acceptance in 7a; it is
  not permission to reuse the tunnel key as the mail credential.

**Parked dependency:** the local backlog's MC-948 (`88235e19`) says Ron parked
the tunnel/control-plane release on 2026-09-28 and the deploy workflow remains
disabled deliberately. Its last recorded deployment is the June build plus the
MC-947 hotfix; that is a dated record, not a fresh production probe. The workflow
YAML still contains push triggers, which does not establish that GitHub has
enabled it. Master source and older remote-access design prose saying Access
has gone are not evidence that the JWT replacement shipped. No real account,
workflow-state, JWKS or production endpoint was queried in this preflight.

### Evidence shared by all three alternatives

The following is a proposed protocol, not an existing API. Timings and limits
are review candidates. No client-supplied callback-registration operation is
part of it; the service derives the origin from trusted provisioning records and
uses the receiver path fixed in its own supported-install contract.

1. A human starts enrollment through the existing authenticated dashboard and
   a new human-only local guard (unit 7b), after the mail disclosure. Generate
   the **mail** key locally in its separate keystore. The service reserves an
   opaque install id and expected generation in a durable pending record;
   record a resumable opaque flow id locally before proceeding. No ping id is
   accepted. A reconnect resumes this identity instead of minting another one.
2. The service creates a 32-byte random challenge, proposed 15-minute expiry,
   bound to that pending record and the originating human flow. Obtain fresh
   mailbox control as well as provider verification: Cloudflare's verification
   link can establish destination readiness, but cannot select an install.
   After the provider reports the exact destination verified, a separate
   single-use service code must be entered in the originating authenticated
   enrollment flow. Store a keyed digest, bound to that flow, not the code in
   logs. Proposed code: 128 random bits rendered for copying, five failed
   attempts then invalidate. A link GET, scanner, provider status change or
   forwarded approval cannot activate anything. Final confirmation is a
   human-only POST; no credentials or confirmation secrets travel in URLs.
3. Construct one bounded canonical UTF-8 JSON array `R`, using strings only
   and the transport's unambiguous compact encoding. Its ordered fields are:

   ```text
   ["clayrune-mail-enrollment","1",service_origin,flow_id,challenge_id,
    challenge_nonce,expires_ms,install_id,expected_generation,device_id,
    device_public_key,mail_key_id,mail_public_key,owner_address_sha256,
    authoritative_tunnel_uuid,authoritative_route_origin,
    "/api/agent-mail/ingress",manifest_sequence,service_key_id,
    service_public_key_sha256]
   ```

   The service fills authoritative route fields after lookup; the install
   checks them against its intended enrollment before signing. The owner hash
   covers the exact validated address bytes stored in the pending row; do not
   introduce Gmail-style alias equivalence. This hash is identified data,
   neither anonymous nor a password substitute. It is not public telemetry.
   Verify both `Ed25519(device_private, R)` and `Ed25519(mail_private, R)`
   against their respective trusted/claimed public keys. The device signature
   links a provisioned device to this **specific** mail key and challenge; the
   mail signature proves possession of the newly enrolled key. Neither proves
   mailbox control or human consent without steps 1-2. Human consent relies on
   the product's human-only guard, not a claim of cryptographic human detection.
4. An authority described in A/B/C checks the current non-revoked device row,
   account suspension state, device public key and provisioning relationship.
   It cross-checks the exact DNS record and tunnel ingress against the
   Cloudflare management API using service-owned read access, including tunnel
   deletion and expected hostname/service mapping. Never fetch a claimed URL
   to establish ownership. Missing fields, unsupported/local-only tunnel
   configuration, conflicting mappings or an unavailable authority keep the
   flow pending. A third-party provider needs its own reviewed adapter;
   `mc_remote_iface` alone does not make it compatible.
5. The Worker accepts only the authority's evidence for `SHA-256(R)`, and
   checks its own pending challenge, both key proofs, exact owner confirmation,
   fresh provider verification and the expected generation. Proposed authority
   grant lifetime: 60 seconds. Recheck expiry after asynchronous work and at
   commit. Atomically consume the grant id/challenge and compare-and-set the
   pending row; uniquely bind the provisioned device to a mail install, reserve
   its address forever, and serialize generation/revocation/capacity updates.
   Exact retries return the same result, changed transcripts refuse. Retrying
   an expired flow keeps the install identity but requires fresh evidence.
   Rotation replaces the existing binding; it cannot evade limits by creating
   another install. Activation counting happens once, after all readiness gates.

Firestore/Cloudflare reads and D1 commits cannot be one distributed transaction.
Use fresh versioned authority evidence with a proposed five-minute ownership
lease; revalidate before activation and renew before sends/replay once it
expires. No renewal on missing/revoked/changed records or authority failure.
Derive lease expiry from the authority's check time, never grant receipt or D1
commit time; a delayed grant cannot extend old evidence.
This bounds, rather than eliminates, races with external tunnel/account changes
to at most the lease window. Local mail-off still blocks immediately and service
generation revocation still follows the transport contract. The operational
lease uses the enrolled mail key for its request; it does not repeatedly ask the
device key to sign mail traffic. An ownership adapter can re-read its recorded
device/route binding without granting new consent or changing keys.

For a concrete operating budget, one uncached authority check can use two
Firestore document gets (device and account) and three Cloudflare reads (tunnel,
ingress configuration and the recorded DNS record). At five-minute renewal,
`I` continuously active installs give a steady cadence of `288 * I` checks/day,
`576 * I` registry reads and `864 * I` Cloudflare reads, excluding retries and
enrollment/provider verification. At 200 installs that is 57,600 checks,
115,200 registry reads and 172,800 Cloudflare reads/day. These are design-derived
upper-load examples, not observed traffic or a billing quote; idle installs
need no check until their next send/replay. Budget provider quotas and jitter
renewals; throttling must hold work rather than extend expired evidence. C with
manual renewal spends that same check cadence in operator time.

**Ownership is separate from delivery.** Even a valid grant does not set
`route_verified=1` or active consent. All alternatives still need a bounded,
signed machine-to-install custody probe at the exact registered origin/path,
with redirects refused, passing the unchanged edge and dashboard gates. A
browser loading the dashboard, DNS resolution, tunnel `healthy`, a generic 200,
or a browser-relayed signature does not prove that the mail Worker can deliver.
The existing Access-protected deployment supplies no proven mail-scoped machine
authorization here. Never copy a human cookie, passcode, tunnel token or paired
phone credential into the Worker, add an Access bypass, or exempt the receiver
from dashboard auth to finish enrollment. Where the existing route cannot pass
the probe, ownership can be recorded pending but mail remains inactive. A future
machine-auth solution is a separate decision/review, not silently included in B.

### A. Control plane issues a narrowly scoped signed enrollment grant

**Evidence chain:** human flow + mailbox code -> device and mail signatures on
`R` -> control plane reads its own device/account records and checks Cloudflare
provisioning -> control plane signs an ES256 grant -> Worker verifies it against
the configured issuer's JWKS -> Worker consumes it with its pending challenge.
Grant claims must include a distinct token type, fixed mail-enrollment audience,
issuer, subject, `jti`, `iat`, `nbf`, `exp`, device/tunnel/route ids, install and
expected generation, `SHA-256(R)` and authoritative-record version/check time.
The Worker pins ES256 and configured issuer/JWKS origin; no client `jku`, issuer
URL or algorithm negotiation. A dashboard-audience token cannot pass this check.

**Exists versus build:** registry, provisioning, device verification pieces and
ES256/JWKS source exist. A mail-specific issuance operation, verifier, challenge
binding, Cloudflare cross-check, lease renewal and local mail enrollment do not.
This is a new narrow authority operation to specify/review, **not** an existing
callback setter or a claim that `/session/start` can issue this grant today.
No tunnel token is returned to the mail Worker. A separate grant-signing key id
and purpose avoid treating a dashboard session as a mail capability.

**Parked control plane: YES.** Source change plus an authorized control-plane
deployment are required. Deploying master would also pull the parked Access/JWT
cutover and its signing-key, edge authorization and MC-947 compatibility gates
back into scope. A separately reviewed backport onto the recorded deployed
baseline might avoid that cutover, but still changes/redeploys the parked control
plane and needs Ron's go-ahead. Neither path is authorized by unit 7a.

**Abuse, replay, rotation and cost:** apply the shared single-use/capacity rules,
per-flow/device/owner/source and global verification limits before provider writes,
and the short grant/ownership leases. Rotate the grant issuer via controlled JWKS
publication/overlap through the last grant expiry plus skew and cache lifetime;
unknown issuer keys fail closed. Mail-service pin rotation is the separate human
ceremony below. Lowest long-term duplication of ownership logic, but highest
release coupling now: new CP module/tests, Worker verifier and provider reads,
plus the parked deployment review. No new independent registry reader service;
incremental request/read/signing cost is unpriced, not assumed free.

### B. Separate read-only enrollment verifier (recommended under the park)

**Evidence chain:** human flow + mailbox code -> both signatures on `R` -> a
separate mail-enrollment verifier reads the existing Firestore device/account
records with a new read-only identity and checks Cloudflare DNS/tunnel ingress
-> verifier issues a 60-second signed grant over the same claims/digest as A
-> Worker verifies the separately pinned enrollment-issuer public key and
atomically consumes the grant/challenge. Trust the issuer because its key is
installed in the Worker deployment configuration, never because the install
supplied it. The verifier checks the Worker-created challenge through a fixed,
authenticated service channel, not an install-supplied authority URL.

**Exists versus build:** existing provisioning records and local device signing
are usable inputs. No such verifier, grant key, authenticated service channel,
read-only identity, Cloudflare read adapter or mail challenge implementation
exists. Proposed deployment boundary: a small separate Cloud Run service, with
its own grant-signing secret, and a 256-bit random service-channel bearer secret
held only by that verifier and the Worker. Ron provisions both and the service
identity through the existing vault/managed secret surfaces; agents never create
credentials. Responses contain only the bounded evidence projection, never
device-row secrets, owner account history or tunnel tokens. Error responses do
not disclose whether an arbitrary device exists. Requests are bounded and
authenticated/rate-limited before database work; disable body/secret logging.

**Parked control plane: NO code change, redeploy, Access removal or workflow
enablement.** It does depend on read access to that control plane's current
registry and Cloudflare provisioning metadata. This is new infrastructure/IAM,
not a claim that all of 7a fits in the existing Worker. Ron must authorize that
access and deployment. The verifier must support the actually deployed row
shape; failure to verify it leaves enrollment pending. It cannot repair rows,
provision tunnels, delete Access apps or write to the registry.

**Abuse, replay, rotation and cost:** shared rules and leases apply. Pin a
dedicated issuer key in the Worker; rotate it by a reviewed deployment with an
overlap lasting only outstanding grant validity. The install's mail-service pin
uses the human ceremony below, not this service-channel secret. Keep CP secrets
out of the multi-tenant mail Worker. However, **read-only is not harmless**:
device rows already contain `cf_tunnel_token` and `enrollment_token_hash`.
A Firestore field mask is output minimization, not an IAM security boundary;
compromise of the verifier's database identity can expose readable row secrets.
Do not describe a database viewer role as field-scoped. Review actual IAM
reach before approval; the isolated verifier's broader read privilege is an
explicit cost. Build/operate a new service, adapter and credential rotation,
plus enrollment/read/signature calls and fail-closed renewal availability.
No measured traffic/billing or dollar estimate is available from this preflight.

### C. Operator-signed enrollment grants for a bounded pilot

**Evidence chain:** the Worker creates the same pending challenge -> human
completes the mailbox/consent flow and local dual-key proof -> an operator uses
existing authorized read-only access to inspect the authoritative device row,
Cloudflare tunnel/DNS and signed `R` -> an operator-controlled offline enrollment
key signs the same short-lived, challenge-bound grant -> Worker verifies that
pinned operator issuer and consumes it. Inspection of a hostname or screenshot
alone is insufficient. The operator signs only after checks, with a fresh
challenge if inspection outlives its expiry. Never email tunnel credentials or
turn a free-text operator note into machine authority.

**Exists versus build:** the records/management surfaces exist; no audited
operator grant tool, issuer key distribution, review queue, renewal procedure or
mail enrollment flow exists. The tool needs to fetch records directly, validate
both signatures and project evidence before the operator's explicit signing
action; no manual SQL inserting arbitrary callbacks. The service pin still comes
from the independently authenticated manifest below, not an operator's email.

**Parked control plane: NO code change or redeploy.** Existing authorized
registry/Cloudflare reads and new operator signing custody are still required.
This **changes D1 from managed to operator-approved enrollment** and cannot be
adopted without Dave explicitly revisiting D1. It is an honest pilot alternative,
not an implementation fallback if A/B fail.

**Abuse, replay, rotation and cost:** keep all shared challenge and verification
limits; require operator review before destination creation to avoid turning
the queue into a verification-mail cannon. Single-use grants prevent replay,
not an operator selecting the wrong install; tooling must show exact key
fingerprints, owner and route. Issuer rotation requires a reviewed Worker trust
set update. Continuing ownership checks still need either recurring operator
signed leases (five-minute policy makes this impractical) or B's read-only
renewal adapter. No permanent manual grant or infinite ownership lease. Lowest
automated initial issuance work, highest human operating cost and availability
constraint; with automated renewal it also carries B's service/IAM cost. Useful
only for scheduled short pilot windows, not managed general enrollment.

### Install learns and rotates the mail-service public-key pin

This applies to A, B and C. The ES256 control-plane JWKS, enrollment-grant issuer
and Ed25519 **mail delivery** pin have different purposes. Learning an unknown
mail key from a response signed by that same unknown key is circular trust.

**Bootstrap proposal:** ship a dedicated offline mail-enrollment root **public**
key in the app's reviewed release. Its private key remains operator-controlled,
outside the Worker and registry; it is new key management, not reuse of tunnel,
device or dashboard signing keys. Use it to verify a public manifest obtained
from a fixed configured service origin. The signed, strictly bounded canonical
manifest contains purpose/version, origin, monotonic sequence, issuance/expiry,
mail key id and Ed25519 public key, activation/retirement times, and explicitly
retired key ids. Verify signature, origin, purpose, validity and supported key
encoding before use. Persist the highest accepted sequence; reject rollback or
same-sequence conflicting bytes. HTTPS protects transport; the bundled root
authenticates the key. This inherits the app installation/update trust baseline;
it does not claim the current updater already authenticates such manifests.
The public root/manifest implementation and operator signing procedure are new.

Include the accepted manifest sequence and mail-key fingerprint in `R`. After
the originating human confirms the exact owner/install enrollment, the local
unit-7b code records the public pin using the existing
`MailIngress.pin_service(ServicePin(...))` seam. Service-signed ingress cannot
set it. A/B/C's grant must match that fingerprint; a transport request or an
untrusted key URL can neither bootstrap nor replace the pin. No new root or
private key is generated in this design round.

**Planned rotation:** the operator signs a higher-sequence manifest with the
offline root, publishes it before using the new operational key for new
enrollments, and keeps signing existing installs with their enrolled old key
during a bounded overlap. The new key may be downloaded/verified as a candidate,
but **repinning requires a new explicit human enrollment confirmation, old
generation revocation and a new generation**, as required by the transport and
immutable local pin contract. Reverify owner and ownership; do not transfer old
threads/events into the new generation. The service and local store converge
through a pending state, not an in-place pin overwrite. Changing the install's
mail key uses the same new-generation ceremony. Missing old private material
requires ownership/human recovery; a signature from the replacement key alone
does not recover an identity.

**Offline/compromise recovery:** an install absent beyond overlap stops at an
unknown/retired key, then resumes authenticated human enrollment against the
offline-root-signed manifest. A compromised operational key cannot authorize its
own replacement manifest; do not automatically repin even with an old-key
cross-signature. Revoked-key notification may disable an old pin without granting
a new one; an offline install learns retirement only on reconnect/refresh, not
instantaneously. Root compromise/loss needs a trusted app update or explicit
human recovery that supplies a separately authenticated new root; never trust a
root replacement merely signed by a suspected root or supplied in mail. An
operator rotation procedure and deliberate human repinning are the cost under
every option. There is no automatic remote key-update mechanism in the current
receiver, and this proposal does not add one.

### Recommendation and next decision

**Recommend B for 7a's ownership authority while MC-948 stays parked**, contingent
on Dave accepting the isolated verifier's IAM/read-secret exposure and Ron later
authorizing its credentials/deployment. It preserves D1's managed experience
without making mail depend on deploying master control-plane code that removes
the existing Access boundary. A is the cleaner long-term single-authority design
if Ron explicitly reopens the parked control plane. C makes its manual D1 change
and recurring renewal cost visible rather than pretending to deliver managed
enrollment. None is adopted by this preflight.

Dave must select A/B/C (or keep 7a pending) before implementation. That selection
does not authorize a live account call, credential creation, infrastructure or
DNS deployment, mail, or dashboard-auth change. In particular, **machine delivery
through the unchanged gate remains unresolved for all three options**; a build
can establish pending ownership without claiming reply-capable activation.

### Evidence, documentation checks and limits of this round

Read the transport signing/enrollment/unit-4 handoff, spec Enrollment/D1/D7,
MAIN journal's unit-2/D7/unit-7 decisions, MC-948 and MC-1166 backlog records,
commit `837d6d7` and the current source cited above. Also read the MAIN
checkout's older `docs/remote-access/02-attestation-protocol.md` and
`03-control-plane-api.md`, which are absent from this worktree; design prose
does not override the parked deployment record or current code.

Public primary references consulted 2026-11-09 (documentation only, no real
account/API request):

- [Cloudflare tunnel configuration read API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/subresources/configurations/methods/get/)
  supplies hostname/ingress configuration with a tunnel-read permission. This
  supports a metadata cross-check; it does not prove possession of a device key.
- [Cloudflare destination lookup](https://developers.cloudflare.com/api/resources/email_routing/subresources/addresses/methods/get/)
  exposes the exact destination and its verification timestamp; absent
  verification is not readiness. This cannot bind a human confirmation to an
  installation by itself.
- [Firestore server IAM](https://docs.cloud.google.com/firestore/native/docs/security/iam)
  describes document reads and the broad viewer role. The inference here is
  that an application choosing fewer output fields must not be presented as
  restricting its service identity to those fields.

This is a design consistency/source review; no executable implementation or
test file changed. Tests: 0 run, framework result/exit N/A; pyright and browser
smokes N/A. `git diff --check` and scoped committed-diff review are the checks
for this document. README, USER_GUIDE, CHANGELOG, rules and memory need no edits:
there is no shipped/user-visible behavior or new adopted policy. The
document-commit-deploy skill's push/deploy steps are excluded by this dispatch.
MAIN journal receives a separate append so the checkpoint survives the worktree.
