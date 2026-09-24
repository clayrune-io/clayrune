# Clayrune v2.4.1

67 commits since v2.4.0 (2026-09-23 to 2026-09-24). Both downloads are now signed: the Windows installer with a Microsoft-verified code-signing certificate, the Mac app signed and notarized by Apple.

### New
- **Signed Windows installer.** `Clayrune-Installer.exe` is Authenticode-signed and timestamped, so Windows and your browser no longer treat it as an unknown download.
- **Install progress per AI tool during setup.** Each vendor you pick shows its own bar: queued, installing (with elapsed seconds), then installed or failed with the reason.
- **Vault passphrase lock.** The secrets vault can be locked behind a passphrase, locks itself after a period of inactivity, and has a "Lock now" button.
- **Date dividers in agent chats** that stay pinned to the top while you scroll.

### Fixes
- **Setup terminals stay visible.** The install and sign-in terminals opened from setup used to sit behind the setup card, so you could not see progress or finish a sign-in. They now stay on top, including after you click inside the setup card.
- **Every selected AI tool installs on its own.** One failed install no longer stops the rest; each reports ok or failed separately.
- **Tools installed during setup are detected right away.** Codex (and Claude on some machines) could stay "not installed" until a restart even after installing correctly.
- **The Next button says what it is waiting for**, right next to the button, naming the vendors that still need installing or signing in.
- **Your conversation list survives a restart.** Scheduled chats with a hired agent appear under that agent again, a long chat that continued in a new session shows once instead of several times, and chats are titled by your own message instead of "=== Prior conversation".
- **Background jobs wake the chat that started them** instead of finishing unnoticed.
- **A dispatched agent reports back once**, not after every one of its turns, and the "Dispatched by" label no longer changes after the child replies.
- **A stuck agent restart can no longer freeze a whole project.**

### Security
- Vault lock changes require the dashboard passcode, leave tamper evidence, and quarantine old master-key copies.
- Inline HTML file previews are limited to registered project folders.
