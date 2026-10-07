# MC-1062 follow-up: reading settings in the one wizard

Status: local branch `clayrune/mc1062-reading-permissions`, based on master `a5b4d4b4`. No push, merge, server restart or live-account mutation.

## Change

The one wizard suppressed the old account Read via card. That also hid the activity-page editor and left older LinkedIn app-read settings with no way to choose the pane.

- `desk-v1-connect-reading-step.js` owns one registered Permissions section: reading method, saved browser profile, coverage lookup and activity addresses. Only X offers app reading; other accounts use the pane. A legacy unsupported app setting requires an explicit choice. Reading settings are independent of permission grants.
- `desk-v1-connect-read-pages.js` separately owns activity-address drafts, validation and Add/Remove controls. Its old card editor was converted to a draft factory used by the registered section; the two concerns can change independently.
- `desk-v1-connect-account-reopen.js` restores the selected saved account into the same wizard's Permissions step. Back, Review and Result stay in that frame. Its registered saved-account branch keeps the connection and grants as they are; only changed reading fields are PATCHed on Save. It never invents a sign-in adapter for a site that lacks one.
- `desk-v1-connect-permissions-step.js` provides the section registry and includes sections in validation, summary, apply, outcome and discard. The frame's `resume` method takes non-secret selection data. Render and bind now both receive account context through the existing Add service bridge.
- The shared copy module owns every new label and instruction; CSS is in a separate reading-step file. Review shows method, profile and pages. Result distinguishes saved, unchanged and refused settings and retries the retained draft. All account PATCH authorization remains on the existing server route; Desk and browser grants keep their separate passcode checks.
- Removed the unreachable `DeskV1ReadPages` card calls and `DeskV1Services.create` after a repository search found no remaining callers. Activity pages are now reached only through the Permissions section.

No new dependency, backend route, schema, credential access or posting path. The browser profile entered for a saved account is still validated by the existing server. When another connection is chosen through Back, the normal setup/permission flow applies and incompatible drafts are discarded.

## Verification

The retargeted `desk-v1-live-read-pages.mjs` uses production activation and real service type projections against a fake server, at 1440x900 and 390x844. It checks pre-fill, supported choices, no mutation before Save, untouched preservation, `pages_needed`, HTTPS rejection, Review/Back, saved removal/profile edits, exact server errors, retry, account isolation, coverage errors/retry and viewport fit.

Screenshots, visually inspected, are generated at `_scratch/connect_reading/youtube_1440.png`, `youtube_390.png`, `linkedin_1440.png`, and `linkedin_390.png`. They show Permissions for YouTube needing activity pages and LinkedIn after its explicit pane choice. Files are local review artifacts, not release assets.

All 1,428 checks in the desk-connect pytest set plus `test_desk_account_read_pages.py` passed (`rc=0`). All 34 requested connect/live smokes passed; the dashboard `boot-smoke.mjs` and full `npm test --prefix tools/smoke` suite also finished with `rc=0`. Relevant smokes were rerun after splitting the address draft into its own module. `git diff --check` is clean. No new Python module or schema requires a pyright/migration check.

| Smoke | rc |
| --- | --- |
| `desk-v1-connect-api-step.mjs` | 0 |
| `desk-v1-connect-browser-permission.mjs` | 0 |
| `desk-v1-connect-custom.mjs` | 0 |
| `desk-v1-connect-discover.mjs` | 0 |
| `desk-v1-connect-flow.mjs` | 0 |
| `desk-v1-connect-held.mjs` | 0 |
| `desk-v1-connect-login-step.mjs` | 0 |
| `desk-v1-connect-mcp.mjs` | 0 |
| `desk-v1-connect-package-step.mjs` | 0 |
| `desk-v1-connect-permissions-step.mjs` | 0 |
| `desk-v1-connect-purpose.mjs` | 0 |
| `desk-v1-connect-remote.mjs` | 0 |
| `desk-v1-connect-remote-step.mjs` | 0 |
| `desk-v1-connect-signin-details.mjs` | 0 |
| `desk-v1-connect-signin-fill.mjs` | 0 |
| `desk-v1-connect-simplify.mjs` | 0 |
| `desk-v1-connect-slice2.mjs` | 0 |
| `desk-v1-connect-summary-step.mjs` | 0 |
| `desk-v1-connect-unknown-step.mjs` | 0 |
| `desk-v1-connect-wizard.mjs` | 0 |
| `desk-v1-connection-status.mjs` | 0 |
| `desk-v1-connections.mjs` | 0 |
| `desk-v1-live-accounts.mjs` | 0 |
| `desk-v1-live-calendar.mjs` | 0 |
| `desk-v1-live-campaign.mjs` | 0 |
| `desk-v1-live-engagement.mjs` | 0 |
| `desk-v1-live-home.mjs` | 0 |
| `desk-v1-live-pieces.mjs` | 0 |
| `desk-v1-live-read-pages.mjs` | 0 |
| `desk-v1-live-render.mjs` | 0 |
| `desk-v1-live-results.mjs` | 0 |
| `desk-v1-live-review.mjs` | 0 |
| `desk-v1-live-storyboard.mjs` | 0 |
| `desk-v1-live-writes.mjs` | 0 |

Local logs: `_scratch/mc1062-checks/<check>.log`; the machine-readable manifest is `_scratch/mc1062-checks/results.json`. The main checkout's existing smoke dependencies were reused through a junction, with no install; the junction was removed after testing. Generated changes to older tracked screenshot fixtures were restored.

The user guide and this ticket record the user-facing change. No new rule or install workflow requires changes to AGENTS/CLAUDE, agent rules or README. Live integration and Ron's walkthrough remain unverified until this branch is merged and served by the normal release process.

## Ron's UI test script

1. Open Desk > Connections. Select a YouTube account whose coverage needs activity pages, then click **Change how this is connected**. Permissions should name the account and show its saved profile, a browser reading choice, and the activity-page instruction. No editor should appear on the account card.
2. Enter an `http://` address and click **Add address**. It should require HTTPS and keep Continue off. Replace it with the correct `https://` activity address and add it. Review should list the profile and address; Back should keep the draft. Save should record it; reopening should show it again. Remove should remain a draft until Save.
3. Open a LinkedIn account. Its saved pane choice/profile should be filled in. If its older saved method was app reading, Continue should be off until you explicitly choose **Browser sign-in (no charge)**. It should offer no app-reading option. Review should show your choice; Save and reopen should preserve it.
4. Open an X account. Both reading choices should appear, pane by default. Selecting **Platform app (may cost money)** should hide pane-only fields and appear in Review. Save and reopen should retain the choice. Changing reading method should not sign in, grant a permission or post anything.
5. Repeat the YouTube and LinkedIn checks at a 390px viewport. Address/profile fields should fit without sideways scrolling; Back and Continue should remain reachable. Close a draft without saving and reopen: the stored settings should remain unchanged. If a save is refused, Result should show the server's reason and allow retry without losing the draft.

## Rollback

Revert the local fix commit after integration. No data migration is involved. Saved account reading fields remain compatible with the existing backend; reverting the UI reintroduces the unreachable-editor defect.
