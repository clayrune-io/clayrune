# Clayrune v2.3.0

260 commits since v2.2.0 (2026-09-03 to 2026-09-13).

### New: build a workflow on a canvas
- **Drag agents, approvals, actions and waits onto a canvas** and connect them. Branch on an agent's outcome, with a required "otherwise" path so nothing falls through silently.
- **Put a workflow on a schedule.** The same scheduler that runs your recurring agents can now start a workflow.
- **Watch a run and cancel it.** Open a saved workflow to see what step it is on. Editing a workflow mid-run only changes future runs.

### New: the Desk
- **An in-house marketing surface next to the Floor**: a board of campaigns, a review queue, a calendar and a ledger of what went out.
- **A writer that suggests what's worth saying**, citing the project activity behind every pick, and learns your voice from how you already write.
- **Push back with a note and it reworks right away.** Publishing stays yours; the Desk records what you posted.

### New
- **Backup and restore.** Back up the whole install or export one project, with progress, Cancel, and restore points you can roll back to.
- **Drag to hire.** Drag an agent from the Floor onto a project to add it to that project's roster.
- **Agents you dispatch report back.** A dispatched agent's result arrives in the chat that sent it, and the worker nests under its parent in the rail.
- **Wrong-question detection.** If an agent never touched something you named in your request, the turn is flagged.
- **Memory stays current in long chats**, refreshed every turn instead of once at the start.
- **Fable 5.1** in the Claude picker and **GPT-6 Astra** in the Codex picker.

### Security
- Decisions reserved for a human (vault secrets, promoting a skill, changing settings, backup export) now refuse an unattended agent instead of trusting it.
- The unattended-agent fence blocks commands whose real identity is only decided at runtime, not just known dangerous spellings.
- Incognito chats stay incognito after a restart and no longer show up in search.

### Reliability
- **Agent history could be wiped by a restart.** State files are now written atomically, and a file that fails to read is never rebuilt over the real one.
- **Session cost always showed $0.** Fixed.
- **The Floor lagged and could freeze.** It refreshes every 5 seconds, recovers from a failed load, and shows helpers while their parent waits.
- **Codex chats** keep their history, search and Documents, and a follow-up continues the same thread instead of starting a new one.
- **Remote access** survives Windows wiping its credential store and keeps alerting during a long outage.
- The Documents tab is fast again on projects with many conversations.
