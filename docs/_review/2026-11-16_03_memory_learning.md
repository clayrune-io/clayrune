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

