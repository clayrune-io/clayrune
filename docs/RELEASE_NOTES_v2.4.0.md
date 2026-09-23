# Clayrune v2.4.0

326 commits since v2.3.0 (2026-09-14 to 2026-09-23).

### New
- **First-run setup is no longer tied to the tour.** You can configure agent connections, theme, conversation flow, connectivity and the LAN passcode without running the guided walkthrough, and skip the walkthrough without skipping setup. Settings gains "Run setup again" next to "Take Tour."
- **A default-model tier step during setup**, with Best / Balanced / Fast to choose from and Balanced recommended. It only writes a tier if you don't already have a model set, so an existing pin or tier is never overwritten.
- **One resolver decides which model an agent gets**, everywhere a model is chosen (new chat, resume, dispatch, revive). An install that never answered the tier question now keeps the CLI's own default instead of silently landing on Opus.
- **Opus 5.5** in the Claude model pickers.
- **Post a Desk draft yourself, in one click.** An approved draft now offers "Open in X" and "Open in LinkedIn", which open the platform in your own browser with the text already filled in, plus "Copy". You press Post; no developer account or API key is needed. LinkedIn company pages cannot be prefilled, so that route copies the text and opens the page's editor.
- **GPT-6 in the Codex pickers.** The model list is now read from Codex's own live catalog instead of a hand-kept list, so newly released models show up without a Clayrune update.
- **Saved model choices upgrade themselves.** A model pinned on an agent, project, schedule or workflow step moves to the newer model in the same family when its price is known and no higher; anything uncertain is left alone.
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
- **Codex no longer shows "signed in" when it isn't.** A bare OPENAI_API_KEY in the environment used to count as signed in, while every Codex dispatch failed with a 401.
- **CLI updates from Settings work on Windows again.** Updating an npm-installed CLI (Gemini, Codex) failed silently, leaving it one release behind.
- **The agent log is no longer quarantined over a momentary file lock.** A read that collided with a save was treated as corruption and the log was set aside; it is now retried and never moved.
