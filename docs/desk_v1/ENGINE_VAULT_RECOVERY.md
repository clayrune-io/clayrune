# Studio vault recovery (MC-1065 follow-up)

Backlog bb6f9ac8. Branch `clayrune/agent/7f877e5efb1b`. Local implementation only: no merge, push, restart, real unlock or vendor call.

## Root causes and behavior

Connection status and price errors independently rendered the same vault message/action. Studio never listened for unlock completion; ddfcf287 only refreshed a Connections row on popup success. The already-unlocked popup branch emitted no completion event. Studio kept cached disconnected engines and the refused price, leaving Render disabled.

- `desk-v1-engine-vault.js` owns the new recovery concern: one registry of mounted cards, prompt coalescing, free re-read sequencing, and lifecycle listeners. The engine module imports and wires it; no HTML script registration is needed.
- `vault-unlock.js` emits `vault-unlocked` once after a successful unlock response reports `state: unlocked`. All existing forms use it, including `DeskV1VaultGate` and Vault settings. The gate itself needs no change. The popup stops emitting duplicate success and emits the same event for its already-unlocked status result.
- Each visible mounted video/image card discards its old quote and price confirmation, disables submission, force-loads engines in its original project scope, then prices the current input. Model/engine/shape, storyboard and image description survive. No job/render/publish request is sent by recovery; human spending gates stay in place.
- Render/Generate require both a ready engine and a returned estimate without a refusal/error. A status-read failure keeps the button disabled and displays the reason; a later unlock/status action can retry.
- Quote sequence numbers advance before the debounce and on recovery. Old requests and obsolete mounts cannot overwrite refreshed state. Image repaint detaches the old textarea change handler before DOM replacement, avoiding a recursive blur/change repaint; oninput has already saved the draft.
- Focus and visible-document lifecycle events re-read vault-held cards, covering a vault unlocked elsewhere when returning to this dashboard. No existing vault-state push event was found and no polling loop is added. An external unlock while this page remains continuously focused is detected by pressing Unlock vault (already-unlocked path), or by leaving and returning.

## Validation

Every final command below returned **rc=0** from the worktree root. Browser checks serve the real SPA/modules with fake API responses and fixture credentials; they never reach the operator's vault or an engine vendor.

| Exact command | rc |
|---|---|
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | 0 |
| `node tools/smoke/desk-v1-vault-gate.mjs` | 0 |
| `node tools/smoke/desk-v1-vault-locked-card.mjs` | 0 |
| `node tools/smoke/vault-unlock-link.mjs` | 0 |
| `node tools/smoke/secrets-vault-passcode.mjs` | 0 |
| `node tools/smoke/desk-v1-live-render.mjs` | 0 |
| `node tools/smoke/desk-v1-studio.mjs` | 0 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 |
| `python -m pytest -q tests/test_vault_lock_responses.py tests/test_desk_vault_locked.py tests/test_desk_engines.py tests/test_desk_render_price_confirmation.py tests/test_secrets_routes_exec.py` | 0 |

The new smoke covers 1440px and 390px, two video cards plus one image card: one lock message/action for HTTP errors and quote refusals, preservation of unrelated refusals, failed unlock, successful popup unlock (one event), inline unlock, already-unlocked recovery without a credential POST, returning-focus recovery, a late locked quote after successful recovery, failure to refresh engines, unready engines despite a price, preserved drafts/shape, original project scope, and no paid request or new polling loop. Final screenshots: `_scratch/engine-vault-recovery/{locked,ready}_{1440,390}.png` (untracked).

The smoke dependency junction uses the main checkout's installed Playwright and is relocated out of tools/smoke to `_scratch/mc1065-smoke-node_modules` before commit. The unattended fence rejected deleting the link; reversible relocation succeeded. Test-generated tracked screenshots are restored. `static/index.html` is untouched, so its special boot-change gate is not applicable. No Python modules were added/moved, so the new-module pyright gate is not applicable.

`docs/USER_GUIDE.md` and CHANGELOG are updated for the visible recovery behavior. README and agent rules need no changes: no installation change or new conduct constraint. `mc/desk_engines.py` is untouched; its owner retains the separate backend work.

## Integration and Ron walkthrough

Dave owns integration. After the commit is integrated/served, **hard reload the dashboard** so all changed JS and the new imported module load. **No server restart is needed for this frontend-only follow-up.**

1. Open a storyboard while its saved engine is behind a locked vault: exactly one unlock message/button appears, Render stays disabled.
2. Unlock with passphrase plus dashboard passcode: every visible Studio card refreshes, the selected engine loses `(not connected)`, and Render enables if the returned quote is allowed. Choices and draft text remain.
3. Unlock in another surface/device first, then click the stale card's Unlock vault: the already-unlocked toast closes the popup and the card recovers without asking for credentials. Render still needs your click and passcode.

Live operator/vault/vendor behavior remains unverified here. No merge, push, restart or live configuration change was performed. Rollback is a revert of this scoped commit; no schema/data migration is involved.
