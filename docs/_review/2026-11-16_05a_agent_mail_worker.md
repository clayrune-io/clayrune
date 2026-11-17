# Agent Mail Worker review, pass 05a (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `f0373733`.
Review only: no source file was changed. Every line cited was opened. A cost or
a wrong result is marked **measured** (a scratch script against the pure
function, run with Node outside the repo tree) or **read, not measured**.
`server` was never imported, the live server was sent nothing, no model call
was made, no mail was sent, no external service was called, and no process
that binds a port was started.

Scope: everything under `workers/agent-mail/` (Cloudflare Worker: 62 source
files, about 10,000 lines; 6 migrations; 85 test and fixture files, about
19,600 lines). Source first, tests at structure level.

Finding-id prefix for this pass: **W**.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology,
**F** feature does not work. Severity, same scale as passes 1 to 3:
**blocker** = wrong result or lost data on a normal path today, or a security
boundary that one ordinary request gets around; **high** = wrong result or lost
data on a reachable path, or a cost paid on every request; **medium** =
narrower trigger, or a latent defect behind a setting that ships off;
**low** = hygiene.

STATUS: IN PROGRESS. Written incrementally. The ranked summary, feature table
and streamlining section at the top are filled in last; until then, the
"Working notes" section at the bottom is the live record. If this line is
still here, the pass did not finish: continue from "Progress log".

## Ranked summary

(written last)

## Feature table (lens 4)

(written last)

## Streamlining

(written last)

## Findings in detail

(candidates are appended under "Working notes" as they are found, then moved here)

## Prior reviews

(written last)

## Coverage

(written last)

## Working notes

### Progress log

- 2026-11-16: report created. Prior pass summaries (01, 02, 03) read: none
  covers `workers/agent-mail/`. Next: design docs, then source in dependency
  order.

### Candidate findings

Notes are terse on purpose; each is re-verified and expanded when moved to
"Findings in detail". "C" = candidate, not yet confirmed against every caller.

- C1 (L/E, high?) `pull-retention.js:149` `retain()` has no production caller.
  `worker.js:100-124` scheduled runs only `sweep` and `deliverControlMail`.
  Importers of `pull-retention.js`: `test/pull-retention.test.js` only. So
  nothing ever deletes `pull_nonces`, `pull_tickets`, `pull_budgets`,
  `pull_operations`, `pull_audit`, `pull_tokens`, `pull_browser_sessions`,
  terminal `pull_events`, `pull_dedupe`, `pull_threads`, `pull_intents`, and the
  sealed owner addresses (`old_owner_sealed`, `new_owner_sealed`,
  `envelope_sealed`) are never nulled. Acknowledged as cut in
  `docs/AGENT_MAIL_THIN_SLICE.md:132-135` ("retention passes (W03)"), and
  `docs/AGENT_MAIL_PULL_CONTRACT.md:2124` gives the wiring to "W30 scheduled
  glue". W01 (`control-mail-delivery.js`) was wired, W03 was not.
- C2 (E/L) legacy `rate_limits` table is still written on the live path:
  `session-auth.js:66,179,206` and `ingress.js:56-61` call `Ledger.limit`
  (`ledger.js:25-30`), one new row per source per minute. Its only deleter is
  `Ledger.reconcile` (`ledger.js:131`), called only from `replay.js:21,29`
  (dead). Not covered by `pull-retention.js` either (its families list has no
  `rate_limits`). So even wiring C1 leaves this table growing.
- C3 (L, low) `pull-sweep.js:57` notify cursor is saved to the last install id
  even on a short page; the next tick selects `install_id > last` and finds
  nothing, then resets. With 1..99 due notifications every second tick is a
  no-op. Retention does it right (`pull-retention.js:161`). To measure.
- C4 (E) `ledger-pull.js:119-121` and `pull-ledger.js:211-214`: every rejection
  runs `SELECT COUNT(*) FROM pull_audit` twice. Once the table holds 10,000 rows
  (and with C1 it never shrinks) each dropped mail scans 20,000 index rows.
- C5 (L, low) `ingress.js:58` per-install refused bucket is written, result
  never read; nothing else reads `ingress-refused:<install>`.
- C6 (M, low) `wrangler.toml.example:42` "Apply schema.sql then migrations/0001
  through 0004": there are six. Noted as stale in
  `docs/AGENT_MAIL_THIN_SLICE.md:148`, still stale.
- C7 (L) contract names routes with no handler:
  `docs/AGENT_MAIL_PULL_CONTRACT.md:2121-2122` `/mail/key-recovery`,
  `/mail/delete` (files `key-recovery-http.js`, `account-delete-http.js` do not
  exist). Line 2119 says the manage page renders forms for them. Verify in
  `owner-management.js`.
- C8 streamlining: `PullLedger.expire/purgeDetails/purgeDedupe/cursor`
  (`pull-ledger.js:316-352`) duplicate `pull-sweep.js:86-113` and
  `pull-retention.js`; check callers.
- C9 streamlining: non-pull branch of `ingress.js:98,104-113`,
  `held-envelope.js`, legacy `Ledger` methods; worker always passes
  `Ledger.pull()` (`worker.js:92`).
- C10 (L) `pull-ledger.js:147` enqueue refuses text with NUL / whitespace-only;
  `ingress-pull.js:74-78` maps that to `storage_unavailable` and throws, so the
  SMTP transaction fails instead of a drop. Check what `strip.js` can emit.
- C11 (E, medium) `pull-mailbox.js:40-46` `current()` is 4 D1 round trips
  (registration 1, authorize 2, empty atomic batch 1) and runs at the top of
  every message (`:193`), in every `drain` (`:144`) and inside every `send`
  (`:50`). `offer` adds 6 more (`pull-ledger.js:235-256`). A resume that pages
  10 events is roughly 130 D1 calls inside one `blockConcurrencyWhile`. To
  measure with the test facade.
- Progress: read fully so far: worker.js, production.js, signing.js,
  enrollment-transcript.js, signed-v2-verifier.js, request-body.js,
  sql-clock.js, response-signing.js, headers.js, held-envelope.js, mailbox.js,
  content-id.js, session-auth.js, pull-socket-route.js, pull-socket-config.js,
  pull-socket-mailbox.js, pull-frame.js, manifest-http.js, ledger.js,
  pull-retention.js, pull-sweep.js, ledger-pull.js, ingress-pull.js,
  ingress.js, pull-ledger.js, pull-mailbox.js, owner-registry.js.

