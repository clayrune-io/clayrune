# Agent-owned browser tabs (MC-1079)

Branch build, not deployed. New ownership logic lives in `mc/browser_agent_tabs.py`; Desk profile selection lives in `mc/desk_browser_profiles.py`. The browser blueprint has thin launch/input/read/stop/teardown wiring; `_run_cdp` is unchanged so MC-1078 can add its observer independently.

## API contract

An HTTP launch without a browser Origin uses the existing agent routing signal. When its resolved named profile is already running, `POST /api/browser/launch` creates a `Target.createTarget` with `background:true` in that Chromium. It returns HTTP 200 with:

```json
{"session_id":"agent-tab-...","browser_session_id":"existing-pane-id","target_id":"CDP-target-id","agent_tab":true,"reused":true,"url":"https://example.com/","profile":"main"}
```

Use the returned `session_id` for `/api/browser/input`, `/api/browser/read`, and `/api/browser/stop`. The handle pins to a target, never the pane's current tab or a URL-matched tab. Read uses the existing hidden-content filter, HTML refusal and untrusted-content envelope; it captures the final URL in the same target evaluation and refuses Clayrune's own origin. A lost target fails closed. Input supports the existing page navigation, mouse, wheel, typing, key, history and reload operations; pane-wide viewport/site/screencast commands are not accepted on a tab handle.

Stop closes only the owned target and its CDP connections. A failed close retains the handle for retry; it never falls back to closing the browser. Closing the pane drops its handle connections; named browser teardown continues to close Chromium gracefully to flush cookies. Handles are runtime-only and disappear on server restart.

Targets use the proven hidden-read marker and disposition, registered before creation; they do not enter the pane's tab strip or steal focus. Popups from those hidden tabs follow the existing hidden-tab popup refusal. Dashboard Origin launches keep today's existing-session navigation behavior. This is routing isolation, not a permission system: no fence, policy, passcode or human-only sign-in gate is expanded.

## Desk profiles

New accounts default to configured `browser_default_profile`, or `main`. The first account on a different site shares it. A second same-site account gets a distinct default (X OAuth uses its stable account-id suffix so a held sign-in's profile stays the same across Save). Explicit LinkedIn sharing remains allowed for personal profiles, Pages and unknown destinations.

Saved browser setup/read references remain authoritative. Legacy store binding keeps its old per-account profile names. New OAuth accounts record an internal creation-time `oauth_profile_binding`, independent of the credential reference, so attachment still refuses a corrupted or foreign reference and changing the configured default cannot redirect an existing OAuth binding. Legacy OAuth accounts retain their deterministic profile names. Profile directories are never moved or deleted by this change. OAuth bindings take precedence over separate browser-read/sign-in bindings. Per-account vault naming remains unchanged. Shared default profiles are never a cancelled flow's disposable profile; the Higgsfield singleton engine keeps its existing lifecycle.

The sign-in options metadata includes `default_profile`, selected against workspace accounts. The login screen uses it while retaining explicit choices and saved-account defaults. Connect profile validation accepts the native browser profile contract (up to 64 lowercase letters/digits/dots/hyphens/underscores, no `..`), so configured defaults such as `shared.main` work. Desk's X pane reader selects the established `HiddenTabReader` when the profile is running, rather than treating that as fatal. Actual failure to create a tab can still return `profile_in_use`.

## Verification

New backend tests use temporary profile roots and local HTML only; no account sign-in or operator profile contents are used. Existing pane smokes include public LinkedIn/Google navigation in temporary profiles.

- `pyright mc/browser_agent_tabs.py mc/desk_browser_profiles.py`: rc=0, 0 errors/warnings.
- `node --check static/js/desk-v1-connect-login-step.js`: rc=0.
- `git diff --check`: rc=0.
- Full pytest command/count/rc is recorded below after the final run. The registered foreground runner expands all `tests/test_desk*.py` files, and adds browser routes, agent tabs, hidden reads and profile selection tests.
- Pane smokes: 17 of 18 distinct files pass. `browser-pane-mobile-typing.mjs` returns rc=1 on the real-backend, no-viewport-meta field tap (typing never reaches the field); the unchanged main checkout reproduces the same coordinates and failure. No MC-1079 adoption occurs on that fresh throwaway launch. It remains a separate existing defect. The initial public LinkedIn navigation timeout passed on the branch rerun; baseline also showed this public-site timing failure.
- No operator sign-in, live named-profile modification, server restart, merge, push or deployment was performed.

## Ron's five-line UI test (after Dave integrates and restarts)

1. Keep the `main` pane on a page and note its address; run an authorized agent launch using `profile:main` to another page.
2. Confirm the pane stays on your page; the agent receives a different `agent-tab-...` handle.
3. Have the agent navigate/read with that handle, then Stop it; your page and browser remain open.
4. Add the first account on a new site: the suggested profile is `main` (or your configured default); add a second on that site: it suggests a separate profile.
5. Reopen an older saved connection: its original profile stays selected; cancel an OAuth setup on `main` and confirm the shared profile remains saved.
