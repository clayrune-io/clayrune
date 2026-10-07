# MC-1062/14: fixes from the first live test

Three reported defects: personal sign-in showed an empty destination search rather than saved logins; saved information asked for a credential name instead of letting the user choose it; a GitHub repository address was rejected as an npm package name. This follow-up is implemented on a feature branch and needs integration before live retesting.

## Account and credential selection

`desk-v1-connect-vault-picker.js` owns the shared metadata picker and username/password fields. Browser sign-in reads `/api/secrets`, lists matching global login entries by name and username, and offers **Add another account**. X and LinkedIn personal sign-in derive a new destination from the selected or entered username. A Company Page still needs its own destination; the login that reaches it remains a member login. Provider sign-in help uses the same picker. No read returns a password.

Information-only connections use the same picker, with **Add a new credential**. Selecting an entry enables Continue. Removing it returns to the no-credential choice. New login values stay in retained input nodes, never markup, storage or a draft while typing. The existing final human/passcode Save creates one login entry with its username; no early create or overwrite path was added. A regression covers the retained input listener after repaint so it updates the current Continue button.

All new visible vocabulary is in `desk-v1-connect-copy.js`. The existing six-screen sequence, independent permission gates and saved-tile Check action remain.

## GitHub repository setup

`desk-v1-connect-github.js` hands repository addresses to the package screen. The human-only staging route calls `github_connection.py`, which reuses `mcp_installer.classify_url`, `stage_clone`, `extract_config`, `detect_secrets`, `dependency_audit`, `security_scan` and `install_commands`. Every preview has its own directory. Git hooks, file protocol and inherited Git configuration are disabled for this staging path. Links and oversized trees are refused.

Configuration extraction explicitly disables the Claude fallback. Scanning uses the certified tool-free `run_text_transform` seam; failure is reported as unavailable under Details, with no fallback CLI. A read-only audit uses an existing lockfile and does not generate one or install code. Missing deterministic configuration shows a plain message and requires the user to enter the command. Registry launchers such as npx are refused because they would bypass the saved commit.

Review uses the existing custom approval service/store. The exact 40-character commit SHA, command and arguments, source-file inventory, credential names, reach and selected install steps are fingerprinted. Install commands and every declared package script are listed and OFF initially. An npm dependency command uses `--ignore-scripts`; only explicitly selected script bodies run. Save takes only the stored request/fingerprint through the unchanged human/passcode route. Selected steps run in the existing fixed, secret-free install environment. Their dependencies can be unpinned; the card states this risk rather than claiming a dependency pin.

`github_activation.py` and `github_manifest.py` record installed files and register a guarded launch. Before every start, `tools/github-mcp-gate.py` checks the approved operation and file inventory before the credential wrapper receives any vault names. Changed files refuse startup. The existing MCP write/shadow guard still protects the approved server name. This is transparent approval, not a sandbox or proof the code is safe.

## Verification

The full `tests/test_desk_connect*.py` suite plus `tests/test_desk_signin_fill.py` passed: **1415 tests in 315.19 seconds**. Three final safety regressions were added after collection; the final GitHub/vault run passed **26 tests**, including preview-directory collision refusal, reapproval changes and root-junction refusal. The affected sign-in/Node gate run passed 134 tests. Pyright reported zero errors across seven changed Python modules.

All **24 browser scripts** passed: every `desk-v1-connect*.mjs`, boot (seven scenarios and its guards) and inline-handler scope (151 modules). Final simplify and package-step reruns passed after the public package-title correction. Eight new screenshots at 1440 and 390 were inspected; the smoke asserts account metadata, single-entry new-login Save, credential selection, unchanged shared copy, verbatim repository address, fixed commit and install steps OFF by default.

Tests use mocked Git/network/provider calls; they do not prove this particular external repository installs or signs in successfully. No live credentials, install, vendor probe, merge, push or restart is part of this dispatch.

| Screen | Desktop | Phone |
|---|---|---|
| Saved sign-in | [1440](../screens/connect_fix_linkedin_accounts_1440.png) | [390](../screens/connect_fix_linkedin_accounts_390.png) |
| Add another account | [1440](../screens/connect_fix_linkedin_new_account_1440.png) | [390](../screens/connect_fix_linkedin_new_account_390.png) |
| Saved credential | [1440](../screens/connect_fix_youtube_credentials_1440.png) | [390](../screens/connect_fix_youtube_credentials_390.png) |
| GitHub approval | [1440](../screens/connect_fix_github_approval_1440.png) | [390](../screens/connect_fix_github_approval_390.png) |

## Five-line UI retest

1. Add LinkedIn, choose Sign in and Personal profile; the account picker shows saved names and usernames.
2. Choose Add another account, enter username/password, and continue through Review; only final passcode Save stores the login.
3. Add YouTube, choose Save information only and Saved credential; select an existing entry and Continue becomes available.
4. Use Add a new credential and select a login; username and password are saved together after the final passcode.
5. Add YouTube, choose Software package, enter the supplied GitHub URL; Review shows its exact commit and unchecked install steps before approval.
