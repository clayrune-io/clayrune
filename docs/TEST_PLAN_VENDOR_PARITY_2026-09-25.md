# UI test plan: vendor parity batch (2026-09-25)

Ron's final gate before pushing `master`. Each test states what to do in the
Clayrune UI and what counts as PASS. Items marked PENDING are not merged yet;
their row is filled in when the fix lands.

**Precondition:** server restarted after the last merge (Settings > Restart).
Every fix below is dead code until then.

| # | Item | Status | Do | PASS when |
|---|------|--------|----|-----------|
| 1 | Codex resume keeps its sandbox (MC-975 gap 5, `7c1d51e`) | merged; PASS 2026-09-25 (Kestrel 42f75b9680d9, 2nd turn replied OK, no error) | Open a Kestrel (Codex) chat. Send "reply OK". Then send "reply OK again". | Second reply arrives; no red error bubble. |
| 2 | Codex usage total survives rollover (`abe0338`) | merged | Nothing to click; covered by tests. Optional: note Kestrel's token count on the chat header before and after a long chat rolls over. | Count never drops back toward zero after a rollover. |
| 3 | AGENTS.md auto-synced from CLAUDE.md (`74a54ad`) | merged; PASS 2026-09-25 (Kestrel printed the generated-from stamp) | Ask Kestrel: "print the first line of AGENTS.md". | Reply shows `<!-- clayrune:generated-from CLAUDE.md sha256=...`. |
| 4 | Background jobs refused on sandboxed sessions (MC-975 gap 3) | merged | Nothing to click; unattended-only path, covered by tests. | n/a |
| 5 | Non-Claude agents get their own worktree (matrix gap 1, `7355a6c`) | merged; PASS 2026-09-25 (Kestrel on clayrune/agent/42f75b9680d9 under .clayrune/agents/; Gemini d078616b86d0 also got a worktree) | Dispatch Kestrel: "run `git branch --show-current` and print your working directory". | Branch is `clayrune/agent/<id>`; path is under `.clayrune/agents/`. |
| 6 | Gemini sees only the project's MCP servers (matrix gap 3, `a12524d`) | merged; PASS 2026-09-25 (Halloway/Gemini self-report: no MCP servers at all; self-report only) | Open a Gemini chat: "list every MCP server and tool you can call". | No `mail` server in the list. |
| 7 | Steward fence gates Codex (MC-975, `4c43454`) | merged, incl. packaged-app fix (`f3ca2a1`) | Nothing to click; unattended-only. Covered by 21 tests and a live self-test (`exit 2 ... CLAYRUNE-FENCE-SELF-TEST-OK`). | n/a |
| 8 | Browser pop-ups as tabs, page dialogs, copy (MC-976 batch A, `14591c9`/`bc33cdc`) | merged | Open linkedin.com/login in the browser pane, click "Continue with Google". Then on any page select text and click the copy button. | A second tab appears with Google's account chooser; closing it returns to LinkedIn. Copied text pastes elsewhere. |

**After all rows pass:** `git push origin master`, then confirm
`git rev-list --left-right --count origin/master...master` prints `0 0`.

Source reports: `docs/VENDOR_HARNESS_MATRIX.md`,
`docs/CODEX_UNATTENDED_SANDBOX_DESIGN.md`.
| 9 | Browser file upload, right-click, IME/shortcuts, sharp text (MC-976 batch B, `853505a`..`54ff080`) | merged | In the browser pane open any site with an upload field (e.g. a Gmail compose attach). Click it, pick a file from THIS device (phone works too). Right-click a link. Press Ctrl+F. | Clayrune's own picker opens and the file attaches; the page's context menu/behaviour fires, not Clayrune's; the page's find bar opens; text looks sharp. |
| 10 | Browser page fills the pane, clicks land, + new tab, copy icon (`da58ae0`/`cd8587c`/`8b106e9`) | merged | Open google.com in the pane. Click the search box. Click `+` at the end of the tab strip. Open two search results in new tabs. | Page fills the pane; the click lands in the box; `+` opens a blank tab with the URL bar focused; both result tabs stay open. |
| 11 | Restart answers in seconds (`365febc`/`9b9268c`/`35f9421`) | merged; PASS 2026-09-25 (`[boot] ready to serve after 2.95s`, was 26.32s) | Power -> Restart. | Dashboard back in under ~10s; no "Restart appears stuck"; no Chromium error page. |
