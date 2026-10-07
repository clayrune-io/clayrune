# MC-1074: browser connections and checks

Branch delivery only. Dave selected option A: two implementation commits on one branch, followed by the requested review fix; no merge, push or restart.

## Generic browser setup

The existing type projection offers browser sign-in for every known service and unknown address. X and LinkedIn retain their account identity and credential gates. Other services use `browser_connections.py`, its own human/passcode blueprint, and the wizard screen/summary registries. YouTube is a browser reference in the saved-service store; this setup does not create or authorize a YouTube read account.

`POST /api/desk/connect/browser-profile/commit` accepts `request_id`, `draft: {name, url, browser_profile}` and the human passcode. It validates public HTTPS addresses before the passcode check. It stores only metadata under `services` in `desk.json`, outside DATA_DIR. Both permissions are false and there is no runtime adapter. Requests cannot supply checks, execution rights or secrets. Duplicate request IDs replay only identical details; existing names cannot silently replace a profile. Known X/LinkedIn addresses must use their account setup.

Setup, Permissions, Review, Save and Result remain the shared flow. Unknown discovery is evidence, never authority. The generic browser option exists independently of discovery success. Saving never opens a pane or signs anyone in.

## Recovery checkpoint

The unattended fence refused writing the global `~/.claude/plans` plan (out of project scope). This document carries the scoped implementation and recovery state. Engram MCP tools are absent; no MCP configuration was changed. After compaction the host also withheld agent-attributed memory writes because runtime registration was unconfirmed. Recovery entries remain in the item's gitignored journal.

## Check connection

`POST /api/desk/connect/browser-profile/check` accepts `{url, browser_profile, check?, confirmed?}`. It is human-only. A read-only check does not use or fill a saved password, grant execution, or change permissions. Credential fill, Save and permission updates keep their existing human/passcode gates. `check:false` reads local status only; it opens no tab and makes no network call.

`browser_check.py` adapts browser probes into the existing `verification.py` record/state system. Checks come only from shipped service profiles; caller fields and discovery output cannot provide scripts, selectors or check URLs. `browser_check_schema.py` validates inert HTTPS URL/selector data on declared hosts. X, LinkedIn and YouTube have fixed checks. Each requires visible signed-in controls on the expected HTTPS host and path; hidden, transparent, off-screen and signed-out fixtures cannot pass. No page text or cookie value becomes an instruction or sign-in verdict.

An open named profile uses `HiddenTabReader` in its own background tab; the user's visible tab does not navigate. A closed profile uses the existing owned `ProfilePageReader`, closed gracefully afterward. Errors retain the reader's refusal semantics: no raw-HTTP fallback. The reader inherits the open pane's project when available; global connections do not assume a particular project exists on someone else's installation.

Verification is bound to the service, named profile, reviewed check revision, and profile/cookie-file metadata, never cookie contents. Known aliases and entered paths refer to the same service/profile; unknown addresses retain their exact address. Profile deletion/replacement, cookie-file changes, revised checks or restart invalidate the record. Idempotent Save replays preserve the saved facts but derive the verdict again from current metadata, so they cannot replay stale green status. The owned reader can flush its cookies while closing, so its successful evidence is stamped after that close; vault providers retain their existing before-probe stamp. This proves browser sign-in only, not account ownership, reading capability or publishing permission.

One `desk-v1-connect-browser-check.js` controller serves Setup, Result and saved account/service details through thin modules. A human starts the first probe. A pass turns green without opening a pane. Missing profile or signed-out evidence opens the named pane once and schedules non-overlapping checks every two seconds after each response, for up to ten minutes. Closing/changing the wizard or removing its node ends polling. Transport failures stop and display failure instead of opening a pane based on a guess.

A service without a reviewed check opens its entered address and asks one inline question: "Clayrune could not check automatically. Are you signed in now?" Human confirmation requires an existing named profile and records **Signed in, not verified**. It never produces verified/green status. The same outstanding question stays available if the pane has not finished creating its profile. **Not yet** dismisses its text but leaves the confirmation action for later; repeated checks after successful confirmation do not ask again. Result and saved details start from the returned metadata status, without a new probe on render.

## Files and scope

Generic storage and checks each have their own modules and blueprints. Wizard screens and summary branches use the existing registries. Existing account projections and connection-status rendering only delegate. There is no new executor, profile deletion, credential management path or automatic fill. The work remains on `clayrune/agent/55b8b8ae5f9e`, with no merge, push or server restart. Rebased onto master `91b20f55`: prerequisite `fc30287d` became `cb185b0a`; checker `3b3502c7` became `404ffddb`. Every master CHANGELOG entry was retained.

## MC-1079 drift fix (Dave review)

MC-1079 accepts dotted profile names through 64 characters and disallows `..`. The original checker/generic Save had a second, narrower 41-character regex. Saved account and service projections used that strict request validator and could raise, returning 500 for an entire list. Both now call `browser_routes._profile_dir`, the existing authoritative browser validation/normalization function; no second backend regex remains. Invalid new requests still fail validation before Save's passcode.

Metadata `status_for` catches failed validation, registry/metadata and file-status errors, logs with `_log`, and returns `{state: unknown, failure: unavailable, verifiable: false, needs_signin: false}`. `account_status` also protects the registry lookup at the account-list boundary. Account/service projections never probe or infer successful sign-in from a lookup failure. Existing account/service facts remain visible. The generic form permits 64 characters and lets the server validate the complete name; its default comes from `desk_browser_profiles.shared_default()` through type metadata, normally `main`.

Review backend run: **326 tests, rc=0**, including dotted/42/64-character names in Check, generic commit and both projections; invalid stored names and I/O/registry failures degrade and log. Five new modules passed pyright, **rc=0**, zero errors. `tests/test_desk_browser_check_agent_tab.py` runs real pipe and port Chromium with an agent-owned tab holding the profile: `_session_using_profile` returns its pane; the checker uses a separate `HiddenTabReader` target, closes only that target, and preserves both existing URLs, visible tabs and active target. The local HTTP fixture deliberately returns signed-out rather than verifying an HTTPS service; this test proves tab ownership and cleanup, not vendor authentication.

All **24 connect smoke scripts passed, rc=0**; return codes are retained in `_scratch/check_connection/matrix.json`. The generic smoke additionally verifies the shared default and saves a dotted 64-character profile through the 390-pixel wizard. The browser-check smoke confirms unavailable status renders without verification or a render-time probe. The **eight-script npm suite passed, rc=0**. No live vendor-account success is claimed. Smoke dependencies were linked from the main checkout and removed before the fix commit.

## Validation

Prerequisite: 153 touched backend checks passed; its two new modules passed pyright. Final slice: 301 backend checks passed, including the real Chromium hidden-reader tests; five new modules passed pyright with zero errors. All 24 `desk-v1-connect*.mjs` scripts passed. There is no separately named add-service script; wizard, flow, simplify and connections scripts exercise Add service. The npm suite (eight scripts, including full SPA boot) passed. The final output matrix is `_scratch/check_connection/matrix.json`, all return codes zero. Later checker/status refinements were covered by the targeted browser-check smoke and backend reruns.

The browser-check smoke covers 20 wizard cases (X, LinkedIn, YouTube: already signed in, pane/recheck, transport failure; unknown: manual confirmation), at 1440 and 390. Three additional real-DOM cases exercise the exact profile selectors against visible, hidden, transparent, off-screen and signed-out controls. Generic save covers four more cases through Permissions, Review, Save and Result. Repeated check clicks, selected-profile propagation and no polling after close are asserted. Screenshots use real branch wizard modules/CSS with mocked outcomes; full SPA regressions also use mocked APIs. They are not evidence that a live vendor account passed.

Initial regression failures were corrected: unknown-service picker assertions had expected only MCP, an empty delegated row hid the no-route account message, and a `status` function name confused the inline-handler audit. Early harness exit failures came from printing Unicode to cp1252; the final foreground harness uses UTF-8. Generated tracked screenshots were restored; task screenshots remain in `_scratch/`. The dependency junction to the main checkout is removed before committing.

Reproduce backend and type checks:

```powershell
python -m pytest -q tests/test_desk_browser_check.py tests/test_desk_browser_connections.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_types.py tests/test_desk_service_profiles.py tests/test_desk_connect_providers.py tests/test_desk_connect_verify_consent.py tests/test_desk_accounts.py tests/test_desk_services.py tests/test_browser_agent_read_hidden.py tests/test_browser_agent_tabs.py tests/test_desk_browser_check_agent_tab.py
pyright mc/desk_connect/browser_check.py mc/desk_connect/browser_check_schema.py mc/blueprints/desk_connect_browser_check_routes.py mc/desk_connect/browser_connections.py mc/blueprints/desk_connect_browser_profile_routes.py
```

Use the main checkout's smoke dependencies in a worktree; do not install them. Run each script matching `tools/smoke/desk-v1-connect*.mjs`, plus any `*add-service*.mjs`, then `npm --prefix tools/smoke test`. Long checks are foreground-owned and their child PIDs registered in Process Manager.

Screenshots: `_scratch/check_connection/{x.com,linkedin.com,youtube.com}-{already,signin,failed}-{1440,390}.png`, `example.com-manual-{1440,390}.png`, `unknown-prompt-{1440,390}.png`, and `generic-{youtube.com,example.com}-{1440,390}.png`. Desktop green, mobile green and mobile unknown prompt were visually reviewed.

## Ron UI walkthrough (after Dave integrates)

1. At 1440 and 390, open Desk > Connections > Add service. Enter X, LinkedIn, YouTube, then an unknown public HTTPS address in separate runs. All use the same six screens and connection words. Select browser sign-in; unknown discovery failure must still leave that choice.
2. For a known service, explicitly choose an already signed-in named profile and press **Check connection**. Expect green **Verified**, no pane navigation/open, and no grant of Read or Post. Check that Result and the saved tile/detail preserve the same status. Repeat the click once.
3. Use a signed-out profile for each known service. Expect the named pane, sign in there, and wait for green without another click. The user's existing visible pane tab must stay on its original page during probing. A failed/blocked network check must say failed and remain unverified; it must not silently try another reader.
4. For the unknown service, use a new profile. Expect its exact entered address and the single confirmation question. Answer **Yes, I'm signed in** after sign-in; expect **Signed in, not verified**, including after Save and on its tile. Read/Post remain off; no generic execution is available. **Not yet** never verifies.
5. Continue through Permissions and Review. Nothing saves before the human passcode; cancel the prompt and confirm nothing was saved. Then save with the passcode. Generic drafts contain only name, address and profile. X/LinkedIn account, credential-fill and permission gates behave as before.
6. While a known-service recheck is pending, close or change the wizard/profile. Confirm polling stops and old evidence does not apply to the replacement. Sign out or replace the named profile and recheck: old verification must disappear. A server restart also requires another check.

Live X/LinkedIn/YouTube account selectors, real sign-in transitions and the integrated HTTP check/pane lifecycle remain unverified in this branch run. Dave owns integration and Ron's live walkthrough; no production-state or vendor-account success is claimed here. No open design decision remains.
