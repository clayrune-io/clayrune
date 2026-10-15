# Picture viewer (MC-1100)

`static/js/image-viewer.js` owns the picture window; `image-viewer-gestures.js`
owns the natural-pixel transform and input gestures; `static/css/image-viewer.css`
owns its layout. `static/index.html` loads the entry module and stylesheet.
Diagram rendering and gestures remain in `mermaid.js`. Both viewers share the
`window._viewerWindows` data bridge (stack and z-index) so only the front window
handles keyboard shortcuts. No dependency or backend route was added.

## Entry point and navigation scope

```js
window._openImageViewer(src); // one picture; existing src-only calls still work
window._openImageViewer(src, { images: [src, otherSrc] });
window._openImageViewer(src, { images: [{ src, name: 'photo.png' }, otherSrc] });
window._openImageViewer(img.src, { source: img }); // chat thumbnail handler
```

The optional source finds an explicit `[data-image-gallery]` or contiguous
ordinary `.agent-line` siblings, bounded by prompt/tool/status lines. A prompt's
attachments stay inside that prompt. This follows the chat renderer's existing
one-div-per-line representation. A source without a message/gallery container
opens one picture. Media passes its currently filtered images; the Desk library
passes the shelf's images, excluding videos. There is no directory enumeration
or `/api/serve-image/siblings` call. Navigation wraps and resets rotation/fit.

Windows/Linux use dark Photos-style title/toolbar/status bars. Mac detection uses
`navigator.userAgentData.platform`, then `navigator.platform`. Tests can force
`{platform: 'mac'}` or `window.__imageViewerPlatform = 'mac'` (`'windows'` forces
the other layout). The override affects chrome only, not capabilities.

## Controls and file behavior

- Wheel/pinch zoom anchors the same natural pixel under the cursor/midpoint.
  Mouse drag pans; one-finger drag pans after zoom. At fit, horizontal swipes
  navigate. Double-click/double-tap toggles fit and 100%. Fit never enlarges a
  small picture; actual size means one source pixel per CSS pixel.
- Fit/actual, percentage presets and slider use the same scale. `+`, `-`, `0`
  (fit), `1` (actual) and arrow keys work while a viewer is active. Text inputs
  retain editing keys. Esc closes the front viewer, leaving other windows open.
- Rotate is view-only, clockwise in 90-degree steps. Copy encodes the current
  orientation to PNG; print prepares that orientation in a temporary frame.
  Save downloads the original blob and filename, preserving animated formats.
  There is no delete action or file-writing backend.
- Info shows MIME format, decoded pixel dimensions and blob byte size. Failed
  loads and unavailable metadata are explicit; navigation aborts prior metadata
  fetches and ignores late results. Image filenames are written via textContent.
- Copy requires ClipboardItem/clipboard.write and browser permission; print
  uses the browser's print UI. Unsupported/denied actions show a status message.
  Fullscreen uses the Fullscreen API where supported, otherwise viewport maximize.
- White/checker/dark canvas preference retains `mc_img_viewer_bg`. Desktop
  windows drag by their filename bar, resize by the corner, and minimize into
  the existing tray. At 960px and below the viewer fills the available viewport,
  with tools above the canvas and zoom/status controls pinned below it.

## Verification

Run from the checkout root with the existing smoke dependencies available:

```sh
node tools/smoke/image-viewer.mjs
node tools/smoke/desk-v1-library.mjs
node tools/smoke/boot-smoke.mjs
```

The picture smoke loads actual worktree modules/styles against hermetic image
fixtures at 1440×900 and 390×900 for both forced platforms. It checks gestures,
navigation, rotation, metadata, controls' geometry, real chat/prompt/media/Desk
callers, window controls, fullscreen, failures, focus, mixed diagram/picture
stacking and document-listener cleanup. Clipboard and print sinks are stubbed
so verification does not write to the OS clipboard or open a real print dialog;
the prepared rotated PNG dimensions are checked. Native OS permissions, printers,
real Mac Safari and physical-phone gestures need manual testing.

Screenshots: `_scratch/mc1100/{windows,mac}-{1440,390}.png` and matching `-info.png`.
The legacy gesture/narrow-toolbar smoke filenames forward to the same matrix.
The Desk smoke writes its captures under `_scratch/mc1100/library/`.

### Branch validation, 2026-10-15

| Command from checkout root | Result |
| --- | --- |
| `node tools/smoke/image-viewer.mjs` | rc=0; 258 checks, four platform/width combinations |
| `node tools/smoke/desk-v1-library.mjs` | rc=0; live 1440/390 and demo |
| `node tools/smoke/boot-smoke.mjs` | rc=0; seven boot scenarios and all guards |
| `node tools/smoke/provider-neutral-auth.mjs` | rc=0 |
| `node tools/smoke/provider-neutral-rail-cache.mjs` | rc=0 |
| `node tools/smoke/agent-stream-dedupe.mjs` | rc=0 |
| `node tools/smoke/delivery-status-visibility.mjs` | rc=0 |
| `node tools/smoke/browser-paste.mjs` | rc=0 |
| `npm.cmd --prefix tools/smoke test` | rc=1 at the pre-existing inline-handler scanner findings below |
| `git diff --check` | rc=0 |

The scanner reports four identical findings on both an archive of the branch's
unchanged HEAD and the final working tree: `active` at `render-core.js:733`,
`scheduler.js:617`, `workflow-builder.js:2867`, and `pending` at
`render-core.js:1007`. It matches quoted template text against the module-private
`active`/`pending` names from `browser-input-timing.js`. No scanner or unrelated
module change is included. The remaining npm checks were run separately, as
listed above; the overall npm command remains failing. All changed JS and smoke
modules also passed `node --check`. No merge, push or server restart was performed.
