# Competitor teardown — Omnara

**Researched 2026-08-02.** Everything below was read off their live sites and
public changelog (no account). Company facts are from public search; the two
marked *unverified* could not be confirmed from a primary source.

---

## What Omnara actually is — two products, one brand

This trips people up, so state it first:

| | `remote.omnara.com` | `omnara.com` |
|---|---|---|
| Name | Omnara (desktop/mobile apps) | Omnara **Managed Agents** |
| Pitch | "The command center for your coding agents." | "The control plane for agents." |
| Status | **Shipped**, weekly releases | **Waitlist** — "early access rolling out soon" |
| Audience | Devs running Claude Code / Codex | Teams deploying production agents |
| Shape | Desktop + web + mobile + watch app | `agent.yaml`, machine pools, tool policies |
| Money | **100% free, unlimited sessions** | Unannounced (this is the monetisation) |

Looking only at `omnara.com` gives the false impression they haven't launched.
The head-on competitor is `remote.omnara.com`, and it is very much live.

## Company

- **YC S25.** Founded 2025 by Ishaan Sehgal and Kartik Sarangmath (childhood
  friends; backgrounds at Meta and Microsoft).
- **Raised ~$500K seed, September 2025.** That is roughly the standard YC deal
  size, so treat it as "YC money, no big outside round." *Crunchbase blocks
  automated reads (403), so this figure comes from secondary aggregators —
  medium confidence.*
- **Traction claim:** "Trusted by 20,000+ builders", with Microsoft, NVIDIA,
  GitHub, Hugging Face, Cloudflare, Amazon and Berkeley logos on the page.
- Support runs through Discord + `contact@omnara.com`.

**Context:** Conductor (Melty Labs) raised a **$22M Series A** for the same
category. Omnara is the scrappier of the two by ~40×.

## Everything Omnara does (from the changelog + landing copy)

**Surfaces** — macOS (Apple Silicon) and Windows (x64 + ARM64) desktop apps,
web app, native iOS and Android apps, **Apple Watch** (notifications + quick
replies), and a CLI (the Linux story). Desktop bundles the CLI.

**Agents** — Claude Code and Codex. Two, and they say more are coming.

**Remote control** — a local daemon relays over WebSocket; no exposed ports,
no SSH. Push notification when the agent needs you, reply from anywhere.

**Cloud sandboxing (opt-in, per repo)** — close the laptop and the session
migrates to a cloud sandbox; agent state, code and uncommitted changes keep
running and sync back on return. Billed separately from the free core app.

**Voice** — genuine two-way spoken conversation with the agent (it asks
clarifying questions), plus one-way dictation. Works on mobile, web, desktop.

**Git-centric workspace** — parallel agents across worktrees, diff viewer
(split/unified), inline comments, commit UI, session forking with correct path
rebasing, repo viewer, remote-base PR diffs.

**Workspace scripts** — setup script (runs once per worktree creation) and run
script, with output panels, on desktop *and* mobile.

**Ecosystem plumbing** — MCP support (loads in background so it can't block
startup), slash commands, Codex skills via `skills/list`, `/compact`, model
picker incl. Opus 4.8, provider auth status, per-session cost/token breakdown,
keyboard shortcuts, mermaid rendering, image paste, "Open In" to Explorer /
Terminal / VS Code / Cursor / Zed / Sublime / JetBrains.

**What is NOT there** (no mention anywhere in the changelog, landing page or
FAQ): a scheduler, recurring/cron agent runs, an autonomous or self-directed
agent, a cross-session memory system, a learning loop, or a backlog / task
list of its own.

---

## Omnara vs Clayrune

Clayrune rows verified against this repo, not memory: runtimes from
`agent_runtime.py`, voice from `static/js/composer-extras.js`, licence from
`LICENSE`.

| | **Omnara** | **Clayrune** |
|---|---|---|
| Licence / model | Free app, closed cloud tier | **MIT, self-hosted** |
| Funding | ~$500K, YC S25 | None |
| Team | 2 founders | 1 |
| Users | claims 20,000+ | pre-launch |
| **Agent CLIs** | 2 — Claude Code, Codex | **7** — Claude, Gemini, Codex, OpenCode, Goose, Aider, Kiro |
| Desktop | macOS + Windows native | Windows installer, signed/notarised macOS `.app`, Linux |
| Mobile | native iOS + Android | Android APK (Capacitor); iOS in progress |
| Apple Watch | **yes** | no |
| Remote access | daemon + WS relay, no ports | Cloudflare tunnel |
| Laptop closes | **migrates session to cloud** | session stays on your machine |
| Voice | **two-way conversation** + dictation | dictation only |
| Git worktrees / diffs / commit UI | **first-class** | not the primary surface |
| **Scheduler (cron/daily/interval)** | no | **yes** |
| **Autonomous steward** (sets its own goal) | no | **yes** |
| **Multi-agent hivemind** (workstreams, bus, shared knowledge) | no | **yes** |
| **Cross-session memory** (Scribe, archive, search) | no | **yes** |
| **Learning loop** (distilled skills, safety rails) | no | **yes** |
| Per-project backlog | no | yes |
| Skills + MCP management UI | partial (consumes them) | yes (full manage surface) |
| Secrets vault + TOTP | no | yes |
| Live browser pane the agent can drive | no | yes |
| Terminal pop-out | no | yes |
| Incognito mode | no | yes |

### The honest read

**They beat us on reach and polish.** Watch app, two-way voice, cloud
failover, native iOS, weekly shipping cadence, 20k users. Those are real and
we should not pretend otherwise.

**We beat them on autonomy and breadth.** Seven agent CLIs to their two. And
the entire right-hand column — scheduler, steward, hivemind, memory, learning
— has no counterpart in their product at all.

**The two products answer different questions.** Omnara answers *"my agent is
running, how do I watch and steer it from anywhere?"* Clayrune answers *"how
do I keep work moving across projects when I'm not driving?"* Their centre of
gravity is the **repo**; ours is the **project and the agent working on it**.

### What this means for positioning

1. **Do not lead with remote access.** They own it, they're free, they're on
   the watch, and they have cloud failover we don't. Fighting there is a loss.
2. **Lead with unattended work** — scheduler, steward, hivemind. It is the one
   axis where they have literally nothing, and it is already the chosen hook.
3. **Multi-CLI breadth is a cheap, checkable win.** 7 vs 2, in a table.
4. **Their free tier resets the price conversation.** The core app is free with
   unlimited sessions; they removed the old paid plumbing outright. Any
   Clayrune paywall has to clear that bar, so convenience-pricing needs a
   rethink against this.
5. **Watch their Managed Agents launch.** `agent.yaml` + durable agents that
   "run for minutes, hours, or days and resume exactly where they left off" is
   the closest anyone has come to our persistence story — but aimed at
   infrastructure teams, not at a solo developer's projects.
