# Direct vault unlock

Ron requested an accessible button or URL wherever Clayrune asks him to unlock the vault. Branch: `clayrune/vault-unlock-link`. Local implementation only; no push, merge, restart, real unlock, or vendor call.

## Contract

- The URL is the dashboard's origin followed by `/#unlock-vault`. It opens on cold load and hashchange. A chat link matching that dashboard fragment becomes an action on the viewer's current origin, so a localhost link also works on the remote dashboard.
- `window.openVaultUnlock()` opens one native modal dialog, with a title, labels, close button, Escape dismissal, native focus containment and return to the trigger. Layout is viewport-bounded, scrolls vertically when needed, and has 44px controls.
- `vault-unlock-popup.js` reads status, then mounts the existing `DeskV1VaultGate` form. Unknown/unreachable status is shown as an error. Already unlocked/unconfigured states close with an explanation. Success closes with the existing gate toast. Both credentials clear on submission; none is remembered. Closing does not cancel an unlock request already submitted.
- The existing `POST /api/secrets/vault-lock/unlock` remains the only request, with its existing human gates. Unlocking does not retry reads, paid renders or publishing. Retry the held action; recheck Connections to refresh cached rows.

## Integration

The new `mc/blueprints/vault_lock_responses.py` blueprint annotates JSON from Desk, secrets and passkey API surfaces. It marks the specific response/status object whose existing code/state/reason names a locked vault, including legacy `not_connected` Studio errors. It never probes or unlocks the vault. Existing response codes, status, reason and sibling statuses remain unchanged; non-JSON and streaming replies are untouched.

The popup owns the shared `VaultUnlockUI` detector and button markup. Studio preserves structured error bodies through display, so flags work even without lock words. Shared toasts also recognize older locked-vault text. Connections publishing/API rows, engine buttons, X guidance, engagement coverage/check lines and conversation gaps expose the same action. Saved-login/API setup warnings and purpose status rows also offer it; the wizard's copy formatter preserves the shared action label. Passkeys lock recovery opens this popup, while passphrase setup still opens its existing page. Existing setup flows retain their inline gate; the Vault settings panel retains its recovery-key form.

New runtime concerns live in separate JS, CSS and Python modules. Changes to the existing large files only wire the module/blueprint or call the shared rendering helper; popup and response-normalization bodies were not added to the monoliths.

## Verification

All checks below returned exit code **0**. The browser harness uses a fake API and fixture credentials, with no connection to the running vault. `tools/smoke/boot-smoke.mjs` is the repository's boot smoke; there is no `boot.mjs`.

- **395 backend tests:** `test_vault_lock_responses`, `test_desk_vault_locked`, `test_desk_engines`, `test_desk_oauth`, `test_desk_connect`, `test_desk_connect_providers`, `test_desk_signin_fill`, `test_secrets_routes_exec`, `test_passkeys_prerequisites`.
- **Type/syntax:** pyright basic on the new Python module: 0 errors; `py_compile` on it and `server.py`; inline-handler scope check: 155 modules clean.
- **34 targeted browser scripts:** the 30 below plus `secrets-vault-passcode.mjs`, `boot-smoke.mjs`, `passkeys-assert.mjs`, and `desk-v1-engagement.mjs`.
- **Full npm smoke suite:** `npm test --prefix tools/smoke`, including the seven boot scenarios and dispatch, migration, identity, backlog, memory, calendar, scheduler, floor, stream, paste, delivery and viewer checks.
- **New smoke:** 1440px and 390px, cold load, repeat-open, hashchange, chat-link rebasing, focus return, Escape, toast action, wrong/missing passcode, input clearing, success and already-unlocked handling, unreachable status, Studio price and Render refusals, Connections API status and engagement coverage/poll buttons from a structured flag. The wizard copy formatter preserves the shared action label. Final screenshots were visually reviewed; the 390px form fits without sideways scrolling.

| Script under `tools/smoke/` | rc |
|---|---|
| `secrets-engine-labels.mjs` | 0 |
| `secrets-editor-ux.mjs` | 0 |
| `desk-v1-studio.mjs` | 0 |
| `desk-v1-studio-delete.mjs` | 0 |
| `desk-v1-studio-capture.mjs` | 0 |
| `desk-v1-studio-article.mjs` | 0 |
| `desk-v1-connections.mjs` | 0 |
| `desk-v1-connection-status.mjs` | 0 |
| `desk-v1-connect-wizard.mjs` | 0 |
| `desk-v1-connect-unknown-step.mjs` | 0 |
| `desk-v1-connect-summary-step.mjs` | 0 |
| `desk-v1-connect-slice2.mjs` | 0 |
| `desk-v1-connect-simplify.mjs` | 0 |
| `desk-v1-connect-signin-fill.mjs` | 0 |
| `desk-v1-connect-signin-details.mjs` | 0 |
| `desk-v1-connect-remote.mjs` | 0 |
| `desk-v1-connect-remote-step.mjs` | 0 |
| `desk-v1-connect-purpose.mjs` | 0 |
| `desk-v1-connect-permissions-step.mjs` | 0 |
| `desk-v1-connect-package-step.mjs` | 0 |
| `desk-v1-connect-mcp.mjs` | 0 |
| `desk-v1-connect-login-step.mjs` | 0 |
| `desk-v1-connect-held.mjs` | 0 |
| `desk-v1-connect-flow.mjs` | 0 |
| `desk-v1-connect-discover.mjs` | 0 |
| `desk-v1-connect-custom.mjs` | 0 |
| `desk-v1-connect-browser-permission.mjs` | 0 |
| `desk-v1-connect-api-step.mjs` | 0 |
| `desk-v1-vault-gate.mjs` | 0 |
| `vault-unlock-link.mjs` | 0 |

`desk-v1-connect-signin-details.mjs`, `desk-v1-connect-simplify.mjs`, and the new smoke were also rerun after their shared copy/status changes; all returned 0. Smoke screenshot rewrites are restored before commit, and the main checkout's dependency junction is removed. No browser/dependency installation was performed.

**Unverified:** no real unlock, live tunnel session, or vendor operation was exercised. No push, merge or server restart was performed. The deep link must be served from the integrated change before Ron's walkthrough applies. Repository-wide type checking is outside this scoped validation; the only new/moved Python module passes basic checking.

## Ron's three-step walkthrough (after integration)

1. Open the dashboard's `/#unlock-vault` URL on desktop or phone; confirm the popup has Vault passphrase and Dashboard passcode.
2. Dismiss it; trigger a locked-vault Studio price check or click an agent's unlock URL, then press **Unlock vault**.
3. Enter both credentials; confirm the popup closes with **Vault unlocked**, then retry the held action or recheck Connections.

## Rollback

Revert the scoped commit. No data/schema/config migration or credential change is involved. Existing Vault settings and inline setup unlock forms remain available.
