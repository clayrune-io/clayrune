# Clayrune v2.5.1

1,535 commits since v2.4.8, November 3 to November 25, 2026. v2.5.0 was tagged but never published; this release contains everything prepared for it plus the context rollover changes below. Full detail is in `CHANGELOG.md`.

## Floor and chats

- Open **N working** in the desktop status row to see who is working across projects and jump to their chat. It also appears in the phone app bar when someone is working.
- A chat that delegates work shows each agent's project, elapsed time and progress, including when it is waiting for an answer.
- Opening a chat brings its person and conversation into view in the left rail, including when you arrive from the Floor.
- Your messages and each uninterrupted stretch of agent text show their recorded time in your time zone.
- Long follow-up instructions and unanswered messages carry over in full when an agent starts a fresh context.
- Reopening a chat returns it to its recorded working copy. If that worktree is gone, Clayrune tells you.
- Chat flags a test result as **UNVERIFIED** when it recognizes a command that hides the test runner's exit status.
- Live test progress is off by default. Turn on **Floor Preferences > Working defaults > Live test-run progress** for progress cards in chat, a testing badge on the Floor, and test history in the project.

## Long chats

- When a long Claude chat moves to a fresh context, the size at which it does so is now worked out per chat: from what that chat loads on its first call and from measured growth by model, effort and built-in agent type. With no measurement yet, the limit is 200,000 tokens.
- A limit pinned for an agent type still applies when it leaves at least 60,000 tokens above what the chat loads at start. A pin below that is skipped, with one line in the server log saying so.
- You can set explicit limits with `context_rollover_overrides` through the config API. `docs/ROLLOVER_THRESHOLDS.md` explains the order in which pins, overrides and the computed default apply.
- A message that never reached the agent is no longer listed as unanswered in the handoff, and an over-long message keeps its beginning and its end.

## Desk

- The Desk loads in the background so it no longer holds up the dashboard. Its home screen opens even while its data is loading.
- Campaign, storyboard and article planners can choose a model and effort level for each hired agent. The dispatch card shows the choice.
- Accepting a proposal twice starts one writer. If the writer fails to start, the proposal is available to try again.
- New connected accounts start with Read and Post switched off. You choose which permissions to grant.

## Agent mail

- You can reply to an agent's email from your verified mailbox and have the answer reach the conversation that asked. Mail is off by default. On an install with a configured mail service, turn it on in **Settings > Connectivity > Agent mail**.
- With agent mail enabled, the Inbox tells you when a reply is held and when a held reply expires undelivered. You can review held replies in Agent mail settings.

## Memory and project settings

- Clayrune makes a verified daily copy of each project's memory and keeps the latest 14 automatic copies by default.
- Backlog items suggested by an agent are marked **Agent proposed**. You can accept or dismiss them before the runner picks them up.
- **Settings > Connections** shows one collapsible card per AI vendor. A card starts open only when that vendor needs attention, such as a rejected sign-in, a bad key or a used-up quota.
- The project menu has a **Safety** dialog for worktree and merge settings. Review before merge and independent review are off by default and can be enabled there.

## Workflows

- Reset in the workflow editor returns to the last successful save. Undoing across a save preserves the existing schedule.
- **Run now** changes to **Starting** after the first tap, so a double tap starts the workflow once.

## Security

- Only a person using the host computer can set its first dashboard passcode.
- Including vault entries in a project backup asks you to re-enter your dashboard passcode.

## Installers

- The Windows installer links to the exact setup script it runs. When CLI repair needs a running Claude session closed, setup identifies it and asks you to close it.
- On macOS, the script installer creates a working launcher in Applications that opens Clayrune from its install folder. The packaged Mac app also includes the full set of built-in skills.
- A standard fresh Linux install still opens the dashboard for provider setup. On Linux, macOS and Windows, rerunning the script installer stops before a resync would discard tracked edits or local commits.
