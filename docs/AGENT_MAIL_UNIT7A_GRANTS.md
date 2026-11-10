# Agent mail unit 7a: signed ownership grants

Status: **disabled, local review candidate; not deployed or reply-capable**.
Authority choice: Dave selected A in the unit-7a trust-authority decision.
This supersedes the preflight's unadopted recommendation B, not its delivery gate.

## Module boundaries

- `workers/agent-mail/enrollment.js`: human-flow enrollment core; no HTTP route.
- `enrollment-challenge.js`: random challenge/code and purpose-bound keyed digests.
- `enrollment-transcript.js`: strict canonical R and dual Ed25519 proof verification.
- `enrollment-grant.js`: pinned ES256 authority-grant verification, no network client.
- `migrations/0001_enrollment.sql`: pending identities, challenges and atomic ownership commits.
- `control_plane/app/mail_enrollment_transcript.py`: matching canonical R encoding.
- `control_plane/app/mail_grant_signing.py`: explicitly provided mail-purpose P-256 signing key.
- `control_plane/app/mail_grants.py`: authority reads and grant issuance via injected adapters.
- `mc/agent_mail_service_manifest.py`: offline-root signature and rollback-floor verification.

There are no new public enrollment/CP/JWKS endpoints, startup hooks, production
adapters, bundled root keys, signing-key generation or configuration mutations.
All three callable cores default to disabled. Existing Worker entrypoints and
dashboard/Access gates are unchanged. Passing `enabled=True` in a test or adapter
does not establish consent, a published issuer, or production readiness.

## Enrollment contract

Future unit 7b must guard every call with the originating authenticated **human**
ceremony. The opaque flow id alone is insufficient: the core also requires a
32-byte flow secret, retains only its SHA-256 digest, and compares it on resume,
confirmation and completion. Never expose `begin()` directly as an agent API.
No mailbox code, flow secret or confirmation request belongs in a URL or log.

`begin(value, flowSecret)` takes exactly flow id, owner address, device id/public
key, mail key id/public key, claimed tunnel/route and the accepted service-pin
sequence/id/fingerprint. The service pin must match trusted constructor config;
an unknown-key response cannot supply that configuration. The exact validated
owner address is stored and hashed without mailbox alias equivalence. The input
owner must already be in the existing parser's canonical mailbox encoding.

The core reserves one opaque install/address for a provisioned device. It
generates a 32-byte challenge, 15-minute expiry and 128-bit copying code, storing
only HMAC-SHA256(code purpose/version/flow/challenge/code) under an explicitly
provided, separate 32-byte secret. Five wrong answers atomically invalidate the
flow and erase the digest. A correct answer erases the digest and records the
confirmation; repeating confirmation only reports that same flow's state.
Provider verification, a scanner GET, and a link click cannot confirm this code.

`begin()` returns the code **only to a trusted service-side delivery adapter**,
once. That adapter must send only to the stored owner, with provisioning abuse
limits; it must never return the code to the requesting install/agent. This unit
does not implement real mail or destination creation. Resume returns the same
transcript/status, without reissuing the code. If that initial return was lost
before delivery, start a fresh flow after expiry, retaining the install/address.
The generation has at most one pending flow. Invalidated/expired challenges do
not mint another install for the device.

The library conservatively limits lifetime reserved identities to 200, adjustable
downward by trusted configuration. This is a bounded synthetic service budget,
not an observation of available Cloudflare destination capacity. No pending-row
garbage collection or address reassignment is implemented. Provider account
capacity, rate limits, durable code delivery and cleanup are integration gates,
not claims inferred from the constructor's budget.

R is compact ASCII JSON of exactly these 20 **string** fields, in order:

```text
["clayrune-mail-enrollment","1",service_origin,flow_id,challenge_id,
 challenge_nonce,expires_ms,install_id,expected_generation,device_id,
 device_public_key,mail_key_id,mail_public_key,owner_address_sha256,
 tunnel_uuid,route_origin,"/api/agent-mail/ingress",manifest_sequence,
 service_key_id,service_public_key_sha256]
```

Keys use canonical unpadded base64url of 32 raw Ed25519 public-key bytes;
fingerprints/owner hash use lowercase SHA-256 hex. No callbacks, query strings,
ports, Unicode, object-order dependence or floating-point numbers enter R.
Both the provisioned device key and the separate mail key sign the **same R**.
Device-key use is limited to this enrollment proof, never ordinary mail traffic.

## Authority and verifier

`MailGrantIssuer.issue()` checks both proofs, then reads the authoritative
device and its account; it checks exact id/key, explicit nonrevocation,
active provisioning and explicit nonsuspension. Missing/unsupported schema
fails closed, including absent revocation/suspension fields.
Adapters may normalize supported legacy rows only under a reviewed contract;
this unit makes no assertion that the parked deployment already fits the shape.

The injected reader is configured for the authority's account/zone. It reads the
recorded tunnel, ingress and DNS record by their authoritative ids. It must
return versioned `Snapshot(data, version)` objects from fresh reads. Accepted
Cloudflare projections require remote (`cloudflare`) configuration, an explicit
nondeleted tunnel, exactly the expected hostname-to-local-service mapping plus
404 catch-all, and the recorded proxied CNAME to that tunnel's
`<uuid>.cfargotunnel.com`. Extra ingress rules/path restrictions, deleted/local
tunnels, conflicting DNS, missing versions and read failures refuse issuance.
No claimed origin is fetched. Unsupported providers need a separately reviewed
adapter; unavailable adapters do not trigger a fallback authority.

Only version strings and an allowlist of binding claims enter the grant; device
rows/tunnel tokens never leave the authority in it. Adapter errors become a
constant refusal without their exception body/chain. Reads are not a distributed
transaction: evidence freshness starts **before the first authority read**.

The signer requires an explicitly supplied P-256 private key with
`purpose=clayrune-mail-enrollment` and `kid=mail-grant-...`. It never loads the
dashboard key, generates a fallback key or publishes JWKS. Future operator
configuration must use independently managed key material for this purpose.

The Worker accepts exactly `alg=ES256`, header type
`clayrune-mail-enrollment+jwt`, and audience/token-use
`clayrune-mail-enrollment`. Trust configuration supplies the exact issuer,
service origin, dedicated `<issuer>/v1/mail-enrollment/jwks` location and its
reviewed public JWK set. The verifier performs **no fetch**; publication,
authenticated configured-origin JWKS loading/cache/rotation remain integration
work. It rejects header extras (`jku`, inline JWK, `crit`, etc.), algorithm
negotiation, unknown/duplicate key ids, wrong curves and private JWK material.
Dashboard JWTs fail the type and audience checks even if accidentally offered.

Claims bind issuer, account subject, jti, iat/nbf/exp, device, tunnel, route,
install, expected generation, R SHA-256, authoritative-version digest and
`checked_at_ms`. Grant lifetime is at most 60 seconds; no clock-skew grace is
used. Both keys, owner confirmation and fresh exact provider destination
verification are independently required at the Worker. The provider's freshness
starts before its I/O. Crypto and provider latency cannot extend the grant.

## D1 state and ownership lease

Apply `migrations/0001_enrollment.sql` **after** the existing `schema.sql`, only
under a later authorized migration/deployment. No migration ran against D1 here.
Transport-table column layouts are unchanged.

One INSERT into `mail_enrollment_commits` owns the unique grant jti and flow.
SQLite admission time is computed inside that INSERT. A BEFORE trigger checks
challenge/code/provider/grant freshness and installation generation CAS; an
AFTER trigger advances the generation, writes the inactive registration and
consumes the challenge. Constraint/trigger failure rolls back **all** effects.
This does not depend on cross-statement `changes()` propagation in D1 batches.
An existing active registration refuses replacement; the later human revocation
ceremony must first disable it. Old threads are never migrated to a new generation.

The ownership lease ends at `checked_at_ms + 300000`, never receipt/commit time.
Even a delayed accepted grant cannot renew old evidence. Exact authenticated
retries of an already consumed token return the original pending result, even
after expiry; they perform no renewal, fresh grant claim or reactivation. Changed
tokens/transcripts refuse. Concurrent identical commits converge on the durable
result. A previously consumed jti cannot complete another flow.

**Every successful result has `active=false`, `route_verified=false`, and
`state=ownership_recorded_delivery_pending`.** No activation counter is
incremented. Ownership is separate from delivery and consent. Before any future
activation/send/replay, integration must revalidate or renew expired ownership
under the mail-key authority contract and independently prove machine custody
through the unchanged dashboard/Access gates. Lease renewal, activation, remote
revocation/deletion APIs and their human controls are not implemented here.

## Offline service manifest

The library accepts a separately trusted offline Ed25519 root public key and an
explicit origin; it does not learn either from mail, ingress or an unknown key.
The signed canonical array is:

```text
["clayrune-mail-service-manifest","1",origin,sequence,issued_ms,expires_ms,
 key_id,Ed25519_public_key,activate_ms,retire_ms,sorted_retired_key_ids]
```

Scalars are bounded ASCII strings; retired ids are a sorted unique list, capped
at 100. Purpose/version/origin, encoding, key identity, time bounds, retirement
and root signature must all pass. A durable caller-provided store must atomically
compare-and-set `(sequence, payload_digest)` against its previous value, outside
the repository. Lower sequences and changed bytes at the same sequence refuse.
Even an exact-sequence retry uses CAS to reject a concurrent higher manifest.
Corruption and storage failures never reset the rollback floor.

The return value is a **candidate**, never an installed pin. Unit 7b must verify
the human's new-generation ceremony and use the immutable local pin seam. An
operational mail key cannot sign its own replacement manifest. Root replacement
requires a separately trusted app/human recovery path. No real root exists or
is bundled by this unit; tests create ephemeral keys in test functions only.

## Validation and remaining gates

The Worker suite uses SQLite, real crypto and synthetic provider callbacks.
The CP suite uses fake registry/Cloudflare snapshots; one cross-language test
passes a real test-issued Python ES256 grant to the actual Worker verifier and
compares canonical R bytes. Manifest tests cover rollback, equal-sequence
conflicts, operational-key forgery, retirement and failed/racing persistence.

Run each Python package separately: both test trees have `tests.conftest`, so a
single mixed pytest invocation fails collection with ImportPathMismatchError.
Use the installation test launcher from this branch's root, preserving argv:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- node --test workers/agent-mail/test/*.test.js
python <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest control_plane/tests
python <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest tests/test_agent_mail_service_manifest.py
pyright mc/agent_mail_service_manifest.py
```

Exact operator-specific command/result records and the blocked optional global
plan-file write are in the MAIN checkout's ignored item journal. No frontend
changed; boot/mobile smokes are inapplicable. README, USER_GUIDE and agent rules
need no change for inaccessible, internal libraries. No new dependency was added.

Final branch results: Worker **797 passed**, control plane **98 passed / 1
skipped**, manifest **22 passed**; all three launcher invocations exited **0**.
The CP skip is the existing edge-contract test requiring a sibling
`clayrune-cloud` checkout. Pyright across all four new Python modules reported
**0 errors / 0 warnings**, exit **0**. The CP run reported incomplete launcher
progress telemetry (`telemetry_queue_full_progress_incomplete`); that is not
presented as complete dashboard reporting. Framework counts come from the actual
test output, not script-unit telemetry. SQLite trigger rollback, grant/challenge
single use, active-generation replacement refusal, database-time expiry and
read-start lease anchoring have explicit tests.

Still unverified: actual D1 trigger/JSON/time behavior and remote contention;
deployed registry/Cloudflare schemas, clock alignment and read adapters; real
destination/code mail; human API/secret-store/durable manifest-store wiring;
signing/root/JWKS provisioning and rotation; ownership renewal and revocation;
unchanged-gate machine custody; privacy/capacity review and real-mail continuation.
The parked control plane requires Ron's authorization before deployment, including
any separately reviewed backport. No deploy, JWKS publication, real key generation,
credentials, cloud/Firestore/mail call, merge, push or restart was performed.
