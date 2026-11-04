# YouTube assisted campaign channel

Branch: `clayrune/agent/9a6ab784e60d`. Base: `e9a35f2d` (master at dispatch).
Local review candidate only. Dave owns merge and release; no push or restart.

## Behavior and files

- `mc/desk_assisted_youtube.py` owns Studio steps, first-line title / remaining-line
  description projection, 100/5000-character limits and exactly-one-video validation.
  No text is invented or truncated. It stops on Details; audience, checks, visibility
  and final Publish/Save belong to the person. Attaching can save a private draft.
- `mc/desk_assisted_post.py` registers the route, runs its validator before using
  the pane, supports separate replacement fields and refuses final Save as well as
  Publish. `mc/desk_assisted_pane.py` replaces filename/default copy rather than
  appending to it. `mc/desk_pieces.py` invokes the same route validator at approval.
- `mc/desk_connect/profiles/youtube.json` declares the channel kind and browser
  sign-in. Existing `pane_account.save` creates the named account/profile; registered
  assisted platforms default to assisted. Account label is `YouTube · identity`.
  There is no read/post grant or implicit migration of existing none accounts.
- `static/js/desk-v1-youtube-post.js` owns composer requirements and mapping;
  composer and shared preview wire it in. Preview excludes image-only attachments.
  Assisted controls label YouTube correctly and disclose private draft upload in
  their existing human/passcode prompt. Existing none accounts have the explicit
  passcode-gated enable control; unattended callers still cannot enable it.
- `docs/USER_GUIDE.md` and `CHANGELOG.md` describe the connection and upload flow.
  No rules/index/README change is needed: this adds a channel to existing surfaces.

## Verification

Commands run from the branch root; smokes serve this checkout's assets through
hermetic fixtures, never the running :5199 server. No live account or upload is used.

```powershell
python -m pytest tests/test_desk_assisted_youtube.py tests/test_desk_assisted_post.py tests/test_desk_assisted_accounts.py tests/test_desk_pane_account.py tests/test_desk_readonly_accounts.py tests/test_desk_connect_types.py tests/test_desk_browser_connections.py tests/test_desk_connect.py tests/test_desk_connect_registry_bounds.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_permission_policy.py tests/test_desk_connect_permission_execution.py tests/test_desk_pieces.py -o addopts= -q --tb=short
pyright mc/desk_assisted_youtube.py mc/desk_assisted_pane.py mc/desk_assisted_post.py
node tools/smoke/desk-v1-youtube-channel.mjs
node tools/smoke/desk-v1-connect-browser-profile.mjs
node tools/smoke/desk-v1-assisted-post.mjs
node tools/smoke/desk-v1-composer-preview.mjs
node tools/smoke/desk-v1-composer-media.mjs
node tools/smoke/boot-smoke.mjs
git diff --check
```

Final results (all rc=0): pytest **557 passed in 46.46s**; pyright **0 errors**
across the new module and changed engine/driver; all six Node smoke commands and
`git diff --check` passed. The smoke dependency junction was removed before commit.
The new pytest module
includes real Chromium fixture checks of field replacement on both CDP pipe and
port transports. Those checks exercise local fixture HTML, not YouTube markup.
Smokes cover Connect, composer, video-only validation, text limits, preview and
assisted controls at 1440px and 390px; existing Instagram regressions stay green.

## Live limitations and phase 2

No selector in the Studio route has been run against a signed-in live Studio.
Sign-in, channel identity, file acceptance, Google challenges, upload processing
and actual title/description persistence remain unverified. A selector miss stops
with `step_failed`, leaving the pane for manual completion. The route deliberately
does not advance beyond Details, so `final_button_present` may be false on success.
No final submit was clicked; no user profile, MCP install or live config changed.

Limits and Create → Upload videos flow were checked against
[YouTube Help](https://support.google.com/youtube/answer/57407?co=GENIE.Platform%3DDesktop&hl=en).
Phase 2 remains direct OAuth `videos.insert`, requiring a Google OAuth client and
fresh review of project requirements. The dispatch's historical private-only
restriction should be rechecked: Google's current
[videos.insert reference](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)
says unverified API projects are **not** restricted to private viewing. This fact
does not change the assisted-only scope. The installed public-data/API-key YouTube
MCP is untouched and is not used for channel identity or uploads.

The unattended fence refused the global plan file outside the project. The plan
and session checkpoints are retained in the gitignored worktree journal
`docs/_journal/9a6ab784e60d-youtube-assisted.md`; that journal must be copied if its
history is needed after this worktree is removed. No Engram tools were available.
