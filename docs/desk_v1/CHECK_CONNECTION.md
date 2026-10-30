# MC-1074: browser connections and checks

## MC-1105: passwordless account metadata and recovery

Branch-only extension, based on Dave's D1-D3. `mc/secrets_store.py` and vault value/write rules are unchanged. The two browser Save paths accept optional `signin_method` (`google`, `phone_code`, `browser_password`, `other`), defaulting to `other` for older callers. Generic services store it in `browser_connection`; X/LinkedIn in `browser_setup`; pane-only accounts beside their `browser_profile`. Both Details branches import one shared control. Password login branches send `browser_password`. Existing saved setups retain their no-replacement rule; a changed method cannot silently replace an existing setup, and a changed idempotency replay is refused.

`mc/desk_browser_accounts.py` owns the allowlisted read-only projection and expiry requests. `GET /api/secrets/browser-accounts`, in its own blueprint, returns `browser_accounts` and `signin_requests` from Desk, including while Vault is locked. This is distinct from the secret-entry list: names/profile/method only, no credential references, values, cookie data or new vault entry type. It has no write route. Old metadata remains `other` without backfilling or guessing. Removed/replaced references cannot inherit a request raised for a different record.

Only `browser_check.run(check=True)` observes verdicts. `signed_out` failure or a missing local named profile after an explicit check raises a request; unchecked/status projections and inconclusive or transport failures do not. Requests live under `browser_signin_requests` in `desk.json` (outside DATA_DIR), keyed by canonical service and failed profile, bound to projected source IDs. Repeated expiry is a no-op. A verified check or the existing successful human confirmation clears only the checked profile, retaining failures for other profiles of the same service. Reads and restarts never clear requests.

The existing attention surface is `_buildAttentionList`: Feed **Needs you** and desktop/mobile dashboard **Waiting on you**. A small module polls only local metadata and adds one card per affected service. Its card opens Vault. Read-only Browser accounts use the existing shared BrowserCheck controller, named pane, and existing short recheck loop after a human click; no credential fill or Google/SMS automation is added. Unreviewed services cannot automatically detect vendor expiry; local missing-profile evidence and human confirmation retain the original check contract.

Recovery/report and validation commands: `docs/desk_v1/PASSWORDLESS_SIGNIN.md`. No merge, push or server restart is part of this delivery. Live vendor-account sign-in remains a human integration check.

Branch delivery only. Dave selected option A: two implementation commits on one branch, followed by the requested review fix; no merge, push or restart.

## Generic browser setup

The existing type projection offers browser sign-in for every known service and unknown address. X and LinkedIn retain their account identity and credential gates. Other services use `browser_connections.py`, its own human/passcode blueprint, and the wizard screen/summary registries. YouTube is a browser reference in the saved-service store; this setup does not create or authorize a YouTube read account.

`POST /api/desk/connect/browser-profile/commit` accepts `request_id`, `draft: {name, url, browser_profile}` and the human passcode. It validates public HTTPS addresses before the passcode check. It stores only metadata under `services` in `desk.json`, outside DATA_DIR. Both permissions are false and there is no runtime adapter. Requests cannot supply checks, execution rights or secrets. Duplicate request IDs replay only identical details; existing names cannot silently replace a profile. Known X/LinkedIn addresses must use their account setup.

Setup, Permissions, Review, Save and Result remain the shared flow. Unknown discovery is evidence, never authority. The generic browser option exists independently of discovery success. Saving never opens a pane or signs anyone in.

## Recovery checkpoint

The unattended fence refused writing the global `~/.claude/plans` plan (out of project scope). This document carries the scoped implementation and recovery state. Engram MCP tools are absent; no MCP configuration was changed. After compaction the host also withheld agent-attributed memory writes because runtime registration was unconfirmed. Recovery entries remain in the item's gitignored journal.

## Check now

`POST /api/desk/connect/browser-profile/check` accepts `{url, browser_profile, check?, confirmed?}`. It is human-only. A read-only check does not use or fill a saved password, grant execution, or change permissions. Credential fill, Save and permission updates keep their existing human/passcode gates. `check:false` reads local status only; it opens no tab and makes no network call.

`browser_check.py` adapts browser probes into the existing `verification.py` record/state system. Checks come only from shipped service profiles; caller fields and discovery output cannot provide scripts, selectors or check URLs. `browser_check_schema.py` validates inert HTTPS URL/selector data on declared hosts. X, LinkedIn and YouTube have fixed checks. Each requires visible signed-in controls on the expected HTTPS host and path; hidden, transparent, off-screen and signed-out fixtures cannot pass. No page text or cookie value becomes an instruction or sign-in verdict.

An open named profile uses `HiddenTabReader` in its own background tab; the user's visible tab does not navigate. A closed profile uses the existing owned `ProfilePageReader`, closed gracefully afterward. Errors retain the reader's refusal semantics: no raw-HTTP fallback. The reader inherits the open pane's project when available; global connections do not assume a particular project exists on someone else's installation.

Verification is bound to the service, named profile, reviewed check revision, and profile/cookie-file metadata, never cookie contents. Known aliases and entered paths refer to the same service/profile; unknown addresses retain their exact address. Profile deletion/replacement, cookie-file changes, revised checks or restart invalidate the record. Idempotent Save replays preserve the saved facts but derive the verdict again from current metadata, so they cannot replay stale green status. The owned reader can flush its cookies while closing, so its successful evidence is stamped after that close; vault providers retain their existing before-probe stamp. This proves browser sign-in only, not account ownership, reading capability or publishing permission.

One `desk-v1-connect-browser-check.js` controller serves Setup, Result and saved account/service details through thin modules. A human starts the first probe with **Check now**. A pass turns green without opening a pane. Missing profile or signed-out evidence opens the named pane once and schedules non-overlapping checks every two seconds after each response, for up to ten minutes. During this recovery window the status reads **Waiting for you to sign in**, including while a recheck says **Checking…**. Verification ends the wait; a transport failure or a final unsuccessful check after the window shows **Not working**. Closing/changing the wizard or removing its node ends polling. Transport failures stop and display failure instead of opening a pane based on a guess.

A service without a reviewed check opens its entered address and asks one inline question: "Clayrune has no automatic sign-in check for this service. Are you signed in now?" Human confirmation requires an existing named profile and displays **Not checked yet** (`not_checked` metadata). It never produces verified/green status. The same outstanding question stays available if the pane has not finished creating its profile. **Not yet** dismisses its text but leaves the confirmation action for later; repeated checks after successful confirmation do not ask again. Result and saved details start from the returned metadata status, without a new probe on render.

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
2. For a known service, explicitly choose an already signed-in named profile and press **Check now**. Expect green **Ready**, no pane navigation/open, and no grant of Read or Post. Check that Result and the saved tile/detail preserve the same status. Repeat the click once.
3. Use a signed-out profile for each known service. Expect the named pane and **Waiting for you to sign in**, including during **Checking…** rechecks; sign in there and wait for green without another click. The user's existing visible pane tab must stay on its original page during probing. A failed/blocked network check must say **Not working** and remain unverified; it must not silently try another reader.
4. For the unknown service, use a new profile. Expect its exact entered address and the single confirmation question. Answer **Yes, I'm signed in** after sign-in; expect **Not checked yet**, including after Save and on its tile. Read/Post remain off; no generic execution is available. **Not yet** never verifies.
5. Continue through Permissions and Review. Nothing saves before the human passcode; cancel the prompt and confirm nothing was saved. Then save with the passcode. Generic drafts contain only name, address and profile. X/LinkedIn account, credential-fill and permission gates behave as before.
6. While a known-service recheck is pending, close or change the wizard/profile. Confirm polling stops and old evidence does not apply to the replacement. Sign out or replace the named profile and recheck: old verification must disappear. A server restart also requires another check.

Live X/LinkedIn/YouTube account selectors, real sign-in transitions and the integrated HTTP check/pane lifecycle remain unverified in this branch run. Dave owns integration and Ron's live walkthrough; no production-state or vendor-account success is claimed here. No open design decision remains.

## 2026-10-29 UI sweep follow-ups (F1-F3)

Sign-in recovery has a display-only waiting state; it does not rewrite the check's returned metadata. A signed-out poll while recovery is active says **Waiting for you to sign in**, including beside **Checking…**. Verified evidence or a real failed check replaces that waiting state. Existing polling ownership, permissions and passcode gates are unchanged.

The browser-check smoke's added waiting assertion failed against the original controller (rc=1), then passed with the fix (rc=0). Its 48 wizard cases at 1440 and 390 now include X **New profile**, a completed signed-out poll, a held in-flight poll, successful verification and a real failure during recovery. It retains manual-confirmation, saved-status, no-render-probe and polling-cleanup checks. Desktop and mobile waiting screenshots were visually inspected. The vault-unlock-link smoke also passes at both widths (rc=0) with a realistic saved OAuth reference; the original exactly-one-Unlock assertion is unchanged.

The operator checklist uses the current **Check now** / **Not checked yet** wording. Its MC-1076 safety row names **Scheduler > New Steward**, the existing browser-side safety write; no per-project safety editor was added. This follow-up remains branch-only with no merge, push or restart. All browser results above use mocked API outcomes, not a live vendor sign-in.

Final sweep validation: **43 smoke scripts, all final rc=0**: `vault-unlock-link.mjs`, all 39 `desk-v1-connect*.mjs` entries (including Connections, status, removal and language), `desk-connect-guides.mjs`, `desk.mjs` and `boot-smoke.mjs`. Three scripts initially failed before their assertions because their Python imports depend on the working directory; the same scripts passed with the worktree root on `PYTHONPATH`. Run them from the repository root, or set `PYTHONPATH` to that root when launching from `tools/smoke`. No smoke dependency was installed; the main-checkout junction was removed before commit. JavaScript syntax checks and `git diff --check` passed. The operator journal and scratch evidence retain original output, retry output and the return-code matrix.
