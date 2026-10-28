# Connections: shared language and account details

Branch candidate, 2026-10-28. This change is not merged, pushed or live.

## Contract

Saved connections use the five words below. Readiness uses the existing derived credential state and check/read evidence; it never grants permission. Read and Post are separate rows. A saved browser profile or registered server alone remains Not checked yet.

| Previous display | Shared display |
| --- | --- |
| Connected, Verified, Working, Reading by browser, API signed in | Ready, when supported by the existing evidence |
| Sign-in required, API not signed in, Needs re-auth | Needs sign-in, for an existing route needing sign-in |
| Browser set up, not read yet; Browser profile saved; Read only; Registered | Not checked yet, until there is check/read evidence |
| Saved, cannot run yet; setup failed; changed/missing package or credential; unreadable/locked credentials | Not working, with the reason in Details |
| Publishing app review pending, with no ready route | Waiting on the service |
| Reading is off | The saved sign-in's readiness; Read is Off with its own reason |
| Check it now, Check connection, Check the connection, Check again | Check now |
| Edit settings, Change connection, Review again | Edit |
| Sign in on an existing failed route | Sign in again |

Social details have header, next-action sentence, Permissions (Read and Post, On/Off plus an Off reason), collapsed Details and actions in this order: Check now, Edit, Sign in again when needed, Remove. Route/profile names, scope identifiers, costs and app-review reasons stay in Details. Engine spend limits retain Save limit. Existing MCP drift acceptance and assisted-post enablement stay inside Details, with their original explicit human controls and passcode checks.

Explicit Read/Post values come from the existing read-only permission endpoint. The browser-read gate can still turn an effective Read off. Legacy accounts retain their previous effective behavior; Details discloses that no choice is recorded. Invalid or failed policy reads never display a grant. Opening a card only reads metadata; the existing browser, provider and MCP checks remain human-started.

## Ownership

- `static/js/desk-v1-connection-words.js` owns status labels, standard action names, permission labels and shared next-action/reason copy. `desk-v1-connect-copy.js` re-exports it and shares the values with the wizard.
- `static/js/desk-v1-connection-account.js` owns the common social summary and read-only permission cache. `static/css/desk-v1-connection-account.css` owns its layout.
- Connections, status facets, browser sites, engines, saved checks, sign-in buttons, Post notices, read-off notices and purpose summaries consume the shared values. Existing execution and approval owners are unchanged.
- `tools/smoke/desk-v1-connection-language.mjs` rejects retired display strings in the six named renderers and verifies both viewport widths, permission rows, action order, collapsed Details and human-started checks. Grid/status smokes now cover the replacement summary, while saved-permissions/live-read-pages retain editor coverage.

## Remaining target gaps: decisions for the spawner

1. A generic saved browser site is not an account. It gets the same summary with Read/Post Off and an explanation; Edit is disabled because no account permission target exists. No migration was built. Smallest follow-up: let the human explicitly attach/add an account using the saved site/profile, then reuse the existing Permissions flow. Alternative: remove and re-add it as an account. Recommendation: explicit attachment, preserving the saved sign-in and existing human gates.
2. Social-account Edit opens the existing wizard at Permissions. Engine and custom-MCP Edit retain the existing Add-service handoff. They cannot safely restore a complete saved setup: the custom connection projection omits package command/arguments/entry and parts of remote authentication setup; engine provider Setup owns the saved-field/replacement flow. Smallest complete follow-up: a names-only saved-draft projection and handoff through those existing owners, with the same Review/passcode controls. Alternative: explicitly allow Edit to start Setup for these kinds. Recommendation: saved-draft restoration. This branch does not claim this part of the target is complete.

## Verification and artifacts

The handoff journal and `_scratch/connections_language/` hold exact exit-code matrices, logs and captures. Screenshots are hermetic fixtures, not the live dashboard: `--before=7f1e0f18` serves the task's base static files and the normal run serves branch static files, with identical accounts, servers, browser site and engine at 390×844 and 1440×1000. No real credential, browser profile, account data, server restart, merge or push is used. Unsupported checks remain disabled; an X app connection does not acquire a new provider check.

Commands:

```text
node tools/smoke/desk-v1-connection-language.mjs --before=7f1e0f18
node tools/smoke/desk-v1-connection-language.mjs
node tools/smoke/desk-v1-connection-status.mjs
node tools/smoke/desk-v1-connections.mjs
python -m pytest -q tests/test_desk_accounts.py tests/test_desk_services.py tests/test_desk_engines.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_types.py tests/test_desk_connect_purpose_verification_consent.py
```

Remove the worktree's smoke dependency junction before committing. Do not install Playwright in a worktree or remove a dependency junction recursively.

## Recorded results

- 44 affected Connect/Connections/engine/read-page smoke entry points: rc 0 each, including boot.
- Account/service/engine/setup/type/verification backend set: 269 cases, rc 0. Permission/browser/consent backend set: 241 cases, rc 0.
- Broader sweep: 95 unique smoke entry points, 89 green after the affected reruns. How, live-campaign and map fail in the same places on the untouched base commit: planned-row/banner expectations, pause/resume settling and launch Pause visibility respectively.
- Default smoke: rc 1 because it requires an isolated loopback test server and explicitly refuses the live port. Exit aggregate and journey: rc 124 at the 180-second runner timeout; their completion remains unverified.
- No new backend module or schema change; pyright is not applicable. Guide/changelog and the two superseded presentation specs are updated. No install instructions or standing agent rules changed.

| Affected smoke (`tools/smoke/`) | RC |
| --- | --- |
| `boot-smoke.mjs` | 0 |
| `desk-v1-account-signin.mjs` | 0 |
| `desk-v1-assisted-post.mjs` | 0 |
| `desk-v1-connect-action-align.mjs` | 0 |
| `desk-v1-connect-api-step.mjs` | 0 |
| `desk-v1-connect-browser-check.mjs` | 0 |
| `desk-v1-connect-browser-permission.mjs` | 0 |
| `desk-v1-connect-browser-profile.mjs` | 0 |
| `desk-v1-connect-custom.mjs` | 0 |
| `desk-v1-connect-discover.mjs` | 0 |
| `desk-v1-connect-flow.mjs` | 0 |
| `desk-v1-connect-held.mjs` | 0 |
| `desk-v1-connect-login-step.mjs` | 0 |
| `desk-v1-connect-mcp.mjs` | 0 |
| `desk-v1-connect-package-check.mjs` | 0 |
| `desk-v1-connect-package-creds.mjs` | 0 |
| `desk-v1-connect-package-progress.mjs` | 0 |
| `desk-v1-connect-package-save.mjs` | 0 |
| `desk-v1-connect-package-step.mjs` | 0 |
| `desk-v1-connect-permissions-step.mjs` | 0 |
| `desk-v1-connect-purpose.mjs` | 0 |
| `desk-v1-connect-remote-step.mjs` | 0 |
| `desk-v1-connect-remote.mjs` | 0 |
| `desk-v1-connect-save-refresh.mjs` | 0 |
| `desk-v1-connect-saved-fields.mjs` | 0 |
| `desk-v1-connect-saved-permissions.mjs` | 0 |
| `desk-v1-connect-signin-details.mjs` | 0 |
| `desk-v1-connect-signin-fill.mjs` | 0 |
| `desk-v1-connect-signin-reuse.mjs` | 0 |
| `desk-v1-connect-simplify.mjs` | 0 |
| `desk-v1-connect-slice2.mjs` | 0 |
| `desk-v1-connect-summary-step.mjs` | 0 |
| `desk-v1-connect-unknown-step.mjs` | 0 |
| `desk-v1-connect-wizard.mjs` | 0 |
| `desk-v1-connection-language.mjs` | 0 |
| `desk-v1-connection-remove.mjs` | 0 |
| `desk-v1-connection-status.mjs` | 0 |
| `desk-v1-connections.mjs` | 0 |
| `desk-v1-engine-vault-recovery.mjs` | 0 |
| `desk-v1-live-accounts.mjs` | 0 |
| `desk-v1-live-read-pages.mjs` | 0 |
| `desk-v1-live-render.mjs` | 0 |
| `desk-v1-reads-off.mjs` | 0 |
| `desk-v1-vault-locked-card.mjs` | 0 |

## Files in this change

- `CHANGELOG.md`
- `docs/USER_GUIDE.md`
- `docs/desk_v1/connect_flow_tickets/07-browser-permission.md`
- `docs/desk_v1/connect_flow_tickets/13b-management-status.md`
- `docs/desk_v1/connect_flow_tickets/13c-connections-language.md`
- `static/index.html`
- `static/css/desk-v1-connection-account.css`
- `static/js/desk-v1-account-signin.js`
- `static/js/desk-v1-browser-account.js`
- `static/js/desk-v1-browser-service.js`
- `static/js/desk-v1-connect-browser-check.js`
- `static/js/desk-v1-connect-copy.js`
- `static/js/desk-v1-connect-purpose.js`
- `static/js/desk-v1-connect-saved-check.js`
- `static/js/desk-v1-connection-account.js`
- `static/js/desk-v1-connection-words.js`
- `static/js/desk-v1-connection-remove.js`
- `static/js/desk-v1-connection-status.js`
- `static/js/desk-v1-connections.js`
- `static/js/desk-v1-engines.js`
- `static/js/desk-v1-post-permission.js`
- `static/js/desk-v1-reads-off.js`
- `static/js/desk-v1-services.js`
- `tools/smoke/connection-language-fixture.mjs`
- `tools/smoke/desk-v1-connection-language.mjs`
- `tools/smoke/desk-v1-account-signin.mjs`
- `tools/smoke/desk-v1-assisted-post.mjs`
- `tools/smoke/desk-v1-connect-action-align.mjs`
- `tools/smoke/desk-v1-connect-browser-check.mjs`
- `tools/smoke/desk-v1-connect-package-check.mjs`
- `tools/smoke/desk-v1-connect-package-step.mjs`
- `tools/smoke/desk-v1-connect-save-refresh.mjs`
- `tools/smoke/desk-v1-connect-saved-permissions.mjs`
- `tools/smoke/desk-v1-connect-simplify.mjs`
- `tools/smoke/desk-v1-connect-summary-step.mjs`
- `tools/smoke/desk-v1-connection-status.mjs`
- `tools/smoke/desk-v1-connections.mjs`
- `tools/smoke/desk-v1-live-accounts.mjs`
- `tools/smoke/desk-v1-live-render.mjs`
- `tools/smoke/desk-v1-reads-off.mjs`
- `tools/smoke/desk-v1-vault-locked-card.mjs`
