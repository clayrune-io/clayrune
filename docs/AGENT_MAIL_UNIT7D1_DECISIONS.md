# MC-1166 unit 7d-1: build scope decisions

Status: **implementation stopped for Dave's judgment; neither option adopted**.
Base `c487701d97e9daadefb621d0e4bb7bc2286c7c8f`; branch
`clayrune/mc1166-u7d1-recovery`. No production code changed.

The [authoritative preflight](AGENT_MAIL_UNIT7D_PREFLIGHT.md) selects delivery
authority A and splits local recovery (7d-1) from capability/proof work (7d-2).
The 7d-1 dispatch also forbids CP/Worker/edge changes and requires escalation
of genuine design choices. Source inspection exposed the following two choices.

## 1. Remote fencing and renewal cross the present unit boundary

Recovery step 2 explicitly requires implementing the absent remote fence, rather
than merely invoking local retry. Step 3 requires safe status/resolution and
bounded renewal within existing consent. Current contracts do not implement
those remote operations:

- `mc/agent_mail_enrollment_revocations.py:18-28` defines Python adapter
  requirements, including durable `cancel_flow` assignment resolution and
  exact-generation `revoke`. `Revocations.retry` consumes that adapter; it is
  not a service implementation. A local queue must not be reported as a remote
  cancellation acknowledgment.
- `workers/agent-mail/worker.js:24-25` exposes only signed POST send and reconcile.
  Both require an active, owner-verified registration. Reconcile accepts an empty
  object and asks the service to replay inbound events. It cannot fence an
  incomplete enrollment, resolve an assignment, renew ownership, or query an
  uncertain outbound receipt.
- `workers/agent-mail/enrollment.js` is an injected core, explicitly not a public
  HTTP handler. It implements begin, confirm and complete, with exact retries
  explicitly nonrenewing. It has no cancel/revoke/status/renew implementation.

No remote endpoint, service authentication scheme or renewal receipt may be
invented in the local factory. No background reuse of human ceremony/device-key
proof is authorized. An adapter returning success without the service fence
would weaken the old-generation revocation barrier.

| Option for Dave | Cost and consequence |
| --- | --- |
| **A: defer remote lifecycle protocol/implementation to 7d-2 (recommended)** | Amend 7d-1 acceptance: build bounded local retries with explicit remote-adapter-unavailable/pending states, retain every unresolved cancellation, block new generation enable until a real acknowledgment. Keep remote status/renewal unavailable, with expiry blocking sends. Complete the authenticated remote lifecycle contract alongside 7d-2 before activation. No Worker/CP/edge edit in 7d-1. |
| B: expand 7d-1 to include remote lifecycle fencing/status/renewal | Revise the dispatch's no-Worker/CP-change boundary. Requires a reviewed authentication, generation/flow/epoch scope, durable cancellation fence, late-request ordering and renewal contract plus cross-service tests. Still no keys, credentials, live calls or rollout. |

Reversing A moves the remote work into an earlier unit; it cannot reuse a fake
acknowledgment because none would be recorded. Reversing B leaves local work
default-off and removes the added unshipped service contract.

## 2. Ownership retention needs an approved retirement policy

The preflight explicitly reserves exact capacity/retention values for a measured
implementation proposal. Existing limits do not already settle that policy:

- `MailStoreConfig` documents its values as provisional: 600 active events,
  90-day thread TTL and 30-day terminal metadata retention. Active-event capacity
  does not bound historical ownership or legacy question volume.
- `Outbound.reconcile` currently deletes channel receipts after their expiry plus
  terminal retention, without proving an old form cannot reappear. That is the
  authority-loss bug the new retirement design must avoid, not a usable policy.
- `_selection_locks`, `_delivered` and `_outbox` currently have process lifetime;
  expiry cannot safely remove a lock reference held by an unacquired waiter.
  Timers, publication windows, parsers/readers and retained forms also pin work.

| Option for Dave | Cost and consequence |
| --- | --- |
| **A: compact durable owner tombstones, no age-only authority eviction (recommended)** | Jointly retire volatile caches only after all references/obligations end; retain compact legacy qid-wide or exact mail revision ownership. At an approved capacity, hold new ownership admissions instead of evicting retained authority. Release tombstones only after an explicit irreversible-form-retirement criterion, such as the existing destructive session/project deletion path. Needs sizing and a capacity proposal; may hold new questions when old forms remain indefinitely. |
| B: add an explicit durable form-retirement fence | Mark a form irrevocably retired and reject every later parser/revival/notification attempt before pruning its owner. Adds a new form-lifecycle contract and more integration/revival tests; existing retained forms need an explicit transition. Capacity still needs sizing and backpressure. |

A is the smaller ownership-preserving change; B introduces a broader product
lifecycle. Neither permits silent owner deletion or replacing a waiter's lock.
Neither numeric capacity nor age threshold has been selected.

A scratch-only sizing script was prepared for proposed compact SQLite receipts
at 600, 2,400 and 24,000 rows with 32/128-character synthetic identities. These
are comparison scenarios, not adopted limits or measured workload forecasts.
Automatic tool approval rejected the PowerShell manifest-preparation/launch
command before execution because of variable substitution in command position.
**No sizing result exists.** The rejected command is preserved in the main item
journal; it was not retried or bypassed. No application import, account access,
credential read or network measurement ran.

## Verification and handoff limits

Source inspection only; application/question/CP/Worker test counts: **0 run**,
child exits N/A. Pyright, browser/boot, mobile and installer checks are N/A:
there is no executable change. The existing failing flag-off cache-loss probe
has not been fixed, copied, changed, skipped or rerun. The full requested build
and its acceptance suites remain outstanding.

The committed artifact is this decision record only. MAIN journal contains
commands, approval rejection and the commit checkpoint so worktree deletion
cannot strand the report. README, USER_GUIDE, CHANGELOG, AGENTS, rules and memory
need no changes: no available behavior or new adopted policy. No dependencies,
merge, push, restart, deploy, real keys, credentials, providers or mail.
