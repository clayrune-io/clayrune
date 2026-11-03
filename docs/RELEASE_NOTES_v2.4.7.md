# Clayrune v2.4.7

1,079 commits since v2.4.6 (2026-10-01), about 220 changelog entries. Full detail is in `CHANGELOG.md`; this is the short version.

In-app updates already track `master`, so most installs have much of this. This release refreshes the downloadable installers.

## Desk
- Desk v1 opens by default.
- Posts are written in one composer: copy, media, channels from the campaign, preview, scheduling, send to review.
- The When calendar shows one card per post; drop it on a channel row to schedule it there.
- YouTube campaign accounts, with assisted video upload.
- Saved connections: edit, restore and remove them; LinkedIn Company Page reading; clearer sign-in recovery.

## Studio
- Every new video opens in the same Studio editor, with a timeline you can drag scenes into and reorder.
- Play and export the timeline locally; free preview of arranged scenes.
- A fixed-scale time ruler you can drag to stretch or squeeze; the timeline uses the full editor width.
- Your own media folders and a PC file explorer; Windows video choosers show common video formats.

## Agents and stewards
- Steward roles: role editor, guarded role cycles, shared assignment state, pilot migration tool.
- Hivemind runs workers from the roster with pinned context and validated routing plans.
- Native resume delivers current instructions; selected MCP servers carry into Codex, Gemini, Qwen and Aider launches.
- Opt-in unattended Aider, restricted to an audited coding surface.
- Pace-aware dispatch routing; Haiku 5.5 in the model pickers.

## Browser pane and vault
- The browser pane passes Google's "may not be secure" check again, and follows you between phone and desktop.
- An unlocked vault stays unlocked across server restarts.
- Agents cannot change project safety or routing settings; those need a person.

## Installers
- Windows and Linux/macOS install scripts add optional passkey support. A machine with no wheel for it installs everything else and reports passkeys as unavailable.

## macOS
The v2.4.7 Mac build follows within a day. Until then this release carries the notarized v2.4.6 Mac app, so the Mac download keeps working and Mac installs do not see a false update.
