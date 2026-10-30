# Browser service status: unverified is not signed out

Branch candidate for the 2026-10-29 Connections/Where Instagram report. No merge, push, server restart, real sign-in, named-profile change or vault change was performed.

## Cause

`BrowserProvider.credential_state()` reports `probe_ready` when the named profile exists. `verification.status()` previously mapped that to `sign_in_required` unless a matching process-local probe record existed. A restart clears those records, and ordinary Chromium cookie database changes invalidate their stamp. Neither event is evidence of being signed out. The service status button then opened `status.signin_url` directly, which could simply show an already signed-in feed.

## Behavior

| Evidence | Status | Sign-in needed |
| --- | --- | --- |
| Profile exists, no matching record | `not_checked` / Not checked yet | No |
| Profile missing | `sign_in_required` / Needs sign-in | Yes |
| Successful probe, matching snapshot | `verified` / Ready | No |
| Signed-out probe, matching snapshot | `sign_in_required`, `failure=signed_out` | Yes |
| Probe unavailable | `check_failed`, `failure=unavailable` | No |

Signed-out evidence is remembered in the same bounded process-local record store as successful evidence. Both lose validity on stamp mismatch, profile removal or server restart. The cookie metadata stamp and positive verification requirements are unchanged. No recommendation to remove Cookies mtime: this change fixes the incorrect projection without reducing evidence invalidation.

`desk-v1-status-signin.js` delegates service heading and tile-pill actions to the existing `desk-v1-connect-browser-check.js` controller. A checkable unverified service offers **Check service-name** as its accessible label while displaying **Not checked yet**. The shared controller posts `check:true` to `/api/desk/connect/browser-profile/check` with the saved URL/profile. Only the check's signed-out result opens the reported sign-in page; a successful check repaints Ready and opens nothing. Failures are visible in compact cards, with no sign-in opened. Duplicate clicks while a check is running share the controller's busy guard; recovery reuses its existing pane.

`desk_accounts.v1_account()` already projects through `browser_check.account_status()` and therefore receives the corrected status. The account browser facet now uses the shared status keys, so an observed signed-out result is `reauth`, not `saved`. Explicit account **Sign in** actions retain their existing route. `desk_browser_accounts.observe()` already requires an explicit check plus actual expiry evidence before raising a persistent request; no changes to that logic were necessary. Where consumes the same service/account projection and needs no separate status implementation.

## Verification

All browser fixtures are synthetic: real SPA/modules and registry projections with intercepted API responses and a recording pane stub. No real account was opened. Unit coverage exercises profile existence, no record, replacement/cookie stamp invalidation, restart, signed-out evidence retention/invalidation, successful recheck, account projection and read-only status paths.

Commands run from the worktree root:

```text
python -m pytest -q -o addopts= tests/test_desk_browser_check.py tests/test_desk_browser_check_agent_tab.py tests/test_browser_accounts.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_providers.py tests/test_desk_connect_status_account.py
node tools/smoke/desk-v1-account-signin.mjs
node tools/smoke/desk-v1-where.mjs
node tools/smoke/desk-v1-where-services.mjs
node tools/smoke/desk-v1-journey.mjs
node tools/smoke/boot-smoke.mjs
python -m pytest -q -o addopts= tests/test_desk_browser_connections.py tests/test_desk_connect_display_text.py
```

| Check | Result |
| --- | --- |
| Relevant pytest files | rc=0, 227 passed |
| Browser connection/display-text pytest files | rc=0, 25 passed (252 total) |
| Account sign-in smoke, including section 7 check-first | rc=0, 1440px and 390px |
| Where smoke | rc=0, desktop and phone |
| Where saved services smoke | rc=0, 1440px and 390px, including `not_checked` |
| Journey smoke | rc=0 |
| Boot smoke | rc=0, seven boot scenarios and all guards |

Section 7 also covers a stale Needs sign-in heading that checks successfully without opening a pane, duplicate clicks during a check, unavailable and HTTP-error results, and account route evidence despite stale read coverage. Section 8 retains keyboard/pill recovery coverage. Verified-service screenshots at both widths were visually inspected. `git diff --check` passed.

The main checkout's existing `tools/smoke/node_modules` was linked for smoke execution; the junction was removed before committing, with the main dependency directory retained. Live authenticated Instagram behavior remains unverified under the task's prohibition on sign-in/profile access. Integration owner must merge/restart/hard-reload before the live UI changes. No open design decisions.

`CHANGELOG.md` and `docs/USER_GUIDE.md` document the visible behavior. README, generated agent instructions and rules need no change: there is no new installation step, agent convention or authority grant. The existing small verification/status/controller modules retain ownership of this correction; no new feature was appended to a monolith.

Rollback: revert this branch's commit. No migrations, persistent verification records or configuration changes are introduced.
