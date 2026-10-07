# MC-1074: browser connections and checks

Branch delivery only. Dave selected option A: two commits on one branch, no merge, push or restart.

## Generic browser setup

The existing type projection offers browser sign-in for every known service and unknown address. X and LinkedIn retain their account identity and credential gates. Other services use `browser_connections.py`, its own human/passcode blueprint, and the wizard screen/summary registries. YouTube is a browser reference in the saved-service store; this setup does not create or authorize a YouTube read account.

`POST /api/desk/connect/browser-profile/commit` accepts `request_id`, `draft: {name, url, browser_profile}` and the human passcode. It validates public HTTPS addresses before the passcode check. It stores only metadata under `services` in `desk.json`, outside DATA_DIR. Both permissions are false and there is no runtime adapter. Requests cannot supply checks, execution rights or secrets. Duplicate request IDs replay only identical details; existing names cannot silently replace a profile. Known X/LinkedIn addresses must use their account setup.

Setup, Permissions, Review, Save and Result remain the shared flow. Unknown discovery is evidence, never authority. The generic browser option exists independently of discovery success. Saving never opens a pane or signs anyone in.

## Recovery checkpoint

The unattended fence refused writing the global `~/.claude/plans` plan (out of project scope). This document carries the scoped implementation and recovery state. Engram MCP tools are absent; no MCP configuration was changed.

## Validation

Prerequisite: 153 touched backend checks passed (`test_desk_browser_connections`, `test_desk_connect_browser_setup`, `test_desk_services`, `test_desk_connect_types`); pyright on the two new modules reported zero errors. `desk-v1-connect-browser-profile.mjs` passed four UI cases (YouTube/unknown, 1440/390). Boot smoke passed all scenarios. The final connect regression matrix and Ron UI test script are added after the checker slice.
