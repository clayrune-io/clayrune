# UI sweep fixes, 2026-10-16

Four ordered commits on the builder branch. Dave reviews and merges; no push or server restart.

## MC-1048: registry version lists

`custom_npm_registry` uses a registry-only 16 MiB / 500,000-node JSON budget. Default page/config parsing remains 2 MB / 20,000 nodes, with the same depth limit. Fixed registry host, request deadline, version-count, archive and closure limits remain enforced.

Validation: `python -m pytest tests/test_desk_connect_registry_bounds.py tests/test_desk_connect_npm_closure.py -q --tb=short` passed (60 tests). `node tools/smoke/boot-smoke.mjs` passed. A read-only call of this checkout's `custom_connection_service.prepare` against npm resolved `@modelcontextprotocol/server-memory@1.0.0` with 98 dependencies, including zod. A headless wizard rendered that actual returned Review card via response interception; Save was blocked. The running server still has its original backend; this was not a live deployment test. The scripts are in the builder's ignored `_scratch/npm_live_review.py` and `_scratch/npm_review_ui.mjs`.

USER_GUIDE needs no change for this fix: the documented Review behavior is restored without new controls or workflow.

## MC-1063: report-waiting notices

Status caches `reports_waiting`; `report-waiting.js` restores notices for the modal's output nodes after the transcript has been reattached. This covers primary and split panes without another network request. The existing smoke now uses a real project modal, receives status with the chat already open, forces a rebuild, closes/reopens, and verifies held/cleared states at 1440 and 390 widths. It previously used an artificial output node outside the modal and missed this race.

Validation: `node tools/smoke/report-waiting-marker.mjs` and `node tools/smoke/boot-smoke.mjs`. No actual child dispatch is needed: canned authoritative server responses exercise both timing paths without running another agent. USER_GUIDE needs no change: this restores the existing notice behavior.

## MC-713: service-worker update connection slots

The Installed App update action lives in its own module. It pauses this tab's SSE handles and watchdogs, defers new stream connections, then resynchronizes status on success without an update or on failure. When navigation is pending, streams stay paused until unload: reconnecting immediately can starve the replacement document too. The check has a 12-second deadline, plus the existing bounded installation wait and reload fallback. Late getRegistration completion cannot attach a reload listener after timeout; listeners/timers are cleaned up.

Validation: `node tools/smoke/installed-app-update-streams.mjs` uses a disposable local HTTP/1 server, six real EventSources, and an actual service worker. Unchanged worker: toast and six streams reconnect. Changed worker: activation and page navigation. A deliberately blocked update: timeout, button enabled, streams resume. `node tools/smoke/installed-app.mjs`, `node tools/smoke/agent-stream-dedupe.mjs`, `node tools/smoke/agent-log-epoch-reconcile.mjs`, `node tools/smoke/provider-neutral-rail-cache.mjs` and boot smoke pass. USER_GUIDE now documents the pause and timeout. Other browser tabs own their streams; the timeout covers contention this tab cannot release.

## MC-1033: history dispatch lineage

History reconstruction returns `spawned_by_session_id` using the existing `_row_spawned_by` / `_durable_spawner_of_record` policy, including its manual-callback refusal and legacy-cycle guard. All four frontend reconstruction branches retain it. A small separate `conversation-lineage.js` helper also reads server-projected conversation rows, disambiguates transcript twins, and refuses conflicting parents. The route monolith only wires the existing lineage resolver into existing response shapes; no new policy is implemented there.

The named apex_trader Rusk session `eff04f179307` DOES have a recorded parent, `b03b8d64dd31`, but lies outside the first 20 conversation rows. A cache-only fix would therefore miss it. The branch reconstruction function read the actual saved record and Codex transcript and returned that parent with 84 history lines. A headless browser overlaid checkout assets and that actual response onto the live dashboard: banner visible and composer bottom 814/844 at 390 width (also checked at 412). The running backend was not restarted or modified.

Validation: `python -m pytest tests/test_history_dispatch_lineage.py tests/test_conversation_union.py tests/test_codex_rollout_tool_markers.py tests/test_revive_notify_carry.py -q --tb=short` passed (38 tests). `node tools/smoke/history-dispatch-banner.mjs` passed at 1440 and 390 (rail click, modal rebuild, composer fit, off-page record, transcript twin, missing lineage). `node tools/smoke/split-view-idle-conversation.mjs`, report-waiting smoke and boot smoke passed. Named-session checks are retained in ignored `_scratch/history_live_response.py` and `_scratch/history_live_ui.mjs`. USER_GUIDE documents completed-child banners. No lineage is invented when the server has no record.

## Final cross-checks and handoff limits

- `npm test` in `tools/smoke`: exit 0, including boot, cursor deduplication, provider-neutral rail cache, image viewer and file viewer checks.
- `python -m pytest tests/test_desk_connect_registry_bounds.py tests/test_desk_connect_npm_closure.py tests/test_desk_connect_parameters.py -q --tb=short`: exit 0 (208 tests), checking the page/config parser defaults as well as registry resolution.
- `python -m pytest tests/test_agent_routes.py tests/test_history_dispatch_lineage.py -q --tb=short`: exit 1, solely `test_compose_install_batch_posix_runs_each_vendor_independently` (Windows system bash returned 0, expected 1). The unchanged `_compose_install_batch` function extracted from commit `9f09fea2` reproduces that result under the same shell. No installer code was changed; the failure is outside these four fixes. Reproduction script/log: ignored `_scratch/baseline_install_batch.py`, `_scratch/agent-routes-tests.log`.
- Actual Rusk composer also fits at 412 width: bottom 885/915. Browser screenshots are ignored `_scratch/rusk-fixed-m390.png` and `_scratch/rusk-fixed-m412.png`.
- No server restart, merge, push, passcode entry, package install or actual child dispatch. Backend UI verification used real branch-produced responses through interception, not a claim of live deployment. Real-device testing remains unperformed.
- Engram tools were unavailable. The worktree journal retains the plan; its requested global plan-file write was rejected by the unattended fence and was not retried. Dave should copy the ignored journal/scratch evidence if retaining it after worktree removal.

## 2026-10-21: provider-settings compatibility in the history smoke

The composer now loads `/api/engine/preferences` during modal rebuilds. The
history-banner harness's catch-all API response (`[]`) did not satisfy that
endpoint's `{providers: [...]}` contract. `effective()` then threw during render,
and `openConversation` fell through from the MC session reconstruction to a
transcript-keyed tab. The expected MC-keyed banner could therefore time out while
the transcript-keyed banner was actually present.

The harness now returns `{providers: []}` for this unrelated catalog. No product
code, lineage policy, response interception scope or human gate changes. The
original banner, rebuild, composer, transcript-twin, off-page and missing-lineage
assertions pass at 1440 and 390 pixels with no uncaught page errors.

Validation: `node tools/smoke/history-dispatch-banner.mjs` exits 0. USER_GUIDE,
README and agent instructions need no update: this is test-fixture maintenance
with no user-facing change. Operator-specific acceptance results and live-device
limits belong in the ignored acceptance journal rather than this source document.
