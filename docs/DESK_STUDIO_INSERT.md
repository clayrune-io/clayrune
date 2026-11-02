# Studio timeline insertion (MC-1145)

Campaign and standalone storyboards show a library-picker **+** at every real
scene boundary, including the start/end (one control when empty). Native library
and computer-file drags show an accent line at the chosen boundary. Hovering a
tile chooses its nearest boundary; hovering a **+** explicitly selects that gap.
The drop and marker use the same boundary resolver and `beforeId`.

`static/js/desk-v1-studio-insert.js` owns the controls, layout, drag marker and
drop handlers; `static/css/desk-v1-studio-insert.css` owns their appearance.
`DeskV1StudioTimeline.wire` passes its existing library-material command and
file-upload bridge to the module. No new Studio monolith code or API is needed.
Both assets load through `static/index.html`.

The control lane follows tile geometry below the strip instead of inserting
flex children between tiles. Scene widths and ruler positions remain exact at
all zoom levels. Buttons have 44px targets; nearby controls occupy additional
rows rather than overlap. ResizeObserver updates placement after zoom, resize
or trim, and disconnects when the old strip detaches. Pointer reorder and right
trim remain on the original tiles. Unrelated native drag types are ignored.

Buttons use native Tab/Enter/Space handling and descriptive accessible names.
The existing picker returns focus on cancellation; after insertion replaces the
timeline, focus moves to the replacement control for the same boundary. Native
drag cancellation, leaving the timeline, dropping, and Escape clear the marker.
Escape during an active material drag is consumed before the editor shortcut.

Library picks reuse the timeline's undoable insertion command, including real
clip duration decoding. OS file drops reuse the existing multipart upload and
save command. The Studio repaint still calls `DeskV1StudioPreview.refresh`.
`desk-v1-studio-timebar.js` ignores queued scroll events from detached timelines:
otherwise an old listener can paint replacement tiles against its old scene
array after keyboard focus, insertion or Undo.

## Validation

The offline Chromium harness loads this worktree's actual HTML, JS and CSS.
Desk API responses are mocked; clip decoding is real. It does not verify a
deployed server, actual OS desktop dragging or paid engines.

- `node tools/smoke/desk-studio-insert.mjs`: campaign/standalone at 1440 and 390;
  real Tab/Enter/Space picker inserts at start/middle/end; real native library
  drag and marker; decoded clip insertion and Preview refresh after Undo;
  marker/drop geometry; cancellation; unrelated drag types; synthetic OS
  file/batch drag, multipart upload, PUT and Undo; empty board; non-overlapping
  44px controls when zoomed out; no page errors or horizontal page overflow.
- Regression commands: `node tools/smoke/desk-studio-preview.mjs`,
  `node tools/smoke/desk-studio-timeline.mjs`,
  `node tools/smoke/desk-studio-uploaded-clips.mjs`,
  `node tools/smoke/desk-v1-live-storyboard.mjs`,
  `node tools/smoke/boot-smoke.mjs`.
- Screenshots: `_scratch/desk-studio-insert/{gaps,marker}-{campaign,studio}-{1440,390}.png`.

Final run on 2026-11-01 (all against this worktree):

| Smoke | Exit code |
| --- | --- |
| desk-studio-insert | 0 |
| desk-studio-preview | 0 |
| desk-studio-timeline | 0 |
| desk-studio-uploaded-clips | 0 |
| desk-v1-live-storyboard | 0 |
| boot-smoke | 0 |

No merge, push or server restart is part of this branch task.
