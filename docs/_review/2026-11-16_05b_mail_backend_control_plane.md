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

