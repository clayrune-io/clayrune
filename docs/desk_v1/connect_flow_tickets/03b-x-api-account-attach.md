# MC-1062 / 03b: attach X API to a saved account

[Parent design](../CONNECT_FLOW_SIMPLIFY.md), section 10. Builds the provider side of
ticket 03's `account_attach.target` contract and lifts ticket 09's X refusal.

## Contract

- `POST /api/desk/connect/x/start-held` accepts an optional top-level `account_id`.
  With it, `x_account_attach.target` validates the saved X account and its own OAuth
  token/profile references through `account_attach.target`. No new account id is
  minted. Without it, the existing planned-new-account path is unchanged.
- The provider draft for `POST /api/desk/connect/commit` accepts the same optional
  `account_id`. The account supplies its handle; an explicitly supplied different
  handle is refused. Wrong-platform, unknown, malformed and unusable-reference
  targets fail before writes or OAuth starts. Validation repeats at provider apply.
- X apply reuses that id, label and OAuth references. It never calls `create_account`
  or puts the existing account on the rollback stack. Token Save and deferred OAuth
  start use the validated account's OAuth argument, including the true legacy owner.
  Client ID/optional secret retain their existing create-only workspace-vault path.
- An attachment never writes `browser_setup`, `read_via`, `browser_profile`,
  `connections`, account labels or permission policy. Existing account count and
  record remain unchanged. No permission, probe, paid read or publish is introduced.
- A held claim must name the same account as final Save and the same app as before.
  A failed token write undoes only this Save's new vault entries; the saved account
  survives and the held sign-in can be retried. Duplicate Save remains idempotent.
- Both start-held and final Save retain their independent `human_proof` passcode
  checks and unattended refusals. Frontend carries the id through both requests;
  app edits, account changes and Close discard the old held authorization. Non-X
  saved-account refusal remains unchanged. The wizard stays disabled until ticket 14.

## Ownership and implementation plan

Dedicated feature module `mc/desk_connect/x_account_attach.py`; narrow wiring in
the X provider, provider commit, draft validation and existing held route. Ticket 09
and Held receive only target plumbing and the X refusal change. No ticket 11 files.

Read contract and existing OAuth/undo patterns; implement target validation and
plumbing; exercise real held callback against temporary store/vault; run requested
regressions, pyright and five smokes; document and commit on the assigned branch.
No merge, push, restart or live vendor call. User guide/README stay with ticket 14:
the wizard is disabled and this backend prerequisite is not a released user flow.

## Validation

New tests use ticket 03's actual browser
Save, a real temporary encrypted vault and scripted OAuth callback. First/second
account ownership, complete account-record equality, count, rollback/retry, replay,
wrong target/identity/app/claim, deleted target, legacy creation and human gates
are asserted. No real platform request or operator credential is used.

```powershell
python -c "import glob,pytest; paths=sorted(set(glob.glob('tests/test_desk_connect*.py')+glob.glob('tests/test_desk_accounts*.py')+['tests/test_desk_account_refs.py','tests/test_desk_oauth_hold.py'])); print(*paths,sep='\n'); raise SystemExit(pytest.main([*paths,'-o','addopts=','-q']))"
# Actual run used _scratch/run03b.py to print the same paths and register its PID.
pyright mc/desk_connect/x_account_attach.py mc/desk_connect/account_attach.py mc/desk_connect/commit.py mc/desk_connect/provider_commit.py mc/desk_connect/providers/x.py mc/blueprints/desk_held_signin_routes.py
# tools/smoke/: node <name>.mjs for desk-v1-connect-api-step, desk-v1-connect-held,
# desk-v1-connections, human-proof-guard, boot-smoke.
```

Final results:

- Full unfiltered 29-file selection: `1357 passed in 355.66s (0:05:55)`, exit 0,
  including all 17 new attachment cases and the unchanged real-Chromium tests.
- Basic pyright on all six touched Python modules: `0 errors, 0 warnings, 0 informations`, exit 0.
- API Step: `All connect-api-step checks passed`; Held: `all checks passed`;
  Connections: `All checks passed.`; human-proof guard: `ALL PASS`; boot: `PASS`.
  Each named smoke exited 0. Desktop/phone and 200% text checks are included in API Step.
- JS syntax, fresh helper import, parent link and whitespace checks passed.

Initial fixture/assertion failures and a no-target UI regression were corrected
before this final result. The cancellation smoke waits for the server response,
so it asserts receipt of cancellation rather than racing the route handler.

Rollback: revert this commit; saved browser records and existing OAuth references
need no migration. Reverting restores the prior saved-X-account refusal in Setup.
