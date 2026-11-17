# Mail backend and control plane review, pass 05b (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `f0373733`.
Review only: no source file was changed. Every line cited was opened. A cost or
a wrong result is marked **measured** (a scratch script against the function,
with a fake IMAP object or an in-memory store) or **read, not measured**.
`server` was never imported, the live server was sent nothing, no model call
was made, no mail was sent, no external service was called, and no process
that binds a port or launches a provider CLI was started. Nothing was written
under `data/` or a real memory directory.

Scope: `mc/mail_launder.py`, `mc/agent_mail_*.py` (55 modules),
`mc/blueprints/agent_mail_*_routes.py` (6 modules), `tools/mail-mcp/`,
`tools/night-review/send_mail.py`, and `control_plane/` (app first, tests at
structure level). Passes 1 to 3 (`docs/_review/2026-11-16_01_runtime_core.md`,
`_02_agent_routes.md`, `_03_memory_learning.md`) are not repeated; where a
finding here is the other half of one of theirs, the id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology,
**F** feature does not work as documented.
Severity, same scale as passes 1 to 3, with the brief's addition: **blocker** =
wrong result or lost data on a normal path today, or raw mail text reaching a
tooled agent on the documented path; **high** = wrong result or lost data on a
reachable path, raw mail text reaching a tooled agent on a path that needs an
attacker's deliberate step, or a cost paid on every poll/request; **medium** =
narrower trigger, or a latent defect behind a setting that ships off;
**low** = hygiene.

STATUS: IN PROGRESS. Written incrementally, one commit per section. The ranked
summary, feature table, streamlining section and coverage statement are
written last; until they exist, the detail sections below are the report.

## Ranked summary

(written last)

## Feature table (lens 4)

(written last)

## Streamlining

(written last)

## Detail

### 1. Laundering path: `mc/mail_launder.py`, `tools/mail-mcp/`

#### MB1 — high — L/M — a hostile header charset name is printed to the tooled session, unlaundered, and blocks every later read

`tools/mail-mcp/read_digest.py:107-113`, `tools/mail-mcp/server.py:71-80`.

`fetch_messages` decodes `From`, `Date` and `Subject` with `server._dec`
(`read_digest.py:82-84`). `_dec` calls `part.decode(enc or "utf-8", "replace")`
(`server.py:77`), where `enc` is the charset token of an RFC 2047 encoded word.
The sender picks that token, and Python's `decode_header` accepts any text
without a `?` in it, spaces included. An unknown codec raises
`LookupError("unknown encoding: <token>")`. Nothing in `fetch_messages` catches
it, so it reaches `run()`, which puts `repr(e)` in the `detail` field
(`read_digest.py:110`) and `main` prints it (`read_digest.py:129`). The
launderer is never called.

**Measured** (scratch script, fake IMAP object, no network, no model call): a
message whose `From` header is `=?<60 characters of chosen text>?B?QQ==?=`
produced `{"ok": false, "error": "imap_fetch_failed", "detail":
"LookupError('unknown encoding: <the chosen text, lower-cased>')"}` on stdout,
exit code 1, launder function not reached.

Two consequences:

1. Text chosen by anyone who can deliver a message that matches the query
   reaches the calling session's context without passing the toolless call.
   It is one lower-cased line and it sits inside an error envelope, so it is a
   narrow channel, but it is the exact path this script exists to close, and
   module docstring rule 1 (`mc/mail_launder.py:24-32`) says a failure never
   hands back raw text.
2. The script exits 1 on every cycle for as long as that message is among the
   newest `--count` matches. `AGENT_RULES.md` tells the cycle to report the
   failure and retry later, so one message stops the owner's reply from ever
   being read. The sender can renew it at will.

Smallest fix: in `_dec`, catch `LookupError` and decode as UTF-8 with
`replace`; and in `run()`, report `type(e).__name__` only, never `repr(e)`
(the same rule `claude_mail_transform` already states at
`mc/mail_launder.py:143`: "Do not return exception messages").

Test: none would catch it. `tests/test_mail_launder.py:218-233` covers the
`imap_fetch_failed` envelope with a fetch function that raises a fixed
exception; no test feeds `fetch_messages` a crafted message, and
`test_no_failure_path_ever_leaks_raw_body_text` (`:161`) covers
`launder_mail_digest` only.

#### MB2 — medium — M — the digest is returned whole: extra keys, any types, no length cap

`mc/mail_launder.py:215-236`.

After parsing, the code checks that the nine required keys are present and
that `decision` is one of four words. It then returns `data` itself
(`:234`). Keys the instruction never asked for pass through, a value can be
any JSON type, and no string is bounded. The toolless call saw up to 10
messages of 20,000 characters each (`:45-46`); if the thread steers it, the
whole hostile text can come back in `notes`, in `reply_text`, or under a new
key such as `system` and is printed to the tooled session by
`read_digest.py:129`.

This is inside the module's stated limit ("the output is still untrusted",
`:33-38`), so it is medium and not high: no tool was live when the raw text
was read. It is still the cheapest hardening in the file. Smallest fix: build
the returned digest from the nine named keys only, coerce each to its
documented type, and cap `reply_text`, `quoted_original_ask`, `notes`,
`reply_from_header` and each subject (for example 2,000 characters).

Read, not measured (it needs a model call). Test: none;
`tests/test_mail_launder.py:146-158` test missing keys and a bad `decision`
only.

#### MB3 — medium — L — `decision: approved` can be produced by anyone who can deliver a message, and the fetch ignores the one authenticated signal it has

`tools/mail-mcp/read_digest.py:65-87`, `mc/mail_launder.py:67-89`.

The fetch takes the newest `--count` (default 5) messages that match the
query anywhere in the mailbox (`read_digest.py:72-74`). The instruction asks
the model which message is "most likely" the owner's reply. Nothing ties a
message to the owner: the warning says so itself (`mail_launder.py:54-56`).
A message from any sender with the subject tag and the body "Approved, go
ahead" yields `decision: approved`, `ambiguous_or_mixed: false`. The agent
rules say approval is the owner's reply; the digest cannot tell whose reply
it is.

The owner's documented reply path is a reply to themselves, which Gmail files
with the Sent label. `X-GM-LABELS` is available on the same read-only IMAP
connection and is set by the server, not the sender. Smallest fix: fetch
`X-GM-LABELS` with each message and pass only messages carrying `\Sent` to
the launderer as reply candidates (the rest can still be listed as "the
ask"). Without it, the same five-message window also lets a sender push the
real reply out of view with five newer matching messages.

A second effect of the same window: the query is the role tag, not one ask.
When two asks are open, the newest five messages can hold the reply to one
and the unanswered other, and the single `decision` field reports one
verdict for both. `quoted_original_ask` is the only hint.

Read, not measured. Test: none (the tests inject messages; none asserts
anything about who sent them).

#### MB4 — low — M — `read_email` and friends still serve raw mail to any session that has the server configured

`tools/mail-mcp/server.py:182-201`, `:160-179`, `:139-157`.

This is `docs/UNTRUSTED_INPUT_SURFACE.md` finding #2 and row 2 of the
2026-09-10 security review's prior-audit table, not a new finding; it is
listed so the feature table can refer to it. Current state, read from this
checkout: `steward/fence.py:5068-5071` refuses `mcp__mail__*` for a steward
cycle. Every other session that has the server in its MCP config can still
call `read_email` and receive a full body. `search_email` and `list_recent`
return `From` and `Subject` raw, which are sender-chosen text as well. See
"Prior audits" below.

### 2. Inbound reply path: store, ingress, extraction, wake

Files: `mc/agent_mail_store.py`, `agent_mail_ingress.py`, `agent_mail_envelope.py`,
`agent_mail_signing.py`, `agent_mail_extract.py`, `agent_mail_launder.py`,
`agent_mail_wake.py`, `agent_mail_continuation.py`, `agent_mail_target.py`,
`agent_mail_pipe.py`, `agent_mail_questions.py`, `agent_mail_spawner_fallback.py`,
`agent_mail_rollover.py`, `agent_mail_hold_notice.py`,
`mc/blueprints/agent_mail_holds_routes.py`.

#### MB5 — high (by owner ruling, recorded not disputed) — L — an agent-mail reply reaches the tooled chat as an instruction with no toolless step; the local side cannot check who sent it

`mc/agent_mail_extract.py:1-6`, `:46-59`; `mc/agent_mail_launder.py:79-80`;
`mc/agent_mail_wake.py:18-19`, `:219-223`.

The brief asks for every path where unlaundered mail text reaches a tooled
agent. This is one, and it is deliberate: commit `f4760778` (2026-11-15)
removed the model extraction and the screen on the owner's ruling that a
reply which passed the Worker's per-email token and verified-owner check is
the owner. What runs now:

* `extract_reply` cuts at the first quote header, drops `>` lines and strips
  invisible characters. No model, no screening (`agent_mail_extract.py:1`).
* `MailWake.process_next` prefixes one line, "Email reply from the project
  owner, verified sender + this email's one-time token", and posts the text
  to the chat's follow-up route with the authority of a typed message
  (`agent_mail_wake.py:219-223`).

What the local side verifies before that: the Worker's Ed25519 signature
over method, path, digest, nonce and timestamp (`agent_mail_signing.py:63-88`),
a durable nonce claim (`agent_mail_ingress.py:139-145`), a thread id bound
before the outbound send (`:112-116`), and that the envelope's `auth` block
has the right shape with `dmarc` equal to `pass` (`agent_mail_envelope.py:78-89`).
What it cannot verify: the envelope carries no sender, so "the sender is the
verified owner" is taken from the Worker entirely. The property this whole
path now rests on is therefore in pass 05a's region (the Worker's owner and
token checks), plus the custody of the Worker's signing key.

Not a request to reverse the ruling. Three things follow from it that are
defects in this region today:

1. `docs/AGENT_MAIL_SPEC.md:312-333` still specifies a toolless extractor
   and says "Do not fall back to raw body/subject ... or a first-line
   heuristic". The code is now a line heuristic. `docs/AGENT_MAIL_TRANSPORT.md:345`
   has the new behaviour; the spec was not updated.
2. `mc/mail_launder.py:12` says that module "is the ONLY thing in the
   mail-reply path allowed to see a raw message body". That is true for the
   IMAP digest path only.
3. The names no longer say what the code does: class `MailLaunder`, state
   `laundering`, column `laundered`, and the UI reason
   `laundered_envelope_invalid` describe a quote trim. A reader checking
   "is mail laundered before it reaches an agent" will answer yes from the
   names and be wrong. See streamlining item ST1.

Test: `tests/test_agent_mail_instruction_delivery.py` pins the new
behaviour (instruction text is delivered), so no test would flag it.

#### MB6 — medium — L — only `session_busy` is retried; every other transient refusal becomes a permanent hold that also freezes the chat's later replies

`mc/agent_mail_wake.py:177-188`, `:233-238`; `mc/agent_mail_target.py:99-122`;
`mc/agent_mail_spawner_fallback.py:56-97`; `mc/agent_mail_store.py:785-800`.

`MailWake.hold` sends `session_busy` back to `ready` with a one-second
delay and sends every other reason to `_fail_locked(..., retryable=False)`,
which is `blocked`. `validate` raises several reasons that clear on their
own: `provider_quota_held` (`target.py:120-122`), `provider_recovery_held`
(`:99-101`), `rollover_required` (`:114-119`). A `blocked` row is only
moved by a human retyping the dashboard passcode
(`agent_mail_holds_routes.py:85-117`), and while it sits there
`claim_for_submission` delivers nothing later in the same chat
(`agent_mail_store.py:796-798`).

Three concrete cases:

* **Reply to a long chat.** `rollover_required` is raised when the chat is
  over the auto-fresh threshold. Mail delivery may not roll a chat over
  (`agent_mail_continuation.py:188-190`), so the reply is held. Releasing
  the hold re-runs the same check and blocks again. The only cure is for a
  human to send a message in that chat first, then release. The UI text
  (`static/js/agent-mail-holds.js:38`) describes the state and not the cure.
  The chats that send the most mail are the long-lived ones.
* **Reply while the provider allowance is exhausted.** Held; not retried
  when the allowance returns.
* **Reply to a finished dispatched agent while its spawner is mid-turn.**
  `try_forward` calls `resolve` on the spawner with the default `busy=True`
  (`spawner_fallback.py:71`), gets `session_busy`, logs "refused" and
  returns False; the caller then blocks the event as `worktree_unavailable`
  (`agent_mail_wake.py:236-238`). On the direct path the same busy state is
  a one-second retry. `tests/test_agent_mail_spawner_fallback.py:48-62`
  asserts the permanent hold for a busy spawner, so the test pins the
  defect.

Smallest fix: treat `provider_quota_held`, `provider_recovery_held` and a
busy spawner as `retryable` with the store's existing backoff
(`_fail_locked(..., True)`), and either let a mail wake take the same
auto-fresh path a typed message takes or say in the hold text that the chat
must be opened once. Read, not measured.

#### MB7 — low — L — `pipe.write` accepts the same delivery twice, from two threads

`mc/agent_mail_pipe.py:81-90`.

The worker thread calls `finish(scope, ok)` which calls `scope.accept()`
(`:81-82`), and the calling thread calls `scope.accept()` again when the
write finished inside ten seconds (`:89-90`). Both run `MailWake.accepted`,
`settle`, `gate.accept()` and `consume_question`. It is harmless today
because each step happens to be idempotent (`settle` swallows the second
`IllegalTransition` at `agent_mail_wake.py:87-92`; `consume` finds no
matching question the second time). It is one accept too many and it relies
on that `except ValueError` to stay quiet. Fix: let the worker own the
receipt and have the caller only wait.


### 3. Pull transport and its owner: factory, supervisor, recovery, client, receiver

Files: `mc/agent_mail_factory.py`, `agent_mail_supervisor.py`, `agent_mail_recovery.py`,
`agent_mail_pull_client.py`, `agent_mail_pull_receiver.py`, `agent_mail_pull_schema.py`,
`agent_mail_reviewed_service.py`, `agent_mail_manifest_fetch.py`,
`agent_mail_service_client.py`, `agent_mail_deletion.py`.

#### MB8 — blocker — L/F — one failed HTTPS request stops inbound mail until the server is restarted

`mc/agent_mail_pull_client.py:272`, `:599`, `:617`, `:701-705`;
`mc/agent_mail_service_client.py:267-269`; `mc/agent_mail_reviewed_service.py:83-88`;
`mc/agent_mail_supervisor.py:259-281`.

The pull worker's outer loop sorts failures into "retry with backoff" and
"halt" (`pull_client.py:696-726`). The catch-all at `:701-705` turns **every**
`IngressRefused` or `EnrollmentRefused` into `halt=True`. Two ordinary network
failures arrive as exactly that type:

1. **The ticket request.** `_ticket` calls the transport outside its `try`
   (`:272`). In production the transport is `HttpsTransport`
   (`agent_mail_factory.py:380-381`), which converts any connection error,
   DNS failure or timeout into `ServiceRefused('connection_lost')`
   (`service_client.py:267-269`); `ServiceRefused` subclasses
   `EnrollmentRefused`. `connection_lost` is in none of the three sets tested
   at `:704`, so it is renamed `local_refused` and halts. An unsigned error
   page from the edge in front of the Worker (a 502 with an HTML body) halts
   the same way as `response_unverified` (`:274-283`, `:323-324`).
2. **The manifest refresh.** The loop calls `_authority()` once a second
   (`:617`), which calls `reviewed_service()`. Its cache lasts ten minutes
   (`reviewed_service.py:44`, `:89`); the first call after that does one GET
   with a five-second timeout, and any failure is raised as
   `EnrollmentRefused('service_unavailable')` (`:83-88`). That is 144 fetches
   a day on a healthy connection, and one failure halts the worker.

The supervisor does not start a halted worker again: `_client_started` stays
true, so the start block at `supervisor.py:259-281` is skipped on every later
pass. Its docstring says a halted worker is "not blindly restarted" and lists
what permits another attempt: a changed lifecycle or pin scope, a vault
lock-then-unlock, or stop/start. For a revoked key or a superseded generation
that is the right rule. For a dropped connection it means the first Wi-Fi
drop, sleep/resume or DNS hiccup that outlasts the first retry (at most one
second of jitter after the socket error, `:677`) ends inbound mail for the
life of the process. Nothing is lost: replies stay queued at the Worker. But
they stop arriving in the chat, and the only signals are a log line and
`client.state: held, reason: local_refused` in the status payload.

**Measured** (scratch tests on the repo's own test harness, production
`HttpsTransport` with a fake opener, no network):

| condition | worker after 1.5 s | HTTP calls |
|---|---|---|
| ticket POST raises `URLError` | not running, reason `local_refused` | 1 |
| ticket POST times out | not running, reason `local_refused` | 1 |
| ticket POST gets an unsigned 502 HTML page | not running, reason `response_unverified` | 1 |
| manifest refresh fails once while connected, service back 1 s later | not running, reason `service_unavailable`, no reconnect | 1 ticket |

With a real `Supervisor` over the halted client, five further
`_drive_client()` passes made no further HTTP call; snapshot
`{'state': 'held', 'running': False, 'reason': 'local_refused'}`.

Smallest fix: in `_ticket`, catch `ServiceRefused('connection_lost')` and
raise `_Failure('connection_lost')` (retryable, the existing backoff applies);
treat an unverifiable reply with a 5xx status as retryable
`service_unavailable`, since nothing in it is trusted either way; and in
`_run`, make `service_unavailable` from `reviewed_service()` retryable. Keep
`halt` for the reasons in `HALT`.

Tests: none would catch it, and two pin it. Every pull-client test passes a
fake callable as the transport, which never raises `ServiceRefused`;
`tests/test_agent_mail_pull_client.py:654-682` asserts the halt for an
unsigned ticket reply; `:581-594` asserts the halt for authority loss but has
no case for a failed refresh. `tests/test_agent_mail_slice_e2e.py` uses the
real transport on the success path only.

#### MB9 — high — L/E — a reply reaches its chat only on the recovery tick, and any step that is merely "pending" pushes that tick out to 15 minutes

`mc/agent_mail_supervisor.py:128`, `:138`, `:147-150`, `:195-198`;
`mc/agent_mail_recovery.py:127-128`, `:232-246`, `:323-341`.

The pull client commits a reply to the store and acks it. The step that
trims the quote and posts it to the chat (`launder`, `wake`) runs only inside
`Recovery._inbound`, which runs only from `Supervisor.tick`, which runs once
per `delay()`. Nothing wakes the loop when custody commits. So:

* A healthy install delivers a reply 0 to 60 s after it arrived (default
  interval, `supervisor.py:39`). The one-second "deferred" retry for a busy
  chat (`agent_mail_wake.py:185-186`) is in practice a 60-second retry.
* `tick` counts a pass as failed when `report['ok']` is not true
  (`supervisor.py:128`), and `ok` requires every step to be `ok` or `skipped`
  (`recovery.py:127-128`). `pending` is a normal state for five of the nine
  steps: a signup link not yet clicked (`flow_pending`), an ack in flight
  (`ack_pending`), a socket reconnecting (`pull_disconnected`), a first scan
  (`scan_pending`), a fence awaiting the service (`revocation_pending`).
  Each such pass doubles the delay.
* One held or blocked reply makes `_inbound` raise `step_failed`
  (`recovery.py:335-336`), which ends the wake loop for that pass and counts
  as a failure too, so a reply that needs a human (MB6) slows every other
  chat's mail.

**Measured** (scratch test, real `Supervisor`, fake recovery whose only
non-ok step is `flows: pending`): `delay()` after successive ticks was
`[120, 240, 480, 900, 900]` seconds. While any step stays pending the
install sits at the 900-second cadence and every reply is delivered up to
15 minutes late.

Smallest fix: count only `failed` and `held` steps toward the backoff, not
`pending`; and give the supervisor loop a `threading.Event` that the pull
client sets after a custody commit, so `self._stop.wait(self.delay())`
returns early when there is mail to deliver.

Tests: `tests/test_agent_mail_supervisor_pull.py` covers backoff on failure
and reset on success; none asserts what a `pending` step does to the delay,
and none measures arrival-to-delivery time.

#### MB10 — medium — E — the whole store is integrity-checked twice a second while the socket is open, and again on almost every call

`mc/agent_mail_pull_schema.py:189-208`; `mc/agent_mail_pull_client.py:210-212`,
`:237-239`, `:359-360`, `:423-425`, `:616-641`; `mc/agent_mail_pull_receiver.py:264-266`;
`mc/agent_mail_factory.py:182-190`, `:255-257`, `:339-345`;
`mc/agent_mail_store.py:260-266`, `:311-320`.

`_base` runs `PRAGMA integrity_check` (a scan of every page) and `_validate`
runs a `sqlite_master` comparison plus eight `foreign_key_check` passes. The
pull client's idle loop wakes once a second (`ws.settimeout(1)`, `:611`) and
each pass calls `_authority()` (one `_account`, so one `_base` + `_validate`)
and `_replay()` (one `_current`, so a second one). Every sent frame adds two
more (`_send`, `:402-404`), and every received event runs a fifth inside the
write transaction, with the store lock held (`pull_receiver.py:264-266`).
Each `_read` and `_tx` also opens a fresh connection and sets three pragmas
(`store.py:260-266`).

The factory repeats it on its own paths: `open_store` re-runs `_check_seal`,
which calls `validate_pull`, on every call even for the cached store
(`factory.py:255-257`), and `require_pull_store` then validates again
(`:343-344`). `service_client()`, `pull_client()`, `outbound()`, `health()`
and both store reads in `Supervisor.status` (`supervisor.py:426-427`) all go
through it.

**Measured** (scratch store in a temp directory, `validate()` timed over 20
calls): 1.5 ms on an empty store, 2.1 ms with 100 events of 4 KiB, 5.9 ms at
5 MiB, 33.8 ms at 44 MiB (600 events at the 64 KiB body cap). Opening and
closing a connection alone is 1.7 ms. So an idle connected install spends
roughly 7 to 15 ms of every second re-proving that the schema it checked a
second ago is unchanged, and about 70 ms a second at the body cap. It is not
large; it is permanent, it grows with the inbox, and the copy inside
`receive` holds the write lock.

Smallest fix: keep the schema comparison (it is a `sqlite_master` read) and
drop `integrity_check` and the foreign-key passes from the per-call path;
run them at `open_store`, at `bootstrap`, and once per supervisor tick. Hold
one read connection per worker thread instead of opening one per call.

Tests: none measure it; `tests/test_agent_mail_pull_schema.py` asserts that
corruption is detected, which the cheaper schedule still satisfies at open
and per tick.

#### MB11 — low — M — an unexpected exception in the worker is reported as `connection_lost` and retried forever

`mc/agent_mail_pull_client.py:712-713`.

`except Exception: failure = _Failure('connection_lost')` keeps the worker
alive, which is right for socket errors, but it also absorbs an
`AttributeError` or `KeyError` from a code defect and logs only
`pull_client connection_lost` (`:717`). The exception type is the one thing
the project policy asks to keep. Fix: `_log` the `type(exc).__name__` there,
as `supervisor.py:109-110` already does.

#### MB12 — low — M — two frame verifiers that must agree

`mc/agent_mail_pull_client.py:328-357`; `mc/agent_mail_pull_receiver.py:146-177`.

Every `event` frame is parsed, base64-decoded and signature-checked by
`PullClient._verified`, then handed as raw bytes to `PullReceiver.receive`,
which does all three again with its own copy of the rules. The second pass is
stated as deliberate (`pull_client.py:507-508`). The copies already differ:
a stale frame is `frame_unverified` in one (`:342-343`) and
`stale_signature` in the other (`pull_receiver.py:180`); one decodes with
`unbase64url`, the other with `base64` plus a round-trip check. A rule
changed in one and not the other fails closed today, but which reason the
operator sees depends on which copy ran first. See streamlining ST3.

#### Sound in this section

* Custody before ack: the ack payload is built only after the receiver's
  transaction commits (`pull_receiver.py:310-317`), and only a signed
  `ack_committed` deletes the outbox row (`pull_client.py:463-465`).
* A reply to a deleted chat is a hold, not a crash. **Measured**: after
  `agent_mail_deletion._revoke`, the receiver refused the event with
  `unknown_thread`, the client sent a `hold` frame and stayed connected.
  (This was a suspected halt; the reproduction cleared it.)
* A vanished or replaced store file is never recreated
  (`factory.py:97-113`, `:235-265`); the marker states are exhaustive.
* `HttpsTransport` refuses redirects, duplicate headers, compressed bodies
  and oversize replies before anything is parsed (`service_client.py:243-279`).
* Frame size is bounded before the payload is read
  (`pull_client.py:69-74`), and fragments are capped as they accumulate
  (`:575`).
* Exception text never reaches a log line or a status field anywhere in
  these files; every reason is a closed code.
