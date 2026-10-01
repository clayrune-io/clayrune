# Clayrune v2.4.4

288 commits since v2.4.3 (2026-09-29 to 2026-10-01).

### New
- **Desk v1 (in progress).** A reworked Desk (campaign map, Brief, Launch, outcome loop, Connections) is being built behind the `desk_v1` flag, which is off by default; nothing changes unless you turn it on.
- **Image viewer** gets minimize, maximize and close controls, plus previous and next through the images in the same folder.
- **Settings has an Installed App section** showing the installed app's state and when it last updated (MC-713).
- **Hivemind workers run in their own worktrees.** Each worker edits an isolated copy, and finished workstreams merge serially onto a `hivemind/<id>` branch, never onto master (MC-1013).
- **GPT-6.1 Sol** is the head of the Codex balanced tier.
- **Usage breakdown** counts tokens per range with input breakdown, cache-write TTL and per-model scope, and reports "no window of this kind" when a vendor sends none (MC-998).

### Fixes
- The Mac app's Settings, Update, "Download update" button now opens your browser; before, it did nothing (MC-1003).
- First-run setup is remembered: "Not now" on a fresh install no longer brings the wizard back on every login (MC-1015).
- A routine page load no longer raises the passcode prompt when only one provider CLI is installed (MC-1010).
- Stop no longer kills the server on Linux and macOS, and stopping an agent now ends its whole process tree instead of orphaning child processes.
- Restarting on Linux and macOS no longer inherits the terminal that launched it (MC-1016).
- CLIs installed through nvm are found on Linux and macOS.
- The guardian no longer marks a session errored while it is mid-respawn (MC-1002).
- A revived chat no longer drops your last message from the transcript when the live stream looked connected.
- Keys typed in a text field no longer switch, zoom or close the image viewer.
- Usage calibration no longer counts phantom turns, amended commits or counter resets as real usage (MC-998, MC-1007).
- Right-to-left chat keeps the quote marker outside the name (MC-1000).
- Tile density is now driven by CSS tokens, and the compact view follows the mobile layout at 960px and below (MC-37).
- Dispatched one-off agents start with a smaller context, sized by the job rather than just the model.
- The unattended-agent fence no longer blocks multi-line or redirected commands that only touch localhost (MC-1013).
