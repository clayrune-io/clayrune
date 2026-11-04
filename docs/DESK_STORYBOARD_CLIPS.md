# Uploaded clips in the Studio editor

Branch candidate, 2026-10-31. Implements ticket steps 1–3. No merge, push,
server restart, live Desk edits, account access or paid generation.

## Behavior and ownership

- `static/js/desk-v1-studio-title.js` adapts the existing piece title control:
  piece names PATCH `/api/desk/pieces/<id>` through `DeskV1Store.write` and the
  piece queue; standalone titles use the board's serialized revision-guarded
  PUT. Both use the Desk Undo bus. The old duplicate Studio title field is gone.
- `static/js/desk-v1-studio-clips.js` owns video compatibility probing,
  first-frame thumbnails, duration discovery, and one-time import of attached
  clips. The list and timebar use muted, non-autoplay video elements. New clips
  take their decoded native duration; trim is capped at the available length.
  Fractional durations remain numeric on save and display in the timebar.
- `mc/desk_storyboard_media.py` owns the media AssetRef projection, lexical
  and realpath containment, and bounded uploads. The `picture` field name and
  historical `/storyboard/pictures` route remain compatible. `kind: video`
  resolves under `desk/library/video`, `kind: image` under the image root.
  Sources are derived server-side; client URLs are never stored. Absolute,
  drive-relative, `..` and symlink escapes are refused on write and read.
- The common writer in `mc/desk_pieces.py` accepts a scene media-kind hint,
  retains existing size caps, and checks the library destination before writing.
  Browser-decodable files with unknown or misleading suffixes are stored
  extensionless, preserving their original display title. Material listing and
  inline sources include these files. The default-name/create-piece code is
  untouched, as are What and composer files owned by the sibling job.
- `mc/desk_storyboard.py` accepts and preserves `assets_seeded: true` through
  subsequent PUTs. On an empty piece board, uploaded video assets become scenes
  in asset order, then the normal serialized PUT persists both scenes and marker.
  A competing tab's 409 is adopted without retry. Deleting all imported scenes
  does not re-import them on reopen. Media discovery does not mutate another
  owner after navigation; scene controls stay disabled during initial loading.
- Studio/store/timeline/VideoStrip changes are wiring and shared thumbnail/trim
  hooks. Styling is isolated in `static/css/desk-v1-studio-clips.css` and loaded
  in `static/index.html`. No new dependency or module added to a large registry.

## Render work intentionally deferred

This ticket explicitly permits stopping after steps 1–3 if rendering is larger
than expected. It is: this codebase requires more than adding a planner row.
`mc/desk_engines.py` now refuses selected video AssetRefs with
`local_clips_pending` before schema lookup, credentials, estimates or preflight.
It does **not** quote a clip as free or claim that a trimmed export exists.

Dave can land an explicitly edit-only candidate, or request the render follow-up
before landing. Recommendation: finish the follow-up before releasing the full
uploaded-video-editing workflow. Landing now delivers import/rename/scene trim,
but all clip-containing renders remain blocked. The follow-up must cover:

1. Planner rows tagged local versus generated; native clip durations and zero
   price for local rows, including all-local boards without generator credentials.
2. `mc/desk_generation_preflight.py` and `mc/desk_render_worker.py`: skip local
   media during vendor preparation/submission; reserve and confirm only the
   generated rows. Preserve passcode, idempotency, budget and price-change gates.
3. `mc/desk_render_scenes.py`: explicit local outputs in full/subset snapshots,
   correct scene ordering and reuse; local clips never enter paid children.
4. Progress/finalizer logic in `mc/desk_engines.py`: completion cannot require
   one paid child per scene. Trim local inputs with approved ffmpeg before
   stitching, including a single local clip (currently bypasses stitching),
   missing ffmpeg holds, mixed codecs, aspect ratio and sound handling.
5. The engine panel must expose all-local free assembly even when no generation
   engine is connected. Verify local-only and mixed boards, subset replacements,
   accurate duration, no vendor calls for local rows, and unchanged approval gates.

## Verification

The browser tests load this worktree's real HTML/JS/CSS against isolated API
fixtures. The new smoke uploads a real synthetic 2.4-second H.264 MP4 through
What's file chooser, clicks the card title, and verifies browser-decoded clips.
Backend tests exercise the actual Flask routes and temporary filesystem/store.
Live installed-server behavior remains unverified because this job forbids a
merge/restart and the server serves the main checkout.

Final commands and results are recorded below before committing. Screenshots:
`_scratch/clip-editor/uploaded-1440.png` and `uploaded-390.png`. The first shows
the piece title, scene 1 clip thumbnail, and timebar together. They are test
artifacts, not screenshots of a changed live Desk.

The broad test run initially failed under an implicit Python invocation because
the loaded Flask Request lacked the `max_content_length` setter. A specific
Flask-version diagnosis was not established. The final suite uses explicit
`py -3.12`; no environment install or product workaround was made.

The paste/trash smoke was updated to press **Fit** before coordinate gestures:
fixed-scale tiles could be wider than the viewport, so their centers were not
hittable. This changes the harness gesture, not product drag behavior.

`CHANGELOG.md` and `docs/USER_GUIDE.md` describe both the new editor behavior
and the render limitation. No new agent rule or top-level architecture invariant
was needed; CLAUDE/AGENTS, README and operator rules were left alone. Revert the
candidate commit to roll back code; existing library files are never removed.

### Commands and pass counts

All commands below returned rc **0** on the candidate:

```powershell
py -3.12 -m pytest tests/test_desk_storyboard.py tests/test_desk_storyboard_clips.py tests/test_desk_pieces.py tests/test_desk_engines.py tests/test_desk_render_scenes.py tests/test_desk_render_worker.py tests/test_desk_render_price_confirmation.py tests/test_desk_render_scenes_flow.py tests/test_desk_render_recovery.py tests/test_higgsfield_picture_render.py -o addopts= -q -ra
```

**378 passed**, including 16 new clip tests and the existing paid render suites.
After the final helper simplification, the focused command
`py -3.12 -m pytest tests/test_desk_storyboard.py tests/test_desk_storyboard_clips.py -o addopts= -q`
also returned **48 passed**. `pyright mc/desk_storyboard_media.py` returned
**0 errors**, and `npm --prefix tools/smoke test` passed all **11 programs**,
including the SPA boot gate. No npm install or browser install was run.

| Exact smoke command | Passing checks/scenarios |
| --- | ---: |
| `node tools/smoke/desk-v1-studio.mjs` | 334 checks |
| `node tools/smoke/desk-v1-live-storyboard.mjs` | 27 checks |
| `node tools/smoke/desk-studio-timeline.mjs` | 6 scenarios, 1440/390 campaign/Studio and saved-board Fit |
| `node tools/smoke/desk-storyboard-paste-trash.mjs` | 23 checks |
| `node tools/smoke/desk-video-entrypoints.mjs` | 7 entry points |
| `node tools/smoke/desk-studio-uploaded-clips.mjs` | 42 checks, 1440/390 |
| `node tools/smoke/desk-studio-render-scenes.mjs` | 64 checks |
| `node tools/smoke/desk-studio-render-worker.mjs` | 68 checks |
| `node tools/smoke/desk-v1-piece.mjs` | 25 checks |
| `node tools/smoke/desk-v1-story.mjs` | 47 checks |
| `node tools/smoke/desk-v1-story-chat.mjs` | 51 checks |
| `node tools/smoke/desk-v1-library.mjs` | 110 checks |
| `node tools/smoke/desk-v1-quiet-undo.mjs` | 14 checks |

Smoke dependencies were supplied by a junction to the main checkout; remove
that junction with `cmd /c rmdir tools\smoke\node_modules` before committing.
Logs and result records remain under `_scratch/clip-editor/` in this worktree.

### Worktree hygiene handoff

The story/chat/quiet-Undo smokes regenerate eight tracked documentation PNGs.
They are intentionally **not staged or committed** with this candidate. The
automatic unattended approval fence refused restoring them, stating that a
working-tree restore could discard uncommitted work. No alternate overwrite
path was attempted. In a fresh human-started chat, after checking those are
still only this run's generated artifacts, the exact cleanup command is:

```powershell
git restore -- docs/desk_v1/screens/quiet_undo_header_1440.png docs/desk_v1/screens/story_scene_editor_1440.png docs/desk_v1/screens/story_scene_editor_390.png docs/desk_v1/screens/story_storyboard_1440.png docs/desk_v1/screens/story_storyboard_390.png docs/desk_v1/screens/studio-agent-chat-1440.png docs/desk_v1/screens/studio-agent-chat-390.png docs/desk_v1/screens/studio-agent-chat-popout-1440.png
```

The global plan-directory write was also refused by the unattended fence. The
plan and session record are instead in the gitignored worktree journal
`docs/_journal/dae0999028d5-uploaded-clip-editor.md`; Engram tools were unavailable.

### Commit blocked by mandatory junction cleanup

All implementation and verification work is complete for steps 1–3, but **no
commit was created**. The exact required `cmd /c rmdir tools\smoke\node_modules`
was refused by automatic unattended approval as a destructive delete outside
scratch. The task explicitly requires removal before committing, so the
candidate's source/doc/test files are staged by explicit path and left for Dave.
The junction remains; no alternate deletion/rename path was attempted.

From this worktree in a fresh human-started chat, remove only the junction with
the command above, verify `Test-Path tools/smoke/node_modules` returns False,
then commit the staged candidate, for example:

```powershell
git diff --cached --check
git commit -m "fix(studio): open uploaded videos as named clip scenes"
```

Do not stage the eight regenerated PNGs. Do not merge, push or restart from
this job. Branch: `clayrune/agent/dae0999028d5`.
