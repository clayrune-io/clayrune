# When calendar + composer fixes

2026-10-31 branch candidate, Sol_Tobin. Dave reviews and lands. No merge,
push, server restart, named-profile access, sign-in or real platform action.

**Handoff blocker:** implementation and checks are complete, but no commit was
created. The unattended safety hook rejected dependency-junction removal as
"destructive delete outside scratch" and explicitly forbade retrying it in
this session. The dispatch requires removal before committing. The junction
therefore remains; a fresh human-started session must remove only the junction
with `cmd /c rmdir "<worktree>\tools\smoke\node_modules"`, then commit the
explicitly staged paths. Never delete the worktree recursively while linked.
The review patch and test evidence are copied to the main checkout's scratch
handoff directory so they survive worktree cleanup. There is no commit SHA.

## Result and file ownership

| Files | Change |
|---|---|
| `static/js/desk-v1-calendar-preview.js` | Hover/tap-info controller; renders the existing `DeskV1PostPreview.render` with a plain-text 140-character excerpt, existing media renderer, channel badges, times and states. |
| `static/js/desk-v1-calendar-detail.js` | Chip click and keyboard detail popover, device-local datetime editor, Unschedule, Edit post, Open review only for `needs_review`; shows a failed save after optimistic repaint. |
| `static/js/desk-v1-calendar-tray.js` | One card per family, platform badges, compatible/refused row affordances, family drag and keyboard scheduling. Families without destinations are eligible. |
| `static/js/desk-v1-calendar-unschedule.js` | Time clearing and Undo through `DeskV1Store.write`; restores fixture fallback times as well as live `publishAt`. |
| `static/js/desk-v1-calendar.js` | Minimal wiring and replacement of its old tray/keyboard bodies; chip/agenda info buttons, chip-to-tray drop, conservative review requirement after a cleared approved schedule, first-time demo schedule Undo repair. The monolith shrinks. |
| `static/css/desk-v1-calendar-interactions.css`, `static/index.html` | Separate interaction styles and module script tags. Info buttons are siblings, never nested buttons; phone info targets are 44px. |
| `static/js/desk-v1-composer.js` | Mount existing `DeskV1PieceTitle.html/bind` after the family exists, including first-content creation. No duplicate title-write implementation. |
| `static/js/desk-v1-composer-channels.js` | Optional caller repaint on the existing destination-add write, so calendar Undo repaints badges. |
| `static/js/desk-v1-review.js` | Remove the redundant empty-state back button; retain the shell's back control. |
| `tools/smoke/desk-v1-calendar-interactions.mjs` | New stateful browser contract at 1440px and 390px. |
| `tools/smoke/desk-v1-calendar.mjs` | Connect the positive-drop fixture explicitly: held accounts now refuse drops and have separate refusal coverage. |
| `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/desk_v1/COMPOSER.md`, this report | User behavior, amended placement decision, verification and limits. |

Sibling-owned title, store, naming, video and studio modules were not edited.
No new dependencies or backend production changes.

## Persistence, approval and capability evidence

- Live timing is `PATCH /api/desk/pieces/<id>/versions/<vid>` with
  `{scheduled_at: ISO-or-null}`. `mc/desk_pieces.py::_clean_when` and
  `patch_version` already accept clearing; the v1 projection omits `publishAt`
  when empty. Existing `test_version_fields_and_schedule` covers null.
- `mc/desk_tick.py::_due_candidates` only sends a `scheduled` version when
  its nonnull parsed time is due. Clearing its time keeps its approval record
  and state, allowing time-only Undo without impersonating a human approval.
  Giving it a new time after clearing has no approved day to compare against;
  the existing confirmation/`needs_review` withdrawal applies. No write sends
  `approved` or `scheduled`. Already-sent versions refuse time changes.
- An existing destination uses `deskV1CalendarRescheduleVersion`. A new one
  calls `DeskV1ComposerChannels.sync` inside `deskV1QueuePiece`, then schedules
  the resulting version. Its selected set preserves every current destination.
  Undo reverses the time, then the destination in two ordinary commands.
  A failed POST rolls back and sends no time PATCH. A failed time PATCH leaves
  the successfully added destination unscheduled and available for retry.
- Dave's explicit amendment allows When to add destinations, always inside
  **that family's** campaign `plan.accounts`, including in All campaigns view.
  No campaign bound or approval permission is changed.
- Held refusal uses `DeskV1Kit.channelCapabilityCopy`; YouTube incompatibility
  uses `DeskV1YouTubePost.errors` (one video, title/description requirements).
  `DeskV1MediaFormats.error` validates upload files, not channel compatibility.
  No shared check exists for other platforms' media duration/format refusal;
  those cases remain allowed. The composer's X 280-character counter remains
  a warning, not an invented calendar refusal. Manual/assisted capability is
  not a content incompatibility and remains schedulable.
- First tray scheduling retains the previous noon default. The device-time
  editor follows `desk-v1-composer-when.js`. Week rows support channel drops;
  Month/phone retain the card's channel/date keyboard/tap picker. Phone uses
  tap-info so long-press remains available to PointerDrag.

No unresolved product choice. README and agent rules need no change: this
adds no setup step or new conduct rule. USER_GUIDE was updated in this commit.

## Verification

Commands below run from the worktree root. Browser tests serve real local
markup/modules against fake APIs, never the running server's workspace. The
main checkout's existing smoke dependencies were junction-linked, not installed.
Remove that junction before commit or worktree deletion; never recursively
delete through it. Logs and screenshots are under `_scratch/when_calendar/`.

| Command | Final rc |
|---|---:|
| `node tools/smoke/desk-v1-calendar-interactions.mjs` | 0 |
| `node tools/smoke/desk-v1-calendar.mjs` | 0 |
| `node tools/smoke/desk-v1-live-calendar.mjs` | 0 |
| `node tools/smoke/desk-v1-composer.mjs` | 0 |
| `node tools/smoke/desk-v1-composer-media.mjs` | 0 |
| `node tools/smoke/desk-v1-composer-preview.mjs` | 0 |
| `node tools/smoke/desk-v1-composer-duplicate.mjs` | 0 |
| `node tools/smoke/desk-v1-youtube-channel.mjs` | 0 |
| `node tools/smoke/desk-v1-post-preview.mjs` | 0 |
| `node tools/smoke/desk-v1-review.mjs` | 0 |
| `node tools/smoke/desk-v1-live-review.mjs` | 0 |
| `npm --prefix tools/smoke test` | 0 |
| Main checkout `.venv/Scripts/python.exe -m pytest -q tests/test_desk_pieces.py tests/test_desk_tick.py` | 0 (147 tests) |
| `git diff --check` | 0 |

The npm aggregate includes the real boot smoke, provider/auth/rail checks,
stream dedupe, text selection, delivery visibility, browser paste, image
gestures/resize and file viewer. New interaction checks include family-card
counts/badges, real mouse drops to existing/new destinations, held/media
refusal with zero writes, failed POST rollback, two-step destination Undo,
detail click and keyboard paths, time PATCH and refusal recovery, tray and
detail unscheduling/Undo, new-time approval withdrawal, phone channel picker,
workspace reload, 140-character copy and attached thumbnail, Edit post, Name
rename after first-content creation, and a single empty-Review back control.

Development failures retained in the handoff journal: the initial positive
calendar drop failed (rc 1) because its account was held, corrected only in
that positive fixture; early new-smoke runs (rc 1) corrected off-screen drag
setup and the back-button selector. An initial global-Python backend run
(rc 1) had 45 media-route failures: `Request.max_content_length` had no setter
in that environment. The same two full files passed under the repository's
existing `.venv`; no environment installation or production workaround was made.
The first scratch wrapper hit Windows stdout encoding after a passing live
calendar smoke; it was corrected and that smoke rerun, rc 0.

Visually inspected screenshots at 1440 and 390 pixels:
`calendar_1440.png`, `calendar_390.png`, `detail_1440.png`, `detail_keyboard_1440.png`,
`detail_keyboard_390.png`, `preview_1440.png`, `preview_390.png`,
`composer_1440.png`, `composer_390.png`, `refused_1440.png`. Initial visual inspection found a
stretched detail close control inherited from the old keyboard-popover class;
the interaction stylesheet corrects it.

The running server, actual campaign data and platform publishing remain
unverified because this dispatch explicitly forbids merge/push/restart.
Dave should rerun the relevant smokes after landing, then load the updated SPA.
