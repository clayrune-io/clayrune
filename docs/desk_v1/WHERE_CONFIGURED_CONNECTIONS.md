# Desk Where configured connections — 2026-10-29

Branch candidate: `clayrune/agent/ad0fa251aa1c`, based on `aa5ce01a`.
No merge, push, server restart, sign-in, profile creation/removal, or vault change.

## Root cause and evidence

1. Where's account channels are `workspace.accounts` (`static/js/desk-v1-store.js:88`), projected from stored accounts (`mc/desk.py:2277`; `mc/blueprints/desk_routes.py:959`). Browser services are a separate store section (`mc/desk_services.py:123`). An older Instagram sign-in was saved as a service with a URL and browser profile, with `browser_connection.service=null` and no `account_id`; it therefore cannot occur in that account array. Live reproduction details are in the gitignored session journal, not public source.
2. At base `aa5ce01a`, `static/js/desk-v1-where-services.js:12` listed every Connect catalogue destination without an account and line 15 hardcoded **Not connected**. This was the Instagram tile: a catalogue placeholder with no relationship to the saved sign-in. The same code manufactured tiles for never-configured services such as TikTok.
3. New Instagram/YouTube/TikTok sign-ins already go to `pane_account.save` (`mc/desk_connect/browser_connections.py:82`). That forward fix does not convert old saved sites. The explicit legacy attachment flow (`mc/desk_connect/saved_site_account.py:27`, `:64`) requires the account/handle and preserves the existing human Save/passcode/Permissions gate. A browser session alone does not identify which social account should be attached. This patch does not invent a handle or bypass that gate.
4. LinkedIn is **not** the same missing-account bug: its account exists. `connected` means `publish.ready` (`mc/desk_accounts.py:188`); the organization publisher is blocked by app review (`mc/desk_accounts.py:129`). An API publishing wait is not evidence of a missing browser login. Where now uses the existing Connections status projection and the same account-to-project saved read evidence. With no successful browser read evidence, the fixture shows **Waiting on LinkedIn**; a successful saved pane read can show **Ready** independently of API publishing.
5. Profile existence is not authentication evidence. The saved Instagram check currently reports **sign_in_required** via `status_for` (`mc/desk_connect/browser_check.py:154`). No browser was opened or fresh authentication check run. A Ready fixture demonstrates the original signed-in report without claiming that the live saved check is Ready.

## Change

- `static/js/desk-v1-where-services.js`: join saved browser records from `/api/desk/services` to destination catalogue metadata. Legacy null service ids resolve from the saved URL's canonical hostname, never the display name. No saved connection means no tile. Preserve separate records on the same service; suppress only an explicitly attached account's duplicate reference. Render shared saved-check vocabulary and open the existing Connections detail. Refresh on Where entry; report/retry catalogue and saved-list errors.
- `static/js/desk-v1-where.js`: omit preview-only Sources (and never-connected demo records), retaining configured unavailable/read-only accounts. Existing campaign assignments remain intact. Column chips share saved connection state.
- `static/js/desk-v1-where-tiles.js`: use shared Connections readiness words and saved read evidence, independent of `publish.ready`; offer existing Connections recovery for unavailable configured accounts. No permission, transport, posting or account-store change.
- `tools/smoke/desk-v1-where-services.mjs`: live-mode fixtures for legacy Instagram with null service, no account, multiple check states, list errors/retry, missing TikTok, preview TikTok, generation/reference exclusions, direct existing-detail navigation, explicit account attachment deduplication and removal refresh. Fixture API calls cannot reach real profiles.
- `tools/smoke/desk-v1-where.mjs`: preview exclusion plus configured offline/read-only planning coverage. `docs/USER_GUIDE.md` and `CHANGELOG.md` updated.

No new product choice was needed: the saved Connections record is the existence criterion; the already shipped explicit attachment gate remains the account identity authority. Instagram's old saved browser sign-in is visible and recoverable but is not a draggable account until that attachment is saved in Connections.

## Validation

Run from repository root; all final commands returned **rc=0**:

| Command | rc |
| --- | --- |
| `node tools/smoke/desk-v1-where.mjs` | 0 |
| `node tools/smoke/desk-v1-where-services.mjs` | 0 |
| `node tools/smoke/desk-v1-journey.mjs` | 0 |
| `node tools/smoke/desk.mjs` | 0 |
| `node tools/smoke/boot-smoke.mjs` | 0 |

The first Where run used stale preview expectations and failed (rc=1); the updated fixture tests configured offline/read-only accounts instead. A scratch-runner output encoding error occurred after a successful Where command; it did not change the smoke's rc. Scratch logs retain the final output; the final rc matrix is copied here for handoff.

Before/after screenshots are hermetic fixture renders of the real SPA, visually inspected at both requested widths. They are **not screenshots of a live authenticated Instagram session**. The before mode loads the three original Where modules from base `aa5ce01a`; the account/service fixture is otherwise the same.

- [Before 1440](screens/where_configured_before_1440.png)
- [After 1440](screens/where_configured_after_1440.png)
- [Before 390](screens/where_configured_before_390.png)
- [After 390](screens/where_configured_after_390.png)

No backend module changed, so pyright/backend checks were not applicable. Live attachment, authenticated readiness, deployment and post-restart behavior remain unverified and excluded from this task. No new dependency or agent-rule change.

## Worktree cleanup limitation

The unattended approval fence rejected restoring ten golden screenshots rewritten by the existing Where/journey smokes. These generated changes remain **unstaged**, outside this commit. The four new comparison screenshots are committed. The temporary smoke dependency junction was removed, with the main checkout's Playwright directory verified intact. No cleanup bypass was attempted.

If the owner wants the generated goldens reset from a fresh attended chat, the exact refused command (run in this branch worktree) was:

```powershell
git restore -- docs/desk_v1/screens/r2_10_where_1440.png docs/desk_v1/screens/r2_10_where_empty_1440.png docs/desk_v1/screens/r2_10_where_phone_390.png docs/desk_v1/screens/r2_13_journey_based_on_1440.png docs/desk_v1/screens/r2_13_journey_goal_1440.png docs/desk_v1/screens/r2_13_journey_home_active_1440.png docs/desk_v1/screens/r2_13_journey_home_retro_ready_1440.png docs/desk_v1/screens/r2_13_journey_retro_1440.png docs/desk_v1/screens/r2_13_journey_when_1440.png docs/desk_v1/screens/r2_13_journey_where_1440.png
```
