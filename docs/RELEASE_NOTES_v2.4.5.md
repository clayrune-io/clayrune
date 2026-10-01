# Clayrune v2.4.5

57 commits since v2.4.4 (2026-10-01).

### New
- **Agent CLIs update themselves daily.** Clayrune checks Claude, Codex and the other agent CLIs once a day and updates them with the CLI's own install method, skipping any CLI a live session is using. On by default; a Settings toggle turns it off (MC-1025).
- **The Mac app updates itself in place.** Check for updates now downloads the new build, verifies its signature, notarization, team, bundle id and sha256, then swaps it in and restarts. The swap confirms the new build started and rolls back if it did not (MC-1026).
- **Pop Out opens a real window.** A chat popped out of the dashboard is now its own window, including in the Mac app, with a cap of four windows (MC-1027).
- **Usage report shows what one point is made of**, and the breakdown route is about 12 times faster when warm (MC-998).

### Fixes
- Codex no longer fails every run when the installed CLI does not list the chosen model. Clayrune never passes a `-m` the CLI does not offer, shows the right update hint, and re-probes the CLI version when its binary is replaced.
- Scheduled and steward threads that a human typed into are kept as chats, and one-off runs are no longer backfilled as chats.
- Worktree teardown severs every junction and symlink before deleting, so removing a worktree cannot empty the folder it links to.
- Desk v1 (still behind its flag): live Review and Launch, accounts and Connections, Retro, Conversations and Engagement, a standalone Studio with storyboards saved on the server, and generation engines. Demo fixtures no longer ship in production, and the demo paths say "not connected".
