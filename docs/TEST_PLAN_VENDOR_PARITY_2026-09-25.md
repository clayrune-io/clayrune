# UI test plan: vendor parity batch (2026-09-25)

Ron's final gate before pushing `master`. Each test states what to do in the
Clayrune UI and what counts as PASS. Items marked PENDING are not merged yet;
their row is filled in when the fix lands.

**Precondition:** server restarted after the last merge (Settings > Restart).
Every fix below is dead code until then.

| # | Item | Status | Do | PASS when |
|---|------|--------|----|-----------|
| 1 | Codex resume keeps its sandbox (MC-975 gap 5, `7c1d51e`) | merged | Open a Kestrel (Codex) chat. Send "reply OK". Then send "reply OK again". | Second reply arrives; no red error bubble. |
| 2 | Codex usage total survives rollover (`abe0338`) | merged | Nothing to click; covered by tests. Optional: note Kestrel's token count on the chat header before and after a long chat rolls over. | Count never drops back toward zero after a rollover. |
| 3 | AGENTS.md auto-synced from CLAUDE.md (`74a54ad`) | merged | Ask Kestrel: "print the first line of AGENTS.md". | Reply shows `<!-- clayrune:generated-from CLAUDE.md sha256=...`. |
| 4 | Background jobs refused on sandboxed sessions (MC-975 gap 3) | merged | Nothing to click; unattended-only path, covered by tests. | n/a |
| 5 | Non-Claude agents get their own worktree (matrix gap 1) | PENDING Tobin | Dispatch Kestrel: "run `git branch --show-current` and print your working directory". | Branch is `clayrune/agent/<id>`; path is under `.clayrune/agents/`. |
| 6 | Gemini sees only the project's MCP servers (matrix gap 3) | PENDING Tobin | Open a Gemini chat: "list every MCP server and tool you can call". | No `mail` server in the list. |
| 7 | Steward fence gates Codex (MC-975 security finding) | PENDING Bram | Filled in when Bram reports whether fail-closed is achievable. | A blocked write is refused, not run. |

**After all rows pass:** `git push origin master`, then confirm
`git rev-list --left-right --count origin/master...master` prints `0 0`.

Source reports: `docs/VENDOR_HARNESS_MATRIX.md`,
`docs/CODEX_UNATTENDED_SANDBOX_DESIGN.md`.
