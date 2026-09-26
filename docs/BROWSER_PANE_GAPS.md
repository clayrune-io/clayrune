# Browser pane — gap list vs. a real browser

## 2026-09-26: popup completion follow-up (MC-976)

The older gap table below is the pre-tabs baseline, not current status.
Popup tabs now attach to the original Chromium target. No replacement
window is created by the pane. `Target.targetDestroyed` and
`Target.detachedFromTarget` remove closed tabs and return focus to their
opener (`_handle_target_closed` in `mc/blueprints/browser_routes.py`). The
frontend renders that tab list from SSE (`static/js/browser-pane.js`).

**Confirmed defects and fixes:**

- Opening a second popup forcibly closed an existing live sibling. The
  pruning introduced in merge `748be4f` treated shared `openerId` plus
  `canAccessOpener` (or a blank URL) as proof of an abandoned attempt. It
  is not: independent windows and auth helper windows can share an opener.
  Removed automatic sibling pruning. Chromium still reuses named windows,
  pages can close their own windows, and users can close pane tabs.
- Returning to an opener without navigation left `session['live_url']`
  pointing to the popup. Fresh opener frames then carried the wrong address
  in SSE. `_switch_active_tab` now restores the selected tab's URL, including
  `about:blank` when appropriate.

**Reproduction and validation:**

`python tools/smoke/browser_pane_handoff.py` runs the real blueprint and
Chromium in an explicitly ephemeral profile against a local two-origin
fixture. On the pre-fix code, it passed the ordinary postMessage/self-close
flow but failed with `opening a sibling destroyed the first live popup`.
It also observed the popup address on fresh opener frames. With the fixes:

- Cross-origin postMessage and acknowledgement arrive; `window.close()`
  removes the popup, focus returns, and fresh opener frames arrive.
- A cross-origin iframe opens its own popup, forwards its result, and
  navigates the parent across origins to a `COOP: same-origin` page.
- Concurrent popups survive; the first can still deliver its result and
  close without destroying its independent sibling.
- `python -m pytest tests/test_browser_routes.py -o addopts='' -q`:
  **157 passed**. The obsolete tests requiring destructive sibling pruning
  were replaced by the real-browser overlap regression.

**Limit:** this does not establish the cause of the reported blank Google
popup after LinkedIn authentication. Authentication reportedly succeeded
and a manual LinkedIn refresh revealed it. The ordinary and iframe handoff
worked before these fixes. No authenticated Google flow was executed, no
existing profile was inspected or changed, and this local fixture is not a
substitute for a successful GSI sign-in test. Google's
[GSI setup documentation](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid#cross_origin_opener_policy)
also identifies COOP as a possible communication failure; that is a
provider/site hypothesis here, not a measured diagnosis. Do not bypass
COOP or force reloads/close windows based on a blank screen.

For a later user-authorized integration retry: load the patched backend,
close and reopen the pane normally with the same named profile (never
delete it). Existing authentication should be checked by visiting LinkedIn,
without signing out just to exercise this fix. On the next legitimate
Google login, expect the popup to close and its opener to regain focus. If
it remains blank, capture console/network/lifecycle evidence during that
attempt before refreshing; the provider-specific failure remains open.

## Original baseline (historical)

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
