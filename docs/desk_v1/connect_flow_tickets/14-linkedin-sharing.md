# MC-1062/14: LinkedIn sharing and accurate Review identity

Branch implementation; not deployed. Integration requires a backend restart and browser reload after merge. This work does not push, restart, sign in, change credentials or modify existing account records.

## Behavior

The browser setup conflict check accepts shared LinkedIn logins and browser profiles for every account kind, including two known members and legacy records with no kind. `shared_with` still names the other account IDs in the save result. There is no kind backfill. X retains the same-site separation rule because its login/profile identifies the X account in that session. Cross-site sharing is unchanged. This bridge saves X and LinkedIn accounts only; no other service's policy changes.

The login step resolves the placeholder new identity from the selected login's username metadata or the typed username. Review, the suggested new profile name and the final Save use the same calculation. A chosen Company Page destination stays its own identity. Passwords remain in retained inputs until the existing human/passcode Save.

Suggested new profile/login names skip occupied names with bounded suffixes (`-2`, `-3`, etc.). Profile suggestions check all listed browser profiles; login suggestions check all vault entry names, including entries excluded from the sign-in picker by type or scope. Explicit profile/login-name choices remain untouched. The vault's create-only Save guard still rejects a concurrent name collision.

X sharing refusals use the registry's service name and the account identity rather than its icon-bearing label, and tell the user to go Back to Setup.

## Validation

The browser-setup tests cover LinkedIn sharing across all combinations of member/organization/unknown kind and login/profile/both references, unchanged legacy records, real Save between two members, and unchanged X refusals with actionable copy.

The production wizard smoke covers selected-login and typed-login Review identities at 1440 and 390 pixels, suffixing past two occupied profile/login names, Review-to-Save agreement, no early writes, hidden passwords, keyboard Save, and 200% text fit. It captures `_scratch/connect_simplify/linkedin_saved_review_{1440,390}.png` and `linkedin_review_{1440,390}.png` using fixture usernames.

- All `tests/test_desk_connect*.py`: **1,407 passed** (320.60 seconds), including the **107** browser-setup cases and the 27-case sharing matrix.
- All 22 `desk-v1-connect*.mjs` entrypoints: **22 passed**, **4,627 emitted checks**. This total includes compatibility wrappers repeating their replacement suites; it is not a count of unique assertions. The direct production wizard smoke contributes 532 checks.
- `pyright mc/desk_connect/browser_setup.py`: **0 errors, 0 warnings**. `git diff --check`: clean.
- Selected-login and typed-login Review screenshots at both widths were visually inspected: username visible, no horizontal clipping, Save reachable. Borrowed main-checkout smoke dependencies were linked for testing, then the junction was removed.
- No `static/index.html` change, so its separate boot gate is not triggered. Changelog and user guide updated; no agent-rule or README change is needed for this scoped fix. Live integration is unverified and remains with the integrator.

## Live retest after integration

1. Open LinkedIn > Sign in > Personal profile and choose an existing saved login. Review should show that login's username before Save.
2. Confirm the suggested new profile is specific to the username and unused. An existing profile can be selected explicitly.
3. Save using a login/profile another LinkedIn account already uses. Sharing must not refuse Save for any account kind.
4. Try a new typed login: Review and Save must agree on its username and unused suggested vault/profile names.
5. Two distinct X accounts still refuse a shared X login/profile and direct the user Back to Setup.
