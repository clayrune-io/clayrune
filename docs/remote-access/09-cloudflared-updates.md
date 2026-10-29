# Verified cloudflared updates

`mc_remote/cloudflared_update.py` owns the Python supervisor's binary update
policy. `cloudflared.py` retains platform selection, binary discovery and
process management. This does not change the control plane, tunnel identity,
enrollment, or the supervisor's `--no-autoupdate` flag.

## Policy and scheduling

- Minimum supported version: **2026.10.2**, compared numerically as
  `(year, month, patch)`, not as a string.
- A version becomes stale **90 days after the first day of its release
  month**. Versions provide a month, not a release day; month-start is a
  conservative approximation. Ninety days allows several monthly releases
  without letting a binary freeze indefinitely. The minimum bypasses age.
- On the supervisor's `start()`/token-refresh path, run `cloudflared
  --version` with a five-second timeout. Query GitHub's latest stable release
  at most once per 24 hours per process. Check metadata even for a supported,
  fresh version, so status can report that a newer release exists.
- Download only when the binary is missing, below minimum, or stale **and**
  the eligible latest release is newer. A newer release alone is reported,
  without forcing an early update. Never downgrade.
- Failed metadata/download attempts retry after 24 hours, preventing a
  supervisor retry loop from hammering GitHub. Timers and latest-known
  metadata are in memory and reset when Clayrune restarts.
- A running connector defers replacement until it exits or the supervisor
  naturally rotates its token. There is no new timer, daemon, forced tunnel
  restart, or server restart. Calling `ensure_binary()` explicitly also uses
  the policy and running-process guard.

Constructor discovery and update-status reads do no network work. Before
the first supervisor start, version fields may be null. Update work runs
on the existing supervisor thread, not a status request. Existing process
status methods still share the process manager's lock with startup.

## Verification, atomic replacement and rollback

1. Fetch `https://api.github.com/repos/cloudflare/cloudflared/releases/latest`
   (15-second network timeout). Reject draft/prerelease tags and releases
   below minimum. Require the exact platform asset.
2. Read the exact asset's SHA256 from the release body's `filename: sha256`
   list. Missing or duplicate entries refuse the update and log the reason.
   Format reference: [Cloudflare's release checksum list](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.6).
3. Download the asset from that **specific tag**, rather than the moving
   `/latest/download` URL, into a unique sibling temporary file (60-second
   network read timeout). Verify the downloaded bytes against that SHA256.
4. For macOS `.tgz`, hash the archive before reading its one regular
   `cloudflared` member. Copy only that member's bytes; never extract paths
   or links. For other platforms use the raw binary. Set executable mode
   and require the candidate's `--version` to match the release tag.
5. Copy the current binary to an atomic rollback file under
   `~/.clayrune/cloudflared-rollback/<sha256-of-resolved-path>/<binary-name>`.
   The copy stays outside the repository. An unwritable backup prevents
   replacement. The original target name remains present throughout.
6. `os.replace()` the candidate onto the target and recheck `--version`.
   Failed validation restores the previous binary through a sibling temp
   and another atomic replacement. Fresh-install validation failure removes
   only the newly installed candidate. Temporary files are cleaned up.

The updater's reentrant lifecycle lock covers preparation and replacement.
`CloudflaredProcess.start()` holds the same lock through `Popen` and process
registration in the updater. Concurrent `ensure_binary()` cannot slip a
replacement between the version check and launch. Tracked live processes
prevent replacement even after launch. This is a process-local guard,
consistent with Clayrune's single-server invariant; it does not coordinate
independent servers or externally managed cloudflared services.

Network, metadata, checksum, candidate-version, backup and replace failures
log the error and preserve the existing binary for startup. An unreadable
installed version is reported and leaves that binary untouched. A failed
restore is different: record both errors, mark the installed version
unknown, and refuse startup through this updater until explicit recovery.
The rollback copy remains available. This prevents launching a candidate
that failed validation just because its name still exists.

For a stopped connector, `updater.rollback(Path(binary))` restores the
previous copy atomically and clears that refusal. It refuses a live
connector. There is no new rollback endpoint or UI action. The backup
contains only the previous executable, never a tunnel token. Rollback
checks executable/version behavior, not tunnel connectivity; a later
tunnel-health failure does not automatically select the previous version.

## Status contract

`GET /api/remote/status` adds an optional `cloudflared` object, passed through
`ProviderStatus` and the existing route serializer. Other providers default
to null. The reference provider includes it even when unenrolled or the
keystore is unavailable. Existing online/error fields keep their meaning.

| Field | Meaning |
| --- | --- |
| `installed_version` | Last observed `--version`, or null before observation / after failed recovery |
| `latest_version` | Last eligible, checksum-bearing release metadata observed, or null |
| `minimum_version` | `2026.10.2` |
| `last_update_error` | Last failed check/install error, or null after a successful check/install |
| `out_of_date` | Missing, below minimum, stale, or older than latest-known |
| `update_pending` | Policy requires replacement and a newer eligible release is known |
| `in_progress` | Asset preparation/replacement in progress |

The cached snapshot uses a separate lock, so reading update fields never
waits on a download. The payload supports a future **tunnel out of date**
label; this change does not add frontend rendering. `download_state()`
retains its original `in_progress` / `last_error` compatibility shape.

## Verification

Network-free tests use temporary fake binaries, temporary rollback storage,
mocked version probes, release metadata and downloads. They never start a
real connector or access the operator's executable or PID ledger.

```text
python -m pytest tests/test_cloudflared_update.py tests/test_cloudflared_reaper.py tests/test_tunnel_watchdog.py tests/test_remote_routes.py -o addopts='' -q
85 passed in 3.57s

pyright mc_remote/cloudflared_update.py
0 errors, 0 warnings, 0 informations
```

A broader check of the updater, process manager, provider implementation and
provider DTO reports one existing `object.stop` typing error in
`cloudflared.py:reset_for_tests`. Checking the unchanged HEAD version of
that file reproduces the same error. The new updater is clean; this change
does not claim the older process module is fully type checked.

Covered: version parsing/minimum/staleness boundaries, checksum and asset
pinning, mismatch/missing-checksum refusal, no-gap replacement, rollback,
backup/replace/restore errors, daily retries, no downgrade, verified fresh
install, offline startup, macOS member isolation, constructor/status I/O
avoidance, start/update concurrency and status payload serialization.
No live binary update, tunnel authentication or deployment is claimed.
