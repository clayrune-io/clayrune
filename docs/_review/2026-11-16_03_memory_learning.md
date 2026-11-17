# Memory and learning review, pass 3 of 8 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `ce261a94`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function on a copy of
data, or a read-only count) or **read, not measured**. `server` was never
imported, the live server was sent nothing but `GET`, no model call was made,
and no process that binds a port or launches a provider CLI was started.

Scope: `mc/memory.py`, `mc/distiller.py`, `mc/skills.py`,
`mc/memory_note_gate.py`, `mc/memory_turn.py`, `mc/memory_push.py`,
`mc/memory_fts.py`, `mc/memory_delivery.py`, `mc/memory_state_trigger.py`,
`mc/memory_gate_tap.py`, `mc/memory_overlap_df.py`, `mc/positions_review.py`,
`mc/scribe_user_turn.py`, `mc/continuity_liveness.py`,
`mc/continuity_threads.py`, `mc/rollover_threshold.py`,
`mc/rollover_model_table.py`, `mc/skill_scoping.py`, `mc/skill_import_guard.py`,
and the route modules `mc/blueprints/distiller_routes.py`,
`memory_note_routes.py`, `skills_routes.py`, `topics_routes.py`. Passes 1 and 2
(`docs/_review/2026-11-16_01_runtime_core.md`,
`docs/_review/2026-11-16_02_agent_routes.md`) are not repeated; where a finding
here is the other half of one of theirs, the id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology.
Severity, same scale as passes 1 and 2: **blocker** = wrong result or lost data
on a normal path today; **high** = wrong result or lost data on a reachable
path, a way around a safety rail, or a cost paid on every turn/dispatch;
**medium** = narrower trigger, or a latent defect behind a disabled flag;
**low** = hygiene.

Written incrementally, one commit per section. **Status: IN PROGRESS.** The
ranked summary and the coverage statement are written last; until they exist,
the sections below are the record and the last section is where the pass
stopped.

## Ranked summary

_Not written yet (pass in progress)._

---

## Working notes (candidates, NOT yet verified; removed when the pass completes)

Read so far: `mc/memory.py:1-3139`. Each line below is a candidate to confirm
or clear, with the line that raised it.

- K1 `memory.py:1829-1950` `write_continuity` is a read-modify-write of the whole
  `continuity.md` with no lock (contrast `write_position` `:2221`, which took one
  for this exact race). `read_continuity` returns the EMPTY record on any read
  error (`:1761-1763`), so a failed read followed by the write drops every other
  owner's bucket. Other half of pass 1 C1. Check callers for an outer lock.
- K2 `memory.py:2217-2218` `write_position` puts caller `slug` into the path
  unsanitised (`position_<slug>.md`); `:2313` `write_topic_note` same. Check the
  routes for who supplies `slug`. Also `:2217` auto slug = first 48 alnum chars
  of the subject: two subjects sharing that prefix supersede each other.
- K3 `memory.py:2323-2338` `write_topic_note` builds frontmatter by string
  concat; `description` escapes only `"`, `triggers`/`supersedes`/`actor` not at
  all. A newline in any of them adds frontmatter lines (an `origin:` line).
  Check `_note_frontmatter` first-vs-last key and who the callers are.
- K4 `memory.py:2256` + docstring `:2171-2176`: `write_position` stamps
  `origin: unattended` for every caller that omits task/trigger_type;
  `_mem_unit_allowed` `:2571-2579` then withholds it from unattended consumers.
  Check the positions route: does a human-recorded position reach a scheduled
  run at all?
- K5 `memory.py:2571-2579` rail 2 in the read floor covers topic and position
  units only. `managed` and `archive` units (Scribe lines, including those
  written from unattended sessions) carry no origin and are always delivered.
  Check callers passing `consumer_unattended=True` and whether Scribe lines of
  steward sessions are tagged anywhere.
- K6 `memory.py:2709-2713` `_mem_snippet` and `_head` are computed for EVERY
  matched unit before the sort and the top-k cut; `:2682` reads config per unit;
  `:2720-2721` rebuilds the supersede graph per query. Measure on a copy.
- K7 `memory.py:2439-2443` + `:2530-2543`: the archive is one file, so any
  append re-tokenises and re-dedupes all of it on the next search. Measure.
- K8 `memory.py:2432-2435` a failed read caches `[]` for that file under the
  stat taken before the read (`:2542`); the file stays invisible to search until
  it changes again. No log (policy: file I/O must log).
- K9 `memory.py:585-595` `_mem_split_full`: text after the END sentinel is
  dropped by the next compose; END before BEGIN raises `ValueError` at `:587`;
  with no sentinels, the first literal `## Session Log` anywhere in the curated
  text starts the managed region and non-entry lines after it are dropped.
  Check which writers still compose MEMORY.md after the SESSION_LOG.md split.
- K10 `memory.py:2355-2356` `list_positions` reads each file unguarded; one
  unreadable file raises out of the whole list. `_parse_position` `:1512-1515`
  logs its warnings on every parse (every corpus rebuild and every list).
- K11 `memory.py:785`, `:806` comments say the module has no top-level `re`
  import; it does (`:32`).
- K12 `memory.py:2837-2857` negation entries render the description of every
  node resolving to the head with no `consumer_unattended` filter (narrow).
- K13 `memory.py:2098-2136` continuity extraction: model output goes into the
  record and from there into every later system prompt as "YOU SAID YOU WOULD";
  only length caps (`:1554-1565`). Compare with the Scribe's guards.

Read so far (update): `mc/memory.py:1-6219`.

- K14 `memory.py:4950` + `:4624-4630` `_entry_label(task)` keeps newlines; the
  entry is then two physical lines, and `_mem_split_full` `:597-602` keeps only
  lines starting `- [`. So a session whose task has a newline in its first 80
  chars loses its Scribe summary at the NEXT commit (stub title survives), and
  `last_entry_hash` (`:4791`, hash of the two-line string) never matches again,
  so each checkpoint appends instead of superseding. Same at `:5328`.
  VERIFY with the pure functions, and count stub lines in a real log read-only.
- K15 `memory.py:4775` on every commit: `_mem_migrate(existing)` treats the
  first literal `## Session Log` in a sentinel-free MEMORY.md as the start of the
  managed region; `- [` pointer lines after it move to SESSION_LOG.md, other
  lines are dropped, `_write_curated_only` `:4844` persists the cut. (K9 path.)
- K16 `memory.py:4844-4845` (and `:4246-4247`): MEMORY.md is stripped BEFORE
  SESSION_LOG.md is written; a failure between loses the inline entries and
  markers of an unmigrated project. Archive append `:4843` precedes both: a
  failure after it duplicates the evicted lines in the permanent archive.
- K17 `memory.py:4919-4958` `_write_session_memory` says "Never raises";
  `_commit_managed_entry` propagates read/write errors (`:4746`) and is called
  unguarded at `:4957`. An exception also skips Distiller, Beacon, topics, mint.
  Check both call sites for a guard.
- K18 `memory.py:4684-4697` `_append_to_archive` reads and rewrites the whole
  archive per append (strict utf-8 read). O(archive) per eviction; measure size.
- K19 `memory.py:4362-4382` legacy `agent` condense trigger sums MEMORY.md +
  SESSION_LOG.md + the permanent archive against `condense_threshold_kb` (30).
  The archive only grows, so the trigger is permanently true once it passes
  30 KB; only the 1 h cooldown limits it. `agent` is the shipped default.
  Read `_dispatch_condense` to confirm what a fire costs.
- K20 `memory.py:4846` passes `include_claude_md=True`, which also skips the
  `_has_running_agent` check (`:4303`), at checkpoint time when an agent is live.
- K21 `memory.py:4879-4880` `live` is computed before the lock; docstring
  `:4861-4862` says the membership test is re-done inside it. R41 class.
- K22 `memory.py:5161-5173`, `:5197-5211`: sid is added to
  `_checkpoint_inflight` before `_model_for_provider` runs; if that raises
  (pass 1 P1) the outer `except Exception: return False` `:5217` leaves the sid
  in the set forever and the session never checkpoints again. No log.
- K23 `memory.py:6137-6140` the `busy` guard drops the Scribe for a second
  session of the same project instead of queueing it. Unreachable today only
  because R38 serialises completions under the project lock; R38's proposed fix
  ("the busy guard is enough") would turn the wait into lost summaries.
- K24 `memory.py:6164-6165` render I/O failure is reported as `parse_empty`,
  no log. `:5505-5506`, `:5490-5491`, `:5217`, `:5850-5851`, `:5080` swallow
  state-file or transcript I/O with no log (policy).
- K25 `memory.py:5808` no dict guard on `m`; a non-dict JSON line raises and the
  outer handler abandons the rest of that transcript's telemetry.
- K26 `memory.py:5995-6008` docstring says `_memory_search` has no origin
  filter; `_mem_unit_allowed` `:2571` is one. Stale comment.
- K27 `memory.py:3355-3358` `resolve_mint` writes request `candidate` raw into
  frontmatter via `_rewrite_frontmatter_field` `:3316`; a newline adds lines
  after the stamped `origin:`. Not checked against `mint_candidates`. Check the
  route and the parser's duplicate-key rule (with K3).
- K28 `memory.py:3180-3181` mint description = first 160 chars of the subject
  (backlog text, hivemind title), newlines kept, into `write_topic_note`'s
  hand-built frontmatter. If the parse then loses `origin`, an unattended mint
  reads as unstamped and passes `_mem_unit_allowed`. Flag is off by default.
- K29 `memory.py:6018` + `_habit_source_statements` `:5960-5970`: "human said
  it" = `trigger_type == 'manual'` and the seed label equals the configured user
  name. Pass 2 R48 (`/agent/send` is always attended) is the upstream hole.

Read so far (update): `mc/memory.py:1-6955` (whole file).

- K30 `memory.py:6769-6812` the legacy `agent` condense prompt still describes a
  MANAGED region inside MEMORY.md (sentinels, `## Session Log`, `wm` lines) and
  tells the agent to keep that structure. After the SESSION_LOG.md split the
  entries and watermarks are not in MEMORY.md (`_write_curated_only`), the agent
  is never told SESSION_LOG.md exists, and the integrity guard's sentinel and
  watermark checks (`:6340`, `:6354`) compare against an empty pre-image. So on
  a migrated project the agent run can only rewrite curated. With K19 (trigger
  counts the archive) check whether this fires hourly for nothing. VERIFY the
  default `condense_mode` and the trigger lines.
- K31 `memory.py:6909`, `:6923`, `:6931` restore/heal write MEMORY.md with plain
  `write_text`, outside `_get_mem_write_lock`, minutes after the pre-image was
  taken; any commit to MEMORY.md in between is overwritten. Non-atomic (C1).
- K32 `memory.py:6458-6463` + `:6626` a fold `pointer_line` is model output
  written into the always-loaded curated index. Guards: non-empty, single line,
  no `clayrune:`. Not required to start `- `, no length cap, no authority check
  (`_authority_violation` is Distiller-only). Input is Scribe lines, which come
  from transcripts of any session, unattended included (with K5). Judge against
  rails 1 and 2: is this an unattended-output to every-prompt path with no
  human on it? Check whether steward sessions are scribed.
- K33 `memory.py:6848-6850` agent-mode condense is a `--dangerously-skip-permissions`
  agent with tools, cwd = project, reading Scribe lines (summaries of untrusted
  transcripts). `:6840` always the `claude` provider whatever the project uses.
- K34 `memory.py:6893-6896` the condense session is added to `agent_sessions`
  and `mgr.session_ids`; no removal in `_run`. Check `_read_agent_stream` and the
  housekeeping reaper before claiming a leak.
- K35 `memory.py:6256`, `:6292` refusal markers are a substring test on the
  summary; one hit in any map chunk fails the whole summary. Open the marker
  list and test real-looking summaries.
- K36 `memory.py:6507-6508` swallowed archive read, no log (policy, low).

Read so far (update): `mc/distiller.py:1-2884` (whole file).

- K40 `distiller.py:1284`, `:1289` `_evaluate_candidate` keys suppression and
  outbox lookups by the SIGNAL kind (`topic`); every writer keys by the ARTIFACT
  kind (`skill`): `record_push` `:2222-2230`, `_suppress_artifact` `:2751`, the
  outbox marker `:1834`. So for skills the pre-generation "no" check and the 7-day
  outbox dedupe never match: a model call per recurring session end, and (outbox
  has no post-generation re-check) a new `_proposed/` dir each time. VERIFY with
  the pure function, and count repeated fingerprints in `_proposed` read-only.
- K41 `distiller.py:1316-1322` the `later` branch returns None on both arms, and
  is only reached below threshold; above threshold `later` is never consulted.
  `record_push` `:2238-2254` computes `wait_until_recurrence` for nothing.
- K42 `distiller.py:452-468` `_read_skill_stats` returns the empty schema on a
  parse or read error ("preserving file untouched"); every caller is a
  read-modify-write (`_increment_counter` `:507-513`, `_commit_signals` `:1136`,
  outbox `:1832`, `_suppress_artifact` `:2752`), so the next write replaces the
  file with the empty schema: signals, outbox and SUPPRESSIONS gone (rail 3).
  `:467` other read errors do the same with no log. An empty file parses to `{}`
  and `stats['counters']`/`stats['signals']` then raise KeyError.
- K43 `distiller.py:2007-2060` `reframe_exploration_to_skill` returns fresh model
  output for install; no `_authority_violation` here. Read the promote route:
  is the guard applied to (a) the reframed body, (b) any `_proposed/` artifact at
  promote time (legacy flat entries and agent-written mc-distill proposals never
  went through `_generate_and_write_artifact`)? Rail 1.
- K44 `distiller.py:2398`, `:2846` rail 2 trusts the `origin:` line of whatever
  file sits in `_proposed/`. Agents write there themselves (mc-distill). Check
  whether the steward fence blocks `data/skills/_proposed`; if not an unattended
  session can write an exploration stamped `origin: interactive`.
- K45 `distiller.py:1704-1725` the authority guard is a phrase denylist. Test
  paraphrases against the pure function ("act autonomously", "stop asking for
  permission", "no need to confirm"). Read tests/test_distiller_safety.py.
- K46 `distiller.py:2388-2408` `exploration_read_floor` on every dispatch: two or
  three `_increment_counter` calls (each a locked read + full atomic rewrite of
  the stats sidecar), one more per blocked artifact (`:2399`), and
  `list_proposed()` `:2390`, which reads every proposed artifact of every project
  twice (`_read_proposed_meta` `:2822` + `_exploration_snippet` `:2831`). Measure.
- K47 `distiller.py:1448-1525` the cross-project walk reads every project's stats
  file and only writes log lines; `_update_summary_cache` `:1530` writes a
  summary "for future readers". Grep for a reader of either.
- K48 `distiller.py:1375-1400` cost cap reads `stats['cost'][today]`. Grep for a
  writer; if none, the cap never trips.
- K49 `distiller.py:1934`, `:1960`, `:1995`, `:2038` model errors in the renderers
  are swallowed and counted as `render_refuse:<kind>` (`:1784`), which
  `loop_health` reports as the refuse rate. No log (policy).
- K50 `distiller.py:874-881` a second session of the same project ending while
  the first is distilling is dropped (`skipped_reentrant`), not queued. K23 class.
- K51 `distiller.py:907-909` the kill-switch gate is called with a hard-coded
  `incognito: False, housekeeping: False`; check the caller filters incognito.
- K52 `distiller.py:1055-1108` no type guard on model output: a non-dict item in
  `topics`, or `signals` as a list, raises AttributeError and the whole
  session's signals are lost (`daemon_thread_exception`). `paths_tried` items
  are stored and replayed with no type or length cap.
- K53 `distiller.py:1239-1245` global suppression read under the project lock;
  failure swallowed with no log, and then "no" does not bind (fails open). Same
  at `:1641`.
- K54 `distiller.py:2087-2094` `name:` is searched in the whole model output, not
  the frontmatter (low).

Read so far (update): `mc/skills.py` (whole), `mc/blueprints/distiller_routes.py`
(whole), `mc/blueprints/skills_routes.py` (whole).

- K43 CONFIRMED by reading `distiller_routes.py:111-212`: no
  `_authority_violation` call anywhere on the promote path; the reframed body
  (`:173-180`) and any `_proposed/` file not written by
  `_generate_and_write_artifact` install unchecked. Human + passcode present.
- K47 CONFIRMED by grep: `_skill_stats_summary.json` has no reader under `mc/`
  or `server.py`; `cross_project_candidate` is a log line only.
- K48 CONFIRMED by grep: nothing writes `stats['cost']`; read at
  `distiller.py:1385` and `:2283` only.
- K60 `skills_routes.py:125-156` (`POST /api/skills`), `:158-183` (`PUT`),
  `:261-302` (paste import, `force` is a body boolean), `:305`, `:357`, `:426`
  (plugin: also writes the global agents and commands dirs), `:487`, `:186`
  (DELETE), `:212`: none calls `is_unattended_caller` or the passcode gate, and
  none runs the authority check. The 2026-09-10 security review F5 closed
  `/api/distiller/promote`; these reach the same `write_skill` with no
  proposal needed. Rail 1 route. Check `server.py` before_request hooks first.
- K61 `distiller_routes.py:215-228` reject has no unattended or passcode gate:
  an unattended session can permanently suppress and move any pending proposal.
- K62 `distiller_routes.py:192-206` + `skills.py:338-351` promote passes
  `overwrite=True` with a model-chosen `name:`; an existing installed skill of
  that name (hand-written or built-in) is replaced, no backup, no warning.
- K63 `skills.py:216-218` vs `:185-188` dump escapes `\` and `"`, parse strips
  the outer quotes and does not unescape; every read-then-write (`:767`,
  `:1214`, the PUT route) grows the value. Test with the pure functions.
- K64 `skills.py:1284-1352` `skill_usage_stats` re-reads every transcript line
  of every project modified in the window on each `GET /api/skills/usage`
  (called on Skills panel open, `static/js/skills-panel.js:356`); no cache.
  `:1310` `stat()` outside the try. Measure the byte volume read-only.
- K65 `skills.py:759-761` overwrite = `rmtree` then `copytree`; a failed copy
  leaves no skill. `:761` `copytree` follows symlinks (`:900` rejects links only
  for pinned refs). Check the import guard for a symlink rule.
- K66 `skills.py:497-502` a built-in the user archived or deleted is reinstalled
  on the next start (no tombstone).

