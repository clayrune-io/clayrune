# Why Clayrune is slow to load — measured investigation

> **STATUS 2026-08-06 (later the same day): Tier 1 is shipped.** All six Tier-1
> items were implemented and re-measured against the live server. Results, and
> the one open question this investigation could not settle, are in
> **[Tier 1 — shipped and re-measured](#tier-1--shipped-and-re-measured)** at the
> bottom. The analysis below is preserved as written, in the past tense it was
> measured in; Tiers 2 and 3 are still open.

**2026-08-06. Inspection only, no changes made.** Every number here was measured
on this machine today against the live server, with the harness noted. Where I
could not settle a question, it says so.

Tools used (read-only, left in `tools/smoke/_perf_probe*.mjs`):

| probe | what it measures |
|---|---|
| `_perf_probe.mjs` | boot timeline + per-phase requests + re-render cost |
| `_perf_probe2.mjs` | full request census of a cold boot, grouped |
| `_perf_probe3.mjs` | does modal cost scale with a project's data? |
| `_perf_probe4.mjs` | what is actually in the modal DOM |
| `_perf_probe5–8.mjs`| cold vs warm reload, cache behaviour |

---

## Headline

**The server is not slow. The client is.**

Server boot, from its own `[boot]` phase log: **0.24 s – 2.62 s** to ready.
Every API endpoint answers in **under 100 ms**. Meanwhile a single page load
takes **7–8 seconds** and issues **420 HTTP requests / ~6 MB**.

And Ron's instinct is right: the cost **scales with accumulated data**, close to
linearly. Measured across six real projects, opening one project modal:

| project | payload | modal DOM nodes | one re-render |
|---|---|---|---|
| find_ron_a_job | 146 KB | 429 | 4.4 ms |
| clayrune_website | 630 KB | 1,351 | 10.7 ms |
| fl3_v2 | 1.4 MB | 3,033 | 15.6 ms |
| daytrading | 1.4 MB | 4,723 | 28.8 ms |
| day_trading_engulfing_scanner | 5.5 MB | 7,975 | 43.8 ms |
| **mission_control** | **3.7 MB** | **8,268** | **41.1 ms** |

19x the DOM and 10x the re-render cost between the lightest and heaviest
project. Nothing about this is a fixed cost you can ignore as the app ages.

---

## Root cause 1 — 84% of every page load is third-party CDN

Cold boot census: **420 requests, 6.9 MB.** Of that:

| origin | requests | bytes |
|---|---|---|
| **esm.sh** | **327** | **2.04 MB** |
| cdn.jsdelivr.net | 21 | 0.96 MB |
| fonts.gstatic / googleapis | 5 | 0.21 MB |
| localhost:5199 (the actual app) | 67 | 3.71 MB |

**353 of 420 requests reach the public internet before Clayrune renders.** They
come from four eager loads in `static/index.html`'s `<head>`:

- L106–108 `xterm.js` + `addon-fit` + its CSS — render-blocking `<script src>`,
  no `defer`. Used only by the terminal pop-out.
- L111 `qrcodejs` — render-blocking. Used only by Settings → Pair Mobile App.
- L117 `mermaid@11` ESM — used only if an agent emits a ```mermaid block.
- **L197–198 `@excalidraw/excalidraw` + `mermaid-to-excalidraw` from esm.sh —
  this is the 327 requests.** esm.sh serves every transitive dependency as its
  own module file, so one `import()` fans out into hundreds of round trips.
  Used only to make diagrams look nicer, with a documented Mermaid fallback.

The comment on L195 says it is "loaded in the background so it doesn't block
initial mermaid renders" — and it doesn't block *mermaid*, but it absolutely
competes for connections, CPU and main-thread module evaluation during boot.

**This is also a hard external dependency.** On a slow, filtered, captive-portal
or offline network, boot degrades to whatever esm.sh does. A local-first tool
should not need the public internet to draw its dashboard.

**Unsettled, and it matters:** I could not determine whether a real browser
warm-caches these 353 modules. My Playwright harness never cached *anything* —
proven by re-fetching `fonts.gstatic.com` (1-year `max-age`) on reload — so the
"6 MB every reload" figure is the **cold** case and I will not claim it for a
warm one. **To settle it:** open DevTools → Network on a normal reload and read
the Size column. `(disk cache)` on the esm.sh rows = warm loads are fine and
this only hurts first visits and post-update loads. Real transfer sizes = it
hurts every single reload.

---

## Root cause 2 — `/api/projects` is 91% payload nobody renders

`/api/projects` is **789 KB**. Composition:

| key | bytes | share |
|---|---|---|
| **backlog** | **748.7 KB** | **91.3%** |
| activity_log | 60.7 KB | 7.4% |
| everything else | ~10 KB | 1.3% |

mission_control alone contributes 438 KB of that, from **892 backlog items**
(856 of them `done`).

`api_projects()` (`mc/blueprints/project_routes.py:304`) already strips note and
attachment *bodies* down to counts — someone hit this before. But the **item
bodies themselves are still shipped**, and the dashboard reads exactly three
things from `p.backlog` (`static/js/render-core.js:124, 266, 318`):

- `backlog.filter(i => i.status === 'open').length` — the badge
- `open[0].text.slice(0, 52)` — the next-action line
- `backlog.filter(i => i.status === 'done').length` — a modal counter

Roughly **60 bytes of information carried in 749 KB**, on boot and again **every
30 seconds** (`static/index.html:2808`).

Server-side, `load_projects()` re-reads and re-parses **every project JSON from
disk on every call** — ~5 MB of JSON, no cache, including a 2.2 MB
`mission_control.json`. It still returns in 58 ms because the OS page cache is
warm; after a machine reboot it will not be.

---

## Root cause 3 — the same data is fetched two to seven times

Measured on one boot + one modal open:

| endpoint | times fetched | each | wasted |
|---|---|---|---|
| `/api/projects` | **2** | 789 KB | 789 KB |
| `/api/project/mission_control/agent/log` | **2** | 1,631 KB | 1,631 KB |
| `/api/project/mission_control/conversations?limit=20` | **7** | 22.5 KB | 135 KB |
| `/assets/claydo-idle.webp` | 2 | 126 KB | 126 KB |

Two distinct bugs:

**(a) No in-flight guard on the lazy loaders.** `fetchProjects()` has one
(`_fetchProjectsInFlight`, `static/index.html:1083`). `loadConversations()` and
`loadAgentLog()` (`static/js/agent-log.js:74, 98`) do not. Their guard is
`if (!conversationsCache[p.id])`, and the cache is only written *after* the
fetch resolves — so every render during the in-flight window fires another
identical request. Worse, each completion calls `refreshModal()`, which
re-renders, which re-enters the guard. That render→fetch→render→fetch cascade is
where 7x comes from.

**(b) `_resyncOpenModalsFromServer` fires `fetchProjects()` at boot.** It is
bound to `visibilitychange`, `focus` **and `pageshow`** (`static/index.html:3335`),
and `pageshow` always fires on load. It has a 1,200 ms debounce but no
"nothing is open, skip it" early return — so it runs even with zero modals open.
Same handler runs **every time you alt-tab back to the window**, costing another
789 KB + a heartbeat + a status sweep on each return to the tab.

---

## Root cause 4 — the modal DOM is mostly invisible history, rebuilt constantly

The mission_control modal: **8,270 elements, 1,489 KB of innerHTML.**

| subtree | elements | visible? |
|---|---|---|
| **Agent Log tab** | **6,001** | **no — inactive tab** |
| Agent panel (the active tab) | 1,356 | yes |
| — of which the conversation rail | 1,153 | yes |
| Backlog tab | 630 | no — inactive tab |

**73% of the modal is the Agent Log tab, which is not the tab you are looking
at.** It renders **500 log rows x ~12 elements each** — every row carrying a
task, a summary, a timestamp, a session id, a copy button, *and its own
`<input>` + send button* (`agent-log-continue-input`, `btn-send`). 500 live text
inputs built into the DOM for a panel that is closed.

Add the Backlog tab and **~84% of the modal DOM is content behind an inactive
tab.**

Now the multiplier: `refreshModalById()` (`static/index.html:1743`) does a full
`content.innerHTML = modalContentHTML(p)` rebuild, wrapped in elaborate
save/restore gymnastics for scroll, caret, focus, textarea heights and streaming
output nodes — clear evidence of how much this hurts. It costs **41–46 ms** on
mission_control and it fires **on every SSE turn event and every poll tick**.
With two or three heavy modals open that is >100 ms of main-thread block,
repeatedly, forever.

---

## Root cause 5 — the chat rail is fed by the agent log, not by the chat list

This is exactly what Ron noticed, and the mechanism is not what it looks like.

`/api/project/<id>/conversations?limit=20` returns **20** rows. The rail renders
**128**. The other 108 come from `_userInitiatedConvos()`
(`static/js/conversation.js:1424`), which merges the conversation list with
**`agentLogCache[projectId]` — 500 rows** — and regex-filters every one of them
through `_keep()` on each render.

So "trimming the conversations" does nothing. **The rail's real size is the
agent log's size**, capped at 500, and it is re-derived and re-rendered on every
modal refresh.

Underneath, `list_sessions()` (`agent_runtime.py:1042`) `stat()`s every `.jsonl`
in the project's transcript dir — **203 files, 325 MB for mission_control** —
then opens the newest N and JSON-parses **every line of each**. 1.8 GB / 8,088
transcript files exist across all projects. It measures 94 ms warm; cold, off
spinning storage, it will be far worse.

---

## Root cause 6 — smaller, real, cheap to note

- **Duplicate `Date` header on every response.** Verified twice (curl and
  `http.client`). Werkzeug emits one and the response already carries one. A
  malformed header set is exactly the sort of thing that makes a browser refuse
  to cache a response — worth ruling in or out while looking at the cache
  question above.
- **`Cache-Control: no-cache` on all static assets**, even though every URL
  already carries a `?v=<mtime>` cache-buster. The buster alone is sufficient;
  `no-cache` forces a revalidation round trip for 41 JS files + 2 CSS + assets
  on every load, for no benefit.
- **Werkzeug dev server** (`Server: Werkzeug/3.1.8`), single process, threaded,
  `Connection: close`. On localhost the per-request overhead is ~1.7 ms so this
  is not the local bottleneck — **but over the Cloudflare tunnel or from the
  phone, 420 connections with no keep-alive is a completely different story.**
- **`/api/schedules` ships 52 KB** on boot.
- `load_projects()` sorts the project list three times in a row
  (`project_routes.py:213–216`). Harmless, but the first sort is dead.

---

## Ideas — no changes made, ranked by payoff over risk

### Tier 1: big win, low risk, no product decision needed

1. **Trim `backlog` out of `/api/projects`.** Replace with
   `backlog_open_count`, `backlog_done_count`, `next_action_text` (55 chars).
   The modal already lazy-loads the full backlog on open
   (`refreshProjectBacklog`). Turns 789 KB into ~45 KB — **on boot and on every
   30 s poll, for every open tab, forever.** Single highest-value change here.
2. **Make excalidraw/mermaid/xterm/qrcode load on demand.** Import excalidraw
   the first time a diagram actually needs it; load xterm when the terminal
   opens; load qrcode when the pairing panel opens. Removes ~350 requests and
   ~3 MB from a boot that has nothing to do with any of them. Also removes the
   internet dependency from cold start.
3. **Add an in-flight guard to `loadConversations` / `loadAgentLog`**, mirroring
   `_fetchProjectsInFlight`. Kills the 7x and 2x fan-out for a few lines.
4. **Early-return `_resyncOpenModalsFromServer` when no modal is open**, and
   drop the boot-time `pageshow` fire. Saves 789 KB at boot and again on every
   tab focus.
5. **Don't render inactive tabs.** Build the Agent Log and Backlog panels only
   when their tab is selected. **~84% of modal DOM and most of the 41 ms
   re-render, for a change with no behavioural downside.**
6. **Paginate the Agent Log** to ~25 rows with "load more", and stop giving
   every row its own `<input>` + send button — one shared composer, bound on
   click. 6,001 elements becomes a few hundred.

### Tier 2: real wins, need a small decision

7. **Cache `load_projects()`** behind file mtimes instead of re-parsing 5 MB of
   JSON per request.
8. **Cache `list_sessions()`** per (dir, mtime) so the 203-file stat + full JSONL
   parse doesn't re-run on every conversations call.
9. **Stop rebuilding the whole modal.** `refreshModalById` already fights to
   preserve live DOM across an `innerHTML` wipe; targeted updates for the parts
   that actually change (status pill, session list, metrics) would remove both
   the cost and the save/restore machinery.
10. **Serve static assets `immutable, max-age=31536000`** — the `?v=` buster
    already handles invalidation — and fix the duplicate `Date` header.
11. **Consider a real WSGI server** (waitress on Windows) for keep-alive. Matters
    most for tunnel and phone clients, not for localhost.

### Tier 3: Ron's idea, and it is already half-built

> "once we have robust memory, having plethora of chats on the side is no longer
> the best way to handle... it doesn't really matter which chat I choose as long
> as I'm aware which subjects have been discussed."

The data agrees with him, and the replacement **already exists and is already
cheap**:

```
GET /api/project/mission_control/topics
  -> 13 topics, 4,249 bytes, 20 ms
```

`mc/blueprints/topics_routes.py` clusters chats into deduplicated **topics** —
its own docstring says "the unit the owner actually thinks in, not one card per
chat". Compare:

| surface | rows | DOM elements | bytes |
|---|---|---|---|
| conversation rail | 128 | 1,153 | 22.5 KB (x7 fetches) |
| **topics digest** | **13** | ~100 | **4.2 KB** |

So the proposal is not a rewrite, it is a **promotion**: make Topics the default
left-hand surface and demote the raw chat list to a "show all chats" affordance.

Three things to decide before doing it, none blocking:

- **Resume still needs a session id.** Picking a topic has to resolve to *some*
  transcript to continue — newest chat in the topic is the obvious default, but
  the live digest currently reports `sessions: 0` per topic, so that link needs
  checking before it can carry navigation.
- **The rail is fed by the agent log, not the chat list** (root cause 5). Any
  fix that only trims `/conversations` will not change what you see. The 500-row
  agent-log merge is the thing to bound.
- **Topics are Haiku-synthesized and cached** — best-effort by design. Fine as a
  navigation aid; it should degrade to the chat list rather than trap you if a
  synthesis fails.

### What is NOT the problem

Server startup (0.24–2.62 s), API response times (all <100 ms), the dashboard's
own `render()` (1.8 ms), and the number of *projects* (20 projects cost 60 KB of
activity_log between them). **The cost is per-project accumulated history —
backlog items, agent-log rows, transcripts — and the fixed CDN tax.**

---

## Tier 1 — shipped and re-measured

All six items implemented 2026-08-06. Numbers below are the same probes
(`_perf_probe2` / `_perf_probe3` / `_perf_probe4`) re-run against the live
server, **before** the server restart that picks up the Python half — so the
`/api/projects` line is still the OLD 789 KB in the "after" column. Its measured
post-restart size is computed separately and marked.

| measure | before | after | change |
|---|---|---|---|
| cold-boot requests | 420 | **73** | −83% |
| cold-boot bytes | 6,900 KB | **3,137 KB** | −55% |
| requests to the public internet | 353 | **5** (fonts only) | −99% |
| modal DOM (mission_control) | 8,270 elements | **1,613** | −80% |
| modal innerHTML | 1,489 KB | **187 KB** | −87% |
| one modal re-render | 41.1 ms | **13.5 ms** | −67% |
| `/conversations` fetches per boot+open | 7 | **1** | — |
| `/agent/log` fetches per boot+open | 2 | **1** | — |
| `backlog` bytes in `/api/projects` | 687 KB | **6.4 KB** | −99% (needs restart) |

**The open question from Root cause 1 is settled, and the answer is the good
one.** Ron read the Network panel on a real reload: the esm.sh rows show
`(disk cache)` at 0 KB. So a real browser *does* warm-cache those 353 modules —
the "6 MB every reload" figure was my Playwright harness's cold case only, and I
was right not to claim it for a warm one. That re-ranks the CDN work: it was
never a per-reload byte tax, it was a **first-visit, post-update, and
offline/filtered-network** tax, plus ~350 cache lookups and module evaluations
on the main thread during every boot. Still worth removing — a local-first
dashboard should not need the public internet to paint — but it was not the
reload cost it looked like.

### What each item became

1. **`/api/projects` ships a backlog summary, not the backlog.**
   `backlog_open_count` / `backlog_done_count` / `backlog_total_count` /
   `backlog_next_text`; the array is dropped. Measured across all real project
   files: 687 KB → 6.4 KB. Consumers that render item *bodies* already
   lazy-load them per project (project modal, cross-project view); the
   cross-project view now fetches them itself on open, 4 projects at a time,
   filling in progressively. `backlogSummary(p)` in `render-core.js` prefers the
   live array whenever it is loaded and falls back to the counts.
   Guarded by two tests in `tests/test_project_routes.py`.
2. **Diagram / terminal / QR libraries load on first use.**
   `window.ensureDiagramLibs()` (mermaid + the Excalidraw bridge),
   `ensureXterm()`, `ensureQRCode()`. Because Excalidraw is now not already
   warm when the first diagram arrives, the renderer waits for it with a bounded
   8 s timeout — and the bridge fires a new `excalidraw-failed` event on an
   offline network so that wait ends immediately instead of stalling.
3. **In-flight guards on `loadConversations` / `loadAgentLog`**, coalescing on
   the promise so concurrent callers share one request and still await the real
   result. This is what killed the 7× and 2×.
4. **`_resyncOpenModalsFromServer` no longer fires at boot** (`pageshow` is
   honoured only when `event.persisted` — a real bfcache restore), and returns
   early when nothing is open and nothing is streaming, after refreshing the
   grid. The grid refresh on resume is deliberate and kept.
5. **Inactive tabs are not built.** Backlog, Agent Log, Plans, Activity and
   Workflows render only when selected — every tab switch already re-renders the
   modal, so this is invisible. The `agent` panel is deliberately exempt: it
   owns the live streaming nodes `refreshModalById` works to preserve.
6. **Agent Log pages at 25 rows** with a "Show 25 more" button, and a row's
   continue-composer is built only when open — instead of 500 rows each
   pre-rendering a hidden `<textarea>` + send button.

### What Tier 1 did NOT fix

- **The conversation rail is still the biggest thing in the modal** — 1,147 of
  the remaining 1,613 elements, 126 rows derived from the 500-row agent-log
  merge (Root cause 5). That is Tier 3's territory (promote Topics), and it
  needs the product decision, not just a patch.

Everything else on that list moved into Tier 2 below.

---

## Tier 2 — partly shipped, two items declined with a measurement

Three of the five landed. The other two are **declined on evidence, not
deferred vaguely** — the reasoning is here so nobody re-opens them blind.

### Shipped

**8. `list_sessions()` is cached per (path, mtime, size).** This was the real
prize and the doc under-sold it: `/api/project/<id>/conversations?limit=20`
measured **220 ms**, the slowest endpoint in the app, paid on every project-modal
open — it re-opened the newest 20 of 207 transcripts and JSON-parsed every line
of each, every time. Transcripts are append-only, so `(mtime, size)` pins a
file's content *exactly*; a cache hit is correct, not merely likely. Read-through
dict in `agent_runtime.py`, bounded at 512 entries, rows handed out as copies so
a caller can't poison it. Two-sided regression test: a hit must not re-open the
file, and an append must not return a stale row.

**10a. Versioned static assets are `immutable`.** `/` already injects
`?v=<asset_version>` into every `/static/*.js|css` reference, and that token is
the newest mtime across all static dirs — so any static change moves every URL.
That is precisely the precondition for `public, max-age=31536000, immutable`.
Previously all 41 JS + 2 CSS files carried `no-cache` and cost a revalidation
round trip on every load.

**The gate matters more than the header.** `immutable` is granted only when
`?v=` is actually present; a bare `/static/...` URL still gets `no-cache`. Drop
that condition and a client could be pinned for a year to an asset no deploy can
reach — the failure mode that makes long max-age dangerous in the first place.
`sw.js`, `manifest.json` and `index.html` are excluded outright.

**10b. `/assets/*` gets `max-age=3600`.** Brand assets have no `?v=`, so they
can't be immutable — an hour stops `claydo-idle.webp` (referenced from three
places, measured fetched twice per boot, 126 KB of pure duplicate) being
re-pulled, while any change still self-heals.

### Declined, with the number that decided it

**7. Caching `load_projects()` — declined.** Read+parse of all 53 project files
is **24.4 ms of the endpoint's ~28 ms**, so the ceiling looks attractive until
you notice `/api/projects` is a *30-second poll*: the saving is ~0.08% of a
core. Against that, every caller receives a **mutable** dict and several mutate
it in place (`api_projects` alone writes `live_agent`, the relative timestamps,
and pops `backlog`). A correct cache would have to hand out deep copies, and
deep-copying 5 MB of nested dicts in Python costs *more* than `json.loads` does
in C. The dead first sort (three sorts, first one overwritten) was removed while
in there. If this ever does become hot, the fix is to stop re-reading the 2.2 MB
`mission_control.json` — 892 backlog items, 856 of them `done` — not to cache
around it.

**11. Swapping Werkzeug for waitress — declined for now, and it is the one to
revisit.** This is the item most likely to matter for the phone, because
`Connection: close` means every request pays a fresh connection and the tunnel
is where that hurts. But waitress **buffers responses by default**, and this app
is built on SSE — agent output, terminal streams, browser-pane frames, hivemind
buses. Swapping the server without a streaming test plan risks the core feature
to fix a cost nobody has measured on the real device yet. **Order of operations:
measure a real phone over the tunnel first, then swap if the connection cost is
what shows up.**

### Corrected while implementing

**The duplicate `Date` header is not an app bug.** Root cause 6 flagged it and I
first "fixed" it in an `after_request` de-dup — which was a no-op. Flask emits
exactly one Date (`send_file` sets it); the second is added by the **WSGI server
below the WSGI layer**, where no hook can reach. API responses never had two
because Flask sets no Date on them. The working fix is for the app to contribute
*none* on static paths and let the server's stand alone, which is what shipped.
Worth knowing: it is cosmetic either way — the concern was heuristic caches
refusing a malformed header set, and these responses now carry explicit
`Cache-Control`, which overrides heuristics entirely.

### Still open

- **9. Targeted modal updates instead of a full `innerHTML` rebuild.** Real, but
  the payoff shrank: Tier 1's tab-gating already took a re-render from 41.1 ms to
  13.5 ms, so this now buys single-digit milliseconds in exchange for unpicking
  the scroll/caret/focus/stream-node save-restore machinery. Low priority.
- **Tier 3** — unchanged, and still the biggest remaining item in the modal.
