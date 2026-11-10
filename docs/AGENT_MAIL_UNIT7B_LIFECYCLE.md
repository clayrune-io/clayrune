# MC-1166 unit 7b: local lifecycle storage decision

Status: **preflight only; stopped for Dave's storage decision**.
Base: `f51eb22a58e852e20296451b6bd5845e4d5dcb5f` (unit 7a merged).
Branch: `clayrune/mc1166-u7b-lifecycle`.
No lifecycle implementation, key generation, enrollment or activation occurred.

## Decision required before implementation

The dispatch explicitly requires stopping when a real design choice arises,
including key storage format. D7 selects separate mail-only Ed25519 keys, but
the transport and unit-7 split leave their storage as "vault/keystore". They
do not select whether signing material is an ordinary dispensable vault entry
or an internal sealed identity file. These have different exposure and recovery
contracts; neither is silently selected here.

| Option | Contract and cost |
| --- | --- |
| A: ordinary vault entry | Store a versioned mail-only seed through `mc.secrets_store.set_secret`, only inside the human enrollment ceremony. Reuses existing encrypted persistence and management. The entry becomes visible to secret metadata and eligible for existing agent secret-dispensing/placeholder APIs; entry edits/deletion need lifecycle reconciliation. Enrollment must refuse a locked or unconfigured passphrase vault rather than create a fallback master key. |
| B: dedicated sealed identity file, recommended | Store a versioned AES-GCM envelope under `~/.clayrune`, outside the repository, using only the existing unlocked passphrase-vault master key. Bind encryption to mail purpose, identity, generation and key id; expose public metadata and signing operations, never the seed through HTTP/placeholders. Requires a new atomic/fsynced file store, cross-process serialization, corruption/refusal tests and explicit vault-key replacement/recovery behavior. No separate plaintext wrapping key, OS auto-unlock, fallback key minting or new agent vault-write path. |

**Recommendation: B.** `mc_remote/identity_mirror.py` already explains why an
installation signing identity should not be an ordinary agent-dispensable
secret. Reuse that separation, not its independent on-disk wrapping-key scheme:
mail must preserve the standing passphrase cold-boot lock. The proposed seal
would obtain the existing key only through the non-creating
`mc.secrets_store.peek_passphrase_key()` seam and refuse when unavailable.
Vault unlock/setup remains human-owned; pending enrollment remains durable
while locked. This adds an explicit vault-unlocked prerequisite to signing and
key creation, not permission to auto-unlock or bootstrap a new master key.

Neither option protects against arbitrary trusted host code reading process
memory after human unlock. B limits the supported application API surface;
it is not a new operating-system security boundary. Vault master-key replacement
must fail closed and require a separately authorized recovery/re-enrollment,
never silently generate a replacement mail identity. Ordinary passphrase
rewrapping must retain access to the same master key.

Reversing B later requires a human-authorized key-store migration with identity
and generation checks. It cannot be handled as automatic missing-key recovery.
Dave must select A or B before implementation resumes; this recommendation is
not an adopted decision.

## Human gate is already selected by precedent

Use `mc.blueprints.secrets_routes._require_human_passcode` on every enrollment,
verification, enable, owner/key change and repin mutation, plus the existing
`is_unattended_caller` refusal. This shares the dashboard's configured-passcode
requirement and guessing budget. Forged Origin, an authenticated browser session,
loopback, an opaque flow id or a manual agent session cannot replace per-call
human proof. No new human-proof protocol or unlock window is proposed.

The narrow gate precedent is `mc/blueprints/desk_engine_connection_routes.py`;
the mail blueprint must also retain existing dashboard authentication. Read-only
status must omit secrets, copying codes and flow secrets.

## Build acceptance after the decision

Implement separate `mc/agent_mail_enrollment*.py` modules and a dedicated
blueprint through the existing registry, without growing `server.py` or
`agent_routes.py`. Default-disabled and fake-adapter-only until later units
provide reviewed operational adapters.

- Persist the human-authorized pending identity and flow before remote work;
  reopening or retrying cannot mint a second identity. Persist manifest
  sequence/digest CAS outside the repository and refuse corruption/rollback.
- Verify the offline-root manifest as a candidate only. Record the pin solely
  through `MailIngress.pin_service(ServicePin(...))` after exact human
  confirmation. A service-signed request cannot create or rotate the pin.
- Require current tunnel and Claude extraction prerequisites for activation;
  unit 7a ownership success alone remains delivery-pending. Missing machine
  custody authorization remains a later integration gate, never an auth bypass.
- Disable must synchronously close local send/follow-up authority and durably
  queue remote revocation. Failed remote work remains retryable after restart.
  Owner, mail-key and service-pin changes revoke the old generation before a
  new ceremony; old threads/events never transfer into the new generation.
- Synthetic tests must cover forged human proof, locked/corrupt storage,
  restart/idempotency, write failures, stale/racing disable, revocation retries,
  prerequisite holds, and generation isolation. No real keys or external calls.

Affected `tests/` and `control_plane/tests` selections must run as **separate**
commands through the installation test launcher. Run the Node Worker suite if
Worker code changes. New `mc/` modules require pyright with zero errors.

## Verification of this preflight

Documentation only: pytest, Node and pyright are N/A; none was executed or
reported as passing. `git diff --check` is the applicable check. No dependencies,
runtime flags, user-facing behavior, rules, README, user guide or changelog were
changed. The MAIN item's ignored journal records the dispatch and handoff.
No merge, push, restart, deploy, key generation, Cloudflare, mail or control-plane
call is authorized or performed here.
