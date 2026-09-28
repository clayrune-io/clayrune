# Clayrune v2.4.2

321 commits since v2.4.1 (2026-09-24 to 2026-09-28).

### New
- **Desk v1** — the social workspace ships: Home, Campaign pages, Content list, Conversations, Calendar, Results, and a video intake + director flow, all built against real product screenshots with an approval-gated rules popover.
- **Brainstorm this** — a built-in persona that explores a raw idea over several turns to find whether it fits and what to try next, reachable from a new chat or any message.
- **Workflow builder box-view canvas** — describe a workflow in plain text and get a disabled draft laid out on canvas for review before it's saved; a "Check workflow" pass pins findings to the boxes they're about.
- **Bottom usage bar** — weekly quota % per provider (Claude, Codex), with per-provider reset actions and Codex/Gemini usage popup sections.
- **Scheduled auto-backup + retention** ("Protect your work" first-run step): pick a destination and cadence, back up now, and older scheduled backups are pruned while manual ones are never touched.
- **Ideas workspace** and a Claydo-driven handoff that moves a brainstormed idea into a project, with rollback/idempotency if it's interrupted mid-transfer.
- **Mobile browser pane** gets its own phone-sized surface instead of a squeezed-down desktop window, with device-mode emulation and a working address bar.
- Browser pane parity pass: tab strip + popup handling, JS dialogs, file upload, right-click, IME composition and page-level shortcuts, HiDPI-correct clicks, pane maximize/restore, and a copy-URL button.

### Fixes
- **Leaked pane-Chromium processes are swept up.** A server restart used to orphan headless Chromium trees forever (9 trees / ~35 processes found running with no project aware of them); a periodic sweep now closes anything a restart leaked. See CHANGELOG (MC-997).
- **The permanent `/attend` unlock is disabled.** Its "a human is present" check was a forgeable `Origin` header — closed as a security fix (MC-994).
- Server-side sweep for orphaned agent CLI processes (own-package match, dead-chain detection, protected roots, idle >24h), with a dry-run toggle.
- A dispatched or scheduled chat can be handed over to attended control, re-stamping it so a human's click is recorded, not assumed.
- Background jobs for non-Claude agents (Codex, Gemini, Qwen) now wake their session the same way Claude's do, instead of finishing unnoticed.
- Mobile: the Chats tab shows aged-out persona chats again; stale tab-switches no longer hijack a different chat; the modal recovers full height reliably after Stop or a settled turn; touch scrolling and the channel accordion no longer stick.
- Mid-turn context rollover (still off by default) no longer loses a spawner callback, mishandles a background job, or double-rolls on a fast repeat interrupt.
- A stale model pin (Claude, Codex, Gemini) now auto-upgrades to the newer model in its tier family whenever the newer model is no more expensive, on both input and output.
- First-run setup is unmistakable from the guided tour and can no longer be skipped in one click or bypassed by a stuck vendor sign-in.

### Security
- Removed the forgeable `/attend` bypass (MC-994).
- `/api/secrets/exec` fails closed on an omitted session id and redacts before truncating output, not after.
- Steward fence hardening: closes a global-option bypass, an agent-worktree fence hole, and several disguised-program and PowerShell-assignment false positives/misses.
