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

Every mutation requires the existing per-request `_require_human_passcode`,
including its configured-passcode requirement and shared guessing budget.
Dave's 2026-11-09 review decision adds a mail-only conservative gate for
`start`, `resume`, `review`, `confirm`, `verify`, `enable`, `owner`, `key` and
`repin`: any server-recorded running non-manual or unknown-trigger session
refuses the action with `human_required` and `blocking_sessions` containing
session IDs and triggers. Origin, body, cookies and caller session claims do
not affect this gate. This intentionally also blocks the real dashboard while
such sessions run. Idle/manual-only sessions do not block it.

`disable` and `retry-revocations` always remain available to a valid
passcode-holder, including while dispatched sessions run. No unauthenticated
kill switch is added. `mc/unattended.py` and all its other callers remain
unchanged. Trusted per-request caller attribution is a required follow-up
before live mail under MC-948; the conservative gate is not that attribution.
Existing dashboard authentication remains intact; there is no auth exemption.

## Module boundaries and wiring

- `mc/agent_mail_enrollment_keys.py`: immutable versioned seal; fsynced atomic
  publication using the existing writer, plus POSIX directory fsync; purpose,
  local identity, flow, generation and key-id AAD. Key/flow-secret bytes are
  encrypted together; files are named only by validated opaque flow id.
- `mc/agent_mail_enrollment_state.py`: version marker, lifecycle state, remote
  revocation queue and origin-scoped manifest sequence/digest CAS tables in
  the same `MailStore` database. `BEGIN IMMEDIATE` provides cross-process
  serialization; partial/malformed schema and corrupt floors are refused.
- `mc/agent_mail_enrollment_manifest.py`: revalidates retained signed manifest
  evidence and digest using a read-only verifier sink; candidate-cache
  consistency and human-approved digest checks, with no CAS-floor reset.
- `mc/agent_mail_enrollment_revocations.py`: durable flow-keyed cancellation,
  authenticated service-assignment reconciliation and exact-generation revoke.
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
   corrupt floor or write failure refuses. Persist the signed payload/signature,
   digest and acceptance time with the intent. Reverify the signature/structure
   and cached-candidate consistency whenever deriving disclosure or a pin;
   authority steps also require the current floor's sequence AND digest and
   current expiry. This does not create a service pin.
2. Persist the human-authorized flow and local reservation before generating
   fixture material or contacting the injected remote adapter. Seal material
   while holding the shared database write lock, then commit its public key.
   A published seal left by a failed DB commit is reopened on retry. Once
   key-ready, a missing/corrupt file is refused, never regenerated.
3. The begin adapter returns `PendingIdentity`: the service-selected opaque
   install id and authenticated resulting generation (unit 7a expected generation + 1),
   bound to the exact flow, owner, public key and service fingerprint. Persist
   that assignment before human review. The initial local-id reservation stays
   a disabled tombstone; an existing unrelated registration is never adopted.
   A changed assignment on retry is refused. Pending assignment is not proof
   of ownership. The seal remains bound to the stable local identity, flow and
   `seal_generation`; it does not need to be rewritten if the service selects
   a fresh identity with a different authenticated generation. An existing
   inbox's generation is never lowered or silently adopted. A durable
   `remote_started` marker precedes every begin call, so a crash before its
   response leaves cancellable work without guessing the remote identity.
4. Human review discloses the exact owner, install, generation, mail public
   key, service fingerprint, origin, manifest sequence and manifest digest.
   Confirm compares the complete object and persists `confirmed_digest`;
   verify/enable require that same approved digest. A replaced candidate,
   signature, payload, digest or confirmed digest refuses and pins nothing.
   No flow secret or copying code is disclosed in status.
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
   and queues cancellation by stable flow. It returns without waiting for
   remote work. Duplicate disable does not bump again. A never-sent intent
   advances only the local cancellation epoch; it consumes no service
   generation and creates no remote queue item. A sent/unassigned intent
   queues an unresolved flow, never the provisional local install ID. Old
   corrupt manifest evidence cannot prevent acknowledgment of local closure;
   disabled status discloses an empty service fingerprint.
8. Owner, key and repin routes share `change_identity`: disable, acknowledge
   idempotent remote revocation, then create a separate pending ceremony with
   a new flow, key id/material and generation. Pending revocation blocks a new
   start. Old threads/events never transfer. Stale responses cannot overwrite
   the new flow or reopen a disabled generation.

The injected adapter's `cancel_flow(flow)` must durably fence all in-flight and
delayed begin/verify requests and return an authenticated `CancelledAssignment`
for the actual service install/generation, or `None` only when no identity was
ever assigned. Retries must return the same result even after revoke. Known
assignments must match exactly. Persist resolution before calling idempotent
`revoke(install,generation)`; only mark done after both phases succeed. Lost
responses, failed local writes, locked/lost seals and process restarts leave
retryable durable work. Cancellation never requires unlocking the old key or
revoking a placeholder. This is a synthetic adapter contract, not an existing
operational service cancellation endpoint; authenticated network wiring and
the durable remote fence still require implementation/verification before live.

The local module never trusts a
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

`tests/test_agent_mail_enrollment.py`,
`tests/test_agent_mail_enrollment_routes.py` and
`tests/test_agent_mail_enrollment_review.py` cover locked/unconfigured/replaced
vault keys, malformed/lost seals, corruption, fsynced write failures, orphan-seal
recovery, durable intents, service assignment, restart/idempotency, exact human
confirmation, all mutation proof gates, ownership scope/expiry, prerequisites,
pin rollback, stale and concurrent disable, revocation retry, generation and
thread isolation, separate-connection and subprocess CAS, exception redaction,
and the complete synthetic HTTP ceremony. Fixture seeds are fixed; no
operational keys are generated. Broader existing mail tests also use synthetic
cryptography and fake Worker adapters.

The review suite preserves all four Fenn reproductions. Per Dave's explicit
refinement, the forged-Origin/valid-passcode probe now asserts refusal of
`enable` and separately asserts that `disable` succeeds. The old probe's
disable-refusal expectation was deliberately superseded, not claimed passing.
Additional cases cover all enable-direction actions, unknown triggers,
blocker disclosure, idle/manual-only access, corrupt evidence at verify/enable,
unchanged-sequence digest conflicts, lost assignment responses, reconciliation
write failures, and repeated never-sent cancellation.

Lifecycle schema version is now **2**. Version-1 candidate databases fail closed;
no reset, automatic conversion or existing-database upgrade is supplied. No
operational factory was shipped, and this branch's isolated fixture databases
are recreated by tests. Any future upgrade needs a separately reviewed migration.

Run mail `tests/` and `control_plane/tests` as separate commands through MAIN's
installation test launcher. Exact selections, counts and exits are appended
below after final verification and to the worktree's ignored item journal. The launcher
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
operational scheduler/retry wiring, authenticated flow-cancel/reconciliation,
trusted per-request caller attribution, inbox-path migration, real delivery and
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

### Review rework r1 verification (2026-11-09)

Continued on `clayrune/mc1166-u7b-lifecycle` at `e5ee8ca1`, in the existing
`13fc2e6990d9` worktree. All four Fenn probes were made permanent before the
implementation. Their baseline command below exited **1**, **4 failed**:
candidate corruption pinned the replacement; assignment-race retry revoked
only the placeholder; never-sent cancellation rejected generation 1; forged
Origin allowed enable with valid proof. The Origin probe uses enable per Dave's
refined decision; disable with valid proof is deliberately allowed and tested.

Every command below used unchanged inherited environment and this exact common
launcher prefix (the following block's suffix is appended after `--`):

```text
C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe C:/Users/levir/Documents/_claude/mission-control/tools/test-run.py --project mission_control --port 5199 --
```

Baseline four-probe suffix:

```text
C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe -m pytest tests/test_agent_mail_enrollment_review.py -q
```

Final mail suffix, **exit 0, 641 passed in 29.52s**:

```text
C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe -m pytest tests/test_agent_mail_enrollment.py tests/test_agent_mail_enrollment_routes.py tests/test_agent_mail_enrollment_review.py tests/test_agent_mail_deadline_review.py tests/test_agent_mail_wake_review.py tests/test_agent_mail_wake_contract.py tests/test_agent_mail_wake.py tests/test_agent_mail_store.py tests/test_agent_mail_service_manifest.py tests/test_agent_mail_revival.py tests/test_agent_mail_launder.py tests/test_agent_mail_ingress.py tests/test_agent_mail_held.py
```

This includes **76 review cases** and all prior mail regressions. Two obsolete
disable/revocation-retry unattended-refusal cases were replaced by explicit
passcode-holder availability cases under Dave's revised acceptance policy.
No xfails or suppressed framework failures. An earlier expanded lifecycle-only
run exited 0 with 203 passed, before the last four regressions were added.

Separate control-plane suffix, **exit 0, 98 passed / 1 skipped in 1.17s**:

```text
C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe -m pytest control_plane/tests
```

Existing skip at `control_plane/tests/test_edge_contract.py:86`: cloud checkout
not beside this worktree. Existing Starlette/httpx deprecation warning. This is
a separate test invocation, not a combined main/control-plane collection.

Final pyright suffix, **exit 0, 0 errors / 0 warnings / 0 informations**:

```text
C:/Users/levir/AppData/Local/Programs/Python/Python312/Scripts/pyright.exe mc/agent_mail_enrollment.py mc/agent_mail_enrollment_manifest.py mc/agent_mail_enrollment_revocations.py mc/agent_mail_enrollment_state.py mc/blueprints/agent_mail_enrollment_routes.py mc/agent_mail_enrollment_keys.py mc/agent_mail_ingress.py mc/agent_mail_store.py mc/blueprints/agent_mail_routes.py
```

All five touched/new `mc/` modules and the other four original unit-7b modules
were checked. Initial five-file pyright exited 1 with one nullable-generation
error in the new revocation module; persisted queue scope validation fixed it.
The next five-file check and final nine-file check both exited 0. No dependency
installed or upgraded; the pyright upgrade notice was not acted on.

The exact mail run used a temporary junction to MAIN's existing Worker
dependencies. It was removed using verified absolute paths and nonrecursive
`DirectoryInfo.Delete`; MAIN's `postal-mime/package.json` remains present.
No Worker source changed, so its separate Node suite is N/A. No frontend/UI
changed, so boot smokes, README and USER_GUIDE are N/A. Rules, AGENTS and managed
memory need no changes. Handoff/changelog updated; ignored worktree journal
contains the plan, decision, failures, commands and commit receipt.

Launcher diagnostics included `telemetry_queue_full_progress_incomplete` and
`unfinalized_runs_progress_incomplete`; pytest output and actual child exits
are reported, without claiming complete framework/transport telemetry.
`git diff --check` exited 0. No merge, push, restart, deployment, operational
key generation, external account/mail/control-plane calls, subagents or emails.
Trusted caller attribution, real cancellation fencing, migrations and all
previous integration/live gates remain pending. No new decision is required.
