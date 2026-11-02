# Studio storyboard preview

The standalone and campaign video editor can play its current scene arrangement
locally. This is a branch candidate, not a deployed finished-video renderer.

`static/js/desk-v1-studio-preview.js` owns the Preview button, player, snapshot,
clock, media and lifecycle. Its CSS lives in
`static/css/desk-v1-studio-preview.css`; `static/index.html` loads both. Studio
adds three bridge lines: markup, mount (current real scenes plus save-open-editor
callback), and refresh when scenes are repainted. No server or dependency changes.

## Playback contract

- Opening reads `videoDetail[familyId].scenes` through the bridge. Array order
  is timeline order. `placeholder` scenes never enter the snapshot.
- Scene length is `durationSec`, including fractional picture durations.
  The current timebar only trims the right edge and writes this field; there is
  no clip-start/in-point field. Clips therefore begin at source zero. Media uses
  `picture.src` or `picture.play`; no picture means a title/instructions card.
- Play/pause and the range input control the same clock. Media buffering holds
  the clock; an older scene longer than its source holds the final frame for
  the remainder. The active timeline tile has `aria-current` and a scoped outline.
- Reopening takes a new snapshot. Scene edits pause/reset the open player.
  Closing, Escape, remount and navigation pause/release media and cancel RAF.
  A DOM-removal observer also cleans up a detached editor without a global
  navigation hook. Load failures show a message and the scene card; rejected
  clip playback pauses with a retry explanation.
- The preview module makes no API call. Opening first commits any pending scene
  editor text through Studio's existing save path; playback itself never saves.
  The existing render panel still independently
  loads history/estimates on editor mount. Preview neither triggers nor changes
  that panel, costs, human gates, or the refusal of uploaded clips in paid renders.
- This shows the existing scenes, not generated output, transitions, or an export.
  Compatibility is the current browser's image/video decoding support.

## Verification

Run from `tools/smoke` with the existing main-checkout dependencies linked into
the worktree (remove that junction before committing; do not install again):

```powershell
node desk-studio-preview.mjs
node desk-studio-timeline.mjs
node desk-studio-uploaded-clips.mjs
node desk-v1-live-storyboard.mjs
node boot-smoke.mjs
```

The preview smoke serves this worktree's real HTML/JS/CSS, a revision-guarded
fake Desk API, real MP4 bytes with byte-range responses and a decoded PNG. At
1440 and 390 it covers campaign and standalone saved boards, native clip decoding,
trimmed playback, image/card scene order, picture hold time, pause, seek, elapsed
total, active timeline tile, persisted reorder/retime and reopen, empty board,
example exclusion, navigation cleanup, viewport bounds, and zero additional paid
or save requests during preview. It writes screenshots to
`_scratch/desk-studio-preview/{campaign,studio}-{1440,390}.png`.

No live data, paid engine, named browser profile, authentication, merge, push or
server restart is needed or performed by these checks. Native phone WebView and
every user codec remain unverified by the headless Chromium runs.
