# MC-1105: passwordless browser accounts

Delivery branch: `clayrune/agent/d9d12f87febc`. Local commit only; Dave owns integration. No merge, push, server restart, real account sign-in, profile deletion or vault management was performed.

## Implementation

- `mc/desk_browser_accounts.py`: Desk-only metadata projection; persistent service/profile requests from explicit checks, dedupe and per-profile clear, bound to source record IDs.
- `mc/blueprints/browser_account_routes.py`: metadata-only GET `/api/secrets/browser-accounts`; two-line blueprint registration in `server.py`.
- `mc/desk_connect/signin_method.py`: shared field validation; browser connection/setup and pane-account Save paths persist it under their existing gates. `mc/desk_accounts.py` includes saved setup metadata.
- `static/js/browser-accounts.js`: read-only Vault browser rows and local metadata refresh for the existing Feed Needs you / dashboard Waiting on you surfaces. Existing check controller and named browser pane handle recovery. Thin hooks in secrets-panel, feed, mobile and modal close cleanup; shared field module used by both Details branches and their Review summaries. Feature bodies remain in separate modules; the existing large files only delegate.
- `tests/test_browser_accounts.py`, browser-setup tests, generic wizard smoke and new `tools/smoke/browser-accounts.mjs`: isolated metadata, storage, gate and UI checks. No new dependency.

No routes expose secret values. Agents cannot create vault entries, complete Google/SMS sign-in or grant permissions through this feature. No vendor website is probed on a metadata read. Old records show Other sign-in without guessing; previously saved setups retain their existing replacement refusal. Unknown services retain manual confirmation and cannot automatically detect an expired vendor session.

## Ron UI test after integration

1. Desk > Connections > Add service: connect TikTok through browser sign-in; in Connection details select **Phone code**, continue through Permissions and Review, then Save with the dashboard passcode. Cancel once first: no record should be written.
2. Add a browser service/account using **Sign in with Google** in the same Details screen. Open Vault: both rows show method and saved profile, with no Copy ref, Edit or Delete. Lock Vault and confirm both remain visible.
3. Use a saved, signed-out profile for a service with an existing reviewed check (X, LinkedIn or YouTube), or a missing named profile. Start Check connection. Expect one **Sign in again** card in Needs you / Waiting on you. Repeat the failed check: still one card for that service.
4. Press the request, then Check connection on the Vault row. Complete sign-in yourself in the named pane. After the check passes, expect the row request and attention card to disappear. For a service without a reviewed check, use its existing human confirmation; it must remain unverified.
5. With two failed profiles for the same service, recover one: the single service request stays for the other. A failed network check neither creates a new request nor clears an old one. Restart only with Ron's authorization; pending requests should survive it. Closing Vault stops its recovery rechecks.

## Validation

- **299 pytest checks, rc=0**, across the nine backend files below. Covers projection without vault-value access, malformed metadata, enum validation before passcode, round trips through both Save paths, changed replay refusal, dedupe, per-profile auto-clear, persistent requests, stale replacement exclusion, unknown-service confirmation and metadata-only GET/write refusal.
- **Three new Python modules passed pyright, rc=0**, zero errors: the projection/request module, field validator and metadata blueprint.
- **All 31 `desk-v1-connect*.mjs` entrypoints, rc=0**, including historical aliases and the saved connection/detail scripts matched by that glob. Both browser Details paths assert the selected method reaches the Save request.
- **SPA boot smoke, rc=0**: seven boot scenarios and its dispatch, setup, picker, backlog, memory, scheduler, question and Floor guards.
- **Four new browser-account UI cases, rc=0**: real branch SPA at 1440 and 390, with Vault locked/unlocked. Checks read-only controls, escaped identity, one attention card, card click routing, no render-time probes, correct named pane, successful recovery/auto-clear and no recheck after close. Final desktop and phone screenshots visually reviewed.
- **Three existing Vault smokes and inline-handler lint, rc=0**: passcode, engine labels, editor UX, and 195-module handler scope check. No new dependency.

All test fixture stores are temporary; UI account outcomes are mocked. No real Google/SMS/vendor sign-in or production restart is claimed. Existing setup replacement refusals remain; legacy accounts show Other sign-in until connected with selected metadata. USER_GUIDE and CHANGELOG are updated. README and agent rules need no change because setup, installation and standing authority rules are unchanged.

Reproduce the backend checks with pytest on `tests/test_browser_accounts.py`, `tests/test_desk_connect_browser_setup.py`, `tests/test_desk_browser_connections.py`, `tests/test_desk_browser_check.py`, `tests/test_desk_pane_account.py`, `tests/test_desk_accounts.py`, `tests/test_desk_services.py`, `tests/test_secrets_routes.py` and `tests/test_secrets_routes_unattended_gate.py`. Run pyright on the three new Python modules. Run every `tools/smoke/desk-v1-connect*.mjs`, `tools/smoke/boot-smoke.mjs` and `tools/smoke/browser-accounts.mjs` using the main checkout's smoke dependencies via a temporary junction; remove only the junction before committing. Return codes and logs are under `_scratch/mc1105/` in the delivery worktree.
