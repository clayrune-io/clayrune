# Desk v1 default rollout

Desk v1 defaults on as of 2026-10-16. `desk_v1_live` already defaults on, so
opening The Desk uses the live workspace. Turning Desk v1 off restores the
legacy Desk. While on, legacy write handlers retain their read-only guards.
The toggle stays in Settings > Appearance > Experimental beside timezone.

## Saved-config migration

`server._load_config()` calls `mc.desk_v1_default.migrate_desk_v1_default`
after successfully merging saved settings, or with defaults on a fresh install.
An absent `desk_v1_default_migrated` key sets both `desk_v1` and the marker to
true in one `mc.atomic_json.write_json_atomic` write. This handles false values
historically pinned by Settings saving the entire merged config, even when
the user never selected that value. An unmarked intentional opt-out also
switches once because the two cases cannot be distinguished.

Marker presence ends the migration permanently. Marked config is returned
without a write, preserving later toggle choices. The marker is not a
Settings-editable key; normal Settings saves carry it with the whole config.
The write flushes and fsyncs a same-directory temporary file before atomic
replacement. A failed write logs to stderr (config loading precedes logger
wiring), leaves the original file and runtime choice intact, and retries on
the next load. A malformed/unreadable config logs and returns defaults without
writing a marker or overwriting the file.

## Verification

- `python -m pytest -o addopts= -q tests/test_desk_v1_flag.py`: 15 passed.
- `python -m pytest -o addopts= -q tests/test_desk_v1_default.py tests/test_settings_routes.py`: 48 passed.
- `pyright mc/desk_v1_default.py`: zero errors or warnings.
- `node tools/smoke/desk.mjs`: explicit legacy opt-out, plus existing read-only coverage when enabled.
- `node tools/smoke/desk-v1-default.mjs`: requires `MC_SMOKE_BASE_URL` naming a disposable loopback server on a port other than 5199/5200 and its `MC_DATA_DIR`. Reads real config and workspace endpoints, checks the durable marker, completes onboarding on that disposable server, and opens the default Desk and Settings at desktop and phone widths. No mocks, Desk-setting writes, sign-ins or publishing.

The default smoke requires a server started from this checkout with its own
`MC_DATA_DIR` and `MC_REMOTE_ENABLED=0`. A route-only Flask test server can
serve it without starting scheduled/background application subsystems:

```powershell
# Set MC_DATA_DIR to a new scratch directory and MC_PORT to a free port first.
$env:MC_REMOTE_ENABLED = '0'
python -u -c "from server import app, PORT; app.run(host='127.0.0.1', port=PORT, use_reloader=False, threaded=True)"
# In the calling test environment, set MC_SMOKE_BASE_URL to that loopback URL.
node tools/smoke/desk-v1-default.mjs
```

Register any test server/long-running verification command with the Process
Manager immediately. Stop only the server PID returned by that launch. Existing
Desk v1 smokes serve static files against mocked APIs; their names containing
`live` describe the store path under test, not an actual application server.


## Validation record (2026-10-16)

Targeted Python tests: 15 + 48 = 63 passed with the exact commands above.
`pyright mc/desk_v1_default.py`: 0 errors, 0 warnings. `node --check tools/smoke/desk-v1-default.mjs` and `git diff --check` passed.

All 83 existing smoke entry commands below returned exit 0. Counts are each
script's reported check lines, including aggregate/alias entries; they are not
independent assertion totals. The shared fixture helper is not an executable
smoke and was excluded.

| Exact command | Exit | Reported checks |
| --- | ---: | ---: |
| `node tools/smoke/desk.mjs` | 0 | 75 |
| `node tools/smoke/boot-smoke.mjs` | 0 | 15 |
| `node tools/smoke/desk-v1-assisted-post.mjs` | 0 | 22 |
| `node tools/smoke/desk-v1-calendar.mjs` | 0 | 104 |
| `node tools/smoke/desk-v1-campaign-chat.mjs` | 0 | 41 |
| `node tools/smoke/desk-v1-campaign.mjs` | 0 | 122 |
| `node tools/smoke/desk-v1-chat-popout.mjs` | 0 | 45 |
| `node tools/smoke/desk-v1-connect-action-align.mjs` | 0 | 28 |
| `node tools/smoke/desk-v1-connect-api-step.mjs` | 0 | 134 |
| `node tools/smoke/desk-v1-connect-browser-check.mjs` | 0 | 53 |
| `node tools/smoke/desk-v1-connect-browser-permission.mjs` | 0 | 44 |
| `node tools/smoke/desk-v1-connect-browser-profile.mjs` | 0 | 4 |
| `node tools/smoke/desk-v1-connect-custom.mjs` | 0 | 272 |
| `node tools/smoke/desk-v1-connect-discover.mjs` | 0 | 130 |
| `node tools/smoke/desk-v1-connect-flow.mjs` | 0 | 590 |
| `node tools/smoke/desk-v1-connect-held.mjs` | 0 | 134 |
| `node tools/smoke/desk-v1-connect-login-step.mjs` | 0 | 186 |
| `node tools/smoke/desk-v1-connect-mcp.mjs` | 0 | 272 |
| `node tools/smoke/desk-v1-connect-package-check.mjs` | 0 | 0 |
| `node tools/smoke/desk-v1-connect-package-creds.mjs` | 0 | 54 |
| `node tools/smoke/desk-v1-connect-package-progress.mjs` | 0 | 2 |
| `node tools/smoke/desk-v1-connect-package-save.mjs` | 0 | 2 |
| `node tools/smoke/desk-v1-connect-package-step.mjs` | 0 | 272 |
| `node tools/smoke/desk-v1-connect-permissions-step.mjs` | 0 | 200 |
| `node tools/smoke/desk-v1-connect-purpose.mjs` | 0 | 200 |
| `node tools/smoke/desk-v1-connect-remote-step.mjs` | 0 | 374 |
| `node tools/smoke/desk-v1-connect-remote.mjs` | 0 | 374 |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | 0 | 8 |
| `node tools/smoke/desk-v1-connect-signin-details.mjs` | 0 | 134 |
| `node tools/smoke/desk-v1-connect-signin-fill.mjs` | 0 | 8 |
| `node tools/smoke/desk-v1-connect-simplify.mjs` | 0 | 590 |
| `node tools/smoke/desk-v1-connect-slice2.mjs` | 0 | 134 |
| `node tools/smoke/desk-v1-connect-summary-step.mjs` | 0 | 175 |
| `node tools/smoke/desk-v1-connect-unknown-step.mjs` | 0 | 130 |
| `node tools/smoke/desk-v1-connect-wizard.mjs` | 0 | 254 |
| `node tools/smoke/desk-v1-connection-remove.mjs` | 0 | 80 |
| `node tools/smoke/desk-v1-connection-status.mjs` | 0 | 100 |
| `node tools/smoke/desk-v1-connections.mjs` | 0 | 35 |
| `node tools/smoke/desk-v1-conversations.mjs` | 0 | 49 |
| `node tools/smoke/desk-v1-engagement.mjs` | 0 | 18 |
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | 0 | 2 |
| `node tools/smoke/desk-v1-exit.mjs` | 0 | 261 |
| `node tools/smoke/desk-v1-harness.mjs` | 0 | 88 |
| `node tools/smoke/desk-v1-home.mjs` | 0 | 132 |
| `node tools/smoke/desk-v1-how.mjs` | 0 | 38 |
| `node tools/smoke/desk-v1-journey.mjs` | 0 | 38 |
| `node tools/smoke/desk-v1-kit.mjs` | 0 | 87 |
| `node tools/smoke/desk-v1-library.mjs` | 0 | 110 |
| `node tools/smoke/desk-v1-live-accounts.mjs` | 0 | 38 |
| `node tools/smoke/desk-v1-live-calendar.mjs` | 0 | 23 |
| `node tools/smoke/desk-v1-live-campaign.mjs` | 0 | 26 |
| `node tools/smoke/desk-v1-live-engagement.mjs` | 0 | 41 |
| `node tools/smoke/desk-v1-live-home.mjs` | 0 | 17 |
| `node tools/smoke/desk-v1-live-pieces.mjs` | 0 | 33 |
| `node tools/smoke/desk-v1-live-read-pages.mjs` | 0 | 76 |
| `node tools/smoke/desk-v1-live-render.mjs` | 0 | 61 |
| `node tools/smoke/desk-v1-live-results.mjs` | 0 | 18 |
| `node tools/smoke/desk-v1-live-review.mjs` | 0 | 45 |
| `node tools/smoke/desk-v1-live-storyboard.mjs` | 0 | 27 |
| `node tools/smoke/desk-v1-live-writes.mjs` | 0 | 27 |
| `node tools/smoke/desk-v1-map.mjs` | 0 | 112 |
| `node tools/smoke/desk-v1-model-discovery.mjs` | 0 | 28 |
| `node tools/smoke/desk-v1-piece.mjs` | 0 | 17 |
| `node tools/smoke/desk-v1-project.mjs` | 0 | 54 |
| `node tools/smoke/desk-v1-qa-live.mjs` | 0 | 9 |
| `node tools/smoke/desk-v1-quiet-undo.mjs` | 0 | 14 |
| `node tools/smoke/desk-v1-render-scenes.mjs` | 0 | 36 |
| `node tools/smoke/desk-v1-results.mjs` | 0 | 24 |
| `node tools/smoke/desk-v1-retro.mjs` | 0 | 33 |
| `node tools/smoke/desk-v1-review.mjs` | 0 | 51 |
| `node tools/smoke/desk-v1-service-icons.mjs` | 0 | 23 |
| `node tools/smoke/desk-v1-split.mjs` | 0 | 75 |
| `node tools/smoke/desk-v1-store.mjs` | 0 | 29 |
| `node tools/smoke/desk-v1-story-chat.mjs` | 0 | 51 |
| `node tools/smoke/desk-v1-story.mjs` | 0 | 47 |
| `node tools/smoke/desk-v1-studio-article.mjs` | 0 | 47 |
| `node tools/smoke/desk-v1-studio-capture.mjs` | 0 | 2 |
| `node tools/smoke/desk-v1-studio-delete.mjs` | 0 | 36 |
| `node tools/smoke/desk-v1-studio.mjs` | 0 | 366 |
| `node tools/smoke/desk-v1-vault-gate.mjs` | 0 | 32 |
| `node tools/smoke/desk-v1-vault-locked-card.mjs` | 0 | 28 |
| `node tools/smoke/desk-v1-video.mjs` | 0 | 58 |
| `node tools/smoke/desk-v1-where.mjs` | 0 | 71 |

The additional real-server command also returned exit 0, **11 checks passed**:

```powershell
# Disposable test server: MC_REMOTE_ENABLED=0, own MC_DATA_DIR and a free MC_PORT.
# Set MC_SMOKE_BASE_URL to that server's loopback URL in the same environment.
node tools/smoke/desk-v1-default.mjs
```

The final real-server run used a newly created empty data directory and port
53036. It imported the real application and served its Flask routes without
calling `boot()`, so scheduled/background application subsystems were not started.
The test completed only disposable onboarding to avoid the fresh-install wizard
minimizing Desk/Settings during navigation. Desktop and 390px screenshots were
visually inspected; the actual config/workspace requests were not mocked.

The new harness initially used an invalid `Browser.request` API and then exposed
the expected first-run wizard minimizing panels. Those harness issues were
corrected before the successful final run. Screenshot capture disables finite
animations so the Settings transition does not appear as faded text.

Each test runner, direct smoke process and test server was registered. All owned
test servers were stopped. Existing screenshot bytes were restored after the
sweep. Existing smoke dependencies were borrowed through a junction; no npm
install was performed, and the junction was removed before committing.

Not exercised: full `boot()` background subsystem startup, authenticated live
posting/generation, or the running installation's real config migration. This is
a branch-only change; no merge, push or server restart was performed.
