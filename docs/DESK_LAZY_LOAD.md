# The Desk loads in the background, not at dashboard boot (MC-1205, FE21/FE22)

## What loads when

| File | Where it is loaded | Why |
|---|---|---|
| `static/js/desk-v1-loading.js` + `static/css/desk-v1-loading.css` | `index.html` tags (boot) | Paints the Desk home page's loading frame before anything else of the Desk has arrived. No imports, no dependency on the rest of the Desk. |
| `static/js/desk-load.js` | `index.html` tag (boot) | The loader. Waits for the dashboard's `mc-boot-complete` event, then for an idle moment (`requestIdleCallback`, 4 s timeout; 1.5 s `setTimeout` where it does not exist), then imports the entry. Owns the one shared promise (`DeskLoad.ready()`), and `DeskLoad.open()`. |
| `static/js/desk-v1-entry.js` | `import()` from `desk-load.js` | The ONE list of the Desk's scripts (`MODULES`) and stylesheets (`STYLES`). Imports the scripts strictly in list order, which is the order the old `<script>` tags had. |
| everything else `desk-v1-*` | the entry | Not in `index.html`. |

**A new Desk script or stylesheet goes in `desk-v1-entry.js`, never in `index.html`.**
`tests/test_desk_entry.py` fails if a Desk tag reappears in `index.html`, if a file on disk is in neither list, or if a module would run twice. `tests/test_desk_v1_flag.py` pins the store-before-shell order.

## Opening the Desk

`openDesk()` (`desk-open.js`) calls `DeskLoad.open()`.

* Desk already loaded: `deskV1Open()`, same JS turn.
* Not yet loaded: a Desk window (the `__desk` modal) opens in the same JS turn with the home page's loading frame (`DeskV1Loading.preloadHTML`), and swaps to the real Desk when the single load settles. A second click does not stack a second window and does not start a second load. Closing the window before the load finishes cancels the swap, not the load.
* The load failed: the same window shows the error with **Try again** (no endless spinner).

Once the real Desk is open, its own gate (`desk-v1-shell.js` `_renderStoreGate`) paints the same frame, with placeholder rows, until the workspace read answers; a failed read shows its error inside that frame with Try again.

## Non-Desk code that needs Desk code

`await window.DeskLoad.ready()` first. Do not `import` a Desk file from a boot-time file: it drags that file (and what it imports) back onto the boot path.
Current callers: `vault-unlock-popup.js` (`DeskV1VaultGate`), `secrets-panel.js` (`DeskV1Engines`), `browser-accounts.js` (`DeskV1ConnectBrowserCheck`, `SigninMethod`).

## The version token (`?v=`)

`server.py` `index()` rewrites `src`/`href` of every `/static/*.js|css` **in `index.html`** to carry `?v=<asset version>`, and `add_static_cache_headers` serves a request with a `v` as `immutable` for a year. It cannot see a URL built in JavaScript, and a relative `import './x.js'` drops the importer's query. So:

* `desk-load.js` reads the token off its own (stamped) URL and imports the entry as `/static/js/desk-v1-entry.js?v=<token>`.
* The entry reads the token off its own URL and requests every script (`import('/static/js/<name>.js?v=<token>')`) and stylesheet with it.
* A release changes the token, so every Desk file is a new URL and no stale module can be served. A bare URL is served `no-cache`, so it is never stale either, only revalidated.

## One URL per module (FE22)

A module that is both loaded by the entry (versioned URL) and statically imported by another file (bare URL) runs twice. The entry's `BARE` set lists the modules that some other file imports by a relative path; they are requested by that bare URL so there is one instance. `test_no_module_runs_twice_fe22` computes the set and fails if it is wrong.

## Try again after a failed load

A browser remembers a failed module fetch per URL. The retry imports the entry as `...?v=<token>&try=N`, and the entry (which keeps what already ran in `window.__deskV1Entry`) re-requests only what failed, under `&try=N`.

## Smokes

`tools/smoke/desk-v1-first-open.mjs` (home frame in the same JS turn, fills in, one slow read does not hold the others, error + Try again) and `tools/smoke/desk-v1-lazy-load.mjs` (zero Desk requests before boot completes, background load, every Desk file requested once and versioned, warm open, cold open, failed load + Try again, non-Desk caller).
