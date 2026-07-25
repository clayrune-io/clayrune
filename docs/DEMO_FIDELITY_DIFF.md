# Demo ↔ Real App Fidelity Diff

**Goal:** make the marketing demo (`clayrune_website/demo/demo-app.*`, shipped as `demo.html` → iframe `demo/demo-app.html?v=r17`) look and navigate like the real shipped Clayrune app (`mission-control/static/index.html` + `static/js/*.js`). Drop anything fake; add/correct anything missing or divergent.

**Method:** two independent surface inventories (real app vs. r17 demo) were mapped and diffed. This is the full divergence list — **not** limited to in-chat + agent-selection.

**Scope note:** the demo's *dead* `demo/demo-app.css` and root `demo.js` are pre-r17 orphans NOT linked by the shipped demo. Some capabilities (sidebar, project-modal menu, drill-down settings) exist as CSS there but **do not render** — treat them as absent, but reusable as styling starting points.

**Status legend:** ✅ faithful · ⚠️ present but divergent/incomplete · ❌ missing · 🎪 demo-only (keep).

---

## 0. Priority-ranked fix list (what to build, in order)

Ron explicitly called out **1, 2, 3** (three-dot menu, main menu, Settings). Everything is scoped as *simulated / no-backend* — structurally faithful, not functionally live.

**P0 — Ron called these out**
1. **Left sidebar / main nav** (desktop) — the biggest "missing menu". §1
2. **Mobile hamburger + drawer** — currently only a bottom tab bar. §2
3. **Three-dot (⋮) menu on the project view** — the deep project menu + window chrome. §3
4. **Settings → drill-down master/detail** (replace the flat 5-toggle modal). §4

**P1 — core pitch surfaces / high visibility**
5. Command palette (Ctrl+K) + header search-command pill. §5
6. App-bar chrome parity (status pill, Live badge, Tour, breadcrumb count). §6
7. Inbox / Feed / "Waiting on you" / Beacon. §7
8. Backlog as a real list view (not a dead card). §8
9. Scheduler / Automation (Stewards + Scheduled Tasks) — the agent-persistence hook. §9
10. Hivemind (multi-agent tree). §11
11. Plan viewer + wire "Edit plan" and the Plans surface. §14
12. Agent-chat parity: wire "+ New conversation", rail row inline icons, thread-header controls, starter chips. §15

**P2 — completeness**
13. Skills & MCP / Personas (Extensions modal, 3 tabs). §12
14. History modal. §10
15. Processes table. §13
16. Wire the Surfaces cards. §16
17. Providers screen (inside Settings). §17
18. Power/Update/System-status/Terminal/Media/Shared Rules/Incognito stubs (reachable once sidebar exists). §18

**Correctness cautions — do NOT get these wrong:** §19

---

## 1. Left sidebar / main nav — ❌ MISSING (P0)

**Real app:** a persistent left `nav.sidebar` (desktop) is the top-level app menu. Items in exact order:
`Dashboard · Inbox (badge) · New Project` — **Workspace:** `Backlog · Automation · History` — **Advanced** (collapsible, persisted): `Hivemind · Skills & MCP · Personas · Media · Shared Rules · Processes · Incognito` — divider — `Settings · Power` — divider — **Projects** (live list of project rows).

**Demo:** no sidebar at all. Navigation is tile→project view-swap only.

**Fix:** Add the left sidebar mirroring the real order/grouping (Workspace / Advanced-collapsible / Settings+Power / Projects list). Each entry must be *visible*; clicking opens the corresponding simulated surface (Backlog §8, Automation §9, History §10, Hivemind §11, Extensions §12, Processes §13, Settings §4, Power §18) or a lightweight representative panel. This is the "main menu is missing" fix.

---

## 2. Mobile hamburger + drawer — ❌ MISSING (P0)

**Real app (mobile ≤960px):** app bar has a **hamburger → drawer** *and* a bottom tab bar. Drawer contents: `Dashboard · Skills & MCP · Backlog · Hivemind · Automation · History · Shared Rules · Processes · Usage & limits · Incognito`.

**Demo:** only a 5-slot bottom tab bar; **no hamburger drawer**.

**Fix:** Add a hamburger to the mobile app bar that opens a drawer mirroring the real drawer's items. Keep the bottom tab bar (real app has both).

---

## 3. Three-dot (⋮) menu on the project view — ❌ MISSING (P0)

**Real app:** the project detail modal header has window chrome `← Dashboard · pin · ⋮ · minimize · maximize · close` and a deep **⋮ menu**:
- Tabs group (also the tab bar): `Agent · Backlog (+count) · Agent Log · Plans · Activity · Workflows`
- `Change Status ▸` → Active / Waiting / Blocked / Parked
- `Appearance ▸` → accent swatches + domain picker + "New domain…"
- `Edit Profile…` · `Agent Settings` (shows project default model)
- `Advanced ▸` → `GitHub Sync · Code Sync · Memory · Rules · Skills · MCP servers · Personas · Media · Hiveminds`
- `Delete Project` (danger)

**Demo:** no ⋮ anywhere; opening a project is a bare view-swap with no modal chrome.

**Fix:** Add the ⋮ menu + window-control chrome to the demo project header. Populate the menu with a representative/simulated version of the real items (Tabs, Change Status, Appearance, Edit Profile, Agent Settings, Advanced ▸ submenu, Delete). Menu items can open simple simulated dialogs or highlight-in-place.
**Do NOT** add a kebab to conversation/chat rows — see §19.

---

## 4. Settings — ⚠️ DIVERGENT: flat vs. drill-down (P0)

**Real app:** master/detail **drill-down** (list → sub-sections → detail) with a **live search box**. Categories:
- **Providers** — Claude/Gemini/Codex sign-in & API keys, per-provider health cards.
- **Agent** — *Identity* (User Name, Agent Name), *Model* (Auto/Default/Fable 5/Opus 4.8/Sonnet/Haiku), *Effort* (Default…Max), *Behavior* (Max turns, Permissions, Brief replies, Sticky settings, Activity states, Keep awake), *Integration* (Streaming, Remote control, Channels).
- **Memory** — Auto-condense + threshold, Model + Executor, Exploration readback.
- **Appearance** — Theme (Dark/Warm/Editorial), Accent (6), Density, Writing style, Open surfaces as, Agent replies, Background gallery, Enter-key behavior.
- **Connectivity** — Network access (LAN passcode), Remote Access, Push Notifications, Mobile Pairing (QR).
- **System** — Paths & Server, Advanced features (5 toggles), Server (Restart / Update), Help (Take Tour).

**Demo:** a flat single modal with 5 toggles only (Warm tone, Surfaces panel, Default model, Activity states, Show tool lines).

**Fix:** Rebuild demo Settings as a drill-down master/detail with the real category list (Providers, Agent, Memory, Appearance, Connectivity, System) + a search box. Populate each with *representative simulated* controls using the real names. Keep the demo's 5 working toggles inside Appearance/Agent where they belong; the rest can be simulated/read-only. Structure must match the real app.

---

## 5. Command palette + header search-command pill — ❌ MISSING (P1)

**Real app:** header "Search or command…" pill with `Ctrl+K` kbd → `.cmd-palette` with grouped results: **Projects** (all), **Actions** (New Project, Open Incognito, Open Scheduler, Settings, Shared Rules, Processes, Minimize All, Take Tour), **View** (Grid/List, Toggle Compact, Toggle Feed). Footer: ↑↓ / Enter / Esc.

**Demo:** no palette, no search pill.

**Fix:** Add the header search-command pill (visible, with `Ctrl+K` kbd). Ideally wire `Ctrl+K` / click to a simulated palette (Projects/Actions/View groups) that navigates the demo's surfaces.

---

## 6. App bar / top chrome — ⚠️ DIVERGENT/INCOMPLETE (P1)

**Real header (desktop):** breadcrumb `Dashboard · N projects` · search-command pill · agents metric pill · **system-status pill** (rate-limit label, click → 4-tab popover) · tile-layouts button · refresh timer · **Live badge** · **Tour (?)** button. Plus below: schedule banner + Beacon bar + auth banner.

**Demo header:** back button · logo · greet (mobile) · crumb · livepill · "Get it" · settings gear.

**Fix:** Bring the demo header closer — add the search-command pill (§5), a system-status pill, the Live badge, and the Tour (?) button; make the breadcrumb read `Dashboard · N projects`. Keep the demo's "Get it" (🎪 conversion) but as an *addition* beside real chrome, not a replacement.

---

## 7. Inbox / Feed / "Waiting on you" / Beacon — ❌ MISSING (P1)

**Real app:**
- **Inbox** modal/overlay — cross-project notification timeline (day-grouped, ✓/💬 icons, dismiss, search, Mark all read).
- **Desktop Feed** (right column) — **Needs you** (Review/Answer/Unblock) + **Recent** (Fresh/Today/This week) buckets, collapsible with badge.
- **Waiting on you** — inline blocking-events block with per-row action buttons.
- **Beacon "Where we stand"** — summary bar (⚠ N need you / ✓ All clear / active/paused counts / Open report) + full report modal.

**Demo:** none. "Needs you" is only an amber tile/pill + one branching in-thread question. The sidebar/mobile "Inbox" tab has nowhere to go.

**Fix:** Add a simulated Inbox (notification timeline + Waiting-on-you rows), a desktop Feed column (Needs you / Recent), and a Beacon summary bar. Wire the Inbox nav entry (sidebar §1 + mobile tab §19) to it.

---

## 8. Backlog — ⚠️ DECORATIVE ONLY (P1)

**Real app:** full interactive Backlog (project Backlog tab + cross-project **All Backlog** modal): add item + priority, done checkbox, priority badge cycle, GitHub #issue link, ▶ dispatch, 📎 attachments, 📝 notes, ✕ delete, Show/Hide done, Undo.

**Demo:** tile "N open" badges + a **dead** Surfaces "Backlog" card (no handler).

**Fix:** Make the Backlog nav entry and the Surfaces card open a simulated Backlog **list view** — items with priority badges, statuses, notes — read-only but faithful to the real Backlog tab + All Backlog modal.

---

## 9. Scheduler / Automation — ❌ MISSING (P1, core pitch)

**Real app:** Scheduler modal (`__scheduler`): **Autonomous Stewards** (+New; cards with Open chat / Charter / Edit / Stop; scope; objective + cadence; 🛡 fence) + **Scheduled Tasks** (+Add; Daily/Interval/Once/Cron; Runs history; ▶ Run Now / Edit / Del). Plus a pinned schedule banner (Recent/Upcoming).

**Demo:** dead "Scheduler"/"Steward" Surfaces cards only.

**Fix:** Make Automation/Scheduler open a simulated Scheduler modal with a couple of stewards + scheduled tasks. Agent-persistence is the launch hook — this surface should be *shown*, not faked as a dead card.

---

## 10. History — ❌ MISSING (P2)

**Real app:** unified run-log modal (`__history`): chips All/Manual/Scheduled/Hivemind, windows 24h/7d/30d, day-grouped.

**Demo:** none.

**Fix:** Simulated History modal reachable from the sidebar.

---

## 11. Hivemind — ❌ MISSING (P1, core differentiator)

**Real app:** per-project Hivemind tab + Dashboard modal (workstreams, findings, decisions, questions, message bus, Re-synthesize, Runs) + **cross-hivemind** modal with mini-tree viz.

**Demo:** none.

**Fix:** Add a simulated Hivemind surface (a small multi-agent workstream tree + a couple of findings). Multi-agent orchestration is a headline feature — worth a real-looking mock.

---

## 12. Skills & MCP / Personas (Extensions) — ❌ MISSING (P2)

**Real app:** Extensions modal, 3 tabs — **Skills** (scope/project filters, Import ▾, + New, 🧠 Learning queue), **MCP** (per-project loadout toggles, + New server), **Personas** (+ New via Claydo workshop).

**Demo:** none.

**Fix:** Simulated Extensions modal with the 3 tabs and a few canned entries. Reachable from sidebar "Skills & MCP" and "Personas" (both open this one modal — see §19).

---

## 13. Processes — ❌ MISSING (P2)

**Real app:** Process Manager (`__processes`) — count, Refresh, Cleanup Orphaned, table (PID/Name/Project/Status/Task/Duration + Kill).

**Demo:** none.

**Fix:** Simulated process table.

---

## 14. Plan viewer — ⚠️ PARTIAL (P1)

**Real app:** in-thread plan-approval card (parsed steps, Approve / Review) + **Plans tab** (history grid, Select/Delete/Export, rich `.plan-viewer-content` with tables/mermaid/markdown).

**Demo:** an inline approval gate exists, but **"Edit plan" does nothing** (both buttons call the same `doWork()`), and the Plans Surfaces card is dead.

**Fix:** Wire "Edit plan"/"Review" to open a simulated plan viewer (formatted plan doc). Wire the Plans Surfaces card to open a simulated Plans list/viewer.

---

## 15. Agent chat / 3-pane parity — ⚠️ INCOMPLETE (P1)

**Real app:** rail (+ New conversation, search, rows with status dots + Working/Needs-you badges + inline **📌 pin / ✕ hide / ◫ split**, incognito/steward rows) · thread header (status dot+label, provider/model badge, persona badge, **Stop**, token badge, activity ticker, plan-file btn, **Pop Out ↗**, **Dashboard ↗**) · composer (textarea, + attach, 🎤 mic, Dispatch/Send, **Agent/Model/Persona/Incognito** controls row, in-thread Model pill, **starter chips**: 🧪 Fix a failing test · ✨ Add a feature · 📖 Explain the codebase) · Surfaces column.

**Demo (r17):** rail + thread + Surfaces exist and conversation-switching works (good ✅), **but**: "+ New conversation" is dead; composer **+ attach** and **🎤 mic** are decorative; Send only re-runs the scripted flow (typed text ignored); no rail-row inline icons; thread header lacks the real controls; no starter chips; no Model pill popover.

**Fix:** (a) wire "+ New conversation" to a simulated new convo; (b) add rail-row inline icons (📌/✕/◫) — **not** a kebab; (c) add thread-header controls (status+label, model badge, Stop, Pop Out ↗, Dashboard ↗, plan-file btn); (d) add starter chips; (e) make +attach/🎤 at least look active; ideally echo typed Send text before the scripted reply.

---

## 16. Surfaces panel — ⚠️ DECORATIVE (P2)

**Real app:** Surfaces cards (Backlog / Plans / Workflows / Activity / Agent Log / Media) are live and clickable.

**Demo:** cards render but have **no click handlers**.

**Fix:** Wire each Surfaces card to open its simulated surface (§8 Backlog, §14 Plans, Activity/Agent-Log/Media stubs).

---

## 17. Providers / auth — ❌ MISSING (P2)

**Real app:** auth banner ("Claude isn't signed in" → Authenticate) + Providers settings screen.

**Demo:** none.

**Fix:** Add a Providers screen inside Settings (§4). **Intentionally drop** the "not signed in" auth banner — it would confuse a demo visitor (note as a deliberate divergence, not a gap).

---

## 18. Power / Update / System-status / Terminal / Media / Shared Rules / Incognito — ❌ MISSING (P2/P3)

**Real app:** Power dialog (Restart / Shut down), Update (Settings→Server), System-status 4-tab popover (Status/Config/MCP/Usage limit bars), Terminal pop-out (xterm), Media surface, Shared Rules editor, Incognito scratch agent.

**Demo:** none.

**Fix:** Once the sidebar exists (§1), each opens a lightweight simulated/representative panel. Low priority individually; they complete the "everything the sidebar links to has a destination" principle.

---

## 19. Correctness cautions — do NOT get these wrong

- **No kebab on conversation/chat rows.** The real app uses **inline icon buttons** (📌 pin / ✕ hide / ◫ split) on rail rows, and inline controls on the thread header — the ⋮ kebab lives **only on the project modal** (§3). A demo that adds a row kebab would be *wrong*.
- **Label ≠ route quirks to mirror:** sidebar **"Automation"** opens the **Scheduler**; **"Skills & MCP"** and **"Personas"** both open the one **Extensions** modal.
- **Mobile bottom tab bar** real set = `Inbox · Search · ＋ New · Claydo · You`. Demo currently = `Home · Search · New · Claydo · You` → change **Home→Inbox** and wire it (§7). "You" → Settings.
- **Mobile has BOTH** a bottom tab bar and a hamburger drawer (§2) — not one or the other.
- **Keep demo-only elements** (🎪): "Get it" button, conversion nudge toast, end-card, analytics beacons, coach-mark tour, full-screen cover. They're the demo's job — just don't let them crowd out real chrome.
- **Reuse, don't reinvent styling:** the dead `demo/demo-app.css` already contains extracted real styles for a sidebar, project-modal `.modal-menu`, and drill-down settings — mine it as a starting point rather than authoring from scratch.

---

## 20. Summary of net gaps

Absent or faked in the r17 demo vs. the real app: **no sidebar/main nav, no mobile drawer, no project ⋮ menu, no drill-down Settings, no command palette, no Inbox/Feed/Beacon, no real Backlog, no Scheduler/Automation, no Hivemind, no History, no Extensions (Skills/MCP/Personas), no Processes, no real Plan viewer, dead Surfaces cards, dead composer attach/mic/new-conversation, and an app bar missing status/Live/Tour/search chrome.** The demo's strong points (3-pane, conversation switching, scripted Orchard run, Ask Claydo, mobile chat-list) stay — this diff adds the surrounding shell so the demo *navigates* like the shipped app.
