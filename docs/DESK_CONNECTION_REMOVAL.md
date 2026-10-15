# Desk connection removal (MC-1098)

Branch implementation, 2026-10-15. Not merged, pushed, restarted or tested against live operator records.

## Behavior and decisions

Every connection detail uses `desk-v1-connection-remove.js`: the same **Remove** button and `DeskV1Kit.openConfirmSheet`, with Cancel and Remove. The sentence is **Remove this connection from the Desk? Your saved login stays in the Vault.** Opening/cancelling the sheet writes nothing. A successful server response removes the row; cancellation, a wrong passcode or a server refusal preserves it. Account/service removal no longer enters the command bus or offers Undo.

Dave selected Desk-only engine disconnection and confirmation for all kinds. No removal path deletes or alters a Vault entry or signs out a browser/provider account. Existing account/service DELETE semantics, including account-in-use refusals, remain unchanged.

## Connection-kind audit

| Kind on Connections | Removal |
| --- | --- |
| Social account, including browser/API routes | Existing DELETE `/api/desk/accounts/<id>` after the shared confirmation; existing human-only gate and in-use checks unchanged. |
| Saved service/API reference | Existing DELETE `/api/desk/services/<id>` after the shared confirmation; its credential stays in the Vault. |
| Browser-only saved service | The same service DELETE and confirmation; previously its separate renderer omitted Remove. Its browser profile remains available. |
| Approved npm package | New custom DELETE, dashboard passcode, unregister plus approval/manifest/install cleanup. |
| Approved GitHub repository, including `setup_failed` without registration | Same new DELETE; cleans the staged clone and its file manifest, even when registration never happened. |
| Remote MCP | Same new DELETE; drops registration, approval and observation without contacting the remote server. |
| PyPI metadata/reference | PyPI detection currently has no custom activation adapter. References are ordinary saved services. The DELETE also tolerates a legacy approval with no local install directory; this does not add PyPI execution support. |
| Generation engines: Google, OpenAI, Higgsfield API key and Higgsfield sign-in | New engine DELETE, dashboard passcode, persistent Desk-only disconnection. The old sign-out Disconnect action is replaced with Remove. |
| Legacy cloud-source fixture rows | Shared confirmation, preview-only local removal. Production contains no such saved cloud-drive connection adapter. |

## Server contract

`mc/blueprints/desk_connection_removal_routes.py` owns DELETE `/api/desk/connect/custom/connections`:

```json
{"server_name":"example","scope":"project","project_id":"example-project","passcode":"<re-entered by human>"}
```

For global scope omit `project_id`. Unknown fields, invalid names/scopes and malformed project IDs are rejected before a passcode guess. An unattended caller is refused first; the existing `_require_human_passcode` gate then proves the human, independently of forgeable Origin headers. A missing record returns 404. No client-supplied filesystem path is accepted.

`mc/desk_connect/custom_connection_removal.py` serializes against Save's existing per-name lock. It validates each resolved install path as a direct child of the relevant managed root and refuses root links/junctions. It unregisters using the existing locked MCP config writers, retaining the approval until cleanup succeeds. A corrupt/unreadable MCP config is not overwritten. An unregistered failed setup creates no empty config just to remove it. The recorded project path locates a project registration even if its project record no longer exists.

Current and `replaces` operations are both considered. npm installs are content-addressed and may be shared across projects/names. Ownership is re-read under the existing digest install lock before deletion, so a concurrent Save is preserved. Removals serialize against each other. Nested links/junctions are not traversed; Windows Git read-only files are retried once, with errors propagated. Cleanup failure returns an error and retains the approval for retry; registration may already be gone and the files may be partially cleaned. No success is returned for that partial result.

`mc/blueprints/desk_engine_connection_routes.py` owns DELETE/PUT `/api/desk/engines/<id>/connection`, both human/passcode-gated. DELETE saves a disconnected flag in `~/.clayrune/desk_engine_connections.json`, outside project records and source. PUT reconnects using the existing shared login and returns the current derived connection state. Neither operation changes a credential.

`mc/desk_engine_connections.py` owns that state. Missing state preserves existing behavior; corrupt state refuses execution and shows Connection unavailable. `desk_engines.connection`, `_creds` and `_resolve` enforce disconnection before credential resolution, schema refresh, estimation or a new paid submission. An existing idempotency receipt may still return an already-submitted job; this causes no new execution. Removal does not cancel an already-paid vendor job or terminate an existing external process.

Reconnection stays in the existing Add service screens: `desk-v1-connect-engine-reconnect.js` supplies the saved-login setup/review sentence and a fresh passcode-gated PUT through the API setup module. It submits no credential draft. A normal new provider Save can also clear the matching disconnect flag, with rollback in its existing undo stack. Unlocking or editing a Vault entry alone does not reconnect an engine.

## Validation

All browser tests intercept the API and load this worktree's real static assets, not the running dashboard's older files. No live YouTube entry, Vault credential, browser profile or server configuration was changed. Main-checkout smoke dependencies were linked temporarily; no package installation was needed.

- Full backend connect suite and account/service/browser/engine/route coverage: **1,711 passed, rc 0**. The final command is recorded below.
- Final engine/provider/removal regression: **176 passed, rc 0**, before the last shared-package race test; the latest dedicated removal suite is **56 passed, rc 0**.
- **31/31 connect/connection smoke scripts, rc 0**; exact commands below. The new removal smoke covers confirmation, Escape/Cancel, passcode cancellation/refusal, server refusal, exact failed-entry targeting, every row kind, saved-login engine reconnection, and desktop/mobile overflow at 1440 and 390.
- Final API setup smoke plus all seven npm checks after its first checker: **8/8, rc 0**, including boot smoke.
- `pyright mc/desk_engine_connections.py mc/blueprints/desk_engine_connection_routes.py mc/desk_connect/custom_connection_removal.py mc/blueprints/desk_connection_removal_routes.py`: **0 errors, rc 0**.
- `npm --prefix tools/smoke test`: **rc 1** at its first inline-handler checker. The identical four `active`/`pending` reports reproduce from an isolated pristine HEAD archive in `render-core.js`, `scheduler.js` and `workflow-builder.js`, naming bindings in `browser-input-timing.js`. These files/checker are unchanged here. The remaining seven npm checks were run individually and passed; the failure was not suppressed or claimed green.

Screenshots: the new smoke writes `_scratch/mc1098/remove_{1440,390}.png` and `_scratch/mc1098/confirm_{1440,390}.png`; all four were visually inspected. They show fixture data, not the live connection. The handoff journal records stable copies outside the disposable worktree.

## Limits and integration

No live deletion or server restart was authorized. Integration must load both new blueprints and the updated static assets before the live Remove button can work. The existing server restart gate remains unchanged. This branch makes no production-state claim. Reverting this commit restores prior UI/routes; disconnected engine flags left in operator state should be considered before reverting execution checks.

No new agent rule or README change is needed. New route groups, state/cleanup services and shared UI concerns have separate modules; the larger server/engine files receive only registration and enforcement hooks.

## Exact checks

Full backend command (the active Python interpreter):

```text
python -m pytest tests\test_desk_accounts.py tests\test_desk_browser_connections.py tests\test_desk_connect.py tests\test_desk_connect_browser_setup.py tests\test_desk_connect_custom.py tests\test_desk_connect_custom_audit.py tests\test_desk_connect_custom_manifest.py tests\test_desk_connect_discovery.py tests\test_desk_connect_display_text.py tests\test_desk_connect_github.py tests\test_desk_connect_github_install_steps.py tests\test_desk_connect_github_output.py tests\test_desk_connect_github_start.py tests\test_desk_connect_mcp.py tests\test_desk_connect_mcp_followups.py tests\test_desk_connect_names.py tests\test_desk_connect_npm_closure.py tests\test_desk_connect_npm_launch_gate.py tests\test_desk_connect_npm_node_paths.py tests\test_desk_connect_npm_scripts.py tests\test_desk_connect_npm_semver.py tests\test_desk_connect_package_help.py tests\test_desk_connect_parameters.py tests\test_desk_connect_permission_execution.py tests\test_desk_connect_permission_policy.py tests\test_desk_connect_providers.py tests\test_desk_connect_purpose_verification_consent.py tests\test_desk_connect_real_chromium.py tests\test_desk_connect_reference_draft.py tests\test_desk_connect_remote.py tests\test_desk_connect_signin_login_save.py tests\test_desk_connect_types.py tests\test_desk_connect_vault_picker.py tests\test_desk_connect_verify_consent.py tests\test_desk_connect_x_account_attach.py tests\test_desk_connection_removal.py tests\test_desk_engines.py tests\test_desk_routes.py tests\test_desk_services.py -q --disable-warnings --tb=short
```

Final targeted backend and dedicated removal commands:

```text
python -m pytest tests/test_desk_connection_removal.py tests/test_desk_engines.py tests/test_desk_connect_providers.py -q --disable-warnings --tb=short
python -m pytest tests/test_desk_connection_removal.py -q --disable-warnings
```

Both returned rc 0.

| Browser command | rc |
| --- | --- |
| `node tools/smoke/desk-connect-guides.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-action-align.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-browser-check.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-browser-permission.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-browser-profile.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-custom.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-discover.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-flow.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-held.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-login-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-mcp.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-signin-details.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-signin-fill.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-simplify.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-slice2.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-summary-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-unknown-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-wizard.mjs` | 0 |
| `node tools/smoke/desk-v1-connection-remove.mjs` | 0 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 |
| `node tools/smoke/desk-v1-live-accounts.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 |
| `node tools/smoke/provider-neutral-auth.mjs` | 0 |
| `node tools/smoke/provider-neutral-rail-cache.mjs` | 0 |
| `node tools/smoke/agent-stream-dedupe.mjs` | 0 |
| `node tools/smoke/boot-smoke.mjs` | 0 |
| `node tools/smoke/delivery-status-visibility.mjs` | 0 |
| `node tools/smoke/browser-paste.mjs` | 0 |
| `node tools/smoke/image-viewer-gestures.mjs` | 0 |
