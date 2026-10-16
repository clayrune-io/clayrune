# Bounded CLI update retries (MC-1103)

The server's existing hourly daemon owns retries; no new scheduler, thread
or background process is introduced. Auto-update remains on by default and
the existing `cli_auto_update_enabled` setting is honored on every pass.

## Cadence and state

`mc/cli_update_retry.py` owns cadence and registry-cache policy. The normal
check interval remains 23 hours (the hourly daemon makes this roughly daily).
Only `skipped_in_use` is due again after 3,600 seconds from its last attempt.
The existing live-session, registered-process and OS-process checks in
`mc/cli_update.py` are unchanged, including failed enumeration counting as
in use. Nothing is killed, interrupted, renamed or waited on to free a CLI.

`data/cli_update_state.json` remains the only persisted sidecar, outside
`data/projects/`. Each row now has:

- `checked_ts` / `checked_at`: most recent attempt, including an in-use skip.
- `registry_checked_ts`: daily-check anchor, unchanged across cached retries.
- `latest` / `latest_source`: daily registry answer reused by those retries.

A record from an older install uses its existing `checked_ts` as the initial
registry anchor. Reloading the sidecar retains the retry deadline and cached
version. At the original daily boundary, a normal fresh registry check takes
over, even if every intervening attempt was skipped. Thus one daily window
has at most 22 hourly cached attempts, followed by a new daily cycle. Registry
discovery probes do not run during cached attempts; vendor updaters may still
contact their own services when an installation is actually permitted.

An update or a version already at/above the cached latest stops hourly
retries. Any other result (`update_failed`, `update_no_change`, unknown
version/method, unavailable updater, missing CLI or check error) waits until
the next daily boundary. Disabled updates do no probes or writes. Concurrent
script/daemon passes return a skip rather than running two installers.

## Maintenance script integration

`tools/cli-version-check.py --apply` now POSTs an empty JSON object to
`/api/cli-updates/run`, a separate blueprint in
`mc/blueprints/cli_update_routes.py`. This joins the server's guarded pass
instead of a second independent installer. The endpoint accepts no caller
command, path, version or force setting. It honors normal due times, so
repeated script invocations cannot bypass the registry cache/cadence.

The response has `ok`, `checked` (names/statuses attempted this pass),
`records` (those attempts), and `clis` (all persisted rows, including cached
rows not due). A skipped whole pass also has `skipped` and `reason`.
Row statuses are the server's `updated`, `up_to_date`, `skipped_in_use`, etc.,
and check timestamps identify when cached rows were last attempted. The
script retains local PATH-shadow diagnostics, and returns nonzero for a row
needing attention or a shadow. Report-only operation and `--model-upgrades`
keep their existing paths. Goose remains diagnostic-only in report mode,
outside the built-in updater's supported CLI set.

An older/unreachable server, invalid report or HTTP error returns an explicit
error and exit code 1, without a local-install fallback. This requires a
server running the new code before using the new script's `--apply` path.
It no longer invokes the old script's process sweep or rename-aside update
path: busy CLIs defer to the existing server guard and hourly retry cycle.
The script's old `check_one(..., apply_updates=True)` helper remains for
legacy unit coverage; the public CLI entry point does not call it.

## Validation and handoff

All installer, registry and process dependencies are stubbed in retry tests.
The server integration uses a Flask test client with an isolated data root,
not the running server. Requested regression command:

```text
python -m pytest tests/test_cli_update.py tests/test_cli_version_check.py tests/test_cli_install.py tests/test_qwen_cli_install.py tests/test_cli_update_retry.py tests/test_cli_update_client.py
python -m pytest tests/test_cli_update.py tests/test_cli_version_check.py tests/test_cli_install.py tests/test_qwen_cli_install.py tests/test_cli_update_retry.py tests/test_cli_update_client.py tests/test_system_routes_process_sweep.py
pyright mc/cli_update.py mc/cli_update_retry.py mc/blueprints/cli_update_routes.py
git diff --check
```

Coverage includes multiple occupied retries with one registry probe, exact
hourly boundary, original daily cache expiry, reload persistence, external
update, successful update, other statuses, disabled config, overlapping
passes, server/script handoff and explicit failure without fallback.

No real `--apply`, installed CLI updates, merge, push or server restart are
part of this change. Live hourly behavior awaits integration/restart by the
owner. Roll back by reverting the MC-1103 commit; the extra state timestamp
is harmless to older code.

Branch validation on 2026-10-16: the expanded seven-file pytest command above
passed **128 tests**; Pyright reported **0 errors, 0 warnings** for all three
modules; `git diff --check` passed. The initial four requested suites plus
policy tests also passed (108 tests before client tests were split out).
No frontend code changed, so browser smokes are not applicable. USER_GUIDE
and the API reference are updated; no new agent convention or installation
step requires changes to AGENTS/CLAUDE/README.
