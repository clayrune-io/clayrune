# MC-1166 Unit 6: exact-conversation mail continuation

Local review candidate, default disabled. No startup loop, enrollment, outbound
mail, deployment or live configuration changes. Unit 7 must explicitly wire the
controller after human enrollment. There is no fresh/successor fallback.

## Trusted integration order

Before outbound send:
1. Create a local thread with `MailStore.bind_thread()`.
2. Call `MailWake.bind(thread_token, question=stored_form_or_none)` while the
   originating session exists. This snapshots project generation, provider/native
   id, persona, cwd, execution/permission policy, trigger identity, job shape and
   the complete pending question form/id.
3. Bind the opaque transport id with `MailIngress.bind_transport()`.
4. Persist outbound correlation and send through the separately gated transport.

Bindings are immutable/idempotent. A missing snapshot cannot be added after an
inbound event or transport mapping exists. Never infer legacy identity from
current state, subjects, character names or sender-supplied session ids.

After units 4/5 durably accept and launder an event, the local controller may call
`MailWake(store, enabled=True).process_next(flask_app)`; enabled defaults False.
It enters the existing `agent_followup` route under a private Python context.
HTTP fields cannot construct that context. Returned status is metadata only.

Unit 7 still owns polling/scheduling, human enablement, owner/key lifecycle,
session/project deletion hooks, status/recovery UI and real transport integration.
Wire deletion to the existing store revocation operations.

## Exact delivery and durable receipts

The existing inbox claims preserve receipt order. Busy events remain durable;
held/uncertain events are never delivered or automatically replayed. Missing
history, changed identity/generation/permission context, closure, missing persona
or worktree, quota and recovery holds refuse before provider submission.

Supported mail-wake providers are Claude, Codex and Qwen. Gemini is explicitly
held with `gemini_mail_wake_unsupported` at binding and when validating older
snapshots: its runtime returns no transcript path, so persistent native history
cannot be proven. Ordinary Gemini follow-up support does not prove mail-wake
support. History lookup uses the validated snapshot's execution cwd, including
an isolated worktree, rather than its parent project root.

A live canonical owner must match provider/native identity and stored context;
a matching character is insufficient. An explicit closed originating row remains
held. Roster recovery stays held under its separate recovery-review contract.
Every spawn checks exact native resume arguments and cwd before the factory.
Auto-fresh is refused. Alive Mode B identity is checked under the manager lock.
Process creation is serialized with mail-store consent revocation. Pipe turns
have durable admission before starting their writer; blocked pipe I/O does not
hold SQLite. Revocation prevents new admissions; an admitted operation may produce
a later receipt and cannot be recalled.

Mail-origin native revival preserves original trigger type/id, including a
scheduled Codex session's sandbox/fence posture. Existing lifecycle code preserves
persona, worktree, execution context, native history and duplicate-process guards.
Mail does not re-arm spawner/workflow completion callbacks.

Schema v3 adds `mail_continuations` and `mail_turn_receipts` to the existing inbox.
V1/v2 migrations retain events without inventing identity snapshots. Missing v3
tables fail closed. Revocation/expiry purge question snapshots; terminal retention
also removes orphan receipt metadata.

The real `delivery_attempt_id` is persisted against the event before submission.
Prelaunch reservations may update it until admission; afterward another attempt
cannot take it. Publication of the same attempt after fast acceptance is allowed.
Pipe acceptance requires actual write/flush/close as appropriate; shim capture
close waits for CLI delivery. Claude Mode A delivers via argv after an exact-resume
spawn. HTTP 200, a queue or unrelated completion is never acceptance. The attached
scope excludes the raw inbound body. Only the bounded unit-5 envelope is delivered,
with a visible mail-source marker.

Approved policy A: mail-only pipe acknowledgment uses the existing 10-second
window. Context/payload preparation is separate; this is **not a total startup or
follow-up deadline**. Missing acknowledgment leaves visible `uncertain`, retains
the reply, consumes no question and causes no timeout kill/restart or replay.
Ordinary interactive revival retains its indefinite acknowledgment policy;
ordinary provider writers retain their existing failure handling.

A matching late positive receipt reconciles with `reconcile_uncertain()`.
Completion is a separate durable bit tied to project/session/generation/provider/
native id/attempt. Positive receipt plus matching successful completion history
can recover after restart. Terminal history/callback alone cannot accept mail.
Late completion after store expiry remains refused. Revoked content is not rebuilt.

Reconciliation isolates receipt errors per row and logs them, allowing other
projects to continue. Stale/deleted project generations retire accepted incomplete
deliveries with visible `project_generation_changed`: submitted events expire;
submitting/uncertain events are revoked. Missing projects retire similarly with
`project_unavailable`. Original accepted/completed evidence and generation stay
unchanged; content is purged using existing terminal transitions. Terminal events
are excluded from subsequent receipt passes, and late callbacks cannot reopen
them. Other read/storage failures retain the receipt for a later reconciliation
pass; they never authorize replay.

## Questions

The full stored form is hashed to validate revision. Only a matching still-pending
form may be consumed after acceptance and current consent, under manager/store
locks. Notes, late replies and invalid choices cannot clear another question or
any plan-approval gate. Free text stays text; mail never becomes a product approval
button action or an invented approved/declined result.

Exact labels/positions are classified only when unambiguous. Multiple questions
require Q1:/Q2: boundaries; multi-select values stay inside their question. Invalid
or ambiguous choices remain clarification text. Late question replies are labeled.

## Verification and boundaries

Separate modules own target proof, private continuation scope, pipe acknowledgments,
question validation, wake/receipt handling and schema migration/cleanup. The route
monolith receives imports, decorators and helper call sites only.

Synthetic tests use real SQLite and existing route/revival/runtime methods with
fake transports/CLI processes. They cover alive/ended Claude A/B and Codex/Qwen
continuation, Gemini's named refusal using its real transcript method, exact
resume/cwd, scheduled sandbox posture, busy/replay/order,
legacy/missing identity refusal, revocation, questions and late receipts. Tests
inspect the actual payload/argv handed to the resumed CLI double, not just HTTP
success. No real provider/model or actual native-provider processing is claimed.

Cold-review regressions use the real Codex transcript locator against synthetic
rollout metadata and real lifecycle SQLite generation changes. They prove isolated
history binding, stale receipt retirement across submitted/revoked/uncertain
cases, late-callback refusal, and progress for a healthy second project despite a
stale generation or a logged history-read error. The prior mocked Gemini success
cases did not establish mail-wake compatibility; that earlier claim is withdrawn.

The earlier broader run had nine continuation-model failures, reproduced
independently on exact base `9f73231f`. Their repair belongs to a separate branch;
the separately reported scoped matrix excludes that file explicitly.

Exact operator-specific argv, cwd, results and correction history live in the
ignored task journal/local verification report. Portable launcher form:

```text
<project-python> <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest <explicit selection> -o addopts= -q --tb=short
<project-python> <installation>/tools/test-run.py --project mission_control --port 5199 -- <pyright> <explicit module selection>
```

Real mail, credentials, tunnel, Cloudflare deployment, restart and live end-to-end
continuation remain unverified. Unit 7 and the separately approved pilot remain
required. README, USER_GUIDE and rules are unchanged: disabled internal integration,
no new user-facing surface. Frontend smoke is inapplicable: no static/UI changes.
