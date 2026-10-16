# UI sweep fixes, 2026-10-16

Four ordered commits on the builder branch. Dave reviews and merges; no push or server restart.

## MC-1048: registry version lists

`custom_npm_registry` uses a registry-only 16 MiB / 500,000-node JSON budget. Default page/config parsing remains 2 MB / 20,000 nodes, with the same depth limit. Fixed registry host, request deadline, version-count, archive and closure limits remain enforced.

Validation: `python -m pytest tests/test_desk_connect_registry_bounds.py tests/test_desk_connect_npm_closure.py -q --tb=short` passed (60 tests). `node tools/smoke/boot-smoke.mjs` passed. A read-only call of this checkout's `custom_connection_service.prepare` against npm resolved `@modelcontextprotocol/server-memory@1.0.0` with 98 dependencies, including zod. A headless wizard rendered that actual returned Review card via response interception; Save was blocked. The running server still has its original backend; this was not a live deployment test. The scripts are in the builder's ignored `_scratch/npm_live_review.py` and `_scratch/npm_review_ui.mjs`.

USER_GUIDE needs no change for this fix: the documented Review behavior is restored without new controls or workflow.

## MC-1063: report-waiting notices

Status caches `reports_waiting`; `report-waiting.js` restores notices for the modal's output nodes after the transcript has been reattached. This covers primary and split panes without another network request. The existing smoke now uses a real project modal, receives status with the chat already open, forces a rebuild, closes/reopens, and verifies held/cleared states at 1440 and 390 widths. It previously used an artificial output node outside the modal and missed this race.

Validation: `node tools/smoke/report-waiting-marker.mjs` and `node tools/smoke/boot-smoke.mjs`. No actual child dispatch is needed: canned authoritative server responses exercise both timing paths without running another agent. USER_GUIDE needs no change: this restores the existing notice behavior.
