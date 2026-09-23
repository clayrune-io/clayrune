# Clayrune v2.4.0

310 commits since v2.3.0 (2026-09-14 to 2026-09-22).

### New
- **First-run setup is no longer tied to the tour.** You can configure agent connections, theme, conversation flow, connectivity and the LAN passcode without running the guided walkthrough, and skip the walkthrough without skipping setup. Settings gains "Run setup again" next to "Take Tour."
- **A default-model tier step during setup**, with Best / Balanced / Fast to choose from and Balanced recommended. It only writes a tier if you don't already have a model set, so an existing pin or tier is never overwritten.
- **One resolver decides which model an agent gets**, everywhere a model is chosen (new chat, resume, dispatch, revive). An install that never answered the tier question now keeps the CLI's own default instead of silently landing on Opus.
- **Opus 5.5** in the Claude model pickers.
- **The +New empty state names the agent you're actually talking to.** It used to say "Claude" no matter which provider or persona was active; it now reads the real one, falling back to "Agent" only when nothing resolves.

### Security
- A throwaway diagnostic run could point at a temporary secrets store and still mint straight into your real OS keyring, orphaning every sealed vault entry. Fixed: a temp store no longer touches the real keyring, and a mismatched key is now detected and refused instead of silently accepted.

### Fixes
- **Posts queued on the Desk no longer vanish.** Two parts of the app saving the same project at once could silently drop a newly queued draft; saves now happen one at a time, and the Desk's pending-drafts count reflects the drafts actually waiting.
- **A dispatched agent no longer gets reported as failed once it has already started.** A slow save of the dispatch log (an antivirus or indexer briefly locking a file) used to bubble up as an error, so the same task could get dispatched twice.
- **Resuming a long conversation no longer double-counts spend** on Claude CLI 2.1.277 and newer, which carries the session's cost total across a resume instead of restarting it at zero.
- **Mobile chat regains full height** after the app is backgrounded or the screen is locked with the keyboard open, instead of staying stuck at keyboard height until you tap the transcript.
- **Diagram thumbnails in Media render** instead of showing as blank white tiles.
- **The Providers panel no longer shows garbled text** on Windows when a CLI's own status line uses non-ASCII characters.
- **Signing in from a phone works again.** Codex, Gemini and Qwen used to open a login listener on the host machine, so completing sign-in on a phone just failed; they now use a device-code flow. The remote sign-in link had also gone silently dead after an unrelated row-markup rewrite; it now works again and falls back to showing the raw URL rather than failing silently.
