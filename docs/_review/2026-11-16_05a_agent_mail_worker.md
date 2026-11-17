# Agent Mail Worker review, pass 05a (MC-1179), 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `f0373733`.
Review only: no source file was changed. Every line cited was opened. A cost or
a wrong result is marked **measured** (a scratch script outside the repo tree,
run with Node against the pure function or the import graph) or **read, not
measured**. `server` was never imported, the live server was sent one GET (the
backlog), no model call was made, no mail was sent, no external service was
called, and no process that binds a port was started.

Scope: everything under `workers/agent-mail/` (Cloudflare Worker: 62 source
files, 9,191 lines; `schema.sql` and 6 migrations; 80 test and fixture files,
about 16,600 lines). Source first, tests at structure level.

Finding-id prefix for this pass: **W**.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology,
**F** feature does not work. Severity, same scale as passes 1 to 3:
**blocker** = wrong result or lost data on a normal path today, or a security
boundary that one ordinary request gets around; **high** = wrong result or lost
data on a reachable path, or a cost paid on every request; **medium** =
narrower trigger, or a latent defect; **low** = hygiene.

This pass crossed three context rollovers. The last one lost an unsaved draft
of the final text, so the report was rebuilt from the committed working notes.
W1, W2, W3, W4, W5, W6, W9, W11, W12, W13, W15 and W16 were re-opened line by
line in the final context. W10, W14 and W17 are carried from notes written when
the lines were open; their line numbers were not re-opened at the end.

Totals: 19 findings: 0 blocker, 3 high, 7 medium, 9 low.

## Ranked summary

| Rank | Id | Finding | Lens | Severity |
|---|---|---|---|---|
| 1 | W1 | A send that is reserved but refused at authorization (daily cap, or a fault between the two batches) stays `reserved` for good; the retry returns the stuck receipt and the mail is never sent | L, F | high |
| 2 | W2 | Key recovery and account delete: the start route accepts them, mails a step-up link and charges the budget, then the page posts to a path with no handler (404). No statement can set an install to `deleted` | F | high |
| 3 | W3 | Retention is never run: `retain()` has no caller. Thirteen tables only grow, the sealed old owner address outlives the revert window, and the rejection audit stops recording at 10,000 rows | L, E | high |
| 4 | W4 | A crash between authorization and the provider result leaves the intent `sending` for good; the contract says it becomes `uncertain` | L | medium |
| 5 | W5 | Legacy `rate_limits` table takes one new row per source per minute on the live path; its only deleter is dead code | E | medium |
| 6 | W6 | Every socket message costs 4 D1 round trips before it does anything; a 10-event resume is about 130, all inside `blockConcurrencyWhile` | E | medium |
| 7 | W7 | Operator-facing text is wrong: the wrangler template lists 4 of 6 migrations, the README describes the retired push transport as current, and the 7d-2 contract's routes all answer 410 with no "superseded" mark | L | medium |
| 8 | W8 | The four top-level catches drop the exception; a code bug and a D1 outage log the same fixed string | M | medium |
| 9 | W9 | 1,255 lines of the retired enrollment and lifecycle stack are loaded on every cold start for two path predicates that answer 410 | E, M | medium |
| 10 | W10 | The request boundary is copied, not shared: five machine-HTTP classes share 100 to 169 lines each with the first; four hosted classes repeat the cookie parser, signed-response builder and HTML headers | M | medium |
| 11 | W11 | A plain-text body with a NUL byte passes `strip()` and is then failed at SMTP as `storage_unavailable` instead of dropped with a reason (measured) | L | low |
| 12 | W12 | Notify sweep saves its cursor on a short page, so with 1 to 99 due wakes every second tick does nothing; both sweep tasks also write a cursor row every minute when idle | L, E | low |
| 13 | W13 | The "already confirmed" receipt in three handlers is unreachable: the link router refuses a consumed token first, so a refresh after success shows "not found" | L | low |
| 14 | W14 | `/mail/change/cancel` has a handler and no form anywhere | L | low |
| 15 | W15 | `releaseAddressStatements` has no caller; an abandoned signup keeps its install, generation and address rows for good | L | low |
| 16 | W16 | Per-install refused bucket is written on every refused mail and read by nothing | E | low |
| 17 | W17 | The control-mail consumer makes about 16 sequential D1 round trips per mail | E | low |
| 18 | W18 | Thirteen file headers say "Unwired" on modules `worker.js` wires; `package.json` says "Not deployed" | L | low |
| 19 | W19 | At least 2,616 test lines cover retired modules; two tests pin W1 as intended; nothing tests retention or the two dead forms through the Worker entry | M | low |

I would block a wider rollout on W1 and W2. W3 is the one that gets worse every
day it is left. W4 to W10 are worth one cleanup unit each. The rest are not
worth a merge block.

## Feature table (lens 4)

Traced by reading entry point, state change and response. Nothing was run
against a Cloudflare runtime; "works" means the code path is complete and
consistent, and where a repo test drives it through `worker.js` the test is
named.

| # | Feature | Main path | Likely failure path | Verdict |
|---|---|---|---|---|
| 1 | Service manifest | `GET /v1/mail/manifest` → `manifest-http.js:24,46` serves the pre-signed var verbatim | malformed var → 503 `manifest_malformed` | works as documented |
| 2 | Signup, verification mail, activate | `/v1/pull/signup/start` → outbox row → cron `deliverControlMail` → `/mail/link` → `/mail/link/confirm` → `/v1/pull/signup/status` → `/v1/pull/activate` (driven end to end by `test/worker-pull-routing.test.js:328`) | admission closed → signed 503 `signup_closed` (`:356`) | works with a defect (W13, W15) |
| 3 | Socket ticket and pull delivery | `/v1/pull/ticket` (`session-auth.js`) → `/v1/pull/socket` → `PullSocketMailbox` → resume, offer, ack, hold (`pull-mailbox.js`, `pull-ledger.js`) | superseded or fenced scope → close 4001/4003 (`pull-mailbox.js:63-65`) | works with a defect (W6) |
| 4 | Inbound owner reply | `email()` `worker.js:89` → `ingress.js:50` gates → `auth.js` decide → `strip.js` → `ingress-pull.js:63` enqueue → DO wake | refusal → `drop()` audit row + SMTP reject (`ingress.js:53-65`) | works with a defect (W11, W3, W16) |
| 5 | Wake retry and expiry sweep | cron `worker.js:107` → `pull-sweep.js:46,84` | wake throws → logged, retried with backoff (`:65-68`) | works with a defect (W12) |
| 6 | Outbound send and send status | `/v1/pull/send` → `outbound.js:102` reserve → authorize → provider → finish; `/v1/pull/send/status` | daily cap → 429 `send_limit` | works with a defect (W1, W4) |
| 7 | Install status | `/v1/pull/status` → `pull-status-http.js` signed snapshot | unsigned → uniform refusal | works as documented |
| 8 | Owner step-up and manage page | `/v1/pull/management/start` → step-up mail → `/mail/link` → `/mail/manage` → form | wrong CSRF → refusal | works with a defect (W2: two of five purposes lead to a 404) |
| 9 | Owner email change, revert, cancel | `/mail/owner-change` → confirm on new address → switch; `/mail/revert` | expired token → refusal | works with a defect (W14, W3) |
| 10 | Resume after disable | `/mail/resume` → `owner-change.js:394-404` | frozen install → refusal | works as documented |
| 11 | Key rotation | manage page approves `rotate` (`owner-management.js:396`) → `/v1/pull/key/rotate` (`pull-key-rotate-http.js:157`) retires the old generation | unapproved → refusal | works as documented |
| 12 | Fence (disable) | `/v1/pull/fence` → `pull-fence-http.js:192-204` | stale generation → refusal | works as documented |
| 13 | Key recovery | start accepted, form posts to `/mail/key-recovery` | 404 `not_found` | cannot work as written (W2) |
| 14 | Account delete | start accepted, form posts to `/mail/delete` | 404 `not_found` | cannot work as written (W2) |
| 15 | Metadata retention | `pull-retention.js:149` `retain()` | never invoked | cannot work as written (W3) |
| 16 | Control mail delivery | cron `worker.js:113-116` → `control-mail-delivery.js` lease, send, settle | provider quota → row back to `queued` (`:271-273`) | works as documented (cost: W17) |
| 17 | Legacy push routes | `/v1/mail/send`, `/v1/mail/reconcile`, enrollment and lifecycle prefixes → 410 `legacy_retired` (`worker.js:57-60`) | n/a | works as coded; the docs still describe them as current (W7) |

Count: 7 work as documented, 7 work with a defect, 3 cannot work as written,
0 could not determine.

## Backlog cross-check

Source: `GET /api/project/mission_control/backlog`, 1,183 items, 91 with status
open, in_progress or blocked (38 in progress, 30 open, 23 blocked). Each was
matched on its text; MC-1179 to MC-1183 skipped. Two touch this region. No
item was patched or noted.

| Key | Subject | Status now | Class | Evidence |
|---|---|---|---|---|
| MC-1176 | Steward decisions over agent mail | in_progress | NEEDS A HUMAN | The merge it names (`6048e618`) is on master and changed only `mc/steward_role_comms.py`, its test and docs; nothing under `workers/agent-mail/` was needed or is missing. What is left is the live end-to-end test, which needs the owner's go-ahead to run a steward cycle. One Worker fact to carry into that test: the send cap is 20 a day per install (`ledger.js:8`), and W1 means the 21st is stranded, not delayed. |
| MC-999 | Feedback send through a Worker | open | NOT DONE | No such Worker exists: `workers/` holds `agent-mail` and `update-ping` only, and `static/js/feedback-modal.js:11,38` still builds a `mailto:` link. It is a different Worker from this one, on a different binding target. Smallest step: a new small Worker reusing the send adapter shape in `email-sending.js` and the limiter in `ledger.js:25-30`; deploy needs the owner. None of my findings is the same defect. |

Classed DONE or OBSOLETE: none. No live item asks for retention, key recovery,
delete or the send-cap fix; W1, W2 and W3 have no backlog item at all.
MC-1166 (the parent item) is already `done` and was not classified.

## Streamlining

Callers were checked with an import-graph script from the deploy entry
(`production.js`, named by `wrangler.toml.example:7`) and by grep of every
symbol named below across source and tests.

| # | What | Where | Evidence it is unused or redundant | Lines |
|---|---|---|---|---|
| S1 | Retired enrollment and lifecycle stack | `lifecycle.js` 256, `enrollment-http.js` 219, `enrollment.js` 169, `delivery-proof.js` 140, `destination-provisioning.js` 72, `cp-client.js` 61, `destination-cloudflare.js` 58, `code-mail.js` 55, `enrollment-begin-proof.js` 47, `enrollment-preflight.js` 44, `enrollment-grant.js` 41, `enrollment-provider.js` 33, `enrollment-reservations.js` 28, `enrollment-challenge.js` 16, `response-signing.js` 16 | Reachable only through `worker.js:5-6`, which imports two path predicates (`lifecycle.js:31`, `enrollment-http.js:43`); `worker.js:57-60` answers 410 for every such path. Measured: cutting those two imports leaves all 15 files unreachable. Replace with two prefix constants. Keep `edge-policy.js` and `delivery-capability.js`: they are in `edge-vendor.manifest.json` for the sibling edge Worker | 1,255 |
| S2 | Test-only legacy entry shims | `legacy-error-boundary.js` 10, `legacy-transport-hooks.js` 39, `legacy-transport-http.js` 53, `replay.js` 70 | No source importer outside each other; importers are `test/transport-support.js`, `test/enrollment-http-support.js` and three replay tests | 172 |
| S3 | Legacy `Ledger` methods | `ledger.js:18-24,31-159` | Live code calls only `limit`, `pull`, `stmt`, `first`, `run` on it (grep of every `.shared.` and `.ledger.` use). `registration`, `recipient`, `claimNonce`, `reserveIngress`, `reject`, `thread`, `intent`, `reserveSend`, `finishSend`, `enqueue`, `reconcile`, `due`, `claim`, `settle` have no live caller | about 130 |
| S4 | Legacy `outbound()` | `outbound.js:54-92` | Only importer is `legacy-transport-http.js:5`; `pull-send-http.js:4` takes `pullOutbound` and `validateSend` | about 40 |
| S5 | Non-pull branch of ingress | `ingress.js:51` (`pull` flag), `:98`, `:104-113`, `held-envelope.js` | `worker.js:92` always passes `Ledger.pull()`, so `pull` is always true in production | about 22 |
| S6 | Duplicate maintenance on `PullLedger` | `pull-ledger.js:320-352` (`expire`, `purgeDetails`, `purgeDedupe`) | No source caller; `pull-sweep.js:84` and `pull-retention.js` do the same work; only `test/pull-ledger.test.js` calls them | about 33 |
| S7 | Copied machine-HTTP boundary (W10) | `pull-activate-http.js`, `pull-key-rotate-http.js`, `pull-status-http.js`, `pull-fence-http.js`, `pull-send-http.js` | Measured with `comm` on sorted unique lines: 169, 146, 120 and 100 lines in common with `pull-activate-http.js`. One base class holding `flatJSON`, `current`, `snapshotGuard`, `flowGuard`, `fresh`, `accounting`, `operation`, `save`, `response` and the `handle` prologue | about 400 |
| S8 | Copied hosted helpers (W10) | cookie parser `signup-http.js:99-104`, `owner-management.js:80-85`, `owner-change.js:39-44`, `link-router.js:53-55`; signed response `signup-http.js:128-142`, `owner-management.js:93-107`, `pull-send-http.js:224-239`; v2 verify `owner-management.js:109-139` against `signed-v2-verifier.js:21-38` | Same code, read side by side | about 100 |
| S9 | Dead `deleted` branches | `owner-change.js:280-291,369,374,388`, `control-mail-delivery.js:240`, `pull-status-http.js:147-149`, `owner-management.js:192,267` | No statement sets `pull_installs.state='deleted'` (W2). Delete them or build the delete route | about 25 |
| S10 | Tests of S1 to S4 | 17 files under `test/` (`enrollment*`, `lifecycle`, `replay*`, `delivery-proof`, `destination-*`, `cp-client`, `code-mail`, `held-envelope`, two support files) | They import only the modules above | at least 2,616 |

Source total: about 2,180 of 9,191 lines (24%), plus at least 2,616 test lines.
Not on this list: `pull-retention.js` (181 lines, dead today) should be wired,
not deleted (W3); legacy tables in `schema.sql` and `migrations/0001` to `0004`
need a D1 migration to drop and `rate_limits` must stay.

S1 to S4 rest on one fact I cannot settle from code: that the push transport
is not coming back. `worker.js:59` calls it `legacy_retired`; `outbound.js:96`
says "remains until route cutover", and the cutover has happened. That is the
owner's call (see Questions).

## Findings in detail

### W1 (high, L/F) send stranded in `reserved`

`outbound.js:130` `reserveSend` commits the intent as `reserved` (with the
request nonce, accounting and operation row). `outbound.js:136` `authorizeSend`
is a second batch. `ledger-pull.js:210` refuses it with `send_limit` when the
install or global day bucket is full (`sendsPerDay: 20`, `ledger.js:8`), and
`outbound.js:161` turns that into 429. The row stays `reserved`.

A retry with the same `intent_id` enters `outbound.js:120-126`: a prior row
exists, so it returns the prior receipt and never calls `authorizeSend`. The
only writers that move a `reserved` intent are the authorize CAS itself
(`ledger-pull.js:216`) and three retirements to `revoked`
(`owner-change.js:251`, `pull-fence-http.js:199`, `pull-key-rotate-http.js:191`).
There is no sweep. `/v1/pull/send/status` reports `reserved` indefinitely, and
the contract tells the client that means "in flight, never resubmit"
(`docs/AGENT_MAIL_PULL_CONTRACT.md:760-761`).

Result: the 21st mail an install sends in a day is never delivered, including
after the budget resets. The same happens if D1 fails between the two batches.
The Python side (pass 05b's region, cited only) labels the 429 "retryable,
Worker proves no reservation/write" (`mc/agent_mail_outbound.py:457-458`),
which is false.

Evidence: read. Pinned as intended by `test/outbound-pull.test.js:220` (second
send returns state `reserved`) and `test/pull-send-http.test.js:390`.

Smallest fix: when a replay finds its prior row in `reserved`, run
`authorizeSend` (its CAS admits exactly one winner). Or, on `send_limit`, call
`finishSend(..., 'failed')`, a transition `ledger-pull.js:275` already allows
and nobody uses. Existing tests would not catch it; two assert it.

### W2 (high, F) key recovery and account delete lead to a 404

`owner-management.js:14` lists `key_recovery` and `delete` as purposes and
`/v1/pull/management/start` accepts both, queues the step-up mail and charges
the verification budget. After the owner confirms, `owner-management.js:386-392`
renders a form whose action is `/mail/key-recovery` or `/mail/delete`. Neither
path is in any registry (`signup-http.js:14-15`, `owner-management.js:11-12`,
`owner-change.js:11-12`), so `worker.js:63` answers 404.
`docs/AGENT_MAIL_PULL_CONTRACT.md:2121-2122` names `key-recovery-http.js` and
`account-delete-http.js`; neither file exists.

No statement anywhere sets `pull_installs.state='deleted'` (the one mention,
`pull-fence-http.js:192`, preserves an existing value), so every `deleted`
branch is dead (S9), and `pull-activate-http.js:155` accepts a completed
`key_recovery` flow that nothing can produce.

`docs/AGENT_MAIL_THIN_SLICE.md:132-134` lists recovery and delete as cut. The
defect is that the entry points were left open: an owner can start either, get
a real email, click through and hit "not found".

Smallest fix: refuse both purposes at `owner-management.js:174` until the
handlers exist. No test posts either form.

### W3 (high, L/E) retention never runs

`pull-retention.js:149` `retain()` has no source importer (measured; only
`test/pull-retention.test.js`). `worker.js:100-124` schedules the notify sweep
and the control-mail consumer and nothing else. The contract gives the wiring
to "W30 scheduled glue" (`docs/AGENT_MAIL_PULL_CONTRACT.md:2124`); W01 was
wired and W03 was not.

Consequences, each read:

- Nothing deletes `pull_nonces`, `pull_tickets`, `pull_budgets`,
  `pull_operations`, `pull_tokens`, `pull_browser_sessions`, terminal
  `pull_events`, `pull_dedupe`, `pull_threads`, `pull_intents`, `pull_audit`,
  `pull_audit_overflow`. Every signed request adds a nonce row and an
  operation row.
- `pull_owner_changes.old_owner_sealed` (the sealed previous owner address) is
  kept after the revert window: `owner-change.js` clears it only on expiry or
  cancel (`:179,256,419`); a completed change is cleared only by
  `pull-retention.js:35-36`.
- The rejection audit is capped at 10,000 rows (`ledger-pull.js:119-121`) and
  its only deleter is `pull-retention.js:78`. After the 10,000th refusal no
  audit row is written again, only the day counter. Each refusal still runs
  `SELECT COUNT(*) FROM pull_audit` twice.

Smallest fix: call `retain({ ledger })` as a third independent job in
`scheduled`, in the same try/catch shape as the other two. The unit test
covers the function; nothing covers its absence from the entry.

### W4 (medium, L) `sending` after a crash never resolves

`authorizeSend` moves the intent to `sending` (`ledger-pull.js:216`), then
`outbound.js:138` calls the provider and `:148` records the result. If the
isolate dies in between, the row stays `sending`. The contract says "Crash
after authorization -> uncertain" (`docs/AGENT_MAIL_PULL_CONTRACT.md:1155`).
The only code that moves `sending` to `uncertain` is the three retirement
blocks (`owner-change.js:252`, `pull-fence-http.js:200`,
`pull-key-rotate-http.js:192`). `pull_intents` has no lease column and no
sweep, unlike the control outbox. Read, not measured.

Smallest fix: in the scheduled job, move `sending` rows older than the 10 s
provider bound plus a margin to `uncertain`. No test covers it.

### W5 (medium, E) `rate_limits` grows on the live path

`Ledger.limit` (`ledger.js:25-30`) inserts one row per bucket per window. Live
callers use a per-source, per-minute bucket: `session-auth.js:66`,
`signup-http.js:147`, `pull-send-http.js:245` and the four other machine
classes. So each distinct caller source adds a row every minute it is active.
The only deleter is `Ledger.reconcile` (`ledger.js:131`), reached only from
`replay.js` (S2, dead). `pull-retention.js` does not list `rate_limits`.

Smallest fix: add `DELETE FROM rate_limits WHERE expires_at<=?` to the
retention job (W3). No test covers it.

### W6 (medium, E) D1 round trips per socket message

`pull-mailbox.js:40-46` `current()` is four round trips: `registration` (1),
`authorize` (2, `pull-ledger.js:100-112`) and an empty `atomic` batch (1) that
re-checks what the previous three just read. It runs at the top of every
message (`:193`), in every `drain` (`:144`) and inside every `send` (`:50`),
which adds a fifth read for event frames (`:51-54`). Counted by read, not
measured: a resume that pages 10 events is about 130 D1 calls, all inside
`blockConcurrencyWhile` (`:38,190`), so the socket handles nothing else
meanwhile.

Smallest fix: drop the empty `atomic` in `current()` (the following real batch
carries the same guard), and check authority once per message, not once per
frame sent.

### W7 (medium, L) operator-facing text contradicts the code

- `wrangler.toml.example:42`: "Apply schema.sql then migrations/0001 through
  0004". There are six; without `0005_pull.sql` every pull table is missing
  and every `/v1/pull/*` request answers 503. `docs/AGENT_MAIL_THIN_SLICE.md:148`
  already notes the template is stale.
- `workers/agent-mail/README.md:3-7,29-30,212`: "Not deployed or enabled",
  a "ledger/replay loop", `replay.js` as scheduled recovery, "Scheduled replay
  and the install recovery call are both required". None is true of the code
  on master. The README never mentions the socket, signup, the Durable Object
  or `production.js`, and its file table covers 17 of 62 files.
- `docs/AGENT_MAIL_UNIT7D2_CONTRACT.md:3` says "nothing here is built" and
  `:448` specifies `/v1/mail/lifecycle/revoke`; every lifecycle and enrollment
  route answers 410 (`worker.js:57-60`). Neither it nor
  `docs/AGENT_MAIL_TRANSPORT.md` carries a superseded mark.

Smallest fix: fix the template line; replace the README's first section and
table; add one "superseded by the pull contract" line at the top of the two
documents.

### W8 (medium, M) top-level catches discard the exception

`worker.js:84-86`, `:95-97`, `:108-110`, `:117-119` catch everything and log a
fixed string. A `TypeError` from a code bug, a missing binding and a D1 outage
are indistinguishable in the log, and the fetch path returns the same 503 for
all three. The project policy asks for the failure to be logged. The Worker's
own rule is the opposite on purpose ("Do not log exception text: a DB error
may include tokens or SQL inputs", `link-router.js:77-78`), and that rule is
sound for the message.

Smallest fix that keeps both: log `error?.name`, and the message only when it
is one of the closed refusal codes the modules already throw. `test/worker-pull-routing.test.js:164`
pins the current behaviour.

### W9 (medium, E/M) retired stack loaded for two predicates

See S1. `worker.js:5-6` imports `isLifecyclePath` and `isEnrollmentPath`; both
are one-line prefix tests. Importing them loads 15 modules (1,255 lines) the
Worker can no longer execute, on every cold start, and keeps them in every
review and every bundle. Measured with the import graph.

### W10 (medium, M) copied request boundary

See S7 and S8. Five classes carry their own copies of body parsing, signature
checking, source limiting, nonce and operation accounting and the refusal
block. A fix to any of them has to be made five times, and the copies have
already drifted in spacing and argument order. Each signed response also
re-imports the Ed25519 private key.

### W11 (low, L) NUL body fails SMTP as a storage fault

Measured: `strip()` returns `{ ok: true, has_text: true }` with the NUL kept
for a `text/plain` body containing a NUL byte, 7bit or base64
(`strip.js:390-395` trims whitespace only). `pull-ledger.js:147` refuses such
text with `invalid_request`; `ingress-pull.js:74-78` maps any code outside its
list to `storage_unavailable` and throws; `worker.js:95-97` rethrows. The mail
is failed as a custody fault and logged as storage, not dropped with a reason
and an audit row. Only a verified owner's correlated reply reaches this point.

Smallest fix: refuse NUL in `strip()` with a reason code. No test covers it.

### W12 (low, L/E) notify cursor

`pull-sweep.js:57` saves the cursor at the last install id even when the page
was short. The next tick selects `install_id > cursor` (`:48`), finds nothing,
and resets the cursor to empty. With 1 to 99 due notifications, every second
tick wakes nobody, so a retry waits up to a minute longer than its backoff.
`pull-retention.js:161` handles the short page correctly. Separately, both
tasks commit a cursor write every tick with nothing due (`:53-58`, `:104-110`):
2,880 D1 writes a day at rest. Read, not measured.

### W13 (low, L) unreachable "already confirmed" receipt

`link-router.js:63-64` requires `t.state='pending'` and `s.state='csrf'`.
After a successful confirm both are `consumed` (`verification-mail.js:301`),
so a refresh or double submit gets the router's "not found" page. The fixed
receipt at `signup-http.js:342-347` (and the matching branches in
`owner-management.js` and `owner-change.js`) is dead through the Worker entry.
`test/link-router.test.js:146,160` asserts the router's refusal, so the two
modules disagree by design and only one can be right.

### W14 to W19 (low)

- **W14** `owner-change.js:407-429` handles `/mail/change/cancel`; the manage
  page (`owner-management.js:386-398`) renders the purpose form and logout
  only. Reachable by a hand-built POST.
- **W15** `owner-registry.js:151` `releaseAddressStatements` has no source
  caller (grep: definition plus `test/owner-registry.test.js:240-257`). An
  abandoned signup keeps its `pull_installs`, `pull_generations` and
  `pull_addresses` rows; retention does not list those tables. Bounded by the
  signup admission budget.
- **W16** `ingress.js:58` charges `ingress-refused:<install>` and ignores the
  result; nothing reads that bucket. One wasted D1 write per refused mail.
- **W17** `control-mail-delivery.js:94-160`: about 16 sequential round trips
  per control mail, page limit 100. Counted by read.
- **W18** Headers that say "Unwired" on wired modules:
  `control-mail-delivery.js:1`, `owner-change.js:1`, `owner-management.js:1`,
  `pull-activate-http.js:1`, `pull-fence-http.js:1`, `pull-key-rotate-http.js:1`,
  `pull-send-http.js:1`, `pull-status-http.js:1`, `session-auth.js:2`,
  `pull-ledger.js:55`, `pull-sweep.js:1`, `production.js:1`; and
  `package.json:5` "Not deployed". `pull-retention.js:1` says the same and is
  the one that is true.
- **W19** Tests: see S10. `test/transport-support.js:69` builds a `legacy`
  worker that production never constructs, so those suites pass on code no
  request can reach. No test goes through `worker.js` for retention, for the
  two dead forms, or for a second send after `send_limit`.

## What is sound

- Inbound gates are in the right order and fail closed: size, recipient
  grammar, active owner, header agreement with the provider's own header
  surface, loop suppression, then auth, then strip, then custody
  (`ingress.js:66-103`). No path forwards, replies or fetches.
- Every state change that matters is a single D1 batch with authority guards
  at both ends (`ledger-pull.js:84-90`, `pull-ledger.js:116-123`); refusals are
  classified by a read after the full rollback, never by a partial write.
- The send path treats an unknown provider outcome as `uncertain`, never as
  failed or retried (`outbound.js:138-145`, `email-sending.js:86-93`).
- The two scheduled jobs are isolated from each other (`worker.js:105-122`)
  and tested both ways (`test/worker-pull-routing.test.js:366,378`).
- Fresh signup admission is operator configuration only, reaches one handler,
  and only the literal `open` admits (`worker.js:41,73`).
- The Durable Object binds each admission to its own id before consuming a
  ticket (`pull-socket-mailbox.js:26-33`).
- The link router gives no purpose oracle: one fixed refusal for every miss
  (`link-router.js:72-80`).

## Prior reviews

- `docs/ENTANGLEMENT_AUDIT.md`: no entry for `workers/agent-mail/`.
- `docs/_review/2026-09-10_efficiency.md`, `2026-09-10_security.md`: no
  finding in this scope; the Worker postdates them. Nothing carried over.
- Passes 01 to 03 of this review: none covers this directory. Pass 01 mentions
  the Python agent-mail hook once, outside this scope. W1's client half is in
  pass 05b's region and is cited, not reviewed.

## Questions for Dave

1. Is the push transport (send, reconcile, enrollment, lifecycle) gone for
   good? If yes, S1 to S4 and S10 can go (about 1,600 source and 2,600 test
   lines). If it may return, they stay and only the two imports in
   `worker.js:5-6` should be cut.
2. W2: refuse `key_recovery` and `delete` at the start route until built, or
   build the two handlers now? I recommend refusing; it is three lines.
3. W13: which side is right, the router's refusal of a consumed token or the
   handlers' fixed receipt? I recommend the receipt for the original browser
   session; the router's lookup would have to admit `consumed` rows.

## Coverage

Read fully (38 source files): `worker.js`, `production.js`, `signing.js`,
`enrollment-transcript.js`, `signed-v2-verifier.js`, `request-body.js`,
`sql-clock.js`, `response-signing.js`, `headers.js`, `held-envelope.js`,
`mailbox.js`, `content-id.js`, `session-auth.js`, `pull-socket-route.js`,
`pull-socket-config.js`, `pull-socket-mailbox.js`, `pull-frame.js`,
`manifest-http.js`, `ledger.js`, `ledger-pull.js`, `pull-ledger.js`,
`pull-mailbox.js`, `pull-retention.js`, `pull-sweep.js`, `ingress.js`,
`ingress-pull.js`, `owner-registry.js`, `pull-send-http.js`, `outbound.js`,
`email-sending.js`, `signup-http.js`, `verification-mail.js`,
`control-mail-delivery.js`, `owner-management.js`, `link-router.js`,
`owner-change.js`, `pull-activate-http.js`, `pull-status-http.js`; also
`auth.js`, `authres.js`, `reply-token.js` and `migrations/0005_pull.sql`.

Read partly: `pull-fence-http.js:64-213` and `pull-key-rotate-http.js:64-223`
(the rest of both is the copied boundary, W10); `strip.js:86-396`;
`wrangler.toml.example:36-61`; `README.md:1-45,205-247`.

Structure level only (exports, imports, headers, the lines cited): the retired
stack (`lifecycle.js`, `enrollment.js`, `enrollment-http.js`,
`enrollment-begin-proof.js`, `enrollment-challenge.js`, `enrollment-grant.js`,
`enrollment-preflight.js`, `enrollment-provider.js`,
`enrollment-reservations.js`, `delivery-proof.js`, `delivery-capability.js`,
`destination-cloudflare.js`, `destination-provisioning.js`, `cp-client.js`,
`code-mail.js`, `legacy-*.js`, `replay.js`), `edge-policy.js`,
`edge-vendor.js`, `canonical.js`, `display-name.js`; `schema.sql` and
`migrations/0001` to `0004`, `0006` (table and index names); all 80 files
under `test/` (names, imports, test titles of the two entry suites, and the
lines cited).

Design documents: `AGENT_MAIL_PULL_CONTRACT.md`, `AGENT_MAIL_THIN_SLICE.md`,
`AGENT_MAIL_UNIT7D2_CONTRACT.md`, `AGENT_MAIL_OPERATOR_SETUP.md` were read at
the sections cited, not end to end. The other sixteen `AGENT_MAIL_*.md` files
were not read.

Not reached: correctness of the retired stack (it cannot run); the header
grammar in `canonical.js`; any behaviour on a real Cloudflare runtime, D1 or
Email Routing. The round-trip counts in W6 and W17 are by read; a run against
the test D1 facade would confirm them.
