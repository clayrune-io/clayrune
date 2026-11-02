# Studio storyboard preview

Standalone and campaign video editors show a black 16:9 preview stage above the
timeline. The centered **Play** button is enabled when real scenes exist. There
is no separate opener, pricing hint, or Close preview control. This is a branch
candidate; it has not been merged or deployed.

## Playback contract

- `static/js/desk-v1-studio-preview.js` owns the stage, scene snapshot, clock,
  media and lifecycle; its CSS is `static/css/desk-v1-studio-preview.css`.
  Studio supplies markup, mount and refresh after timeline/scene repaint.
- Snapshot order is `videoDetail[familyId].scenes` array order, excluding
  `placeholder` scenes. Length is `durationSec`, including fractional lengths.
  Clips begin at zero because the editor only trims the right edge. Pictures
  hold for their scene length; missing media shows scene title/instructions.
- Idle/reset shows black. Play hides the overlay and exposes Pause below;
  pause/end brings back Play/Replay. Native buttons support Tab and Space/Enter,
  with focus transferred between Play and Pause. Seek and elapsed/total time
  remain below. Seeking displays the selected scene.
- Empty boards keep the black stage, disable Play and seek, and display
  ?Add a scene to preview? inside it. Examples never enable playback.
- Buffering holds the clock. Legacy scenes longer than their clip hold the last
  frame. Active tiles receive `aria-current` and follow horizontal timeline
  scrolling. Edits pause/reset playback and refresh replaced tiles.
- Play commits pending editor text through the existing save path. Playback
  makes no API calls. Media failures show a message and scene card; rejected
  playback pauses with a retry explanation. Navigation/remount releases sources,
  pauses audio and cancels animation frames via a removal observer.
- The independent [local MP4 export](STUDIO_EXPORT.md) sits beside seek/time.
  The existing paid Render panel retains its own estimates and approval gates;
  neither Preview nor Export calls it.

## Verification

`tools/smoke/desk-studio-preview.mjs` serves the real worktree HTML/JS/CSS with a
revision-guarded fake Desk API, byte-ranged MP4 and PNG. At 1440 and 390 it checks
campaign/standalone playback, native decoding, trimmed lengths, scene order,
picture hold time, keyboard activation/focus, pause/seek/replay, highlight,
persisted reorder/retime, refresh, empty/example boards, loading/failed media,
cleanup and viewport bounds. Preview adds no paid or save calls.

Screenshots of idle and playing states are written under
`_scratch/desk-studio-export/preview-{idle,playing}-{campaign,studio}-{1440,390}.png`.
The export document records the complete verification set. Native phone WebView
and arbitrary user codecs remain outside the Chromium checks.
