# Clayrune promo video — Stage 1 product brief (video-shotcraft, co-creation mode)

**Mode:** co-creation (`references/guided-free-creation.md`), chosen by Ron 2026-08-29.
**Gate order:** ① product brief → ② requirement-to-execution table → ③ visual
direction / styleframe → ④ feature-to-shot mapping → ⑤ storyboard → production.
**This document is gate ①.** Nothing is captured or rendered until Ron approves it.

Companion, not duplicate: `docs/VIDEO_BRIEF.md` (Posy) is the creative/copy brief —
hook options, per-platform cuts, caption copy. This file is the skill's required
product-understanding table and the compliance envelope it must stay inside.

---

## The brief table

| Item | Judgment / recommendation | Evidence |
|---|---|---|
| **Positioning** | Not another coding agent. The console that runs the Claude Code agents you already have — many projects, one grid. | `LAUNCH_COPY.md` §"the one rule": every headline must survive "so it's Cursor?" |
| **Target user** | Developers already running Claude Code daily, on more than one repo, who lose the thread when they close the terminal. Secondary: the r/selfhosted / HN local-first crowd. | `LAUNCH_PLAN.md` §3.2 (OpenClaw/OpenCode/Roo/Goose users are "our community"); §2 "your machine, your Claude subscription". |
| **Video purpose** | The **conversion mechanism** for the launch essay. The essay distributes, the clip converts, and it is the last section of the post. It is the single item on the critical path. | `LAUNCH_PLAN.md` §3.3 and §4 Phase A.1, verbatim: "The demo video gates the post, and the post gates the launch." |
| **Core claim** | *"Your agents keep working when you close the chat."* Scheduler + steward + hivemind — free, shipped, and no competitor in the Cursor/Cline/Aider set has it. | `LAUNCH_PLAN.md` §1.3, which explicitly rejects "the missing UI for Claude Code" as a *category* line that makes nobody install anything. |
| **Must show** | 1. The fleet (many projects working at once). 2. The phone (reach the agent on your own machine from anywhere). Those are the two things no competitor has; a demo of one agent on one repo is not the pitch. | `MARKETING_ASSETS_SPEC.md`, the standing "show the fleet and the phone" rule. |
| **Available pages/states** | Grid, streaming conversation thread, plan approval, scheduler, hivemind, memory, phone/PWA view. All shipped and demonstrable on real data. | `LAUNCH_COPY.md` verified-claims table (scheduler / steward / hivemind / cross-session memory / phone tunnel all marked shipped). |
| **Opening claim (first frame)** | Recommend leading on unattended work, not on the dashboard. Posy is drafting three hook options against this constraint; pick at gate ④. | `LAUNCH_PLAN.md` §1.3. |
| **Length / aspect / language** | 45s master, English. 16:9 master; 9:16 derived for Shorts/TikTok; square loop for the landing page and README. One capture pass, all derivatives cut from it. | `DEMO_VIDEO_SPEC.md` (45s is the specified launch gate); `DEMO_SHOOT_SCRIPT.md` "one take → all derivatives". |
| **Music / VO** | Recommend music + on-screen text, **no voiceover**. VO dates instantly, blocks localisation, and forces a script rewrite for every platform cut. Shot-craft ships cleared BGM/SFX in `assets/audio/`. | Skill asset library; per-platform cut plan in Posy's brief needs caption swaps, not re-narration. |
| **Data / compliance line** | Public demo data only. No client, personal, internal, key, or live data on screen. Every project name, path, and email visible in a frame must be scrubbed or replaced before capture is kept. | `CLAUDE.md` binding rule: nothing operator-specific ships. Screens show real paths and the operator's email by default. |
| **Visual cues to preserve** | The product's own tokens, pulled from live CSS at gate ③ — not invented. Three-pane layout, the grid, the shimmering "Thinking"/"Working" text. | Current `static/` is the source of truth; the 3-pane redesign merged 2026-07-14→16. |

---

## Forbidden — legal, not stylistic

From `LAUNCH_PLAN.md` §0/§2 and `clayrune-cloud/docs/STEWARD_HANDOFF.md` §2:

- ❌ "always on" / "no computer to babysit" / "works while your laptop is closed" — that is the **parked** Cloud tier.
- ❌ "we host Claude" / "sign in with Claude" / "we hold your credentials".
- ❌ "works with any agent" — Claude-first; Gemini and Codex are *supported*, not equal.
- ❌ "no setup" — there is an installer.
- ❌ Anything about the hosted cloud product.
- ✅ Required, said out loud: **"Clayrune runs on your machine. If your machine sleeps, so does your agent."**

**Verified this session, and it changes what we may say:** `keep_awake_enabled`
is `False` by default (`server.py:157`, `config.json:37`, and false on the live
`/api/config`). So **"Clayrune keeps your machine awake" is not a claim the video
can make** — the capability exists in `mc/wake_lock.py` but ships off. It can be a
setting we *mention*, never a feature line.

*(Correcting a stale note in project memory: `mc/wake_lock.py:216` does call
`atexit.register(release_now)`. The "never registered, orphans caffeinate"
finding no longer holds. Default-off is the live constraint, not leakage.)*

---

## Blocker found at this gate: every screenshot asset we own is stale

video-shotcraft builds from **real page screenshots**. Ours no longer match the product:

| Asset | Dated | Status |
|---|---|---|
| `docs/assets/hero-grid.png` | 2026-07-13 | pre-redesign |
| `docs/assets/still-chat.png` | 2026-07-13 | pre-redesign |
| `docs/assets/still-phone.png` | 2026-07-13 | pre-redesign |
| `docs/assets/still-automation.png` | 2026-07-13 | pre-redesign |
| `docs/assets/demo-desktop.mp4` | 2026-07-13 | pre-redesign |
| `docs/assets/product-cut.mp4` | 2026-07-15 | mid-redesign |

The `refactor/conversation-redesign` merges land **2026-07-14 → 07-16**, with UI
work after that (incognito rail icon 07-17; persona avatar in header and chat list
08-26). So the footage `DEMO_SHOOT_SCRIPT.md` calls "already captured" is obsolete.

**Consequence:** a fresh capture pass is now the real gate, replacing the physical
phone shoot the old script assumed. Desktop surfaces can be captured headlessly off
the running instance. The phone frame is the one open question below.

---

## Needs Ron's decision (3)

1. **Demo content on screen.** Capture against a purpose-built demo project set with
   invented names, or against real projects with names and paths scrubbed? The first
   is safe and slightly fake; the second is authentic and is where operator data
   leaks. *Recommend: purpose-built set, with genuinely real agent activity running
   in it — honest behaviour, safe labels.*
2. **The phone shot.** Screen-record the PWA in a device frame (synthesizable, clean,
   reads as a mockup), or one real hand-held clip of the phone on a desk
   (needs you for ~10 minutes, and it is the beat the old script said no harness can
   fake). *Recommend: the real clip — it is the only shot that proves it.*
3. **Voiceover: confirm none.** Music and on-screen text only, per the table above.
