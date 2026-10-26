# MC-1113: saved entries in provider Setup fields

Branch implementation; not merged, pushed, restarted or exercised against a live sign-in/vault.

## Behavior and storage

- Shared provider secret/app fields choose a typed value or a saved entry name and Value / Username or key ID. Review names the source without dispensing it. Existing canonical details may be retained or explicitly rebound to another source.
- `mc/secret_field_refs.py` owns metadata-only bindings in canonical vault records, source resolution, policy/lock checks, cycle refusal, rollback, metadata edits and source verification stamps. Typed values retain their existing canonical storage. Mixed key-pair slots remain independent.
- `mc/desk_connect/saved_fields.py` adapts the X and shared key providers. Source entries are never written. Human/passcode checks on start-held and Save remain separate.
- `mc/desk_oauth_app_refs.py` validates the current app at token use, including otherwise-fresh access tokens. A changed Client ID asks for sign-in again. A rotated secret is used at the next token call. Missing/empty/unreadable/out-of-scope sources show reconnect; locked sources show unlock. No broken source falls back to older canonical details.
- `mc/desk_oauth_private_pane.py` opens a blank pane first and navigates over CDP. Setup returns a pane session ID rather than an authorization URL containing the resolved app identifier; the resolved identifier is not passed to the Chromium command line or Process Manager registration.
- The picker reads metadata-only `GET /api/secrets`. Desk connections are workspace-wide and have no project identity, so only global sources appear. Project consumers still pass their own project and unattended context into the vault. Username selection retains the vault's existing username policy; a referenced app field additionally requires the unlocked vault. Source usernames remain ordinary metadata in the pre-existing Secrets list.
- Backups preserve references, metadata edits do not silently turn them into copies, and source rotation invalidates previous key verification. Existing source entries and human-only gates are preserved.

## Validation commands and return codes

Commands ran in the worktree under a temporary registered-process wrapper. The codes below are the child commands' return codes. No dependencies were added to the project; the smoke dependency directory was linked from the main checkout and removed before commit.

### Backend regression

```powershell
python -m pytest tests/test_secret_field_refs.py tests/test_desk_connect.py tests/test_desk_connect_providers.py tests/test_desk_oauth.py tests/test_desk_oauth_hold.py tests/test_secrets_store.py tests/test_secrets_routes.py tests/test_desk_accounts.py tests/test_desk_engines.py -o addopts=-ra
```

Result: 453 passed, rc 0. The final corrupt-binding guard was then exercised by the focused command below: all 30 tests passed, rc 0.

```powershell
python -m pytest tests/test_secret_field_refs.py -o addopts=-ra
```

Additional relevant runs (rc 0):

```powershell
python -m pytest tests/test_secret_field_refs.py tests/test_desk_oauth.py tests/test_desk_oauth_hold.py -o addopts=-ra
python -m pytest tests/test_secret_field_refs.py tests/test_secrets_routes.py -o addopts=-ra
```

These passed 85 and 67 tests respectively at those checkpoints. Focused coverage includes both parts, all required refusal cases, policy changes after Save, no plaintext in responses/logs, held-start/Save, secret rotation, fresh-token Client ID mismatch, legacy-fallback refusal on cards, key pairs, rollback, corrupt/cyclic bindings, source counters, metadata edits and backup restoration. Provider HTTP, listeners and sign-in pane launch are mocked.

### Type checking

```powershell
npx --yes --package pyright pyright mc/secret_field_refs.py mc/desk_oauth_app_refs.py mc/desk_oauth_private_pane.py mc/desk_connect/saved_fields.py mc/secrets_store.py mc/desk_oauth.py mc/desk_connect/providers/key_paste.py mc/desk_connect/providers/x.py mc/desk_connect/provider_commit.py mc/blueprints/desk_held_signin_routes.py mc/blueprints/secrets_routes.py
```

Result: 0 errors, rc 0. The four new backend modules are included.

The broader scan also included the three existing modules with pre-existing diagnostics:

```powershell
npx --yes --package pyright pyright mc/secret_field_refs.py mc/desk_oauth_app_refs.py mc/desk_oauth_private_pane.py mc/desk_connect/saved_fields.py mc/secrets_store.py mc/desk_oauth.py mc/desk_engines.py mc/desk_accounts.py mc/desk_connect/verification.py mc/desk_connect/providers/key_paste.py mc/desk_connect/providers/x.py mc/desk_connect/provider_commit.py mc/blueprints/desk_held_signin_routes.py mc/blueprints/secrets_routes.py
```

Result: rc 1, three unchanged diagnostics: `desk_accounts.py:197` optional service argument, `verification.py:125` Provider.probe_stamp attribute (HEAD line 120), and `desk_engines.py:2207` optional campaign ID (HEAD line 2202). Original HEAD source copies reproduce those diagnostics; moving the copies also produced two class-identity diagnostics in the copied engine module. No unrelated typing changes were made.

### Browser smoke suites

All 37 commands below returned 0. They use the worktree's real HTML/JS/CSS with fixture APIs; the live server's code is not substituted.

| Exact command | Return code |
|---|---|
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
| `node tools/smoke/desk-v1-connect-package-check.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-creds.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-saved-fields.mjs` | 0 |
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
| `node tools/smoke/desk-connect-guides.mjs` | 0 |
| `node tools/smoke/desk-v1-vault-gate.mjs` | 0 |
| `node tools/smoke/desk-v1-vault-locked-card.mjs` | 0 |
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | 0 |
| `node tools/smoke/boot-smoke.mjs` | 0 |

The saved-field smoke checks the shared adapter/wizard at 1440 and 390 px, metadata scope filtering, source/part selection, clearing typed values, held-signin cancellation on source changes, Review and Save payloads. The existing API-step smoke retains typed/present-field, saved-account, passcode and 200% text checks. Boot reported PASS for all seven boot scenarios and its additional guards.

### Screenshots and limits

`node tools/smoke/desk-v1-connect-saved-fields.mjs` generates fixture screenshots at `_scratch/mc1113/saved_fields_1440.png` and `_scratch/mc1113/saved_fields_390.png`. Both were visually inspected; the narrow screen retains the pinned Back/Continue actions and scrolls the fields without horizontal overflow. Screenshot artifacts and full smoke logs are intentionally untracked.

No live vault writes, actual vendor sign-in, real refresh request, paid probe, merge, push or server restart. Real named-profile behavior remains an integration check for the coordinator's authorized live test. No open design decision remains after B was approved.
