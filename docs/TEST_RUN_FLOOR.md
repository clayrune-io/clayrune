# MC-1148 Phase 11: Floor server projections

Branch candidate based on `d91da1df`, dark behind the existing exact-true
`test_run_progress_enabled` flag. No frontend rendering, configuration write,
merge, push, restart or deployment is included. Phase 12 owns the Floor UI.

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

The adapter pages the existing canonical list API in blocks of 100, including
older active runs behind terminal history. No cross-request cache is introduced.
The store currently replays records when listing and rescans for each page;
this phase makes no large-history performance claim or store-index change.

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
live adoption and packaged artifacts are not
verified here: no frontend, launcher, reporter or packaging asset changed.
`USER_GUIDE.md`, README and agent rules need no update for this dark server
projection. No new dependencies or product permission gates.
