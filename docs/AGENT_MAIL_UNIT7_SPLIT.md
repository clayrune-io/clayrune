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
