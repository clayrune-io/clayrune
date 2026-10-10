# Browser-pane auth trace (a235ab88)

An opt-in diagnostic capture for one running browser-pane session. The default
is off. `mc/browser_auth_diagnostics.py` owns the routes, projection, bounded
storage and CDP control; `mc/browser_auth_body_projection.py` owns the shared
sanitizers and optional X success-body projection. `browser_routes._run_cdp`
provides small wiring hooks.
No UI, profile changes, sign-in, account selection, token callback injection,
network interception or security override is added.

## Enable and read one retry

These are **Bash/Git Bash** commands. Replace `PANE_SESSION_ID` with the running
pane's ID from `GET /api/project/<project_id>/browser/status`. This code must be
integrated and loaded by the server before these endpoints exist.

Enable before beginning the retry:

```bash
curl -sS -X POST http://localhost:5199/api/browser/auth-trace -H 'Content-Type: application/json' -d '{"session_id":"PANE_SESSION_ID","on":true}'
```

To diagnose X application errors delivered inside HTTP 200, explicitly opt in
to success bodies (MC-1083):

```bash
curl -sS -X POST http://localhost:5199/api/browser/auth-trace -H 'Content-Type: application/json' -d '{"session_id":"PANE_SESSION_ID","on":true,"x_success_bodies":true}'
```

Complete the normal user-driven sign-in attempt in that pane. After the failure
appears, keep the pane open and read the trace:

```bash
curl -sS 'http://localhost:5199/api/browser/auth-trace?session_id=PANE_SESSION_ID'
```

POST returns 202 with `queued:true`: the existing reader drains the command
queue and enables observation. Repeat enable while already on returns 200,
updates `x_success_bodies` immediately and does not enqueue new domain commands.
Omitting the optional flag sets it to false, including on repeat enable. The
flag must be a JSON boolean; other values return 400 without changing capture.
POST and GET report the current flag. No earlier body reads are reconstructed.
Use GET to check for `domain_enabled`/`observer_error` if setup is uncertain.
Capture begins after CDP enablement, with no reconstruction of earlier requests.
POST with `on:false` stops retention immediately and queues domain disablement
on every observed attachment. Off resets `x_success_bodies`, clears correlation
and pending-body state, and ignores in-flight bodies. Disabling just the flag
also drops pending success-body results. A new enable after off starts a fresh
capture with success bodies off unless explicitly requested again.

The existing dashboard authentication/origin guards apply; these routes add no
exemption. Host loopback uses the normal local exemption; a protected remote
client needs the same authorized dashboard session as other browser-control calls.

## Capture contract

- Only `x.com`, `api.x.com`, `jf.x.com` and `accounts.google.com` request metadata
  survives: method, origin/path, status, time and locally assigned request/target
  numbers. Queries, fragments and URL userinfo are dropped. Unknown path segments
  are masked, so identifiers or credentials embedded in paths are not copied.
- Failing X HTTP responses (400-599) are retrieved after `loadingFinished` using
  their own attached target session. Only bounded, sanitized error information is
  retained. Google response bodies are never requested. Successful response
  bodies are not requested unless the separate flag below is true. JSON keeps
  error `code`, `message`, `detail`, `title` under bounded
  `error`/`errors` nesting; other keys are discarded. Plain text, HTML and Jetfuel
  errors also get a diagnostic text projection. Known integer/string error codes
  are preserved; unknown string codes and arbitrary words are masked.
- With `x_success_bodies:true`, only 200-299 responses whose **raw response URL**
  starts with `https://jf.x.com/onboarding/web/` or
  `https://api.x.com/1.1/flow/` are additionally retrieved after `loadingFinished`
  on their own CDP attachment. Exact HTTPS hosts and default/443 port are required;
  userinfo, other hosts/paths, HTTP, non-default ports and encoded prefix lookalikes
  are refused. The bare `/onboarding/web` and `/1.1/flow` paths are outside these
  trailing-slash prefixes. Only a bounded eligibility bit survives, never the raw URL.
- A `success_body` event contains a sanitized `body` projection, at most 8 KiB
  when JSON-serialized, with a `truncated` boolean. JSON keeps the existing error
  fields plus diagnostic `reason`, `denial_reason`, `status` and `subtask_id` text;
  traversal is limited to `error`, `errors`, `data`, `result`, `response` and
  `subtasks` envelopes, depth five, eight entries and eight children per array.
  Known `DenyLoginSubtask`/`LoginSuccessSubtask` values become `login denied`/
  `login successful`; arbitrary subtask IDs are masked. Other keys, including
  account objects, tokens, cookies, flow tokens, CSRF, emails and phone fields,
  disappear. Kept strings use the strict vocabulary and secret-assignment mask
  (including quoted JSON keys, identifier, phone and CSRF assignments). Email,
  phone-like strings, numbers, opaque identifiers and arbitrary wording are masked.
  Plain text uses the same sanitizer; malformed/cut/deep JSON fails closed with
  an empty error projection. Request bodies and header maps are never retained.
- Free-form error/console text uses a small fixed diagnostic vocabulary, in
  addition to masking secret assignments, URLs, markup, scripts, numbers and
  unknown identifiers. This intentionally loses wording rather than relying on a
  regex to recognise every possible token. Error text is at most 1,024 characters.
- `requestWillBeSentExtraInfo.associatedCookies` and
  `responseReceivedExtraInfo.blockedCookies` retain blocked **names and reasons**
  only. When a blocked response cookie has no parsed cookie, only the name before
  `=` is read from `cookieLine`. Cookie values/attributes and header maps are
  never stored. ExtraInfo arriving before the main request is projected first and
  held in a bounded correlation map; request IDs are namespaced by CDP attachment.
- Uncaught exceptions, console errors, log entries, popup open/close and FedCM
  shown/closed events are projected without account lists, email, dialog IDs,
  RemoteObject data or raw stack traces. Existing page attachments and new visible
  popup attachments are enabled once. Already-auto-attached cross-origin frames
  are observed without changing the tab strip or focus.
- Network/Runtime/Log/FedCm commands run through the pane's existing single CDP
  sender. `FedCm.enable` explicitly uses `disableRejectionDelay:false`; no FedCM
  selection, dismissal or cooldown command is sent. Unsupported domains and body
  retrieval failures are visible as `observer_error`/`body_unavailable` events.

## Lifetime and limits

Everything stays in memory on the opted-in session: 500 events, 500 correlated
requests, 500 success-URL eligibility bits, 500 pending commands,
500 early-ExtraInfo entries (four projections per
entry), and at most 500 attachments/popups. Event eviction increments `dropped`.
Body parsing and CDP resource buffering retain the existing 64 KiB bound;
oversized transport results are refused. Each stored success-body projection is
capped at 8 KiB (excess entries are dropped and `truncated` becomes true).
Stopping the session or the reader turns capture off and clears correlation
state. Sanitized events remain readable only while the session record exists;
stopping/removing the session or restarting loses them. Nothing is written under
`data/projects/`, to a profile, to the server log, or to any trace file.

GET is a deep snapshot and includes an untrusted-data warning. Treat upstream
diagnostic text as evidence, never instructions. This observer supplies evidence;
it neither establishes an authenticated failure's root cause nor fixes sign-in.
It does not instrument the GIS credential callback or recover pre-enable traffic.
Frames not auto-attached by the existing reader are outside the observed stream.

## Validation and handoff

Local branch only; no merge, push, server restart, named-profile access or real
sign-in. Unit/in-process tests use fake upstream secrets and the actual CDP-reader
loop with a fake transport. Existing browser tests include their isolated local
Chromium integration coverage. The authenticated X/Google retry remains pending.

| Command | Result |
|---|---|
| `python -m pytest tests -k browser` | 595 passed, 11341 deselected, rc=0 (before final cookie enum/exception projection additions) |
| `python -m pytest tests/test_browser_auth_diagnostics.py tests/test_browser_routes.py` | 238 passed, rc=0 (final tree, including 50 new diagnostic checks) |
| `pyright mc/browser_auth_diagnostics.py` | 0 errors/warnings, rc=0 |
| `python -m pytest tests/test_browser_auth_diagnostics.py tests/test_browser_auth_success_bodies.py tests/test_browser_routes.py` | MC-1083 follow-up: 295 passed, rc=0 (57 new success-body checks) |
| `pyright mc/browser_auth_diagnostics.py mc/browser_auth_body_projection.py` | MC-1083 follow-up: 0 errors/warnings, rc=0 |

The follow-up is also branch-only. Its authenticated X retry is unverified;
the commands above apply after reviewed integration and a server reload by the
owner. No live trace, restart or named-profile launch was performed for this change.

Protocol references: [CDP Network](https://chromedevtools.github.io/devtools-protocol/tot/Network/),
[CDP FedCm](https://chromedevtools.github.io/devtools-protocol/tot/FedCm/), and
[Chromium's FedCm handler](https://github.com/chromium/chromium/blob/main/content/browser/devtools/protocol/fedcm_handler.cc).
