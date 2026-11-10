# MC-1148 Phase 11: Floor server projections

Phase 11 server candidate based on `d91da1df`, dark behind the existing
exact-true `test_run_progress_enabled` flag. Phase 12 rendering based on
`7b8dc527` is described below. No configuration write, merge, push, restart or
deployment is included in either candidate.

## Contract

`mc/test_run_floor.py` owns grouping, measurement, owner classification,
visibility and ordering. Its `project` and `summarize` functions accept canonical
snapshots and return fresh display dictionaries without mutating source data.
`read_projects` is a lazy storage adapter using the already wired Phase 4 store.
`mc/blueprints/floor_routes.py` only passes the projection through the existing
figure and room paths. No new server wiring or second store is introduced.

When enabled, existing `/api/floor` room and figure objects gain `test_runs`.
Disabled calls perform no test-run storage reads and add no payload fields.
The existing five-second poll cadence is unchanged.

Summary fields:

| Field | Meaning |
| --- | --- |
| `available` | False when canonical storage could not be read; error code is `test_runs_unavailable`. This is not an empty successful observation. |
| `active_count` | Independent active runs after removing active children represented by their active same-owner parent. |
| `phase` | `progress`, `finalizing`, `collecting`, `unknown`, or `multiple`; empty summaries are `unknown` with zero active runs. |
| `runner`, `unit` | Common measurement kind when compatible; otherwise null. |
| `selected_total`, `completed`, `counts`, `percent` | Aggregate only for complete capture, known nonnegative integer totals, valid completed counts and the same runner/unit. Otherwise null. Zero total never yields a percentage. |
| `capture` | `complete` if all projected runs have complete capture, otherwise `partial`; individual runs retain their capture state. |
| `total_recalculated` | At least one canonical run changed its denominator. Percentage can move backwards. |
| `runs`, `runs_omitted` | First 100 independent active display summaries ordered by creation time/ID, and an explicit omitted count. Aggregates and owner classification include all independent active runs. |
| `ownerless_count`, `unavailable_owner_count` | Room-only counts of independent runs with null owner or an owner that cannot be linked to a displayable session in that project. |

Per-run display data is allowlisted: IDs, runner/unit, selected/completed/outcome
counts, capture, collection errors, denominator revision/recalculation and update
time. No cwd, process identity, display label, stdout, failure names, receipt,
command or environment is added to the cross-project board. The project/chat
read API remains the detailed history surface.

## Ownership and lifecycle

- Running/idle sessions keep their existing figure. A completed, errored or
  otherwise ended session still present in the server session map is retained
  only while it has active projected work. Its state remains idle; no synthetic
  model turn or activity is created. Existing working activity remains unchanged.
- Questions and plan approvals keep the primary `asking` state. Testing and
  working rooms sort together below asking rooms and above idle rooms. The
  renderer must keep test progress secondary when a question is pending.
- Absent owners appear at project level through `unavailable_owner_count`, never
  through another chat's figure. Ownerless work also makes a room live with an
  empty figure list; it does not fabricate a conversation. Counts of figures
  remain counts of actual displayed sessions.
- Incognito/private projects are not read. Restricted or hidden owners are
  excluded, including sandboxed sessions and ordinary housekeeping; existing
  hivemind housekeeping remains displayable. This adds no restricted capture.
- All canonical terminal states remove the active testing projection on the
  next poll. Ordinary idle/working figures remain under existing rules; a figure
  retained solely for tests disappears. History is neither changed nor deleted.
- Reporter finish alone does not clear a pin: only canonical process finalization
  makes a run terminal. Completing the denominator yields `finalizing`, not a
  success verdict. Unknown or partial capture never invents an aggregate percent.
- Active manifest parents represent their active same-owner children to avoid
  mixing script and test denominators or counting one execution twice. An active
  child whose parent is terminal remains independently visible. Unrelated runs,
  projects and session owners never merge by command or test name.

No ETA, new liveness heuristic, archived-session reconstruction, process scan,
history retention policy or stale-to-terminal transition is added. A known
nonterminal snapshot stays visible until canonical evidence changes its state.

## Reads and failure behavior

The adapter calls `all_snapshots` once per project, including older active runs
behind terminal history, without paging/rescanning the public list API. The
store caches only validated snapshot copies across requests, keyed by checked
absolute path, nanosecond mtime and size, bounded by 4096 entries and 32 MiB of
snapshot objects. Changed/new files still replay and corrupt records still
fail. Create/append invalidate cache entries; events are never cached.

Directory enumeration and sorting do not hold the store lock; each individual
record's stat/cache/validation holds it separately, so a submit need not wait
for a full folder replay. The resulting per-file view may omit a run created
during enumeration until the next poll. Room summaries and current-session
owner/privacy classification are rebuilt on every request; there is no route
or project-record cache. See `TEST_RUN_STORE.md` for cache bounds and the
metadata-preserving external-edit limitation, and `TEST_RUN_READ_PERFORMANCE.md`
for copied-data timing and validation evidence.

A read failure is logged, that room's summary is explicitly unavailable, and
other rooms still render. An unavailable room remains visible so the failure
is observable even without figures. No partial page is presented as a complete
successful summary. Nothing here starts, stops, finalizes or mutates a run.

## Verification

Initial focused command before the final acceptance additions:

```text
python _scratch/run_phase11_checks.py tests/test_test_run_floor.py tests/test_floor_routes.py
```

113 passed in 12.66s, rc=0. The scratch wrapper launches `python -m pytest` with
the same supplied arguments, immediately registers its child PID, waits in the
foreground and returns the actual exit code. It is gitignored, not a product
dependency. Final combined command:

```text
python _scratch/run_phase11_checks.py tests/test_test_run_floor.py tests/test_floor_routes.py tests/test_floor_subagents.py tests/test_test_run_store.py tests/test_test_run_routes.py tests/test_test_run_detection.py tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_background_job_endpoint.py tests/test_clayrune_mcp_tools.py tests/test_atomic_json.py
```

519 passed in 35.32s, rc=0, with no skips or warnings. This run includes all
final code and acceptance tests. Equivalent reproducible command is
`python -m pytest` followed by those same eleven test paths. Type command:

```text
pyright mc/test_run_floor.py tests/test_test_run_floor.py
```

Zero errors, zero warnings and zero informations after all final code/test
changes, rc=0 (non-failing newer-version notice only). Browser/static smokes,
live adoption and packaged artifacts were not verified in Phase 11:
no frontend, launcher, reporter or packaging asset changed in that phase.
`USER_GUIDE.md`, README and agent rules need no update for this dark server
projection. No new dependencies or product permission gates.

## Phase 12: Floor rendering

`static/js/test-run-floor.js` owns pure text/HTML rendering of the existing
projection. The Floor imports it and delegates its activity line after the
existing asking check, appends figure details and adds a room summary. No
data-layer import, second fetch, event listener or timer is introduced.
`static/css/test-run-floor.css` is loaded by the page and owns all new styles.
The Floor's poll and error-recovery code is unchanged.

- Absent projections and successful zero-active summaries return empty HTML;
  existing figures/rooms gain no testing elements when the flag is off.
- Complete compatible safe-integer totals show done/total and a native
  accessible progress bar. Percentage is floored from the supplied aggregate
  numerator/denominator, never averaged or extrapolated from time. Zero total
  displays `0/0` without a bar or percentage. Completed totals say finalizing,
  never passed. Denominator recalculation is labelled and may move backwards.
- Collecting, total unknown, multiple runs and partial capture keep explicit
  wording. All six canonical outcome counts are shown when supplied. Without
  aggregate counts, the first three runs show their own observed outcomes;
  remaining runs, including the server's omitted runs, have an explicit count.
  Incompatible units are never added in the client. IDs are escaped tooltips;
  runner text is escaped. No test names or detailed failure text are available
  in this projection, so none are fetched or invented.
- Asking remains the primary activity and existing state pill. Test progress
  appears underneath in a secondary treatment. This does not change model
  activity, state colors, ownership or chat navigation.
- Room summaries show ownerless/unavailable-owner counts. A failed room read
  says testing unavailable even with no figures. Next-poll terminal or flag-off
  cleanup removes the display without deleting history.
- Dynamic text is escaped before entering HTML/attributes. Numeric values and
  count fields require nonnegative safe integers; classes and unit labels are
  fixed renderer vocabulary. There are no new dependencies or permission gates.

### Phase 12 verification

`node tools/smoke/test-run-floor.mjs` serves the real worktree page and static
assets with fixture APIs, aborting all other requests. It makes no live store
or configuration changes. Eighteen cases cover flag-off absence, known/unknown
totals, collecting, mixed/compatible multiple runs, asking priority, unavailable
rooms, ownerless/unavailable owners, malicious name/runner/run ID, partial
capture, finalizing, zero total, recalculation, bounded detail, invalid fields,
terminal cleanup and narrow cards. Result: **18 passed, rc=0**.

Desktop and 390px fresh-open screenshots were inspected in
`_scratch/test-run-floor/asking-{desktop,mobile}.png` (gitignored). The narrow
card's new details have no horizontal overflow; this is a Chromium viewport
check, not a physical-device WebView or real touch test.

Existing `floor-poll-survives-error.mjs` and
`floor-poll-survives-render-error.mjs`: **ALL PASS, rc=0** each. These scripts
normally load the main checkout from port 5199. Scratch copies preserve all
original assertions and API calls while intercepting `/` and `/static/**` with
this worktree's HTML/JS/CSS. Their read-only Floor API remains live. The wrapper
announces that distinction; no server restart or config mutation is needed.

Full `node tools/smoke/boot-smoke.mjs`: **PASS, rc=0**, all seven boot scenarios
plus dispatch, first-run migration, model picker, identity prefill, backlog,
backlog links, memory panel, calendar, scheduler layout, question repaint,
Floor and agent-face guards. This hermetic script loads worktree assets.
All Node smoke processes are immediately registered via the foreground
`_scratch/run_phase12_checks.py` wrapper, which waits and returns their actual
exit status. Existing main-checkout smoke dependencies are junctioned into the
worktree for checks and removed before commit; none are installed.

Syntax/whitespace checks pass. The user guide documents the conditional UI.
README, AGENTS/CLAUDE and rules need no change: this dark phase adds no install
step, control, dependency or binding convention. Live enabled server adoption,
P9 integration, actual frozen artifacts and physical-device checks remain
unverified. No merge, push, restart or config write; Dave owns integration.
