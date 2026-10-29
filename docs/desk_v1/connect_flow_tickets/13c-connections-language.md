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


## MC-1118 review fix round (2026-10-28)

Dave selected option B for unsupported Read. Instagram's profile stays unchanged; the new default-deny policy remains. `desk-v1-connect-read-support.js` derives availability from the type projection, whose coverage comes from the service profile and whose runtime purposes come from route_readiness. All account Permissions screens show a disabled, unchecked Read with a service-specific one-line reason unless both coverage and executor exist for the account kind. No unsupported Read writes a domain grant. The attachment regression verifies default-deny at the actual engagement guard.

Saved key engines explicitly update through the existing provider commit. `{keep:true}` retains the untouched ciphertext or reference edge; no keep marker becomes a self-reference. New saved sources still require replacement confirmation; passcode, scope and source-policy checks remain. `saved_key_update.py` owns this update and compensating rollback, separate from create-only key handling.

Generic website edits PATCH their original connection ID through the existing browser-profile blueprint; `saved_browser_edit.py` owns validation, atomic update and bounded replay. Name collisions and conversion to an account are refused, and non-setup metadata is preserved. Remote Review serializes prefix verbatim, including empty or whitespace-only prefixes. Check now interpolates the shared label.

Regression evidence before fixes: all five focused UI cases failed; attachment reached PermissionDenied when Read was offered, saved-engine updates returned HTTP 400, and generic edits had no update route. The revised option-B attachment assertion also failed before the availability gate was added. Regression coverage is `desk-v1-connect-edit-regressions.mjs`, `desk-v1-connect-read-support.mjs`, `test_desk_saved_key_update.py` and `test_desk_saved_browser_edit.py`. `saved-edit-fixture.mjs` shares the hermetic production-UI fixture with the existing saved-edit smoke.

Validation results and integration limits are recorded below after the full sweep. No live sign-in, real credential update, external installation, merge, push or restart is part of this round.


### Final review-round validation

All five MC-1118 findings are addressed on this branch. No Instagram profile or default-deny change, no merge, push or production restart.

| Command / scope | rc | Result |
| --- | --- | --- |
| `python -m pytest @deskTests -q --tb=short` after `$deskTests = @(Get-ChildItem -Path tests/test_desk*.py | Sort-Object FullName | ForEach-Object FullName)` | 0 | Full matching set: 3,603 passed, no skips/failures. Initial PowerShell literal-glob attempt returned 4; the corrected invocation expands the same complete set. |
| `python -m pytest tests/test_desk_saved_key_update.py tests/test_desk_saved_browser_edit.py -q --tb=short` | 0 | 15 focused tests on final code, including five added after full-suite collection; rollback, source policy, field retention, metadata, gates and replay. |
| `python _scratch/mc1118/run_smokes.py` | 1 | 103 entry points attempted. After the two assertion reruns below, 97 pass; the six remaining failures/timeouts reproduce at original head `5849d43f`. |
| `node tools/smoke/desk.mjs` | 0 | Core Desk smoke. |
| `node tools/smoke/boot-smoke.mjs` | 0 | Real SPA boot and guards. |
| All 35 Connect entry points listed below | 0 | Includes five review regressions, 14 real service/kind availability pairs, saved Edit at 390/1440, permissions and source-field gates. |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` and `node tools/smoke/desk-v1-connect-remote.mjs` after assertion correction | 0 each | Initial rc1 expected the old omitted prefix. Final assertion requires the exact empty prefix. |
| `pyright mc/blueprints/desk_saved_mcp_draft_routes.py mc/blueprints/desk_saved_site_account_routes.py mc/desk_connect/saved_browser_edit.py mc/desk_connect/saved_key_update.py mc/desk_connect/saved_mcp_draft.py mc/desk_connect/saved_site_account.py` | 0 | All six new branch modules: zero errors. |
| `node --check <each changed/new JS or MJS file>` | 0 each | 15 files. |
| `git diff --check` | 0 | Whitespace/diff validation. |

The broad suite is **not green**. The following were rerun on a detached `5849d43f` checkout with the same limits; none is introduced by this review round.

| Entry point | Current rc | `5849d43f` rc | Finding |
| --- | --- | --- | --- |
| `desk-v1-default.mjs` | 1 | 1 | Also retried correctly with disposable app/data and loopback port: obsolete Settings ?Experimental? lookup fails on both versions. The unconfigured first attempt refused missing test env. |
| `desk-v1-how.mjs` | 1 | 1 | Two existing campaign walkthrough assertions fail. |
| `desk-v1-live-campaign.mjs` | 1 | 1 | Existing pause/resume harness settle timeout. |
| `desk-v1-map.mjs` | 1 | 1 | Existing launch-pause selector timeout. |
| `desk-v1-exit.mjs` | 124 | 124 | Aggregate gate exceeded the 240-second runner limit; its child output also reports the existing Map failure. Not a pass. |
| `desk-v1-journey.mjs` | 124 | 124 | Same 240-second limit; both stop after dragging a source into a campaign column. Not a pass. |

No live sign-in, real credential replacement or external MCP install was exercised. Backend tests use temporary vault/store state; browser tests use hermetic responses. The disposable default-routing servers imported the real app without boot/lifecycle services and were stopped by captured PID. Both dependency junctions were removed non-recursively and the diagnostic checkout was removed; main Playwright remains intact.

Tracked smoke-generated images were restored to the initial head. The broad/aggregate tests also refreshed eight pre-existing **untracked generated Studio screenshots**. Their pre-run bytes were not backed up; they remain untracked and are excluded from these commits. An after-first-run copy is retained in `_scratch/mc1118/pre-existing-screens`. No screenshot is staged. This artifact side effect is separate from the source fixes.

Per-command outputs, first-run matrix, remote reruns, configured default runs and baseline matrix are under `_scratch/mc1118/`; the final machine-readable summary is `final-results.json` there. The gitignored journal retains resumption details. README/install and agent rules need no change.

### Every smoke command and exit status

Commands run from this branch root. ?Final rc? includes only the two documented remote assertion reruns; initial failures are retained in the first column. The configured default retry remains rc1 on both versions.

| Command | First rc | Final rc |
| --- | --- | --- |
| `node tools/smoke/boot-smoke.mjs` | 0 | 0 |
| `node tools/smoke/desk-campaign-ui-fixes.mjs` | 0 | 0 |
| `node tools/smoke/desk-connect-guides.mjs` | 0 | 0 |
| `node tools/smoke/desk-storyboard-paste-trash.mjs` | 0 | 0 |
| `node tools/smoke/desk-studio-passcode-replay.mjs` | 0 | 0 |
| `node tools/smoke/desk-studio-render-scenes.mjs` | 0 | 0 |
| `node tools/smoke/desk-studio-render-worker.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-account-signin.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-assisted-post.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-calendar.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-campaign-chat.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-campaign-content.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-campaign.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-chat-popout.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-action-align.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-browser-check.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-browser-permission.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-browser-profile.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-custom.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-discover.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-edit-regressions.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-flow.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-held.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-login-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-mcp.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-check.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-creds.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-read-support.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 1 | 0 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 1 | 0 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-saved-edit.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-saved-fields.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-saved-permissions.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-signin-details.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-signin-fill.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-signin-reuse.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-simplify.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-slice2.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-summary-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-unknown-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-wizard.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connection-language.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connection-remove.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-conversations.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-default.mjs` | 1 | 1 |
| `node tools/smoke/desk-v1-engagement.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-exit.mjs` | 124 | 124 |
| `node tools/smoke/desk-v1-file-explorer.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-home.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-how.mjs` | 1 | 1 |
| `node tools/smoke/desk-v1-journey.mjs` | 124 | 124 |
| `node tools/smoke/desk-v1-kit.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-library.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-accounts.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-calendar.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-campaign.mjs` | 1 | 1 |
| `node tools/smoke/desk-v1-live-engagement.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-home.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-pieces.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-read-pages.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-render.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-results.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-review.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-storyboard.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-live-writes.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-map.mjs` | 1 | 1 |
| `node tools/smoke/desk-v1-model-availability.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-model-discovery.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-outcomes.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-piece.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-project.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-qa-live.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-quiet-undo.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-reads-off.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-reapproval.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-render-scenes.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-results.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-retro.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-review.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-service-icons.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-split.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-store.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-story-chat.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-story.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-studio-article.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-studio-capture.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-studio-delete.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-studio.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-vault-gate.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-vault-locked-card.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-video.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-where.mjs` | 0 | 0 |
| `node tools/smoke/desk.mjs` | 0 | 0 |

Review-round code and regression commit: `fbbe267493a6daf1ee4ae8e47029ba00fdf5c502`. The following documentation commit contains this report and the guide/changelog/API updates; both remain local on `clayrune/agent/73607ec1a093`.


## P1 follow-up: grant revocation fixed; LinkedIn execution BLOCKED (2026-10-29)

**Not ready to merge.** Dave explicitly required a stop if fresh LinkedIn Read needs backend changes beyond the projection. The new execution regression proves it does. No backend implementation beyond the executor-table declaration has been changed. This follow-up is retained as a WIP commit after f1a8f093, without rebase/squash/merge/push/restart. Master 433a6701's CHANGELOG conflict is left for Dave.

### Completed and proposed changes

- A live browser-domain grant takes precedence in Permissions: checked and enabled even without projected runtime or coverage, including stale Instagram. Unticking and passcode Save removes the exact service domain while preserving x.com in the regression. Untouched grants are disclosed and do not create Desk scopes. Existing shared-profile and broader-domain safeguards in browser-permission are unchanged; this fix removes the projection barrier to revocation, not those safeguards.
- The proposed LinkedIn browser read_own -> pane executor-table entry uses its already-declared member/organization coverage. Fresh LinkedIn Read is offered by the projection; **the real explicit-consent execution is still blocked below**, so UI smoke success is not end-to-end readiness. Instagram without an existing grant remains disabled; a synthetic change cannot restore it after revocation. Service profiles and default-deny policy are unchanged.
- Owners: static/js/desk-v1-connect-read-support.js, desk-v1-connect-permissions-step.js, and the single declaration in mc/desk_connect/route_readiness.py. Tests and guide/changelog/ticket 08 capture the intended contract, but LinkedIn's new-consent behavior is not shipped. No install or agent-conduct change, so README/AGENTS/standing rules need no update.

### Backend blocker and proposed bounded next step

mc/desk_engagement_poll.py `_require_read`, lines 37-40, enumerates every browser transport route. LinkedIn has both linkedin-browser and linkedin-manual. Two candidates cause route_id='', so the explicit policy rejects both read_own/mentions and read_own/post_metrics, despite a valid stored Allow. Legacy reads succeed because their policy is unset. The exact same selector is present at master 433a6701; master having a working pane reader and Company Page activity scoping does not repair this consent-routing seam.

The new allow case in tests/test_desk_engagement_permission_execution.py demonstrates the production poll result: permission_denied for both capabilities with an empty route ID, 0 new_items, 0 metrics_written, 0 spent. The existing absent-executor test is retained by explicitly removing the LinkedIn executor inside that test fixture; it still refuses a forged/stale Allow. No production permission check was changed to make the test pass.

Recommended next step for Dave's authorization: resolve the actual registered read_own pane executor, excluding manual browser routes and failing closed if it is still missing or ambiguous. This is a backend execution/consent selector change, not projection data. **Stopped before implementing it**, as requested. Its review should retain Company Page activity-address scoping and the existing profile, budget, permission and passcode gates.

### Exact verification results

Full Desk command, same complete sorted tests/test_desk*.py set, explicitly expanded (no subset):

```powershell
$deskTests = @(Get-ChildItem -Path tests/test_desk*.py | Sort-Object FullName | ForEach-Object FullName)
python -m pytest -o addopts= -q --tb=short @deskTests
```

**rc 1: 3,607 passed, 1 failed in 752.83s.** Failure: the old unsupported_allow case expected LinkedIn's executor to be absent. The updated execution file retains that missing-executor case with fixture removal and adds a real Allow case. Its rerun **rc 1: 46 passed, 1 failed** exposes the backend selector above. The full suite was not rerun after that test-only revision because the required backend-scope stop had been reached. No claim of green backend validation is made.

`python -m pytest -o addopts= -q --tb=short tests/test_desk_engagement_permission_execution.py`: rc **1**, allow denied by empty route_id. No network, live profile or real credential accessed. Earlier pre-fix `node tools/smoke/desk-v1-reads-off.mjs --revocation-only`: rc **1**, 12 hidden-grant/disclosure failures at 1440 and 390. After fix: rc **0**; final complete reads-off below adds fresh grant, cancellation and revoked-Instagram regrant refusal.

All 35 Connect entry points (34 desk-v1-connect-* plus desk-connect-guides), four Connections entry points, reads-off, boot and core Desk: **42/42 final rc 0**. Initial runner rc **1**, 41/42 passed: saved-permissions still expected a live x.com grant to open with Read off and waited for a prompt on an unchanged tick. Updated it to distinguish fresh-grant writes from untouched live grants; rerun rc **0**. Raw original matrix is retained; final-smokes-results.json overlays that one rerun. These are hermetic production-UI fixtures, not live authentication or proof that LinkedIn explicit-consent execution works. The prior wider 103-entry sweep was not repeated.

| Command | Initial rc | Final rc |
| --- | --- | --- |
| `node tools/smoke/boot-smoke.mjs` | 0 | 0 |
| `node tools/smoke/desk-connect-guides.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-action-align.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-browser-check.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-browser-permission.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-browser-profile.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-custom.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-discover.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-edit-regressions.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-flow.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-held.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-login-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-mcp.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-check.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-creds.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-read-support.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-saved-edit.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-saved-fields.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-saved-permissions.mjs` | 1 | 0 |
| `node tools/smoke/desk-v1-connect-signin-details.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-signin-fill.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-signin-reuse.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-simplify.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-slice2.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-summary-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-unknown-step.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-wizard.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connection-language.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connection-remove.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-reads-off.mjs` | 0 | 0 |
| `node tools/smoke/desk.mjs` | 0 | 0 |

| Additional command | rc |
| --- | --- |
| `node --check static/js/desk-v1-connect-read-support.js` | 0 |
| `node --check static/js/desk-v1-connect-permissions-step.js` | 0 |
| `node --check tools/smoke/desk-v1-connect-read-support.mjs` | 0 |
| `node --check tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 |
| `node --check tools/smoke/desk-v1-connect-saved-permissions.mjs` | 0 |
| `node --check tools/smoke/desk-v1-reads-off.mjs` | 0 |
| `pyright mc/desk_connect/route_readiness.py` | 0 |
| `git diff --check` | 0 |

Receipts/logs: _scratch/mc1118-read-revocation/{backend,execution,smokes,saved-permissions-rerun,final-smokes,checks}-results.json and *.log; blocked-results.json holds the handoff summary. The initial receipt helper used nonexistent pyright.cmd; fixed to installed pyright.EXE, final six syntax checks + pyright + diff check all rc 0.

All 559 pre-existing screenshot files were copied and hashed before their tests, including eight untracked Studio outputs. 71 refreshed files restored to this round's starting bytes and every manifest hash verified. This does not recover the originals lost in the prior round. Mobile stale-Instagram and fresh-LinkedIn fixtures were visually reviewed; no layout problem, live grant visibly checked, fresh grant unchecked/enabled, Publish disabled, Continue reachable. Captures are retained under _scratch/mc1118-read-revocation/captures. Smoke dependency junction removed nonrecursively before commit.

Global plan creation was denied by the steward fence as out-of-project configuration; the full plan and checkpoints are in the worktree journal, not retried outside the project. No live sign-in, cookie/profile access, external installation, production restart, merge or push. **Open decision for Dave: authorize the bounded backend selector fix above.**

## Authorized LinkedIn backend selector fix (2026-10-29)

Dave authorized the bounded selector change in the latest `DECIDED WITHOUT RON` entry of `docs/_journal/39aba274-connections-one-vocabulary.md`. This follow-up supersedes the backend-scope blocker above. Work continues on `clayrune/agent/73607ec1a093`, parent `37f6564a`; no rebase, squash, merge, push or restart.

### Change and boundaries

`mc/desk_engagement_poll.py::_require_read` now considers a non-X browser route only when it is not manual and the existing executor table registers its `read_own` executor as `pane`. Exactly one candidate supplies the route ID. Zero or multiple candidates still supply `''`, so explicit consent fails closed through the unchanged shared permission check. The selector never derives its route from the consent record.

The X branch, permission_check/permission_policy semantics, legacy unset-policy path and existing profile, budget, permission and passcode gates are unchanged. Activity addresses and Company Page scoping are untouched; this branch is not rebased onto the later engagement work on master. No service capability or executor is added in this follow-up. The existing LinkedIn executor declaration remains the one introduced in `37f6564a`.

The real-Allow regression now reaches both mentions and post metrics. Missing-executor/forged-Allow refusal stays covered. Two new regressions reject two registered pane routes without reading a page, laundering content or spending, and exclude a manual route even if an executor entry is mistakenly supplied for it. No production permission seam is mocked to turn a refusal into success.

### Verification receipts

Focused command: `python -m pytest -o addopts= -q --tb=short tests/test_desk_engagement_permission_execution.py`: **49 passed in 1.97s, rc 0**.

Full command (all 119 matching files, sorted and explicitly expanded, same command as the earlier 13c run):

```powershell
$deskTests = @(Get-ChildItem -Path tests/test_desk*.py | Sort-Object FullName | ForEach-Object FullName)
python -m pytest -o addopts= -q --tb=short @deskTests
```

Full Desk result: **3,611 passed in 740.47s (12m20s), rc 0**. All 119 matching files ran, including the 49 execution-permission cases; no subset or deselection.

| Requested smoke | rc | Duration |
| --- | --- | --- |
| `node tools/smoke/desk-v1-reads-off.mjs` | 0 | 13.72s |
| `node tools/smoke/boot-smoke.mjs` | 0 | 46.52s |

Reads-off exercises the production UI at 1440 and 390 pixels, fresh LinkedIn Read, cancellation, existing grant visibility and revocation, and Instagram regrant refusal. Boot passes all seven scenarios plus its dispatch, identity, backlog, memory, calendar, scheduler and Floor guards. These fixtures do not sign in to a live account.

All **577** pre-existing screenshot files were copied and SHA-256 hashed before tests (the 559 files under `docs/desk_v1/screens` plus 18 prior reads-off captures). Nine refreshed reads-off files were restored, and every manifest hash verified. The smoke dependency junction was removed nonrecursively; the main checkout's dependencies were retained. No screenshot is included in this follow-up commit.

Raw receipts and logs are in `_scratch/mc1118-selector/`; `desk-test-files.json` lists the entire full-suite input set. All test processes are registered with the Process Manager. The first smoke runner passed reads-off but exited 1 while printing its Unicode log to a Windows cp1252 console, before boot ran. The runner was corrected to UTF-8 and both requested smokes rerun; this was a receipt-runner error, not a product failure.

`docs/USER_GUIDE.md` replaces the now-stale blocked-candidate warning; CHANGELOG records the selector fix and preserves branch-only status. No new UI, setup, install, schema or agent-conduct behavior requires README, AGENTS, API-reference or standing-rule changes. No new dependency or production deployment.

The global plan-directory creation was blocked by the steward fence as human-owned `.claude` configuration, so the plan is recorded in the worktree journal. No retry or permission change. Engram memory tools were unavailable; no persistent Engram write is claimed. No real product choice arose; Dave owns integration and live validation.

## Final Desk Read consent correction (2026-10-29)

Fenn's review at `1543bc76` found that a live browser-domain grant overrode an explicitly denied Desk policy in the Permissions checkbox. Untick then retick returned the wish to `null`; applying Save reported success with no permission commit and Desk Read still false.

The shared Read row now derives its initial state from both consent parts, without allowing the browser-domain part to override an explicit Desk denial. Incomplete consent appears unticked and indeterminate with the sentence "Reading is not fully allowed yet. Tick this and save to finish it." Returning an incomplete row to off preserves the revocation wish; reticking produces the actual Desk grant. An untouched incomplete policy writes nothing. Genuine legacy unset-policy behavior remains unchanged, and a browser grant remains revocable when no Desk route is projected. The Read/Publish list and passcode gates stay the same; no site editor or service-specific screen is added.

The production LinkedIn projection drives three new browser regression cases at 1440 and 390 pixels: explicit-deny untick/retick, explicit-deny revocation, and complete-grant plain revocation. Retick asserts one permission commit containing the exact projected LinkedIn read scopes, Desk Read true, and zero browser writes for the already-granted domain. Revocation asserts Desk Read false, only linkedin.com removed, and example.org preserved. Both incomplete cases also verify that an untouched apply preserves the denial without writes. The saved-permissions smoke now expects the same incomplete-consent sentence for the opposite half-complete state.

The new regression was run before the source change: permissions-step **rc 1**, ten failed checks across both widths, including the original zero-write / Read-false result. With the fix, the focused permissions-step smoke passed **rc 0** in 31.05 seconds. Final full-suite receipts are below.

CHANGELOG and USER_GUIDE document the corrected consent state. README, API reference, AGENTS and standing rules need no change: this fixes the existing screen without introducing a setup, API, schema or conduct contract. This is a branch candidate only; no merge, push, restart or live-account verification.


### Final consent-fix verification

Full sorted `tests/test_desk*.py`: **3,611 passed in 779.67s, rc 0**; all 119 matching files ran without deselection. The runner invokes `python -m pytest -o addopts= -q --tb=short` with each sorted path explicitly expanded. The exact command and input file list are retained in `_scratch/mc1118-consent/backend-results.json` and `desk-test-files.json`.

All 38 `desk-v1-connect*.mjs` entrypoints plus reads-off and boot: **40/40 rc 0**. Browser checks load this worktree's static files with hermetic API fixtures; the new consent regression uses the production LinkedIn type projection. No live sign-in or external account access. The final permissions-step run includes 1440/390 incomplete-consent screenshots and layout checks, plus the three consent cases.

| Smoke command | rc | Seconds |
| --- | --- | --- |
| `node tools/smoke/desk-v1-connect-action-align.mjs` | 0 | 3.49 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 | 16.55 |
| `node tools/smoke/desk-v1-connect-browser-check.mjs` | 0 | 142.08 |
| `node tools/smoke/desk-v1-connect-browser-permission.mjs` | 0 | 1.15 |
| `node tools/smoke/desk-v1-connect-browser-profile.mjs` | 0 | 1.76 |
| `node tools/smoke/desk-v1-connect-custom.mjs` | 0 | 16.06 |
| `node tools/smoke/desk-v1-connect-discover.mjs` | 0 | 10.0 |
| `node tools/smoke/desk-v1-connect-edit-regressions.mjs` | 0 | 6.96 |
| `node tools/smoke/desk-v1-connect-flow.mjs` | 0 | 28.36 |
| `node tools/smoke/desk-v1-connect-held.mjs` | 0 | 0.7 |
| `node tools/smoke/desk-v1-connect-login-step.mjs` | 0 | 22.2 |
| `node tools/smoke/desk-v1-connect-mcp.mjs` | 0 | 15.78 |
| `node tools/smoke/desk-v1-connect-package-check.mjs` | 0 | 0.04 |
| `node tools/smoke/desk-v1-connect-package-creds.mjs` | 0 | 6.32 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 | 1.69 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 | 1.89 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 | 16.62 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 | 29.34 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 | 30.45 |
| `node tools/smoke/desk-v1-connect-read-support.mjs` | 0 | 0.34 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 0 | 25.47 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 0 | 26.09 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 | 2.41 |
| `node tools/smoke/desk-v1-connect-saved-edit.mjs` | 0 | 10.91 |
| `node tools/smoke/desk-v1-connect-saved-fields.mjs` | 0 | 4.57 |
| `node tools/smoke/desk-v1-connect-saved-permissions.mjs` | 0 | 9.63 |
| `node tools/smoke/desk-v1-connect-signin-details.mjs` | 0 | 22.21 |
| `node tools/smoke/desk-v1-connect-signin-fill.mjs` | 0 | 0.9 |
| `node tools/smoke/desk-v1-connect-signin-reuse.mjs` | 0 | 5.41 |
| `node tools/smoke/desk-v1-connect-simplify.mjs` | 0 | 25.36 |
| `node tools/smoke/desk-v1-connect-slice2.mjs` | 0 | 0.63 |
| `node tools/smoke/desk-v1-connect-summary-step.mjs` | 0 | 14.7 |
| `node tools/smoke/desk-v1-connect-unknown-step.mjs` | 0 | 8.8 |
| `node tools/smoke/desk-v1-connect-wizard.mjs` | 0 | 7.23 |
| `node tools/smoke/desk-v1-connection-language.mjs` | 0 | 4.12 |
| `node tools/smoke/desk-v1-connection-remove.mjs` | 0 | 7.59 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 | 2.22 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 | 3.22 |
| `node tools/smoke/desk-v1-reads-off.mjs` | 0 | 13.92 |
| `node tools/smoke/boot-smoke.mjs` | 0 | 46.5 |

| Additional command | rc |
| --- | --- |
| `node --check static/js/desk-v1-connect-permissions-step.js` | 0 |
| `node --check tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 |
| `node --check tools/smoke/desk-v1-connect-saved-permissions.mjs` | 0 |
| `git diff --check` | 0 |

All 623 pre-existing documentation PNGs were backed up and SHA-256 checked; 71 overwritten images were restored to their starting bytes. No screenshot changes belong to this fix. Smoke dependencies were linked from the main checkout and the junction removed nonrecursively before commit; no install.

Raw logs, the red/green regression receipts and full command matrices: `_scratch/mc1118-consent/`. Visual review of the mobile incomplete state confirmed the single shared Read/Publish list, explanation and reachable Back/Continue. The worktree journal records the plan and checkpoints. No real design choice arose. Merge, push, restart and live verification remain with Dave.
