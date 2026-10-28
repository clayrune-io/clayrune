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
| Sign in on an existing failed route | Sign in |

Social details have header, next-action sentence, Permissions (Read and Post, On/Off plus an Off reason), collapsed Details and actions in this order: Check now, Edit, Sign in when needed, Remove. Route/profile names, scope identifiers, costs and app-review reasons stay in Details. Engine spend limits retain Save limit. Existing MCP drift acceptance and assisted-post enablement stay inside Details, with their original explicit human controls and passcode checks.

Explicit Read/Post values come from the existing read-only permission endpoint. The browser-read gate can still turn an effective Read off. Legacy accounts retain their previous effective behavior; Details discloses that no choice is recorded. Invalid or failed policy reads never display a grant. Opening a card only reads metadata; the existing browser, provider and MCP checks remain human-started.

## Ownership

- `static/js/desk-v1-connection-words.js` owns status labels, standard action names, permission labels and shared next-action/reason copy. `desk-v1-connect-copy.js` re-exports it and shares the values with the wizard.
- `static/js/desk-v1-connection-account.js` owns the common social summary and read-only permission cache. `static/css/desk-v1-connection-account.css` owns its layout.
- Connections, status facets, browser sites, engines, saved checks, sign-in buttons, Post notices, read-off notices and purpose summaries consume the shared values. Existing execution and approval owners are unchanged.
- `tools/smoke/desk-v1-connection-language.mjs` rejects retired display strings in the six named renderers and verifies both viewport widths, permission rows, action order, collapsed Details and human-started checks. Grid/status smokes now cover the replacement summary, while saved-permissions/live-read-pages retain editor coverage.

## Saved Edit completion (follow-up to fc28e2d7)

Dave chose explicit saved-site attachment and names-only engine/MCP draft restoration. All work remains branch-only; no merge, push, restart, live profile access or sign-in.

- A saved Instagram site's Edit opens the same Permissions screen. The person chooses an existing account or enters its handle; neither the site name nor its browser session is treated as account identity. Review passcode Save links the saved site to that account. Source URL/profile/sign-in-method changes are refused before the passcode and rechecked under the store lock. New Instagram accounts have explicit Read/Post off; existing account policies remain intact. The source record is retained, and its duplicate tile is hidden while its account exists.
- Engine Edit reads its provider's field specs and saved credential metadata, then restores the selected route at Permissions. Back uses the existing provider Setup and saved-field chooser; saved key IDs do not need retyping. Unchanged Save makes no credential write and starts no sign-in. Credential replacement retains the provider's existing Review and passcode controls. Unreadable drafts remain on the selected card with an error.
- MCP Edit reads an allowlisted setup projection, preserving names, reach, pinned package/version, entry/command/arguments, or remote protocol/authentication/issuer/scopes/header prefixes. Permissions restores reach; a missing project requires a new project choice. Back restores Setup. The existing Review creates the immutable approval card, with previous script, exposure and server approvals cleared. Saved GitHub code is restaged at its exact saved commit only on Continue to Review; a mismatched pin refuses the review. Nothing starts on Edit.
- Sign-in buttons use Sign in. The shared next-action line is Sign in, then check now.

### Follow-up module ownership

- `mc/desk_connect/saved_site_account.py` and its separate blueprint own atomic, passcode-gated attachment. The existing Desk store holds the source link; no new state file, schema migration or DATA_DIR sidecar.
- `mc/desk_connect/saved_mcp_draft.py` and its separate blueprint project only saved setup fields, never credentials, install scripts or approval.
- `static/js/desk-v1-connect-saved-reopen.js` coordinates read-only re-entry. Site attachment, engine restoration and MCP restoration each live in their own modules. Existing owner modules receive small restore hooks; server.py only registers the two blueprints.
- Saved-field chooser/adapter treat the saved text half of a key pair as stored, just like its saved secret half. Their existing replacement checks remain authoritative.

### Follow-up verification

- Backend: **565 passed in 47.87s, rc 0**, including 27 new attachment/projection cases. Same 510-case base set, plus browser connections, pane accounts and the two new test modules.
- **44 affected smoke entry points: rc 0 each.** Names are the same as the historical matrix below; the follow-up RC matrix is `_scratch/saved_connections/affected-smokes.json` with one log per entry.
- New `node tools/smoke/desk-v1-connect-saved-edit.mjs`: **rc 0**, production activation with hermetic endpoints and real type projections at 390 and 1440. Covers attachment cancellation and separate domain grant, no guessed identity, source-bound payload, duplicate-tile removal, unchanged key/OAuth/pair saves, saved unattended policy, credential-replacement confirmation/cancellation, npm/remote/GitHub draft restoration, fresh MCP approval/passcode cancellation, and read failure staying on the original card.
- Supplemental API-step and saved-field smokes after the final stored-field handling change: **rc 0 each**. Pyright four new Python modules: **0 errors, rc 0**. JS syntax: **23 files, rc 0**. Diff check: **rc 0**. Final boot after the attachment stylesheet was wired into index.html: **rc 0** (7 boot scenarios and all existing dispatch/layout/identity guards). The final aggregate receipt is `_scratch/saved_connections/final-results.json`.
- No live browser profile, cookie, sign-in, credential replacement, external MCP install or deployment was exercised. The wider 95-entry sweep below belongs to the initial commit and was not repeated for this follow-up.

Exact backend command:

```text
python -m pytest -o addopts= -q tests/test_desk_accounts.py tests/test_desk_services.py tests/test_desk_engines.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_types.py tests/test_desk_connect_purpose_verification_consent.py tests/test_desk_browser_check.py tests/test_desk_browser_check_agent_tab.py tests/test_desk_connect_permission_policy.py tests/test_desk_connect_permission_execution.py tests/test_desk_post_consent.py tests/test_desk_connect_verify_consent.py tests/test_desk_engagement_permission_execution.py tests/test_desk_browser_connections.py tests/test_desk_pane_account.py tests/test_desk_saved_site_account.py tests/test_desk_saved_mcp_draft.py
pyright mc/desk_connect/saved_site_account.py mc/desk_connect/saved_mcp_draft.py mc/blueprints/desk_saved_site_account_routes.py mc/blueprints/desk_saved_mcp_draft_routes.py
```

Captures in `_scratch/saved_connections/` at 390x844 and 1440x1000: `instagram-card-<width>.png`, `instagram-permissions-<width>.png`, `engine-permissions-<width>.png`, `engine-setup-<width>.png`. All eight were visually reviewed. These are fixture captures, not live dashboard evidence.

No new product decision was encountered. Dave owns integration and live validation. The worktree journal must be copied when integrating because `_journal` and `_scratch` are gitignored.

### Follow-up files

- `CHANGELOG.md`
- `data/agent_reference/CLAYRUNE_API.md`
- `docs/USER_GUIDE.md`
- `docs/desk_v1/connect_flow_tickets/13c-connections-language.md`
- `mc/blueprints/desk_saved_mcp_draft_routes.py`
- `mc/blueprints/desk_saved_site_account_routes.py`
- `mc/desk_connect/saved_mcp_draft.py`
- `mc/desk_connect/saved_site_account.py`
- `mc/desk_services.py`
- `server.py`
- `static/css/desk-v1-connect-site-attach.css`
- `static/index.html`
- `static/js/desk-v1-browser-service.js`
- `static/js/desk-v1-connect-adapter.js`
- `static/js/desk-v1-connect-api-step.js`
- `static/js/desk-v1-connect-browser-profile.js`
- `static/js/desk-v1-connect-copy.js`
- `static/js/desk-v1-connect-engine-saved.js`
- `static/js/desk-v1-connect-integration.js`
- `static/js/desk-v1-connect-mcp-restore.js`
- `static/js/desk-v1-connect-package-creds.js`
- `static/js/desk-v1-connect-package-step.js`
- `static/js/desk-v1-connect-permissions-step.js`
- `static/js/desk-v1-connect-reading-step.js`
- `static/js/desk-v1-connect-remote-step.js`
- `static/js/desk-v1-connect-saved-fields.js`
- `static/js/desk-v1-connect-saved-reopen.js`
- `static/js/desk-v1-connect-site-attach.js`
- `static/js/desk-v1-connection-account.js`
- `static/js/desk-v1-connection-words.js`
- `static/js/desk-v1-connections.js`
- `tests/test_desk_saved_mcp_draft.py`
- `tests/test_desk_saved_site_account.py`
- `tools/smoke/connection-language-fixture.mjs`
- `tools/smoke/desk-v1-account-signin.mjs`
- `tools/smoke/desk-v1-connect-saved-edit.mjs`
- `tools/smoke/desk-v1-connection-language.mjs`

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

## Original fc28e2d7 results (historical)

- 44 affected Connect/Connections/engine/read-page smoke entry points: rc 0 each, including boot.
- Account/service/engine/setup/type/verification backend set: 269 cases, rc 0. Permission/browser/consent backend set: 241 cases, rc 0.
- Broader sweep: 95 unique smoke entry points, 89 green after the affected reruns. How, live-campaign and map fail in the same places on the untouched base commit: planned-row/banner expectations, pause/resume settling and launch Pause visibility respectively.
- Default smoke: rc 1 because it requires an isolated loopback test server and explicitly refuses the live port. Exit aggregate and journey: rc 124 at the 180-second runner timeout; their completion remains unverified.
- The initial commit added no backend module or schema change; its pyright check was not applicable. Guide/changelog and the two superseded presentation specs are updated. No install instructions or standing agent rules changed.

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
