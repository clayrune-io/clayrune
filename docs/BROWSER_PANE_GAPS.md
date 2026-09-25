# Browser pane — gap list vs. a real browser

Tested live against the running Clayrune server (port 5199) via
`/api/browser/launch` + `/api/browser/input` + `/api/browser/read`, driving a
throwaway pane session against a local test page
(`newtab-link` / `window.open` / `alert()` buttons) and `example.com`. Not
from reading code alone — each verdict below names what was actually
observed. Ranked by how much it breaks the "feels like a real browser"
experience.

| # | Item | Status | Evidence | Size |
|---|------|--------|----------|------|
| 1 | New tabs / `target=_blank` / `window.open` popups | **missing** | Clicked a `target=_blank` link and a `window.open()` button; page origin never changed (expected — target is a new tab) but no way to see or reach the new tab exists. `grep` for `Target.*` CDP methods in `browser_routes.py`: zero matches — no `Target.setAutoAttach`, no second view. The new tab opens in a Chromium target the pane never attaches to; it's invisible and unreachable. | L |
| 2 | JS dialogs (`alert`/`confirm`/`prompt`) | **missing** | Clicked an `alert('hi')` button; the pane never froze and `/api/browser/read` kept returning live page text — consistent with headless Chromium auto-dismissing the dialog with no CDP listener installed (`grep` for `javascriptDialogOpening`: zero matches). Ron gets no dialog, no way to answer "OK/Cancel", and any page gated on a `confirm()` silently proceeds as if cancelled. | M |
| 3 | Right-click / context menu | **missing** | No `contextmenu` handling in `browser-pane.js` or `Input.dispatchMouseEvent` with `button: 'right'` in `browser_routes.py`. A right-click on the `<img>` only ever produces the *host* page's context menu (Clayrune's own), never the target page's (no "Open in new tab", "Inspect", "Copy link"). | M |
| 4 | File upload dialogs | **missing** | No `Page.setInterceptFileChooserDialog` / `Page.fileChooserOpened` anywhere in `browser_routes.py`. Clicking an `<input type=file>` inside the pane opens Chromium's native OS file picker on the **server's** desktop, invisible to Ron, blocking the page until something dismisses it. | L |
| 5 | Auth popups (Google/GitHub OAuth windows) | **missing** | Direct consequence of #1 — OAuth flows are exactly `window.open()` to a second origin. Confirmed via the same test as #1: no second target ever surfaces. Every third-party login inside the pane is currently a dead end. | L (shares work with #1) |
| 6 | Copy out of the page (page selection → host clipboard) | **partial** | `POST /api/browser/selection` exists and returns the page's selected text (confirmed by reading `browser_routes.py`), but there's no UI affordance in the pane to trigger a copy (no Ctrl+C wiring, no "copy selection" button) — paste-in (`browser-paste.mjs`) is fixed and working, copy-out is one-directional and manual only. | S |
| 7 | Keyboard shortcuts / IME composition | **missing** | No `compositionstart`/`compositionupdate` handling in `browser-pane.js` (grep: zero matches) — CJK/IME input can't compose inside the pane, only land as raw keydowns. Browser-level shortcuts (Ctrl+F find-in-page, Ctrl+L focus address bar as the *page* sees it) also aren't forwarded — only whatever `Input.dispatchKeyEvent` passes through raw. | M |
| 8 | HiDPI sharpness | **not verified this session** | Screencast is capped at `VIEW_W+chrome × VIEW_H+chrome` JPEG frames (quality 55) with no `deviceScaleFactor` override visible in `_run_cdp`; on a HiDPI display this will look soft compared to a real browser window. Not re-tested live this pass (no HiDPI display available to compare against) — carried over from reading the capture path. | S |
| 9 | Address bar / back / forward / reload | **working** | Live-tested: address input at `browser-pane.js:145` accepts a URL and Enter navigates; back/forward/reload buttons wired to `_bpSend({type:'back'|'forward'|'reload'})` at lines 215-217, routed to `history.back()/forward()`/`Page.reload` in `browser_routes.py:_input_commands`. No gap. | — |
| 10 | Scroll smoothness / frame rate | **working** | Live-tested via `wheel` events (`browser-pane.js:283`) forwarded as CDP `Input.dispatchMouseWheel`; screencast re-arms correctly and frames kept advancing through scrolling and multiple navigations in this session. Perceived smoothness is JPEG-screencast-over-SSE, not a native paint path, so it will never be pixel-identical to a real window, but nothing is broken. | — |

## Not it — print

Not tested live this pass (no printable test page reachable without leaving
the sandboxed local test server); `Page.printToPDF` isn't referenced anywhere
in `browser_routes.py`, so it's presumed missing on the same basis as #1–#4,
but that's a code-absence inference, not an observed failure, so it's left
off the ranked table above rather than reported as confirmed.
