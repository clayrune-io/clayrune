# Passkeys for human-only operations

**MC-1023 / e1bd5f1c · 2026-10-04 · specification only.** Baseline: `ea7817ba`. No implementation or security policy changes are authorized by this document. Dave must resolve the final section before affected slices ship.

## Purpose and fixed requirements

For the dashboard owner, replace repeated MC-995 passcode entry with WebAuthn verification on every human-only operation. Preserve passcode fallback where no passkey is registered, existing authorization restrictions, and one approval per operation. No reusable unlock token, remembered approval, agent enrollment, or automatic fallback after a rejected assertion.

Out of scope: replacing dashboard login/tunnel authentication, multi-user roles, passkey-based vault encryption, and sandboxing host processes. Native app implementation belongs to a separately authorized task; this spec defines its server contract.

## Code-grounded gate inventory

Paths below are relative to `mc/blueprints/`; method/branch distinctions matter. The old “27 routes” is not a current inventory: `tests/test_human_proof_guard.py` already documents its historical count discrepancy.

| Source | Operations that must enter the shared proof boundary |
|---|---|
| `secrets_routes.py` | Secret POST, PATCH, DELETE; authenticator import **commit=true**, preserving preview; `vault-lock/{set,change,unlock,lock,retire-legacy}`. |
| `workflow_routes.py` | Definition create/update/delete/draft; run decision/cancel. |
| `character_routes.py` | Create/team/voice/identity; update/name/avatar/move/delete. |
| `agent_routes.py` | Provider install-launch, batch install-launch, `attend-once`. Deprecated `attend` remains unconditional 410. |
| `settings_routes.py`, `distiller_routes.py`, `guide_routes.py` | Config PUT; distiller promote; brainstorm transfer. |
| `backup_routes.py`, `system_routes.py` | Restore; import apply; rollback non-dry-run; system update's frozen-installer and source `stash=true` branches. |
| `addon_routes.py` | Request approve/decline, install/remove. |
| `desk_routes.py`, `desk_connect_routes.py` | Campaign start/approve/renew; version approve/posted; engine job submission, limit change, render; connection start/disconnect/commit; engagement reply. |

These use `_require_human_passcode` directly or through helpers. It calls `local_auth._local_auth_try_passcode`, sharing the concurrent, per-IP guessing budget. Frontend entry is `static/js/human-proof-modal.js::humanProofFetch`; vault and attend-once also have dedicated callers.

Additional boundaries found, **not currently equivalent passcode gates**:

* `desk_routes.py`: account create/delete; account PATCH when changing `read_via`/`browser_profile`; engagement PATCH beyond agent draft fields; paid engagement poll; presence account read settings; findings confirm/reject/dont-suggest-again/undo-reject/reconfirm/retire. These rely on `is_unattended_caller`.
* `desk_services_routes.py`: service POST/PATCH/DELETE; `desk_connect_routes.py`: verify. Both use the same caller heuristic. Engine estimates/polls and connection tests pass unattended context downstream; preserve existing read permissions, not indiscriminate prompts on polling.
* `browser_pick_routes.py`: pick and hover use caller attribution plus Origin, intentionally without passcode. `settings_routes.py::setup_complete` similarly exempts the harmless wizard flag. Dave must settle these explicit exceptions.
* `project_routes.py`: legacy social queue approve/posted have no cryptographic human proof. Preserve their non-publishing behavior; protect human decision writes and alternative status-writing paths.
* `push_mobile.py`: pairing config GET reveals a credential-bearing `pair_uri`; config PUT/DELETE, generate, and token DELETE lack this proof. Treat reveal as an explicit gated operation, not a routine GET. `remote_routes.py` enable/disable/resume/disconnect and session revoke/revoke-all also need owner-action classification and proof; OAuth/handshake callbacks keep protocol validation rather than interactive prompts.
* `local_auth.py`: first passcode setup and current-passcode rotation are distinct bootstrap/recovery boundaries. Its `_local_auth_exempt()` includes tunnel traffic: **do not reuse it as a host-only enrollment check**.

`mc/desk_tick.py` preserves exact-version approval, campaign bounds, cadence, vault unattended policy, and receipts. An assertion approves one immutable operation, including an explicitly scheduled version; it is never replayed by the tick. Content/account/schedule changes invalidate approval. Campaign approval does not approve arbitrary future content. Keep `mc/desk_publish.py` checks and duplicate/unknown-outcome handling.

## Threat model and limits

With an intact server, credential registry, browser, and authenticator, a shell caller cannot forge a registered private-key signature by inventing Origin headers, stealing a dashboard cookie, reading public keys, or replaying a consumed challenge. A protected phone/security key can require interaction unavailable to an unattended agent.

This is **not proof against unrestricted same-user shell access**. Such a process can rewrite server code, replace registered keys or policy files, tamper with approvals/audits, instrument the browser, or attack unlocked process memory. Outside-repo storage prevents bundling, not same-user tampering. Strong isolation would require a separately protected verifier/store, outside this scope. These limits already appear in [SECRETS.md](SECRETS.md); its vault rules remain binding.

User verification can mean biometric, device PIN, or another authenticator-local check. A UV flag does not prove a fresh biometric, a particular person, hardware custody, or that the person understood the action. Password managers may reuse verification state; software authenticators can assert UV. [WebAuthn specification](https://www.w3.org/TR/webauthn-3/), [Chrome virtual authenticators](https://developer.chrome.com/docs/devtools/webauthn/).

An unrestricted host attacker, a compromised UI misleading the owner, or an accepted stolen fallback passcode defeats the intended boundary. In those cases passkeys add no effective protection over the passcode. Their narrower benefit is phishing-resistant, operation-bound authorization without sending a reusable approval secret to Clayrune.

## Enrollment and storage

First enrollment must originate from the host dashboard: direct loopback peer, exact configured localhost Origin/Host, no tunnel/proxy path, existing passcode retyped. LAN and tunnel requests cannot bootstrap themselves. Loopback alone does not prove humanity; even this depends on trustworthy initial passcode setup. Require setup before agents run; do not silently create credentials on an empty installation.

Registration uses a random, expiring, single-use server challenge, opaque owner handle, `userVerification: required`, duplicate exclusion, and server verification before activation. Proposed compatibility profile: `residentKey: preferred`, no attachment restriction, attestation `none`; UV-capable security keys remain eligible. Additional enrollment/revocation must be authorized by an existing credential for that operation; the new credential cannot authorize itself. Settings lists labels, RP, last use and revocation, with separately gated add/rename/remove actions. New-RP enrollment and lost-device exceptions await Dave.

Store schema-versioned credential ID, COSE public key, RP ID, owner handle, transports, counter, backup flags, label, creation/last-use/revocation times and policy epoch under `~/.clayrune/passkeys/` (`CLAYRUNE_HOME` override for tests). Never use the repo or `DATA_DIR`. Use locked, atomic writes, POSIX 0700/0600 or restrictive Windows ACLs; malformed/missing established stores fail closed, never “zero credentials.” Preserve an enrollment marker and revocation tombstones. Exclude registry rollback from ordinary project restore/builds; explicit recovery must invalidate outstanding challenges.

Private keys/biometrics stay with the provider. Audit operation ID, credential reference, outcome and time without bodies, secrets, assertions or reusable tokens. Challenges are bounded in-memory records, invalid after restart; request-size/rate limits prevent memory or verification exhaustion. Handle synced credentials' zero/non-monotonic counters explicitly; counters alone cannot detect every clone. Dave chooses compatibility policy.

## One ceremony, one operation

Proposed shared modules: `mc/human_proof/` with separate operation registry, challenge store, verifier and credential store; a new blueprint; `static/js/passkeys.js` called by `humanProofFetch`. Keep registries declarative and route business logic in its owning module.

Proposed API: POST `/api/human-proof/prepare`; POST `/api/passkeys/register/{options,finish}`; GET `/api/passkeys`; PATCH/DELETE `/api/passkeys/<id>`. Preparation creates no authority; only the target operation can consume its proof. Return distinct unsupported, proof-required, expired, stale-operation and invalid-proof errors; never turn errors into passcode eligibility.

1. Prepare through a registered action descriptor, not arbitrary URL execution. Validate authorization and payload schema; normalize defaults and resolve referenced content. Bind method, route, project/resource IDs, exact payload, resource revision, browser-session nonce, RP/origin, policy epoch and expiry. Server computes SHA-256 over a versioned deterministic encoding. Bind file bytes/snapshots, not merely their mutable paths. Include secret inputs without exposing them in review text or logs; use only ephemeral storage.
2. Display the server's action summary, target/account, irreversible effects, cost/schedule, and content revision. Freeze that operation. Any edit requires a new challenge. A challenge is 32 random bytes, uniquely bound in server state to the operation digest and ceremony type. A payload hash alone is predictable and insufficient. Never expose secret-bearing hashes as challenges or review fields.
3. Explicit confirmation calls `navigator.credentials.get` with UV required and registered credentials for this RP. No conditional/autofill approval. Server checks type, exact challenge and allowed origin, RP hash, signature, credential ownership/revocation, UP and UV, expiry, session binding and policy epoch; reject cross-origin/iframe assertions. Never trust a client-supplied digest or success flag. [Verification reference](https://duo-labs.github.io/py_webauthn/authentication.html).
4. The original action endpoint accepts the assertion and operation ID, rechecks the normalized request and referenced revision, atomically consumes the challenge, and executes only that operation under the relevant mutation lock. It returns the operation result, never a portable approval token. Secret/proof fields cannot leak into domain stores.
5. Cancellation, stale payload, timeout, origin mismatch or replay performs no side effect. Consumption remains final after failure; retries need new proof. Existing idempotency receipts may return a known result without executing again. Unknown external outcomes remain held for reconciliation, never automatic resend.

Proposed challenge lifetime: two minutes. Dashboard session cookies correlate ceremonies only; they grant no human-proof authority. Keep existing caller/policy checks, CSRF defenses and shared passcode throttle. An assertion must not change an agent's trigger classification or enlarge an attend-once allowance.

The digest encoding must sort object keys, preserve array order and value types, and reject duplicate keys, unknown fields and non-finite numbers. Exclude only transport proof/passcode fields, never business fields. Bound secrets must remain present in the executed payload. Tests must cover equivalent encodings and semantically different null/missing/default values.

Vault unlock still needs its passphrase or recovery key to unwrap the master key. Passkeys authorize the route only. Preserve lock/idle-lock, retirement of legacy keys, metadata-only management responses and agent-use/human-create policy. No WebAuthn PRF/encryption shortcut.

## Origins, platforms and recovery

The server must maintain an explicit RP/origin allowlist, never derive trust from arbitrary Host/forwarded headers. RP ID omits scheme/port; the permitted origin includes them. Use `localhost` and the exact tunnel hostname as separate RPs with separate registrations; never share a parent domain across installs. Tunnel hostname changes require enrollment again. Multiple local installations need separate owner handles/registries and exact port origins; localhost itself is not machine-unique.

| Surface | Required behavior |
|---|---|
| Host browser | Canonical `http://localhost:<port>`; Windows Hello, macOS Touch ID/iCloud Keychain, compatible keys/managers. Loopback aliases are not implicitly interchangeable RPs. |
| Linux browser | Offer phone hybrid QR + BLE, UV-capable USB/NFC key or compatible manager. Bluetooth/browser support is necessary for hybrid; no proprietary QR protocol. |
| Phone browser | HTTPS tunnel; Face ID/fingerprint or provider verification. Phone localhost means the phone, not the server. |
| Plain HTTP LAN IP | No WebAuthn; explain secure-context requirement and offer the HTTPS tunnel. Passcode fallback remains where permitted; HTTP exposes it to network interception. |
| HTTPS LAN hostname | Requires trusted TLS and separately configured RP/origin; not automatic IP support. |

Sources: [secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts), [FIDO hybrid authentication](https://fidoalliance.org/passkeys/), [Apple passkeys](https://developer.apple.com/videos/play/wwdc2022/10092/).

Capacitor is not itself a WebAuthn guarantee. Android WebView supports Credential Manager integration with AndroidX WebKit, runtime `WEB_AUTHENTICATION` detection, explicit `setWebAuthenticationSupport`, and Digital Asset Links; conditional mediation is unsupported. [Android integration](https://developer.android.com/identity/sign-in/credential-manager-webview). iOS WKWebView supports passkeys for associated RPs; configure `webcredentials` entitlement and AASA. Apple identifies system-browser authentication as an alternative for other RPs. [Apple engineer guidance](https://developer.apple.com/forums/thread/723273).

Custom app schemes are not the tunnel's HTTPS origin. Dynamic tunnel hosts require association-file provisioning and signed-app testing. This repository does not establish the shipped shells' native configuration; neither APK nor iOS compatibility is claimed verified. An unsupported shell must direct the owner to perform the complete operation in the authenticated HTTPS browser, not return a reusable unlock token or transfer service credentials in a URL. Tauri/WebView2 also needs an explicit compatibility test.

Recommend two independently recoverable credentials per RP. Losing one: use another to revoke it; revocation invalidates pending ceremonies. Synced credentials may survive device loss but inherit provider-account recovery risk. Losing all: no automatic downgrade; offer the host recovery procedure Dave selects. Never reset the vault passphrase or decrypt vault data as a side effect of passkey recovery.

## Library and shippable slices

Candidate: Duo Labs `py_webauthn` (PyPI `webauthn`), four generation/verification APIs, Python 3.10+. [Repository](https://github.com/duo-labs/py_webauthn), [BSD-3-Clause license](https://github.com/duo-labs/py_webauthn/blob/master/LICENSE). PyPI lists 3.0.1 uploaded September 25, 2026, and earlier 2026 releases: recent maintenance evidence, not a security warranty. Pin the reviewed version/dependencies, retain notices, inspect advisories and verify frozen builds. [Release history](https://pypi.org/project/webauthn/#history).

| Slice | Acceptance required before shipping |
|---|---|
| 1. Core, disabled for actions | Host-only enrollment tests include forged headers, LAN/tunnel bootstrap refusal, duplicate/replayed registration, corrupt store, concurrent writes, revocation and restart. Unit tests use isolated `CLAYRUNE_HOME`; no real authenticator automation claimed. |
| 2. Complete gate migration | Generated route/branch matrix reconciles the inventory against Flask routes and helper callers. Every protected branch rejects missing/wrong/replayed proof and accepts the intended operation only. Test altered body/path/file/revision, concurrent assertion reuse, expiry, invalid signature/RP/origin/UV/UP, fallback eligibility and throttle. No partial rollout called “every route.” |
| 3. Cross-platform release | Real Windows Hello, Touch ID, Linux phone hybrid, security key, password manager, iOS/Android browser and native-shell trials. Test BLE off, cancellation, unsupported WebView, separate localhost/tunnel enrollment, domain change, lost devices, and unavailable tunnel. Run existing human-proof/vault/Desk/workflow smokes after each integration. |

Publishing tests use fake transports; assert one side effect, unchanged vault restrictions and held stale approvals. Virtual authenticators test protocol logic, not human presence. Each enabled platform records OS/browser/provider versions and observed verification behavior.

## Open decisions for Dave

1. **Fallback scope:** per-RP fallback (simpler; another unregistered origin can bypass passkey assurance) or installation-wide protection with explicit legacy-origin exceptions (more friction). Recommend the latter, retaining required fallback but disclosing its security ceiling; enabling exceptions is human-only.
2. **First tunnel credential:** retyped passcode at tunnel enrollment (simple, weakens host-only intent) or host-approved pending enrollment bound to the new credential key/hash, target RP and browser session (extra round trip). Recommend host approval; no activation by a transferable enrollment bearer alone.
3. **Lost-all recovery:** host passcode re-entry (familiar, passcode remains the weakest link) or separate offline recovery secret (extra custody/UI). Recommend host passcode plus explicit revocation/re-enrollment, with no remote automatic reset; Ron must approve any gate weakening.
4. **Assurance:** broad UV compatibility with managers/synced credentials or attested hardware-only policy. Recommend compatibility with the stated limited threat claim; hardware-only contradicts requested manager coverage and adds attestation operations. Neither protects a writable verifier.
5. **Intentional exceptions:** include picker hover/pick and setup completion literally (frequent prompts/bootstrap friction), or classify them as interaction filters outside authorization proof. Recommend documented exceptions, pending explicit scope approval; no silent omission.
6. **Delivery choices:** adopt `py_webauthn` versus evaluate Yubico `python-fido2`; integrate native shells initially versus browser-first. Recommend py_webauthn and browser-first, followed by separately tested shells. Alternative costs: broader dependency review and native signing/domain-association work before initial release.

## Decisions (Dave, 2026-10-04; Ron may overrule any of them)

1. **Fallback scope:** protection is installation-wide. Legacy-origin exceptions are explicit, human-only to enable, and disclosed as the security ceiling. Accepted.
2. **First tunnel credential:** the host approves a pending enrollment, bound to the new credential's key hash, target RP and browser session. A bearer link alone never activates one. Accepted.
3. **Lost-all recovery:** host passcode re-entry plus explicit revocation and re-enrollment. No remote automatic reset. Accepted.
4. **Assurance:** broad compatibility (UV, synced credentials, password managers), with the limited threat claim above. Accepted.
5. **Exceptions:** picker hover/pick and setup completion are documented interaction filters, outside authorization proof, and listed by name. Accepted.
6. **Delivery:** `py_webauthn`, pinned, browser-first. Native shells come later as their own slice. Accepted.

7. **Proof at provisioning, not at use (Ron, 2026-10-06: "reduce friction as much as possible ... when it comes to agents launching actions which were originally provisioned by the user").** A human proves presence once, when they CREATE or WIDEN a grant: connect an account, approve a campaign and its bounds, approve a version, set an engine spend limit, enable a workflow, mark a secret usable unattended. An agent acting INSIDE that grant (a render under the set limit, a post of an approved version on its schedule, a run of an enabled workflow) needs no further prompt. Per-operation proof stays for: creating or widening any grant, credentials and the vault, settings that expand what agents may do, installs/updates/restore/rollback, deleting user data, add-ons, character edits, and publishing anything no approved version covers. Every grant is bounded (scope, spend or count, expiry where the thing has one), visible and revocable in one place, and an agent can never create or widen one. Gates that exist today only because nothing recorded the provisioning (for example a per-job passcode on storyboard render) become grant checks in slice 2.

Build order: slice 1 shipped (655d6746). Ron read the spec and accepted decisions 1-7 on 2026-10-06. Slice 2a (the four prerequisites below, `mc/passkeys/` only) builds now. Slice 2b (gates read passkeys, decision 7 grants) builds after the Desk Connections and Studio work lands, since it touches the same route files.

## Slice 2 prerequisites (from the slice 1 audit, 2026-10-04)

Slice 1 stores a passkey registry that nothing consults. Before slice 2 makes any gate read it, these must be settled:

1. **Registry integrity against a same-user shell.** The registry is a plain file in `~/.clayrune/passkeys/`; a process running as the same OS user can rewrite it, so a passkey enrolled by an attacker would verify. Either MAC the registry (and the enrollment marker) with a key derived from the unlocked vault, or state plainly in the Settings UI that passkeys add no protection against a shell running as the same user. Do not ship slice 2 with the claim unstated.
2. **Add-credential and revoke move behind passkey proof once one exists.** Slice 1 gates both on the retyped passcode only. After the first passkey is enrolled, adding or revoking one must require a passkey assertion, with the passcode path kept only as the documented lost-all recovery (decision 3).
3. **A documented host reset for the registry-without-marker crash state** (`store._write_state` writes the registry, then the marker; a crash between them leaves a registry holding credentials with no marker, which `load()` refuses rather than reading as empty). The reset is a host-only, passcode-gated action that also clears pending challenges, and is written up where an operator will find it.
4. **Stale-lock double-unlink race in `store._write_lock`.** Two waiters can both judge the lock stale; one unlinks it and takes a fresh lock, then the other unlinks that fresh lock. Fix before concurrent assertion writes (counter updates) make it reachable.

## Slice 1 delivery note

`webauthn` and its pins live in `requirements-passkeys.txt`, not `requirements.txt`: `cbor2` is a Rust extension with no wheel on every platform we install on, and `pip install -r` is all-or-nothing. The installers and `mc/update_requirements.py` install that file as a separate best-effort step; without it passkeys report unavailable and nothing else changes.
