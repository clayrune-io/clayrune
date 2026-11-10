# MC-1166 unit 7b: local enrollment lifecycle

Status: **implemented, disabled branch candidate; synthetic adapters only**.
Branch: `clayrune/mc1166-u7b-lifecycle`; preflight `7bd5ad3e`, base `f51eb22a`.
No merge, push, restart, deployment, operational key generation or external calls.

## Adopted storage and human gate

Dave explicitly selected preflight option B on 2026-11-09. The mail seed is not
an ordinary vault entry: supported agent secret/placeholder APIs must never
return the installation signing identity. The original option A was rejected
because it would make that identity dispensable to agents.

`IdentitySeal` uses AES-GCM with only the existing unlocked passphrase-vault
master key obtained through non-creating `peek_passphrase_key()`. It never
sets up/unlocks the vault, calls `load_master_key`, creates another wrapping
key or writes a vault entry. Locked/unconfigured vaults refuse key creation,
reopening and signing. Ordinary passphrase rewrapping retains the same master
key. A replaced master key or corrupt/lost seal refuses access; only a new
human-authorized ceremony can revoke and replace the old identity. No automatic
recovery, identity rotation or permission expansion is provided.

The seed stays inside the seal module. Python adapter seams expose public keys,
signatures and the separate flow secret, never the seed. No HTTP response,
placeholder or log exports either the seed or flow secret. Cryptographic host
code can still read unlocked process memory; this is API isolation, not a new
operating-system boundary.

Every mutation, including resume, review, disable and remote-revocation retry,
requires `is_unattended_caller()` refusal followed by the existing per-request
`_require_human_passcode`. The same configured-passcode requirement and guessing
budget apply. Forged Origin, loopback, an authenticated dashboard session, a
manual agent session, a service signature or knowledge of a flow id cannot
replace the per-call proof. Existing dashboard authentication remains intact;
there is no auth exemption.

## Module boundaries and wiring

- `mc/agent_mail_enrollment_keys.py`: immutable versioned seal; fsynced atomic
  publication using the existing writer, plus POSIX directory fsync; purpose,
  local identity, flow, generation and key-id AAD. Key/flow-secret bytes are
  encrypted together; files are named only by validated opaque flow id.
- `mc/agent_mail_enrollment_state.py`: version marker, lifecycle state, remote
  revocation queue and origin-scoped manifest sequence/digest CAS tables in
  the same `MailStore` database. `BEGIN IMMEDIATE` provides cross-process
  serialization; partial/malformed schema and corrupt floors are refused.
- `mc/agent_mail_enrollment.py`: pending intent, resume, exact confirmation,
  scoped ownership evidence, prerequisite-gated local enable, synchronous
  disable and new-generation owner/key/pin ceremonies.
- `mc/blueprints/agent_mail_enrollment_routes.py`: separate human-only blueprint.
  The existing ingress blueprint registers it through Flask's blueprint
  registry. `server.py` and `agent_routes.py` do not grow.
- Existing `MailIngress.pin_service` and `MailStore.revoke_registration` accept
  an optional trusted Python-owned transaction so activation+pin and
  disable+revocation queue+event cancellation each commit atomically. Their
  original standalone call behavior remains unchanged.

`agent_mail_enrollment_enabled` must be literal `true`; it defaults absent/off.
Disabled routes return 404. Even with the flag set, the registered blueprint
has **no operational factory** and returns 503 `operational_adapter_unavailable`.
No production adapters, roots, key generator, HTTP client, config mutation,
startup worker or background retry loop are supplied. `enabled=True` in a
fixture does not establish consent or production readiness.

All lifecycle state and seals must live outside the repository. The explicit
location helper is `~/.clayrune/agent-mail`; a future reviewed factory must use
one shared inbox database there and an identity subdirectory, and pass that
same store to ingress, outbound and continuation. No relocation/migration of
the earlier receiver's default `data/agent_mail.sqlite3` is performed. Tests
inject isolated temporary paths. Never put sidecars into `data/projects/`.

## Durable ordering and authority

1. Verify the offline-root manifest as a candidate and persist sequence/digest
   CAS. A lower sequence, same-sequence conflict, stale CAS, signature failure,
   corrupt floor or write failure refuses. This does not create a service pin.
2. Persist the human-authorized flow and local reservation before generating
   fixture material or contacting the injected remote adapter. Seal material
   while holding the shared database write lock, then commit its public key.
   A published seal left by a failed DB commit is reopened on retry. Once
   key-ready, a missing/corrupt file is refused, never regenerated.
3. The begin adapter returns `PendingIdentity`: the service-selected opaque
   install id and resulting local generation (unit 7a expected generation + 1),
   bound to the exact flow, owner, public key and service fingerprint. Persist
   that assignment before human review. The initial local-id reservation stays
   a disabled tombstone; an existing unrelated registration is never adopted.
   A changed assignment on retry is refused. Pending assignment is not proof
   of ownership. The seal remains bound to the stable local identity and flow.
4. Human review discloses the exact owner, install, generation, mail public
   key, service fingerprint, origin and manifest sequence. Confirm compares
   the complete object. No flow secret or copying code is disclosed in status.
5. The verify adapter owns the unit-7a challenge/code/dual-proof/authority work.
   It returns an authenticated scoped `OwnershipReceipt`, with an absolute
   expiry at most five minutes ahead. Local code checks every scope field,
   current manifest, expiry, flow/epoch/generation and current assignment after
   remote work. Code is never persisted or returned. Ownership success sets
   `delivery_pending`, never delivery authority.
6. Explicit human enable requires fresh ownership, exact confirmation, current
   trusted tunnel and Claude-extraction probes and machine-custody authorization.
   All three default false. Request booleans cannot supply them. In one local
   transaction enable registration and call only
   `MailIngress.pin_service(ServicePin(...))`; recheck expiry after the write.
   A pin/storage failure rolls back activation. Service-signed ingress cannot
   set or replace the pin.
7. Disable atomically disables/bump-fences the registration, revokes thread
   bindings, cancels queued follow-ups/purges bodies, advances the local epoch,
   and queues the old generation for remote revocation. It returns without
   waiting for remote work. Duplicate disable does not bump again. Remote
   failures leave a durable queue item for a later retry, including restart.
8. Owner, key and repin routes share `change_identity`: disable, acknowledge
   idempotent remote revocation, then create a separate pending ceremony with
   a new flow, key id/material and generation. Pending revocation blocks a new
   start. Old threads/events never transfer. Stale responses cannot overwrite
   the new flow or reopen a disabled generation.

Adapters must enforce remote flow+generation idempotency and make revocation
fence delayed remote begin/verify requests. The local module never trusts a
caller-supplied ownership receipt or prerequisite flag. Adapter exceptions are
converted to closed reason codes, including exceptions using the local refusal
class, so untrusted error text cannot reach HTTP/logs. Failed material callbacks
are redacted as well.

## HTTP contract

`GET /api/agent-mail/enrollment` returns bounded public lifecycle metadata,
without owner mailbox, flow id, code or secrets. All responses are no-store.

Human-only `POST /api/agent-mail/enrollment/<action>` actions:

| Action | Additional body |
| --- | --- |
| `start` | `owner`, signed `manifest` array, `signature` |
| `resume`, `review`, `enable`, `disable`, `retry-revocations` | none |
| `confirm` | exact `confirmation` object from human review |
| `verify` | copying `code` |
| `owner`, `key`, `repin` | `owner`, signed `manifest` array, `signature`; revoke first |

Every POST also requires the human `passcode`; it is neither stored nor
forwarded to the adapter. Unknown actions return 404, GET on mutations 405,
malformed bodies 400, human refusal 403, closed lifecycle refusal 409 and
storage/unavailable-adapter failures 503. These are integration seams, no UI.

## Verification and remaining gates

`tests/test_agent_mail_enrollment.py` and
`tests/test_agent_mail_enrollment_routes.py` cover locked/unconfigured/replaced
vault keys, malformed/lost seals, corruption, fsynced write failures, orphan-seal
recovery, durable intents, service assignment, restart/idempotency, exact human
confirmation, all mutation proof gates, ownership scope/expiry, prerequisites,
pin rollback, stale and concurrent disable, revocation retry, generation and
thread isolation, separate-connection and subprocess CAS, exception redaction,
and the complete synthetic HTTP ceremony. Fixture seeds are fixed; no
operational keys are generated. Broader existing mail tests also use synthetic
cryptography and fake Worker adapters.

Run mail `tests/` and `control_plane/tests` as separate commands through MAIN's
installation test launcher. Exact selections, counts and exits are appended
below after final verification and to MAIN's ignored item journal. The launcher
reports incomplete transport/parent progress capture in this environment;
pytest output and child exit codes remain available. No complete framework
telemetry claim is made. The initial broader mail run failed only six Node
bridge checks because this worktree lacked `postal-mime`; the exact selection
is rerun using a temporary junction to MAIN's existing Worker dependencies,
removed nonrecursively before commit. No package installation is performed.

Node Worker suite is N/A: Worker source is unchanged. README, USER_GUIDE, rules,
AGENTS and boot smoke are N/A: no available UI/user-facing feature or frontend
change. Changelog records the disabled internal candidate, not shipped mail.

Still unverified/unimplemented by design: real service/CP/Cloudflare adapters,
unit-7a transcript/grant networking, offline-root provisioning, D1 and existing
DB upgrades, live machine-custody authorization, recurring ownership renewal,
operational scheduler/retry wiring, inbox-path migration, real delivery and
end-to-end continuation. These remain later-unit and MC-948 deployment gates;
none is bypassed. No new design question remains for Dave in this build.

### Final verification (round 2)

Cwd: the existing `13fc2e6990d9` worktree; inherited environment unchanged.
Common launcher prefix, using the project interpreter and MAIN installation:

```text
C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe C:/Users/levir/Documents/_claude/mission-control/tools/test-run.py --project mission_control --port 5199 --shell powershell --command
```

The exact PowerShell command strings following that prefix:

```text
.venv/Scripts/python.exe -m pytest tests/test_agent_mail_enrollment.py tests/test_agent_mail_enrollment_routes.py tests/test_agent_mail_deadline_review.py tests/test_agent_mail_wake_review.py tests/test_agent_mail_wake_contract.py tests/test_agent_mail_wake.py tests/test_agent_mail_store.py tests/test_agent_mail_service_manifest.py tests/test_agent_mail_revival.py tests/test_agent_mail_launder.py tests/test_agent_mail_ingress.py tests/test_agent_mail_held.py
```

Final exit **0**, **567 passed** (133 new lifecycle/route cases, 434 existing mail
regressions). Earlier same broader selection before the last seven new checks:
exit **1**, **554 passed / 6 failed**, all six due to missing `postal-mime` in the
worktree's Python-to-Node bridge. No subset result replaced that failure. Final
exact selection ran with MAIN's existing Worker dependency junction; the link
was removed using nonrecursive `DirectoryInfo.Delete`, and MAIN's package was
verified intact. No packages installed; no Worker source changed.

```text
.venv/Scripts/python.exe -m pytest control_plane/tests
```

Separate command: exit **0**, **98 passed / 1 skipped**. The existing skip at
`control_plane/tests/test_edge_contract.py:86` requires `clayrune-cloud` beside
this checkout. Existing Starlette/httpx deprecation warning only. No combined
main/control-plane command was attempted.

```text
C:/Users/levir/AppData/Local/Programs/Python/Python312/Scripts/pyright.exe mc/agent_mail_enrollment.py mc/agent_mail_enrollment_keys.py mc/agent_mail_enrollment_state.py mc/blueprints/agent_mail_enrollment_routes.py mc/agent_mail_ingress.py mc/agent_mail_store.py mc/blueprints/agent_mail_routes.py
```

Final exit **0**, **0 errors / 0 warnings / 0 informations** on all seven touched
Python modules. Existing installed Pyright used; upgrade notice not acted on.
Initial `python -m pyright` attempt exited 1 because Pyright is not installed in
the project interpreter; no type check ran from that command. The explicit
existing executable above is the actual passing check.

`git diff --check`: exit **0**. Final commit and staged/range checks are recorded
in MAIN's item journal. All launcher runs warned that parent/transport progress
capture was incomplete; pytest output and child exit codes are reported here,
without claiming complete framework telemetry. Node suite and frontend smokes
remain N/A for this change.
