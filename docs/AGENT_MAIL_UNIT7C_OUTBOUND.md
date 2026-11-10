# MC-1166 unit 7c: outbound notes and question adapter

Status: **disabled branch candidate, synthetic transport only**.
Branch `clayrune/mc1166-u7c-outbound`, exact base `d1290a28` (7a/7b merged).
Contract: row 7c in [AGENT_MAIL_UNIT7_SPLIT.md](AGENT_MAIL_UNIT7_SPLIT.md),
[AGENT_MAIL_SPEC.md](AGENT_MAIL_SPEC.md),
[AGENT_MAIL_UNIT7B_LIFECYCLE.md](AGENT_MAIL_UNIT7B_LIFECYCLE.md), and
[AGENT_MAIL_UNIT6_HANDOFF.md](AGENT_MAIL_UNIT6_HANDOFF.md).

No operational factory, HTTP client, public note-send API, startup loop or
activation. No merge, push, restart, deployment, human-gate modification, real
keys/mail, Cloudflare/control-plane calls, account access or new dependency.
The control-plane tests below run entirely against local injected fixtures.

## Interface and module ownership

- `mc/agent_mail_outbound.py`: `Outbound(enrollment, transport=..., enabled=False)`;
  `prepare(project_id, session_id, request, question=None)`, `send(intent_id)`,
  `status(intent_id)`, local `reconcile()`, and durable question-channel ownership.
- `mc/agent_mail_outbound_state.py`: versioned tables, immutable correlation
  triggers, AES-GCM payload sealing and metadata-only results.
- `mc/agent_mail_outbound_signing.py`: canonical request bytes and fresh
  timestamp/nonce/signature using the existing `IdentitySeal.prove` seam.
  The seed never leaves that seal. No new key storage or vault entry.
- `mc/agent_mail_outbound_questions.py`: Python-only, initially absent factory,
  per-revision transport selection, form rendering and the shared parser hook.
- `mc/question_channel.py`: narrow hooks for revision/grace/selection/delivery
  and exclusion of new mail from legacy replies/polling.
- `mc/agent_runtime.py`: only a two-line registration hook at `mc:question`
  parsing. The new concern lives in its own module; no implementation body is
  appended to this monolith. `server.py`/`agent_routes.py` are unchanged.

The request accepts **exactly** `request_id`, `subject`, `body`, `kind`.
Kind is `note|question`; ids use the existing wire ID grammar. Subject is
nonempty, at most 200 UTF-8 bytes, without ASCII controls. Body is nonempty
plain text without NUL, bounded to 16 KiB including the added project,
conversation and reply explanation. Oversize content is refused, never clipped.
Recipient/CC/BCC/header/raw-MIME/From/Reply-To/Message-ID/callback/install or
generation overrides are rejected before creating an intent. Routing parameters
are trusted local Python arguments, not message headers or inbound fields.

The sole recipient is derived by the existing Worker from its verified
registration, independently of the local request. From, Reply-To and full
Message-ID are constructed there. Worker receives only the six documented wire
fields, with no local routing fields; project/conversation context is plain
outbound body text. The test bridge exercises the actual Worker verifier,
outbound handler, real SQLite D1 fixture and fake send provider with local
fixture signatures, and verifies its sole recipient and durable thread/intent.
This is not evidence of Cloudflare/D1 deployment or real delivery.

## Durable ordering and authority

1. Explicit outbound and 7b enrollment enablement are required. Check active,
   assigned, confirmed ownership, exact generation/epoch, current signed
   manifest sequence/digest/expiry, installed service pin/path, sealed public
   identity, unexpired ownership lease and trusted current prerequisites.
   Locked vaults, stale proofs or unavailable prerequisites cannot send.
2. Persist a random 128-bit intent and separate random 128-bit reply token in
   a sealed immutable wire payload; retain its SHA-256 and local correlation.
   The request key is unique per project/session. Changed content or question
   under that key refuses; retries reopen the exact original bytes.
3. Bind the local token hash with `MailStore.bind_thread`, capture the exact
   unit-6 native identity/context/form using `MailWake.bind`, then call
   `MailIngress.bind_transport`. Only after these commit can the draft become
   ready. A failed partial preparation remains a durable draft, cannot send,
   and may finish those same bindings on an identical retry. No legacy thread
   or native identity is reconstructed from a subject/persona.
4. Recheck all authority and binding consistency under manager -> SQLite lock
   ordering; validate the originating session, native history, policy and exact
   question revision. Fresh signatures use the pinned service origin and exact
   `/v1/mail/send` path. Recheck expiry after slow probes/signing. Commit a
   unique attempt with state `sending` **before** the injected transport call.
5. Network work never holds SQLite. An immediate signed reply can obtain
   custody, laundering and unit-6 submission while the outbound receipt is
   pending, using only the recorded conversation/form. Tests use synthetic
   extraction results; no model call occurs.
6. Transport acceptance needs `accepted` plus the exact deterministic
   install/generation/intent Message-ID hash and a bounded domain. It means
   transport acceptance, not inbox delivery/read or a completed agent turn.
   Worker persists its full Message-ID/thread before provider write; local
   custody does not depend on receiving that response first. Local metadata
   retains the positive Message-ID receipt. An admitted write may finish after
   disable; `state=revoked, transport_state=accepted` preserves both facts
   without reopening local authority or suggesting that mail was recalled.

Schema initialization is explicit when `Outbound(enabled=True)` is constructed;
disabled construction creates no outbox tables. Version 1 is an undeployed
extension of the shared inbox database, not an upgrade of a live deployment.
Malformed/partial tables or missing immutability triggers refuse reopening.
All operational storage must use 7b's outside-repository inbox location and
the same store for enrollment, ingress, outbound and continuation.

The wire payload (body, subject, token) uses AES-GCM with the existing unlocked
passphrase-vault master key and fresh 12-byte nonce; correlation/digest is AAD.
No plaintext token is written to the inbox DB. The lookup binding remains a
hash. Accepted payloads are purged immediately; pending/uncertain payloads are
bounded by the store's active-event count and thread TTL. Local reconciliation
purges disabled/stale/expired payloads and eventually removes metadata after
expiry plus terminal retention. It also retires channel receipts after that
retention window. These are logical SQLite purges, not secure-erasure claims
about backups/pages. Unit 7d owns recurring cleanup without an open tab.

## Retry, uncertainty and question behavior

Only the Worker's explicit `send_limit` result is retryable: its code path
precedes reservation/provider write. A retry uses the same intent/thread/token,
identical body bytes and fresh nonce/signature. Exceptions, malformed responses,
unknown failure codes, `sending` and `uncertain` never authorize resending.
A crash after admission or lost local receipt stays sending; after 60 seconds
status/reconciliation shows uncertain. Neither automatic restart nor `send()`
can replay it. A matching late positive result from the original attempt may
settle uncertainty; late acceptance cannot undo revocation/expiry. Broader
outbound receipt reconciliation/recovery UI remains later-unit work.

Existing delivery ownership is checked under the shared notification lock before
adapter eligibility, with a per-qid mutex serializing selection and admission.
Factory calls and durable receipt I/O run outside the shared lock; a stalled
mail store or factory no longer holds that global mutex. Same-qid notifications
wait on their own mutex while legacy reply handling/polling remains available.
Other new-channel selections may independently wait on the same mail store.
After waiting for the per-qid mutex, ownership is checked again: a legacy qid
or an already selected new-channel full
revision wins on re-entry, before or after delivery, even if the flag/factory
changes. A retained session selection also wins after notification-cache loss.
For an unclaimed question, new-channel selection is fixed per
project/session/full form revision in SQLite when the outbound flag is literal
true and the installed factory has an initialized outbox. With the flag off or
factory absent, an unclaimed question uses the
base legacy qid-only deduplication and metadata, without a revision snapshot or
additional parser callback; revising a same-id question cannot send another
legacy email. Legacy reply matching is unchanged. Selection alone grants
no mail authority. A future operational factory must remain available to read
these receipts while enrollment/feature sending is off. Before any factory
exists, the session holds only the existing runtime fallback choice; **do not
import or activate old legacy pending questions on startup**. Legacy in-memory
mail has no recoverable unit-6 binding. Unit 7d must preserve this boundary.

Recovery limitation (deferred by Dave): if **all** volatile ownership caches
are lost while the exact old form and its durable agent-mail receipt survive,
the notification entry point with the outbound flag off bypasses receipt
selection and may send legacy mail. With the flag on and factory available,
receipt recovery works. This simulated flag-off gap also exists at `3708ecd8`;
no live revival path retaining that exact old form was established. Revival
currently builds sessions without pending forms and parsing assigns fresh
question ids. Operational/recovery integration must address this gap; the
direct `adapter.selected()` restart test does not establish notification-entry
recovery with the flag off. Do not claim full cache-loss recovery coverage.

The original grace timer and SSE attendance recheck remain authoritative.
Answered/revised forms do not send. New mc:question forms use the same entry
point across providers; the existing common turn-boundary notification dedupes
against that full revision. New-channel delivery retains all questions/options,
including free text/multiple questions/multi-select. Failure or uncertainty of a
selected new channel never falls back to SMTP. Its entries are excluded from
legacy reply matching and IMAP polling, so new mail cannot cause legacy
credential reads. New replies enter only through signed ingress, screening,
laundering and unit 6; no approval/human-only gate changes.

## Initial build verification (9fa4dc48)

Working directory: this branch's worktree. Inherited environment unchanged.
Tests use the MAIN installation launcher/project interpreter; tests and
control-plane are separate commands because their `tests.conftest` packages
collide when combined. No packages were installed. The temporary Worker
dependency junction points to MAIN's existing `node_modules` for the existing
Python/Node bridge. After all tests it was removed nonrecursively; the worktree
link is absent and MAIN's `postal-mime/package.json` remains present, verified.

Portable resolution of the actual executable paths used (host-expanded exact
commands are in the operator-local item journal):

```powershell
$MainCheckout = Split-Path (git rev-parse --path-format=absolute --git-common-dir) -Parent
$ProjectPython = Join-Path $MainCheckout '.venv/Scripts/python.exe'
$Launcher = Join-Path $MainCheckout 'tools/test-run.py'
$Pyright = (Get-Command pyright).Source
```

Exact final test/type argument selections:

```powershell
& $ProjectPython $Launcher --project mission_control --port 5199 -- $ProjectPython -m pytest tests/test_agent_mail_deadline_review.py tests/test_agent_mail_enrollment.py tests/test_agent_mail_enrollment_review.py tests/test_agent_mail_enrollment_routes.py tests/test_agent_mail_held.py tests/test_agent_mail_ingress.py tests/test_agent_mail_launder.py tests/test_agent_mail_outbound.py tests/test_agent_mail_outbound_questions.py tests/test_agent_mail_revival.py tests/test_agent_mail_service_manifest.py tests/test_agent_mail_store.py tests/test_agent_mail_wake.py tests/test_agent_mail_wake_contract.py tests/test_agent_mail_wake_review.py
& $ProjectPython $Launcher --project mission_control --port 5199 -- $ProjectPython -m pytest control_plane/tests
& $ProjectPython $Launcher --project mission_control --port 5199 -- $ProjectPython -m pytest tests/test_question_channel.py
& $ProjectPython $Launcher --project mission_control --port 5199 -- $Pyright mc/agent_mail_outbound.py mc/agent_mail_outbound_state.py mc/agent_mail_outbound_signing.py mc/agent_mail_outbound_questions.py mc/question_channel.py mc/agent_runtime.py
```

Final results:

| Selection | Result | Exit |
| --- | --- | --- |
| All 15 `tests/test_agent_mail*.py` files, explicit ordered selection above | 707 passed in 95.68s; includes 66 new outbound/question cases and 641 existing checks | 0 |
| `control_plane/tests`, separate invocation | 98 passed / 1 existing skip in 1.10s | 0 |
| Existing `tests/test_question_channel.py`, final separate invocation | 41 passed in 1.06s | 0 |
| Pyright basic, all six changed `mc/` modules above | 0 errors / 0 warnings / 0 informations | 0 |

The CP skip is `test_edge_contract.py:86` (sibling clayrune-cloud missing), with
the existing Starlette/httpx warning. No CP code changed. The launcher reported
`telemetry_queue_full_progress_incomplete`, `unfinalized_runs_progress_incomplete`
on CP and `transport_unavailable_progress_incomplete` on final mail/Pyright;
complete dashboard telemetry is not claimed. Pytest output and child exits are
available. No environment, permission or dependency installation changed.

Earlier exact all-mail selection passed 705 checks, exit 0, before adding two
receipt-failure/immediate-question checks. Focused intermediate commands used
`-m pytest tests/test_agent_mail_outbound.py tests/test_agent_mail_outbound_questions.py tests/test_question_channel.py`
and passed 98, then 105, then 107 checks (all exit 0); the final full selections
above cover the last poller exclusion and legacy-recipient fixture change.

Earlier attempts: expanded focused run exited 1 with 95 passed/1 failed because
its fixture expected a project display name not present in its authoritative
project fixture. The output correctly used the project id; corrected that
assertion, then 98/105 focused checks passed. Initial Pyright exited 1 with
eight inferred-union/optional errors; explicit row typing and optional-form
narrowing resolved them. No operational behavior was inferred from those runs.

CHANGELOG records disabled internal behavior. README, USER_GUIDE, rules and
AGENTS are unchanged: no available user feature or new conduct requirement.
Browser/boot/mobile/installer and full standalone Worker suite are N/A because
no frontend, installer or Worker source changes. The actual Worker boundary is
exercised by the Python bridge and existing all-mail tests.

## Remaining gates and handoff

No new design choice is required for this unit. Dave owns review/integration.
Still unimplemented/unverified: operational authenticated transport and factory,
durable supervisor/scheduling/renewal, real cancellation, trusted caller
attribution, unchanged-gate machine custody, real D1/provider evidence and
schema upgrades, privacy/setup/status UI, capacity validation and real-mail
native conversation continuation. All previous MC-948 and human activation
gates remain in force. No live readiness or deployed feature is claimed.

The optional global PLAN-tab write was rejected before execution by the
unattended PreToolUse supply-chain fence: `.claude` is human-owned. No retry of
that write, workaround or permission change. The full plan is retained in the
MAIN item journal. A later attempt to append the rejected command there as
quoted documentation was also refused by that fence; the exact commands remain
in this session's tool output, not the journal or public repository. The branch
build did not require the optional UI artifact. This handoff and the journal
preserve crash recovery context.

## Review rework r1

Dave selected base legacy behavior rather than a revision-aware legacy reply
matcher. The notification entry point now uses qid-only ownership whenever
the new flag is off or the factory is absent. A selected new-channel timer
retains its own form/revision and cannot fall back to legacy if its factory
later disappears. The new parser callback likewise requires both flag and
factory; `handle_reply` was not changed in this rework. An existing adapter
test now accepts base legacy metadata without a new-channel marker.

Admission now resolves the trusted origin, completes signing and authority
validation, then checks the current intent deadline and `_validate_target`
under the same manager/store transaction immediately before recording sending.
An expired intent or thread is durably marked expired and its sealed payload
purged; no send attempt or external transport call starts. Other invalid
targets refuse. This closes a gap where post-send status could previously
report expired/accepted for mail sent with an already-expired reply token.

`tests/test_agent_mail_outbound_review.py` adds seven permanent cases: three
flag/factory combinations prove one SMTP send per legacy qid and a genuine
reply still reaching `_answer`; three deadline cases cover intent, thread and
both expiring inside the real fixture `prove()` (one-second TTL, fixture clock
advanced 1.1 seconds); a final case proves target validity is checked again
after signing. Baseline at `9fa4dc48`: all seven failed, exit 1. After the fix,
the review/new-question/legacy selection passed 66 checks, exit 0.

All application tests are under `tests/`, so the standard conftest runs before
application imports. The new review module additionally refuses fixture
imports unless remote access is disabled and both isolated state/vault roots
are present. Inherited remote/state/vault/temp-root variables were checked and
unset before testing. Legacy SMTP, credential resolution and resume are mocked;
outbound keys, clocks and transports are fixtures. No scratch application probe,
live control-plane/tunnel/attestation/SMTP call or real credential is used.
Separate CP tests retain their in-memory authorities and HTTP mock transport.

An attempted explicit environment-setting shell wrapper was rejected before
execution by the unattended fence for variable/command substitution. It was
not retried and no permission was changed. Fixed-argument launcher commands
using standard conftest isolation then ran; the review module's pre-import
guard passed. The rejected wrapper remains in the session's tool output.

Using the same `$ProjectPython`, `$Launcher` and `$Pyright` resolution above,
the exact final rework selections are:

```powershell
& $ProjectPython $Launcher --project mission_control --port 5199 -- $ProjectPython -m pytest tests/test_agent_mail_deadline_review.py tests/test_agent_mail_enrollment.py tests/test_agent_mail_enrollment_review.py tests/test_agent_mail_enrollment_routes.py tests/test_agent_mail_held.py tests/test_agent_mail_ingress.py tests/test_agent_mail_launder.py tests/test_agent_mail_outbound.py tests/test_agent_mail_outbound_questions.py tests/test_agent_mail_outbound_review.py tests/test_agent_mail_revival.py tests/test_agent_mail_service_manifest.py tests/test_agent_mail_store.py tests/test_agent_mail_wake.py tests/test_agent_mail_wake_contract.py tests/test_agent_mail_wake_review.py
& $ProjectPython $Launcher --project mission_control --port 5199 -- $ProjectPython -m pytest tests/test_question_channel.py
& $ProjectPython $Launcher --project mission_control --port 5199 -- $ProjectPython -m pytest control_plane/tests
& $ProjectPython $Launcher --project mission_control --port 5199 -- $Pyright mc/agent_mail_outbound.py mc/agent_mail_outbound_state.py mc/agent_mail_outbound_signing.py mc/agent_mail_outbound_questions.py mc/question_channel.py mc/agent_runtime.py
```

Rework results: all 16 mail files **714 passed in 54.77s, exit 0**; legacy
**41 passed in 1.62s, exit 0**; separate CP **98 passed/1 existing skip in
1.23s, exit 0**; all six modules **Pyright 0 errors/0 warnings/0 informations,
exit 0**. The seven new review cases are included in the 714.
The CP skip and warning remain the same as in the initial build. Launcher
transport/parent/pytest-enrollment progress capture is incomplete; child exits
and pytest output remain the verification evidence. No dependency installation,
frontend or Worker source change. The temporary existing-dependency junction
was removed nonrecursively after the full run; worktree link absent and MAIN's
existing `postal-mime/package.json` intact were verified.

The original operational/live gates above remain. No merge, push, restart,
deployment, real mail, credential change or human-gate edit; no new open choice.

## Review rework r2

Fenn found that the parser/common turn-boundary re-entry recomputed eligibility
before checking ownership. Disabling the flag or removing the factory changed
the deduplication key from `qid:revision` to `qid`, creating a second legacy
timer; enabling the adapter after legacy delivery created the inverse duplicate.

At r2, `on_question_raised` checked both ownership keys before eligibility, with
selection and admission inside the same notification lock (r3 below narrows
that lock's scope). Existing legacy
ownership covers the whole qid; existing new-channel ownership covers its full
revision. Availability changes cannot create a second key, timer or message.
Retained session selections also remain authoritative if notification caches
are lost. Never-selected legacy forms still have qid-only metadata, no form or
agent-mail marker, and no adapter parser callback. `handle_reply` is unchanged.

Ten permanent cases in `tests/test_agent_mail_outbound_questions.py` call the
real entry point a second time: flag off/factory removed before and after new
delivery (four), disabled/uninstalled adapter restored before and after legacy
delivery (four), and session ownership retained after notification-cache loss
(two). Existing full-revision and base-legacy reply tests remain unchanged.

Verification uses the same interpreter, launcher, ordered 16-file mail suite,
separate legacy/control-plane children and six-module Pyright selection listed
in r1 above. All pytest children run through explicit manifests. Every child
environment overlays these names with JSON `null`, removing only inherited
credential variables from that child: `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCOUNT_ID`, `CLAYRUNE_JWT_SIGNING_KEYS`,
`CLAYRUNE_JWT_SIGNING_KEY_PEM`, `GOOGLE_APPLICATION_CREDENTIALS`.
Probes explicitly preload `-p tests.conftest` before their application imports;
normal app/control-plane children load their respective standard conftest.
No credential values were inspected. Mail, timers, clocks, signing identities,
resume and external transports remain synthetic fixture effects.

The host-expanded exact commands, argv/env manifests and output logs are in
the operator-local item journal and this worktree's `_scratch/u7c_r2_report.md`.
Portable launcher invocations, using the path resolution above:

```powershell
& $ProjectPython $Launcher --project mission_control --port 5199 --manifest _scratch/u7c_r2_builder_manifest.json
& $ProjectPython $Launcher --project mission_control --port 5199 --manifest _scratch/u7c_r2_probes_manifest.json
```

The probes manifest runs these separate children in order, continuing after
the expected first-child failure:

```powershell
& $ProjectPython -m pytest -p tests.conftest _scratch/test_u7c_fenn_probes.py -v
& $ProjectPython -m pytest -p tests.conftest _scratch/test_u7c_fenn_r2.py -v -s
```

Both Fenn sources were copied unchanged into `_scratch/`, never committed.
The original r1 source SHA-256 remains
`831f4a9b0a42e5324c426dca5b6e7544a22bdfa9586791c1d563d9549108cd0d`;
r2 is `de4439d135a49ddb8bb530f6adafabb8c75e2b900ba1af1adfdabf91ae51da02`.
The unchanged r1 revised-form probe still expects a second legacy timer and
fails only at `timers[1]` with `IndexError`; it was deliberately not changed.
The other three r1 checks pass. All five r2 transition probes and three
base-contract probes pass.

Final results:

| Selection | Result | Exit |
| --- | --- | --- |
| All 16 mail files, same ordered selection as r1 | 724 passed in 44.10s, including ten new re-entry cases | 0 |
| Legacy `tests/test_question_channel.py` | 41 passed in 0.58s | 0 |
| Separate `control_plane/tests` | 98 passed / 1 existing skip / 1 warning in 0.72s | 0 |
| Pyright, same six modules as r1 | 0 errors / 0 warnings / 0 informations | 0 |
| Fenn r2 transition/base probes | 8 passed in 3.22s | 0 |
| Unchanged Fenn r1 probes | 3 passed / 1 expected obsolete second-timer assumption, in 2.92s | 1 |

Builder manifest aggregate exit is 0. Probes manifest aggregate exit is 1
because it deliberately includes the unchanged r1 probe. The CP skip remains
`test_edge_contract.py:86` (missing sibling repository), with the existing
Starlette/httpx warning. The launcher printed
`telemetry_queue_full_progress_incomplete`; full dashboard capture is not
claimed. Actual child output and exits are the verification evidence.

An earlier PowerShell builder run printed the same green framework results
but the shell returned 1 and surfaced telemetry stderr as `NativeCommandError`.
That invocation is not counted as a successful aggregate. Repeating the exact
same manifest under `cmd.exe` with native output redirection established the
final aggregate exit 0 above; no code, selection or environment overlay changed.

The initial combined focused command failed before collecting tests because
explicit conftest preloading collided with the normal app-test directory load.
Separate children resolved that bootstrap issue: 28 adapter cases and eight
r2 probes passed, aggregate exit 0. A combined PowerShell preparation command
was rejected before execution for runtime variable substitution; it was not
retried. Explicit manifest edits and fixed commands completed the preparation.
No host configuration, permission or environment was changed.

No new design choice. Operational/live gates remain as above; this is a local
disabled branch candidate with no merge, push, restart, deployment, real mail
or credential use. README, USER_GUIDE, AGENTS and rules need no update because
there is no available user-facing feature or new conduct rule. Browser/boot,
mobile and installer checks remain N/A; no frontend or Worker source changed.

## Review rework r3

Fenn's sole blocker was synchronous factory/receipt selection under the global
question mutex. A second SQLite connection holding `BEGIN IMMEDIATE` stalled
unrelated legacy `poll_replies()` until release. Selection now acquires a
per-qid mutex without holding the global mutex, rechecks ownership under the
global mutex, runs factory/receipt selection outside it, and commits the final
delivery key under the global mutex. Same-qid notifications serialize even
when their selected owner is legacy; different questions and legacy reply
handling/polling do not wait on that selection. Per-qid mutexes have the same
process lifetime as the existing notification caches. No selection I/O moved
into reply handling or polling.

Three permanent cases were added to the existing outbound-question suite:
Fenn's real SQLite writer/poller contention case and concurrent same-question
selection/timer/delivery for both new and legacy owners. All threads are joined
after releasing the synthetic blocker. No real mail/credentials or timers.
The five r2 ownership transitions and three base-contract checks are unchanged.

Every pytest invocation used the installation launcher with a manifest and the
same per-child five-variable JSON-null environment overlay described in r2.
Normal app and control-plane tests stayed in separate children. Scratch review
probes preload `-p tests.conftest`; their source was copied unchanged from
Fenn's review. R2 SHA-256 remains
`de4439d135a49ddb8bb530f6adafabb8c75e2b900ba1af1adfdabf91ae51da02`;
r3 is `1c10ea29b8ba1962ad082a9467d2b9e94dff1c75e10b6142a99d40e43ebc6f00`.

Portable equivalents of the launcher invocations (same path variables as above,
branch worktree cwd; actual cmd.exe syntax with native redirection is retained
in the operator-local journal and scratch report):

```powershell
& $ProjectPython $Launcher --project mission_control --port 5199 --manifest _scratch/u7c_r3_focus_manifest.json > _scratch/u7c_r3_focus_output.txt 2>&1
& $ProjectPython $Launcher --project mission_control --port 5199 --manifest _scratch/u7c_r3_builder_manifest.json > _scratch/u7c_r3_builder_output.txt 2>&1
& $ProjectPython $Launcher --project mission_control --port 5199 --manifest _scratch/u7c_r3_review_manifest.json > _scratch/u7c_r3_review_output.txt 2>&1
```

Focus child argv is `$ProjectPython -m pytest
tests/test_agent_mail_outbound_questions.py -v`. Builder uses the identical
ordered 16 mail files, legacy question file, separate `control_plane/tests`
and six Pyright modules listed in r1/r2. Review manifest contains two ordered
children, continuing after failure:

```powershell
& $ProjectPython -m pytest -p tests.conftest _scratch/test_u7c_fenn_r2.py -v -s
& $ProjectPython -m pytest -p tests.conftest _scratch/test_u7c_fenn_r3.py -v -s
```

| Selection | Result | Exit |
| --- | --- | --- |
| Focused permanent outbound-question suite | 31 passed in 5.90s | 0 |
| Same 16 ordered mail files | 727 passed in 54.27s | 0 |
| Legacy question suite | 41 passed in 0.61s | 0 |
| Separate control-plane suite | 98 passed, 1 existing skip, 1 warning in 1.04s | 0 |
| Same six Pyright modules | 0 errors, 0 warnings, 0 informations | 0 |
| Unchanged Fenn r2 ownership/base probes | 8 passed in 1.90s | 0 |
| Unchanged Fenn r3 probes | 5 passed, 1 expected deferred failure in 2.19s | 1 |

Focus and builder manifest aggregates exited 0; review aggregate exited 1.
The r3 contention probe printed completion before database release with wait
time rounded to 0.0 seconds. Its sole remaining failure is
`test_durable_receipt_recovery_after_all_volatile_ownership_lost[False]`,
the pre-existing simulated cache-loss/flag-off limitation explicitly deferred
by Dave above. It still produced one synthetic new message and one synthetic
legacy message; this failure was neither fixed, skipped nor marked xfail.
No full recovery coverage or live revival failure is claimed. R1's obsolete
second-legacy-timer probe was not part of this round's requested selection.

The control-plane skip remains `test_edge_contract.py:86` (missing sibling
repository), with the existing Starlette/httpx deprecation warning. Pyright's
version notice is non-failing; nothing was upgraded. Launcher emitted
`telemetry_queue_full_progress_incomplete` and
`unfinalized_runs_progress_incomplete`; full dashboard capture is not claimed.
Native exits and framework output are the evidence. Exact host-expanded
commands, manifests and logs remain in the operator-local journal and scratch
handoff report. The existing Worker dependency junction was removed
nonrecursively after verification; the worktree link is absent and MAIN's
`postal-mime/package.json` remains intact. No package installs.

CHANGELOG is updated. USER_GUIDE, README, AGENTS and rules need no update:
disabled internal correction only, no new user surface or conduct rule.
No frontend/browser/boot, mobile or installer check applies. No new design
question, operational integration, merge, push, restart, deployment, real mail
or credential use. Dave owns review/integration and the existing pre-live gates.
