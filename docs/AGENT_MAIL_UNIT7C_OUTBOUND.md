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

New-channel selection is fixed per project/session/full form revision in SQLite
when the outbound flag is literal true and the installed factory has an
initialized outbox. With the flag off or factory absent, notification uses the
base legacy qid-only deduplication and metadata, without a revision snapshot or
additional parser callback; revising a same-id question cannot send another
legacy email. Legacy reply matching is unchanged. Selection alone grants
no mail authority. A future operational factory must remain available to read
these receipts while enrollment/feature sending is off. Before any factory
exists, the session holds only the existing runtime fallback choice; **do not
import or activate old legacy pending questions on startup**. Legacy in-memory
mail has no recoverable unit-6 binding. Unit 7d must preserve this boundary.

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
