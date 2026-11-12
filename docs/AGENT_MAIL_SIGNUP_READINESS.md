# Agent mail: owner signup integration readiness

> **Superseded blocker, 2026-11-11:** Ron selected outbound-connection pull mail,
> with no tunnel or remote-access control plane. The CP deployment-choice blocker
> below is historical. See [the pull design](AGENT_MAIL_PULL_DESIGN.md) for the
> replacement architecture, remaining decisions and build gates; it is not yet
> implemented, and the old configuration checklist is not a pull setup recipe.

MC-1166 integration checkpoint, 2026-11-11. This is a diagnosis, not a shipped
signup implementation. No runtime code, settings, credentials or deployment
changed. Operator-specific observations and the dispatch decision request are
in the item's untracked journal.

## Blocking decision

The mail Worker is not the control plane (CP). Its enrollment implementation
requires an independent CP authority over the enrolled device, account, tunnel,
ingress and DNS binding. A successful public manifest GET proves neither that
authority nor enrollment readiness.

The configured remote-access CP can be healthy while its mail-enrollment routes
return 404. The current source deliberately returns that same 404 when
`CLAYRUNE_MAIL_ENROLLMENT_ENABLED` is off, so an unauthenticated GET cannot tell
an older deployment from a disabled route family. Deployment inventory and
public-key readback are required before claiming that CP mail support is live.

The integration dispatch requires stopping for this deployment choice:

1. **Recommended:** use the existing remote-access CP, after checking its deployed
   revision, release delta and mail configuration. It owns the canonical device
   records already. Cost: CP release review, human-managed signing material and
   authority-reader provisioning; deployment needs separate authorization.
2. Deploy a separate mail CP that reads the same canonical authority. Cost: a
   second service, issuer/key distribution, backend permissions and ongoing
   operations. Isolation avoids a combined CP rollout but does not eliminate
   device/tunnel authority checks.

Neither option has been selected here. Do not point the local grant origin at
the Worker or bypass CP preflight to unblock the pilot.

## Traced local integration gaps

| Boundary | Current source | Required integration |
| --- | --- | --- |
| Settings status/actions | `mc/blueprints/agent_mail_routes.py` registers `enrollment_blueprint()` without a factory | Supply an operational factory through the existing passcode-gated routes. |
| First store | `MailFactory.enrollment()` calls `require_store()`; only `open_store(create=True)` creates a first store | Explicit first-enrollment initialization after human authorization; GET must stay read-only and held stores must never be recreated. |
| Local deployment identity | `MailFactory._deployment()` requires install id, root public key and service origin | Define stable local install-id provisioning; deployment UI currently saves only the latter two public values. |
| Key material | Factory injects `unavailable_material` | Generate the 32-byte Ed25519 seed and independent 32-byte flow secret only in the authorized human ceremony, sealed with the existing unlocked vault key. Never auto-create or unlock the vault. |
| Ceremony | `select_ceremony()` requires both local flags and a grant origin | Compose the real HTTPS client with the selected CP origin and existing keystore device identity. |
| Device authority | `keystore_identity()` requires enrolled device id/key, tunnel UUID and HTTPS route | Verify the actual enrolled device and attestation supplies the tunnel UUID; no substitute identity or guessed route. |
| Delivery proof | Factory supplies no `DeliveryProof`; ingress blueprint receives no production `proof_factory` | Share proof state with enrollment, the signed lifecycle client and fixed ingress proof handler. |
| Proof push/status/activation | `_default_recovery()` does not inject `LifecycleSteps` | Drive proof-arm, signature-verified status evidence, renewal and activation using a persistent composition; do not lose cached evidence by constructing a new client each read. |
| Enable checklist | Factory defaults to `Prerequisites`, whose three booleans are false | Supply real tunnel/extraction probes and derive machine custody from local proof plus fresh signed Worker status. Never force readiness booleans. |
| Human attribution | Enrollment enabling actions refuse while a non-manual session is running | Preserve this conservative human gate; a dispatched diagnostic cannot perform the owner's signup. |

`agent_mail_lifecycle_enabled` exists in the separate `select_remote()` helper;
setting it alone does not wire the factory, proof receiver or recovery driver.
The ceremony selector currently constructs its own lifecycle client when the
ceremony flags are on.

## Deployment settings checklist, not instructions to enable now

No sufficient config-only recipe exists for the current implementation. The
values below are the source-level requirements to review after the CP choice
and local composition are implemented. They are not authorization to set flags.

| Local name | Required value / source |
| --- | --- |
| `agent_mail_root_public_key` | Exact reviewed `root.pub`, canonical unpadded base64url Ed25519 public key; save through the human passcode-gated deployment card. |
| `agent_mail_service_origin` | Exact HTTPS origin in signed manifest element 2; same deployment card. |
| `agent_mail_install_id` | Stable valid local id; provisioning is currently missing, so no operator value is prescribed. |
| `agent_mail_grant_origin` | Selected CP HTTPS origin **without** `/v1`; currently awaits the decision above. |
| `agent_mail_enrollment_enabled` | Literal JSON `true`, after integration approval. |
| `agent_mail_ceremony_enabled` | Literal JSON `true`, after integration approval. |
| `agent_mail_ingress_enabled` | Literal JSON `true` only after ingress/proof path approval and evidence. |
| `agent_mail_supervisor_enabled` | Literal JSON `true` after lifecycle recovery is actually wired. |
| `agent_mail_outbound_enabled` | Separate sending rollout flag; not a repair for signup. |

Worker enrollment needs `MAIL_ENABLED="true"`,
`MAIL_ENROLLMENT_ENABLED="true"`, `MAIL_SERVICE_ORIGIN` equal to the signed
manifest origin, `MAIL_CP_ISSUER` and `MAIL_GRANT_ISSUER` equal to the selected
CP issuer origin, `MAIL_GRANT_JWKS` from its enrollment JWKS, and
`MAIL_SERVICE_PIN` with string fields `manifest_sequence`, `service_key_id`,
`service_public_key_sha256` matching the signed manifest. It also needs the
existing service signing secret, a human-created 32-byte `MAIL_CODE_KEY`,
`MAIL_DESTINATION_ACCOUNT_ID`, `MAIL_DESTINATION_TOKEN`, D1 migrations and the
owner-only send binding. Lifecycle completion additionally needs
`MAIL_LIFECYCLE_ENABLED="true"` and working CP delivery authority. Preserve the
separate `MAIL_INGRESS_VERIFIED` evidence gate.

CP enrollment requires `CLAYRUNE_MAIL_ENROLLMENT_ENABLED=true`,
`CLAYRUNE_MAIL_GRANT_KEYS`, `CLAYRUNE_MAIL_SERVICE_KEYS`,
`CLAYRUNE_MAIL_SERVICE_ORIGIN`, its reviewed issuer and
`CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED=true` with the pinned Cloudflare account,
zone and read-only `CLAYRUNE_MAIL_AUTHORITY_CF_TOKEN`, plus Firestore access.
CP delivery has its own `CLAYRUNE_MAIL_DELIVERY_ENABLED`,
`CLAYRUNE_MAIL_DELIVERY_KEYS` and `CLAYRUNE_MAIL_GRANT_JWKS` configuration.
Public JWKS and service-key pins must agree across components; real secret
values belong in human-managed deployment stores, never this checklist.

## Verification limits

Read-only public GETs and source tracing established the dispatch blocker;
the journal records exact statuses and the difference between a local operator
file and deployed configuration. No enrollment POST, mail, provider action,
config mutation, deployment, merge or push was performed.

Only documentation changed. `git diff --check` validates this checkpoint;
pytest, pyright and frontend smokes were not run. Existing tests with injected
adapters cannot establish live signup readiness. After the decision and
implementation, run the factory, enrollment route, ceremony, lifecycle and
destination suites through the installation test launcher, then the agent-mail
status/actions/setup/deployment smokes and a separately authorized live pilot.
