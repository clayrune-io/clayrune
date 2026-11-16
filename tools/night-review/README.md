# Night-shift maintenance run

A self-gating nightly Clayrune schedule that does conservative repo maintenance
**only when the 5-hour usage window has headroom**, then emails a summary.

## How it works

1. The Clayrune scheduler fires the schedule nightly (default **03:00 local**).
2. The dispatched agent runs `PROMPT.md`. Its **first** step (Tier 0) hits
   `GET /api/system/usage` and reads `usage_limits.five_hour.utilization`.
   - `> 10%` used (or usage data unavailable) → logs one line to
     `docs/night-review-skips.log` and stops. Cheap no-op.
   - `<= 10%` used → proceeds with the maintenance tiers.
3. Tier 1 grooms the backlog, Tier 2 makes/commits only riskless edits (typos,
   dead doc links, formatting), Tier 3 writes human-review-only suggestions.
4. It writes `docs/night-review-<date>.md` and emails the reviewer a summary
   via `send_mail.py` (Gmail SMTP).

The schedule is self-gating because the Clayrune scheduler only dispatches
agents — it can't natively skip a run, so the gate lives in the prompt.

## One-time setup — Gmail credentials (required for the email step)

The email step needs a Gmail **App Password** (not your normal password; the
account must have 2-Step Verification on). Until this is set, runs still work —
they just note "email not sent" at the top of the report.

1. Create an App Password: <https://myaccount.google.com/apppasswords>
   (pick "Mail" / "Other"). Google shows a 16-char code like `abcd efgh ijkl mnop`.
2. Create `~/.clayrune/night-mail.json` (on this box:
   `~/.clayrune/night-mail.json`) — **never commit it**:

   ```json
   {
     "user": "youraddress@gmail.com",
     "app_password": "abcd efgh ijkl mnop",
     "to": "you@example.com"
   }
   ```

   (Spaces in the app password are fine — the mailer strips them.)

   Or, instead of the file, set env vars: `NIGHT_MAIL_USER`,
   `NIGHT_MAIL_APP_PASSWORD`, `NIGHT_MAIL_TO`.

3. Test it:

   ```
   python tools/night-review/send_mail.py --subject "night-review test" --body "hello"
   ```

   Exit 0 = sent. Exit 2 = credentials missing. Exit 1 = send failed (check the
   error; usually a wrong app password or 2FA not enabled).

## Files

- `agent_mail_route.py` — agent-mail-first call-in used by `send_mail.py`.
- `send_mail.py` — Gmail SMTP sender (stdlib only). `--subject` + one of
  `--body` / `--body-file` / stdin; `--to` overrides the recipient. Sends
  `multipart/alternative`: the original text first, styled Markdown HTML
  second. `--plain-only` sends only text. A missing or failing renderer logs
  to stderr and falls back to text without blocking delivery.
- `md_html.py` — stdlib-only renderer shared by every caller of this mailer,
  including workflow **Send email** nodes. Supports headings (`#` to `###`),
  paragraphs (single line breaks are kept), bold/italic, inline code, HTTP(S)
  links, flat bullet/numbered lists, pipe tables, `---` rules and backtick
  code fences. A paragraph containing only one bold span of at most 80
  displayed characters becomes a small heading. All input is HTML-escaped;
  unsupported syntax stays visible text. Styling is inline: 14px sans-serif,
  860px maximum content width, 13px bordered tables with zebra rows and
  unwrapped headers. Wide tables scroll horizontally in clients that support
  overflow. No external images, stylesheets or dependencies.
- `PROMPT.md` — canonical copy of the agent prompt the schedule runs. Edit
  here **and** update the live schedule (`PUT /api/schedules/<id>` or the
  Scheduler UI) to keep them in sync.

## Agent-mail first

When `send_mail.py` runs inside an agent session it tries **agent-mail** before
Gmail (`agent_mail_route.py`, MC-1166). Every agent child gets
`CLAYRUNE_PROJECT_ID`, `CLAYRUNE_SESSION_ID` and `CLAYRUNE_PORT` in its
environment (`mc/agent_spawn.py`); with both ids set (or `--project` /
`--session`) the mail is POSTed to the local
`/api/project/<pid>/agent/mail/note` route. The recipient is always the
install's verified owner.

- **Replies land back in the sending chat, not in the Gmail inbox.**
  `tools/mail-mcp/read_digest.py` reads Gmail, so it will **not** see a reply
  to an agent-mail send. Replies to an SMTP-fallback send still arrive there.
- Accepted: one stdout line, `[send_mail] sent via agent-mail, intent <id>`,
  exit 0. With `--verify`, acceptance counts as verified (no IMAP check; the
  line says so).
- Any failure (route 404/refusal/503, connection refused, no answer in 20s,
  subject over 200 bytes, body over 14000 bytes) prints
  `[send_mail] agent-mail unavailable (<reason>); sending via Gmail` to stderr
  and continues down the unchanged SMTP path. A body is never truncated.
- No session env (server-side alerts, hand runs): SMTP, silently.
- `--smtp-only` skips agent-mail. `--to` and `--message-id` also stay on SMTP:
  agent-mail cannot honour a chosen recipient or Message-ID.
- Retries are idempotent: `request_id` is a hash of session + subject + body,
  so re-sending identical text from one session returns the same intent.
- The HTML part exists only on the SMTP path; agent-mail carries plain text.

### Which senders are agent-mail and which stay Gmail (MC-1176)

- **Agent sessions, including steward and scheduled cycles:** the mailer runs
  from the session's own Bash, so the env above is inherited and the mail goes
  agent-mail. A steward cycle is a resumable session (`continue_session`), and
  the owner's reply wakes that same session with its `trigger_type` intact, so
  the steward fence stays armed on the resumed turn.
- **Server-side steward-role asks** (`mc/steward_role_comms.default_mailer`):
  the server has no session env, so the role's project and session are passed
  as `--project` / `--session`. A mail agent-mail accepted is recorded as route
  `agent_mail`; it is never read from Gmail. Only a Gmail-sent ask is read
  through `read_digest.py`.
- **Workflow Send email and approval-gate notices** (`mc/workflows.py`): pass
  `--project` / `--session` from the latest completed agent step in the same
  run that has both IDs. Replies wake that agent's chat; an approval gate
  still requires a human decision through its existing decision endpoint.
  Without a completed agent session, the invocation stays unchanged (Gmail
  fallback). Send email output reports `sent via agent-mail` or `sent via Gmail`.
- **Gmail only, by design (no session to wake):** the remote-access down and
  enrollment-lost alerts (`mc_remote/tunnel_supervisor.py`,
  `mc/blueprints/remote_routes.py`), and hand runs without session context.
- **`mc:question` mail** (`mc/question_channel.py`) keeps its own transport:
  the dedicated agent-mail question path when selected, otherwise Gmail with a
  fixed `--message-id`/`--to` so its IMAP reply relay can match the answer.
  Routing that Gmail leg through the generic agent-mail note would orphan the
  relay, so it is deliberately not given a session.

## Managing the schedule

- List: `curl -s http://localhost:5199/api/schedules`
- Change the time / disable / re-enable: the Scheduler UI, or
  `PUT /api/schedules/<id>`.
- The dated reports (`docs/night-review-*.md`) and the skips log
  (`docs/night-review-skips.log`) are gitignored runtime output.

## Rendering checks (no email sent)

```bash
python -m pytest tests/test_send_mail_*.py -q -o addopts=
```

Tests construct MIME messages and use a fake transport; the existing pytest
SMTP guard remains in place. Actual Gmail rendering/delivery is not exercised.
The mailer runs as a fresh subprocess per send, so its source changes need no
server restart once merged into the running checkout.
