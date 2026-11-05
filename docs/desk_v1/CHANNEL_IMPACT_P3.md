# MC-1152 P3: GitHub context collection and cached comparison

Status: **dark branch candidate, fixture validated**. Base `31ed0656` (P1).
Backlog `e4796e7b`; Sol_Tobin, 2026-11-05 UTC. The entire
`CHANNEL_IMPACT_SPEC.md`, especially sections 12/13, governs this phase.
D1=A: repository context; channel conversions are **Not attributable by this source**.

## Delivery and darkness

- `mc/desk_impact_sources/github.py`: one GitHub adapter; `registry.py` imports
  and registers its metadata. Imports make no calls and dispense no secrets.
- `mc/desk_impact_github_transport.py`: opt-in GET-only transport. No routes,
  startup hook, scheduler, default connection resolver or live connection is wired.
- `mc/desk_impact_collect.py`: explicit shared collection over the unchanged P1
  store/schema/interface. Campaigns reference shared snapshots, not copied totals.
- `mc/desk_impact_compare.py`: local-only projection for P4. It does not invoke
  an engagement reader, update outcomes, dispense a secret or mutate a goal.
- `mc/desk_impact_time.py`: canonical UTC identities make equivalent `Z` and
  `+00:00` input windows reuse the same cache and exact-window projection.
- The four corresponding test modules and `tests/desk_impact_github_fakes.py`
  supply injected HTTP, clock, connection metadata and isolated temporary stores.
  `tests/test_desk_impact_schema_sources.py` updates P1's empty-registry assertion
  to the P3 GitHub-metadata-only contract.

No P2 composer/draft/release path was edited. No new dependency, credential,
live GitHub API call, vault read, config change, merge, push or restart. No static
files changed, so the static smoke gate does not apply. USER_GUIDE.md/README.md
need no update: this phase adds no user-visible behavior. No agent rules or
memory-system changes are needed. The journal is gitignored and stays in this
worktree; this document carries the durable handoff.

## Adapter and connection contract

The adapter implements P1's `capabilities`, `validate_definition`, `collect` and
`normalize` unchanged. One definition selects one metric/unit/target. Targets
use lowercase `owner` and `repo`; `downloads` additionally requires a positive
stable `asset_id`. Referrer metrics require a `referrer` hostname. No submitted
target can supply a host, URL, path separator, credential or extra field.

P1 intentionally has no default connection resolver. The eventual server seam
must supply its **trusted existing connection metadata**, projected as:

```python
{
    'id': '<chosen connection ID>', 'project_id': '<owning project ID>',
    'target': {'owner': '<lowercase owner>', 'repo': '<lowercase repository>'},
    'vault_ref': '<human-selected vault reference>',
    'read_enabled': True,  # assertion from the existing connection/read policy
}
```

This is an injected view, **not a new connection store or a permission editor**.
Absent/foreign/disabled connections fail closed. Nothing discovers a token by
guessing its vault name. `GitHubTransport` calls existing `get_secret_value`
with project, consumer and the server's true unattended trigger context; vault
scope and attended-only restrictions remain enforced. No agent-facing endpoint
can construct this mapping. Connection UI/server activation remains unmounted.

Only `https://api.github.com/repos/<selected owner>/<selected repo>/...` is
allowed, for stargazers, traffic views/referrers and a configured release asset.
Redirects, including same-host redirects, are refused. Query/header allowlists
exclude arbitrary URLs and Authorization overrides. Each HTTP call has a 10s
timeout and a 2 MB body ceiling. Stargazers paginate at 100/page, at most 100
pages; exceeding the bound is an explicit partial result, never truncation.
The only carried next-page URL must name the exact host/path and next page;
the adapter constructs each URL itself rather than following an external link.
The transport discards HTTP error bodies and all but rate-limit error headers.

`collect` receives an **unbound** `CollectRequest` with a bounded historical UTC
window. It collects repository stars/traffic/referrers, or one configured asset.
The P1 request has no selected-metric field, so the orchestrator deliberately
collects source context once without campaign/definition IDs and returns the
definition revision separately beside snapshot references. It never upgrades
repository scope. Asset selection is explicit; this phase does not discover all
assets or aggregate downloads across replacements.

## Measurement and cache semantics

| Metric | Stored semantics / limits |
|---|---|
| `stars` | Currently retained stargazers timestamped in the exact half-open requested interval; only after complete valid pagination. Not every historical star/unstar event. |
| `star_total` | Observed cumulative retained-star count. Derived changes are labelled **net** changes, never gross new stars. |
| `traffic_views` | Native UTC-day counts, keyed per day so overlapping polls correct a day rather than add windows. Current UTC day remains null/incomplete. Missing/expired days are unknown. |
| `traffic_unique_visitors` | Native per-day unique visitors; the comparison never sums uniques across days. |
| `referrers_listed` | Number of rows in one native rolling 14-day **top-ten** list; zero means an observed empty list only. |
| `referrer_views`, `referrer_unique_visitors` | Per-host values from that native list. No arbitrary-range interpolation, subtraction, overlapping-window addition or attribution. Hosts omitted by the newest successful list are not carried forward. |
| `downloads` | Configured asset's cumulative `download_count`, keyed by its stable ID. First sample is a baseline. A replacement ID starts a separate series. |

Traffic counts/uniques are validated as nonnegative integers; uniques cannot
exceed counts, daily timestamps cannot duplicate or leave the native window,
and returned daily counts must agree with the returned total. Malformed endpoint
data produces no successful observations for that endpoint; other successful
endpoints can retain their independent context. No raw stargazer identities,
user records, response bodies or credential values enter persistence/status.

The orchestrator serializes same-store/project callers in the **single server
process**. P1 retains atomic durable writes. It is not a cross-server HTTP lease;
the project's existing single-instance invariant still applies. A batch groups
campaign definitions by source/connection/target and returns the same snapshot
IDs to overlapping campaigns. Successful same-day reads reuse the cache (stars
also require the exact requested interval). Failed attempts back off 15 minutes;
429/secondary limit retry-after is persisted connection-wide, including across
subsequent batches/asset targets. Manual refresh has no bypass.

Successful observations retain their observed timestamps. Failures change
coverage/last-attempt status and preserve last-good values/last-success time.
Gaps accumulate durably through P1: incomplete requested coverage, missing
endpoint observations and more than one day between successful collections.
Startup backfill can preserve supplied daily observations but cannot manufacture
older missing days. A partial append before a crash cannot make another window
look successfully collected; replay retains one effective value per P1 key.

Download interval deltas require compatible observations **at both exact
requested endpoints**, no missing/failed observation or downtime gap, and no
intermediate counter decrease. There is no interpolation. A reset, deletion,
missing baseline or identity change yields null plus a reason; a negative change
is never clamped into a successful zero. Signed net change, where observed, is
context only. After broken continuity the next successful sample is a baseline
for a new compatible interval, not an acquisition count across the gap.

## Cached comparison contract

`comparison(store, project_id, campaign_id, window, ledger=..., accounts=...)`
returns the P1 Goal binding (`goal`, active `definition`, `revisions`, dark
`auto_progress`) plus `channels` and one separate `repository_context`.
Without injected ledger/accounts it reads the existing local Desk store under
its lock, without the default `list_ledger` retention/row limit.

All plan accounts remain visible. Receipts are filtered by exact project,
campaign and publication interval, then grouped by stable account ID. Modern
`ledger.account` is that ID. Removed IDs retain historical rows; missing/bare
platform identities remain labelled unknown and are not assigned to the first
account on that platform. Repeated receipt IDs and repeated cumulative metric
snapshots are not added. Metrics select the latest persisted `source='feed'`
observation per receipt/metric; manual Goal/outcome values stay untouched.
Missing metrics/clicks are unknown, including a partially measured group of posts.

Metrics retain their exact names and the label **Latest totals for posts
published in this period**. Their original read times remain visible; the oldest
included metric read is the group read time. Existing persisted outbound clicks
occupy `platform_clicks` only. The projection does not switch X non-public reads
on or invent LinkedIn coverage. Every GitHub channel conversion is null with
**Not attributable by this source**. No pooled conversion total, winner, rate,
ROI, campaign progress, publishing action or destination change is derived.

Context includes target/window/unit/scope/provenance/snapshot IDs and separate
attempt coverage. Native referrer windows are explicitly not exact requested
range counts. A failed attempt or observation older than the requested end is
marked stale; timestamps are never replaced with projection time. A truncated
snapshot tail is surfaced via P1's read flag.

## Required P3 evidence

| Section-12 evidence | Offline regression coverage |
|---|---|
| Traffic windows | Native UTC days, current partial day, old/missing day gaps, inconsistent totals/uniques, nonadditive daily uniques. |
| Top-ten referrers | Ten-row bound, observed empty list, rolling-window labels, newest-list host omission, no summing overlapping snapshots. |
| Paginated stars | Half-open boundaries, timestamp media type, complete multi-page counts, duplicates/malformed rows/unsafe next URLs, bounded pagination, failed later page yields no partial count. |
| Cumulative baselines | First sample unknown; compatible daily 100→105 gives 5, 100→100 gives successful 0. |
| Deleted/reset assets | 404 preserves last-good count, replacement ID cannot join, 100→90 and 100→90→110 remain unknown across the reset. |
| 403/404/429/timeouts/partial | Safe typed errors, discarded bodies, explicit incomplete pagination/coverage, retry-after seconds/date/reset, request/body bounds. |
| Overlapping campaigns | Two definitions join identical shared snapshot IDs, three repository requests total; concurrent callers and subsequent same-day retries are read-free. |
| Downtime gaps survive | Four-day outage persists across a reopened store and later success; deltas cannot bridge it. |
| Retry no double count | Same-key corrections retain history but select one effective value; crash during append cannot falsely cache the new interval. |
| UTC equivalence | `Z` and `+00:00` spellings share cache/projection identity; future/unbounded collection cannot bypass validation via a cached asset. |
| Negative changes | Null reason and separately labelled signed net context; never successful zero or new conversions. |
| Paid reads/consent intact | Existing engagement/route/account/consent/spend suites unchanged; cached default projection is tested with platform readers, vault and Desk writes explicitly refused. |

## Reproduce and limitations

Run from the candidate worktree root. The argument file is the exact selected
test list; pytest supports `@` argument files on this installed version.

```powershell
python -m pytest @docs/desk_v1/CHANNEL_IMPACT_P3_TESTS.txt -o addopts='' -q
pyright mc/desk_impact_sources/github.py mc/desk_impact_github_transport.py mc/desk_impact_collect.py mc/desk_impact_compare.py mc/desk_impact_time.py mc/desk_impact_sources/registry.py
git diff --check
```

Recorded results: **441 tests passed, rc=0**; **pyright 0 errors, rc=0**;
**diff whitespace check rc=0**. All new P3 tests prohibit socket connections.
No smoke is omitted for touched static code: no static code was touched.

The complete P1+P3 type surface also passed, rc=0:

```powershell
pyright (Get-ChildItem "$PWD/mc/desk_impact*.py", "$PWD/mc/desk_impact_sources/*.py").FullName
```

No scoped authenticated GitHub read or live scheduling/connection/UI activation
was attempted, as instructed. Fixture success does not establish repository
traffic permission, stargazer entitlement, a real token's scope or a shipped UI.
P4 still owns server connection projection, route/UI gating and authorized live
release evidence. P5 still owns site attribution. No unresolved product choice
was introduced; no credential, permission or attribution assumption was selected.

Source API contracts checked against official documentation:
[traffic](https://docs.github.com/en/rest/metrics/traffic),
[stargazers](https://docs.github.com/en/rest/activity/starring),
[release assets](https://docs.github.com/en/rest/releases/assets).
