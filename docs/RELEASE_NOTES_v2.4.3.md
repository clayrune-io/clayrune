# Clayrune v2.4.3

186 commits since v2.4.2 (2026-09-28 to 2026-09-29).

### New
- **Right-to-left languages.** Hebrew and Arabic replies, messages and question cards now read and align right-to-left, while code, paths and URLs inside them stay left-to-right (MC-1000).
- **Usage breakdown.** Hover the bottom usage bar for a per-provider popup; the full report (tokens, sessions, lines of code, "tokens per 1%" calibration) lives under Advanced (MC-998).
- **Desk v1 reorganised** as Project, then Campaign, then Piece, plus an Engagement dashboard: project cards on Desk Home, per-project Presence settings, a 3-step in-page campaign setup, and archived campaigns restorable from the project page (MC-977).
- **Sonnet 5.5** in every model picker; the auto-router's Sonnet tier uses it and its Opus tier moves to Opus 5.5.
- **Split view** can open a conversation from another project in the second pane (MC-951).
- **Send feedback** link in the sidebar and mobile drawer footer.

### Fixes
- Usage bar: sub-second jitter in the reset time no longer makes every range unmeasurable, idle time no longer blocks calibration, and Claude token totals no longer mix per-run and lifetime counts (they had read about 25x too high).
- The Floor loads project data once per poll instead of once per figure.
- The conversation rail shows "Loading conversations" until its first fetch lands, instead of an empty list.
- The bottom project-tab strip starts past the sidebar instead of under it.
- The guardian nudges about a missed question once per episode, not repeatedly.

### Security
- The dashboard passcode now guards all 27 human-only routes; first-run setup asks for it once (MC-995).
- Unattended-agent fence hardening from a one-shot review pass (MC-994).
- The secrets vault quarantines legacy key copies with correct directory permissions and retries on unlock.
