# Desk map smoke: expired Resume fixture

Base: `96dc8f302bd8a6d1adf8ea54ac3516dd4d39386e`. Local branch candidate;
no master merge, push, production restart, or live Desk mutation.

## Finding

The reported timeout was an expired fixture, not a moved or missing Pause
control. `desk-v1-campaign.js` still renders `data-launch-pause` for active
campaigns and `data-launch-resume` for paused campaigns. `_openResumeSheet`
refuses an expired `plan.end.date`, keeps the campaign paused, and explains
the refusal. The composer specification explicitly leaves When and Launch
unchanged.

The supplied base already includes C6 commit `a3b7ae9a`, which gives the map
scenario a relative future end date. The untouched base smoke passed (rc 0).
An isolated reproduction removed only that existing date setup, retained the
original `2026-10-20` fixture end, and ran the Launch scenario. It failed with
rc 1 at the exact reported `data-launch-pause` wait. Evidence immediately
before the timeout:

```text
state: paused
end: 2026-10-20
toast: Can’t resume “Restore points launch” — its end date has passed. Fix it, then resume.
```

This reproduction is a deliberately altered scratch copy, not a claim that
the current base fails. `af91f31e` lacks C6's future-date setup.

## Change

- Preserve C6's relative future date for successful Resume; assert both the
  campaign state and rendered Live heading after confirmation.
- Then expire the plan relative to today and assert the actual refusal toast,
  paused state, paused heading, Resume present, and Pause absent.
- `DESK_MAP_WIDTH=1440|390` runs the full flow at either width. Existing cases
  with an explicitly specified viewport retain that viewport. Launch screenshot
  filenames use the selected width.
- Serve this worktree's real HTML/JS/CSS from a dedicated loopback HTTP server
  on an OS-selected free port. APIs use smoke fixtures; the server closes in
  `finally`. No application traffic goes to `:5199` (only process registration).

## Validation

The main checkout's `tools/smoke/node_modules` was linked, without installation.
The scratch runner registers its own PID and each spawned fixture/browser
process, then imports the requested, unchanged smoke entry point. Other Desk
smokes retain their own hermetic route servers or ephemeral Flask fixture
servers; none uses the running application on `:5199`.

Final results: both full map widths returned rc 0; `python -m pytest -k desk`
returned rc 0 (4,024 passed, 13,977 deselected, two existing SyntaxWarnings).
Of all 117 matching `desk-v1-*.mjs` files, 116 passed, including the fixture API
helper and the aggregate exit gate (which also reran its 21 child smokes).
The all-green verification requirement remains unmet for default-routing.

The initial default-routing invocation returned rc 1 because its mandatory
isolated-server environment was missing. Its retry used disposable data and
vault directories, remote access disabled, and the real Flask application on
an ephemeral loopback port, without boot workers or config mocks. It passed
default migration, onboarding, and live Desk loading, then waited when Settings
requested the provider catalog. A read-only Python stack capture showed that
request in `maybe_set_sole_provider_default` -> `installed_runtimes` -> Gemini
`health_check` -> `subprocess.run` -> Windows pipe reader join, despite the
version probe's configured 10-second timeout. The runner terminated its own
test process at its 600-second deadline and shut down its isolated server.
The final retry returned rc 1. The first batch runner also returned rc 1.

This separate provider-runtime hang is outside the map test change. No provider
behavior was modified and default-routing is not reported as passing. Dave's
open decision: investigate/fix the version-probe hang separately (recommended),
or explicitly accept this existing verification gap for the map-only change.
The current diff cannot cause the provider hang: no product source is modified,
and the default-routing test does not import the map test.

The commands below name smoke entry points imported through the registration
runner described above. Exact invoked commands and log paths remain in the
gitignored `_scratch/desk-map/EXACT_COMMAND_RESULTS.md`. Both default-routing
attempts are retained; repeated map runs are retained as well.

| Entry point / command | rc |
|---|---:|
| `node tools/smoke/desk-v1-account-signin.mjs` | 0 |
| `node tools/smoke/desk-v1-article-chat.mjs` | 0 |
| `node tools/smoke/desk-v1-assisted-post.mjs` | 0 |
| `node tools/smoke/desk-v1-calendar.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign-chat.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign-content.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign-ended.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign.mjs` | 0 |
| `node tools/smoke/desk-v1-chat-popout.mjs` | 0 |
| `node tools/smoke/desk-v1-composer-duplicate.mjs` | 0 |
| `node tools/smoke/desk-v1-composer-media.mjs` | 0 |
| `node tools/smoke/desk-v1-composer-preview.mjs` | 0 |
| `node tools/smoke/desk-v1-composer.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-action-align.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-browser-check.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-browser-permission.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-browser-profile.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-company-page.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-custom.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-discover.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-edit-regressions.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-flow.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-held.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-login-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-mcp.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-check.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-creds.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-read-support.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-saved-edit.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-saved-fields.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-saved-permissions.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-signin-details.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-signin-fill.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-signin-reuse.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-simplify.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-slice2.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-summary-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-unknown-step.mjs` | 0 |
| `node tools/smoke/desk-v1-connect-wizard.mjs` | 0 |
| `node tools/smoke/desk-v1-connection-language.mjs` | 0 |
| `node tools/smoke/desk-v1-connection-remove.mjs` | 0 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 |
| `node tools/smoke/desk-v1-conversations.mjs` | 0 |
| `node tools/smoke/desk-v1-default.mjs (isolated server retry)` | 1 |
| `node tools/smoke/desk-v1-default.mjs (initial invocation without required environment)` | 1 |
| `node tools/smoke/desk-v1-delete-draft-live.mjs` | 0 |
| `node tools/smoke/desk-v1-engagement.mjs` | 0 |
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | 0 |
| `node tools/smoke/desk-v1-exit.mjs` | 0 |
| `node tools/smoke/desk-v1-file-explorer.mjs` | 0 |
| `node tools/smoke/desk-v1-fixture-api.mjs` | 0 |
| `node tools/smoke/desk-v1-harness.mjs` | 0 |
| `node tools/smoke/desk-v1-home.mjs` | 0 |
| `node tools/smoke/desk-v1-how.mjs` | 0 |
| `node tools/smoke/desk-v1-journey.mjs` | 0 |
| `node tools/smoke/desk-v1-kit.mjs` | 0 |
| `node tools/smoke/desk-v1-library.mjs` | 0 |
| `node tools/smoke/desk-v1-live-accounts.mjs` | 0 |
| `node tools/smoke/desk-v1-live-calendar.mjs` | 0 |
| `node tools/smoke/desk-v1-live-campaign.mjs` | 0 |
| `node tools/smoke/desk-v1-live-engagement.mjs` | 0 |
| `node tools/smoke/desk-v1-live-home.mjs` | 0 |
| `node tools/smoke/desk-v1-live-pieces.mjs` | 0 |
| `node tools/smoke/desk-v1-live-read-pages.mjs` | 0 |
| `node tools/smoke/desk-v1-live-render.mjs` | 0 |
| `node tools/smoke/desk-v1-live-results.mjs` | 0 |
| `node tools/smoke/desk-v1-live-review.mjs` | 0 |
| `node tools/smoke/desk-v1-live-storyboard.mjs` | 0 |
| `node tools/smoke/desk-v1-live-writes.mjs` | 0 |
| `node tools/smoke/desk-v1-model-availability.mjs` | 0 |
| `node tools/smoke/desk-v1-model-discovery.mjs` | 0 |
| `node tools/smoke/desk-v1-outcomes.mjs` | 0 |
| `node tools/smoke/desk-v1-piece-copy.mjs` | 0 |
| `node tools/smoke/desk-v1-piece-editor.mjs` | 0 |
| `node tools/smoke/desk-v1-piece-media.mjs` | 0 |
| `node tools/smoke/desk-v1-piece-name-upload.mjs` | 0 |
| `node tools/smoke/desk-v1-piece.mjs` | 0 |
| `node tools/smoke/desk-v1-post-delete.mjs` | 0 |
| `node tools/smoke/desk-v1-post-preview.mjs` | 0 |
| `node tools/smoke/desk-v1-project-planner.mjs` | 0 |
| `node tools/smoke/desk-v1-project.mjs` | 0 |
| `node tools/smoke/desk-v1-qa-live.mjs` | 0 |
| `node tools/smoke/desk-v1-quiet-undo.mjs` | 0 |
| `node tools/smoke/desk-v1-reads-off.mjs` | 0 |
| `node tools/smoke/desk-v1-reapproval.mjs` | 0 |
| `node tools/smoke/desk-v1-render-scenes.mjs` | 0 |
| `node tools/smoke/desk-v1-results.mjs` | 0 |
| `node tools/smoke/desk-v1-retro.mjs` | 0 |
| `node tools/smoke/desk-v1-review.mjs` | 0 |
| `node tools/smoke/desk-v1-service-icons.mjs` | 0 |
| `node tools/smoke/desk-v1-split.mjs` | 0 |
| `node tools/smoke/desk-v1-store.mjs` | 0 |
| `node tools/smoke/desk-v1-story-chat.mjs` | 0 |
| `node tools/smoke/desk-v1-story.mjs` | 0 |
| `node tools/smoke/desk-v1-studio-article.mjs` | 0 |
| `node tools/smoke/desk-v1-studio-capture.mjs` | 0 |
| `node tools/smoke/desk-v1-studio-delete.mjs` | 0 |
| `node tools/smoke/desk-v1-studio.mjs` | 0 |
| `node tools/smoke/desk-v1-vault-gate.mjs` | 0 |
| `node tools/smoke/desk-v1-vault-locked-card.mjs` | 0 |
| `node tools/smoke/desk-v1-video.mjs` | 0 |
| `node tools/smoke/desk-v1-what-add-modal.mjs` | 0 |
| `node tools/smoke/desk-v1-where-attach.mjs` | 0 |
| `node tools/smoke/desk-v1-where-drag-service.mjs` | 0 |
| `node tools/smoke/desk-v1-where-services.mjs` | 0 |
| `node tools/smoke/desk-v1-where.mjs` | 0 |
| `node tools/smoke/desk-v1-youtube-channel.mjs` | 0 |
| `DESK_MAP_WIDTH=1440 node tools/smoke/desk-v1-map.mjs` | 0 |
| `DESK_MAP_WIDTH=1440 node tools/smoke/desk-v1-map.mjs` | 0 |
| `DESK_MAP_WIDTH=390 node tools/smoke/desk-v1-map.mjs` | 0 |
| `DESK_MAP_WIDTH=390 node tools/smoke/desk-v1-map.mjs` | 0 |
| `python -m pytest -k desk` | 0 |

`node --check tools/smoke/desk-v1-map.mjs`: rc 0. `git diff --check`: rc 0.

No product source changed. README, USER_GUIDE, architecture/rules files and
pyright are not applicable to this test-only change. Test screenshots and
scratch wrappers are not commit candidates.

## Handoff blocker

Automatic approval review refused the required non-recursive removal of the
smoke dependency junction as "destructive delete outside scratch". It requires
a fresh human-started chat; no alternate deletion was attempted. The junction
remains in this worktree. Since the dispatch explicitly requires removing it
before committing, the three named source/doc paths are staged but uncommitted.
The operator-specific cleanup command is in the gitignored session journal.
After removing only the junction (never its target), the spawner can commit
the staged map smoke, CHANGELOG entry, and this report locally. No merge,
push or production restart was performed.
