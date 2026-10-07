# Browser-pane auth trace (a235ab88)

An opt-in diagnostic capture for one running browser-pane session. The default
is off. `mc/browser_auth_diagnostics.py` owns the routes, projection, bounded
storage and CDP control; `browser_routes._run_cdp` provides small wiring hooks.
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

Complete the normal user-driven sign-in attempt in that pane. After the failure
appears, keep the pane open and read the trace:

```bash
curl -sS 'http://localhost:5199/api/browser/auth-trace?session_id=PANE_SESSION_ID'
```

POST returns 202 with `queued:true`: the existing reader drains the command
queue and enables observation. Repeat enable while already on is a no-op (200).
Use GET to check for `domain_enabled`/`observer_error` if setup is uncertain.
Capture begins after CDP enablement, with no reconstruction of earlier requests.
POST with `on:false` stops retention immediately and queues domain disablement
on every observed attachment. A new enable after off starts a fresh capture.

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
  retained. Google response bodies and all successful response bodies are never
  requested. JSON keeps error `code`, `message`, `detail`, `title` under bounded
  `error`/`errors` nesting; other keys are discarded. Plain text, HTML and Jetfuel
  errors also get a diagnostic text projection. Known integer/string error codes
  are preserved; unknown string codes and arbitrary words are masked.
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
requests, 500 pending commands, 500 early-ExtraInfo entries (four projections per
entry), and at most 500 attachments/popups. Event eviction increments `dropped`.
Each body read is capped at 64 KiB; CDP resource buffering is bounded too.
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

Protocol references: [CDP Network](https://chromedevtools.github.io/devtools-protocol/tot/Network/),
[CDP FedCm](https://chromedevtools.github.io/devtools-protocol/tot/FedCm/), and
[Chromium's FedCm handler](https://github.com/chromium/chromium/blob/main/content/browser/devtools/protocol/fedcm_handler.cc).
