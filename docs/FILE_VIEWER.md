# Agent chat file links

Chat `[file:<absolute path>]` and `[file:<absolute path>|Label]` links use a
real `/api/serve-file` URL. The full path remains visible beside a label.

- Text and source formats open a managed, read-only File viewer. Markdown uses
  the same rich-line and pipe-table renderer as plan files. Other text uses an
  escaped, wrapping preformatted view. The header always shows the absolute
  path and an explicit Download link.
- Images, PDF, audio and video use `inline=1` and open in a new tab for the
  browser's native viewer. The existing server HTML/SVG inline restrictions
  still apply; no backend change is included.
- Unknown extensions keep the attachment/download URL. Extensionless files
  attempt a preview, then offer Download if their contents are binary or use an
  unsupported encoding. UTF-8 and BOM-marked UTF-16 text are supported.
- Text and media hrefs include `inline=1` so middle-click, modified clicks,
  long-press and Copy link retain ordinary browser behavior. Only an unmodified
  primary click on a text link is intercepted.

## Ownership and limits

`static/js/file-viewer.js` owns classification, delegated clicks, managed-modal
creation, bounded reads and error states. `static/css/file-viewer.css` owns the
responsive layout. `static/js/markdown-document.js` extracts the existing plan
render loop, exposed as `window.renderMarkdownDocument`; both viewers call it.
`rich-text.js` checks file markers before image paths so a PNG file marker
remains a link rather than an embedded thumbnail. Bare image paths still embed.

Preview reads refuse advertised lengths over 2 MiB and cancel streams once
decoded response bytes cross that cap, even without Content-Length. No partial
preview is rendered. Unsupported/binary content, HTTP errors and network errors
are displayed in the modal; Download stays available. Reads are authenticated
same-origin fetches against the existing route and retain its path/secret
allowlist. File contents are escaped before display, never injected as HTML.

The viewer uses the existing modal registry (focus, close, minimize/restore,
desktop resizing and mobile surface handling). Reopening a path focuses its
existing window. The path wraps at narrow widths without ellipsis. Changes to
shared instruction files, README, or agent rules are unnecessary: this adds a
chat UI affordance, not an agent workflow or install change.

## Verification

Run from `tools/smoke` with the main checkout's existing dependencies linked:

```text
node file-viewer.mjs
npm test
```

The file-viewer smoke serves the real SPA and real modules against hermetic
fixtures. It covers markdown/tables, exact paths with spaces/quotes/ampersands,
escaped HTML and code, native media URLs, binary downloads, extensionless text
and binary refusal, UTF-16, HTTP errors, declared and streamed size caps,
explicit Download, modified clicks, shared plan rendering and viewport bounds
at 1440 and 390 pixels. Screenshots: `_scratch/file_viewer/1440.png` and
`_scratch/file_viewer/390.png`. The default npm suite includes this smoke and
the full boot scenarios. Native phone OS viewers are delegated to the browser;
physical-device file handling is not exercised by the hermetic smoke.

Delivery scope: branch-only; no merge, push, server restart or deployment.

Validated 2026-10-16: dedicated smoke rc=0 (62 checks); full `npm test` rc=0,
including all seven boot scenarios and dispatch/other boot guards. Desktop
and mobile screenshots were visually inspected. The existing node_modules
junction was used; no dependency or browser installation was performed.
