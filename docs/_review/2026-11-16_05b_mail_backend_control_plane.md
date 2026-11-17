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

