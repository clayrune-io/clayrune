# Memory and learning review, pass 3 of 8 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `ce261a94`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function on a copy of
data, or a read-only count) or **read, not measured**. `server` was never
imported, the live server was sent nothing but `GET`, no model call was made,
and no process that binds a port or launches a provider CLI was started.
Nothing was written to a real memory directory or under `data/`.

Scope: `mc/memory.py`, `mc/distiller.py`, `mc/skills.py`,
`mc/memory_note_gate.py`, `mc/memory_turn.py`, `mc/memory_push.py`,
`mc/memory_fts.py`, `mc/memory_delivery.py`, `mc/memory_state_trigger.py`,
`mc/memory_gate_tap.py`, `mc/memory_overlap_df.py`, `mc/positions_review.py`,
`mc/scribe_user_turn.py`, `mc/continuity_liveness.py`,
`mc/continuity_threads.py`, `mc/rollover_threshold.py`,
`mc/rollover_model_table.py`, `mc/skill_scoping.py`, `mc/skill_import_guard.py`,
`mc/unattended.py`, and the route modules `mc/blueprints/distiller_routes.py`,
`memory_note_routes.py`, `skills_routes.py`, `topics_routes.py`, plus the
memory, continuity, positions and mint routes inside
`mc/blueprints/guide_routes.py`. Passes 1 and 2
(`docs/_review/2026-11-16_01_runtime_core.md`,
`docs/_review/2026-11-16_02_agent_routes.md`) are not repeated; where a finding
here is the other half of one of theirs, the id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology.
Severity, same scale as passes 1 and 2, with one addition for this pass's
brief: **blocker** = wrong result or lost data on a normal path today, or a
safety rail that one ordinary request gets around; **high** = wrong result or
lost data on a reachable path, a way around a safety rail that takes a
deliberate step, or a cost paid on every turn/dispatch; **medium** = narrower
trigger, or a latent defect behind a setting that ships off; **low** = hygiene.

"The reviewed install" means the machine the review ran on. Counts taken from
it are read-only and are given as numbers only.

Written incrementally, one commit per section; the ranked summary and the
coverage statement (section 10) were written last. The pass is complete.

## Ranked summary

37 numbered findings: 1 blocker, 12 high, 23 medium, 1 low, plus 25
unnumbered low items listed under "Lesser findings" in sections 2 to 5.
Ranked by consequence, and within one severity by how often the path runs.
Ids are in the order found, so they do not follow the rank.

**Merge-blocking if these were a diff:** ranks 1 to 13. Ten of the thirteen
are about the safety rails: one route that goes around rail 1 outright
(rank 1), and nine paths where a rail is enforced in the function and not on
the way in. Ranks 2, 6, 9, 11 and 12 are measured; the rest are read from the
code. Rank 14 is the one medium that should go with them: it is a second way
to the write in rank 1.

Why rank 2 is high and not a blocker: the transcript survives, and what is
lost is the summary line. It is still the only finding here that is losing
data on an ordinary path today.

| Rank | Id | Finding | Lens | Severity |
|---|---|---|---|---|
| 1 | ML1 | `POST /api/skills` and the other skills routes install a global skill for any caller: no human check, no passcode, no authority guard (rail 1) | L/M | blocker |
| 2 | ML15 | A task with a newline in its first 80 characters loses its Scribe summary at the next commit; 1,226 of 7,727 archive entries are in that state (measured) | L | high |
| 3 | ML4 | At dispatch a scheduled or agent-dispatched session is treated as an attended reader, so it receives unattended-origin notes and explorations (rail 2) | L | high |
| 4 | ML8 | An unparseable stats sidecar reads as empty and the next counter bump writes the empty schema over it: every recorded "no" is gone (rail 3) | L | high |
| 5 | ML6 | The note route stamps `interactive` for any request with an `Origin` header (rail 2) | L | high |
| 6 | ML5 | `resolve_mint` writes a request field raw into frontmatter; a newline re-stamps the note `origin: interactive` (measured, rail 2) | M | high |
| 7 | ML7 | The exploration gate trusts the `origin:` line of a file agents can write (rail 2) | L | high |
| 8 | ML2 | The promote route never runs the authority guard; a reframed body installs unchecked (rail 1) | L | high |
| 9 | ML3 | The authority guard is a phrase list: 7 of 9 plain paraphrases pass (measured, rail 1) | L | high |
| 10 | ML9 | Scribe output carries no origin: the read floor, the cold probe and the fold pointer all hand it to unattended consumers (rail 2) | L | high |
| 11 | ML25 | Every dispatch reads all 2,448 proposed artifacts (520 to 705 ms) and rewrites a 2.9 MB sidecar two or more times; a steward dispatch once per withheld artifact (measured) | E | high |
| 12 | ML24 | The push observer runs a search on the stream-reader thread per file tool call, delivers nothing, and has written a 129.6 MB log with no cap (measured) | E | high |
| 13 | ML16 | The shipped-default condense mode fires on a trigger that cannot clear and launches a tool-capable agent with a prompt for a file layout that no longer exists | E/L | high |
| 14 | ML14 | `slug` from the request goes into a file path unchecked; on Windows it writes outside the memory directory (measured) | M | medium |
| 15 | ML12 | A position recorded through the route is always stamped `unattended`, so no steward run ever sees one (0 of 115 stamped `interactive`) | L | medium |
| 16 | ML10 | The pre-generation "no" check and the 7-day dedupe are keyed by signal kind while every writer keys by artifact kind; neither ever matches for skills (measured) | L/E | medium |
| 17 | ML11 | `POST /api/distiller/reject` has no caller check: an agent can record a permanent "no" (rail 3) | L | medium |
| 18 | ML22 | "Busy" guards drop a second session's work instead of queueing it; 447 dropped distills counted | L | medium |
| 19 | ML17 | `write_continuity` is an unlocked read-modify-write; a failed read then persists only the caller's bucket | L | medium |
| 20 | ML19 | Commit ordering: archive first, `MEMORY.md` stripped before `SESSION_LOG.md` is written; a failure in between duplicates or loses entries | L | medium |
| 21 | ML18 | The first literal `## Session Log` in a sentinel-free index starts the managed region; text after it is dropped | L | medium |
| 22 | ML20 | A failed checkpoint launch leaves the session marked in flight for ever, with no log | L | medium |
| 23 | ML13 | `write_topic_note` builds frontmatter by concatenation; a `---` line in the description removes the origin stamp (measured) | M | medium |
| 24 | ML33 | Promote installs with `overwrite=True` under a model-chosen name; an existing skill of that name is replaced | L | medium |
| 25 | ML37 | The import scanner returns zero findings for a UTF-16 script, a script over 2 MB or a skipped extension, and allows the import (measured) | M | medium |
| 26 | ML30 | Three Distiller controls never take effect: the daily cost cap, "later", and the summary cache nobody reads | L/M | medium |
| 27 | ML31 | A model or transport error during rendering is counted as a refusal, with no log | M | medium |
| 28 | ML32 | Extraction output has no type guard; one malformed item loses the whole session's signals | M | medium |
| 29 | ML34 | Frontmatter dump escapes and parse does not unescape: every edit doubles the backslashes (measured) | L | medium |
| 30 | ML29 | `PATCH` on a note reads the old text outside the lock; a concurrent origin stamp is reverted | L | medium |
| 31 | ML21 | The agent-mode restore writes `MEMORY.md` outside the write lock, minutes after the pre-image was taken | L | medium |
| 32 | ML26 | The archive is one cache unit: each eviction rewrites 2.5 MB and the next search pays 358 ms instead of 79 ms (measured) | E | medium |
| 33 | ML27 | Delivery telemetry rewrites a 1.4 MB JSON file on every search, never prunes, and resets on a corrupt read | E/L | medium |
| 34 | ML28 | One unresolved link costs a scan of every sibling vault (898 ms measured), under the topic write lock | E | medium |
| 35 | ML35 | The continuity label reads ordinary words as branch names and labels a normal finish `LOST IN RESTART` (measured) | L | medium |
| 36 | ML36 | The topic state route never loads the project, swallows write failures and answers `ok` | L/M | medium |
| 37 | ML23 | `_write_session_memory` says "Never raises" and does; the exception skips the Distiller step | M | low |

Three questions are left for a decision and are not answered here
(section 8).

---

## 1. The three safety rails, path by path

Each rail was followed from every writer to every reader. The rails hold on
the paths their own tests exercise (section 9). The fourteen findings below are
the paths that go around one, or that enforce one so strictly it produces a
wrong result. Routes are given exactly.

Shared background for this section, stated once: every dispatched agent can
reach every local route. `mc/unattended.py:4-7` says so in its own docstring
("an unauthenticated localhost surface that every dispatched agent shares …
the steward fence explicitly allows mutating `curl` to `_LOCAL_HOSTS`"), and
`steward/fence.py:1322`, `:1343` are the allow lines. So a rail that is
enforced only where the Distiller writes, and not where a route writes, is not
enforced against an agent.

### Rail 1 — learning may never change what the agent is allowed to do

#### ML1 · blocker · L/M — the skills routes install a global skill for any caller: no human check, no passcode, no authority guard

`mc/blueprints/skills_routes.py:125-156` (`POST /api/skills`) takes `name`,
`description`, `body`, `scope` from the request and calls
`_skills.write_skill` (`mc/skills.py:319-352`), which writes
`<global skills dir>/<name>/SKILL.md`. Nothing on the way calls
`is_unattended_caller`, `_require_human_passcode` or
`_authority_violation`: a grep of `skills_routes.py` for `unattended`,
`passcode` and `authority` returns no line. The same is true of the update
route (`:158-183`), paste import (`:261-302`, where `force` is a body boolean
that re-submits past the import scanner), the other import routes (`:305`,
`:357`, `:487`), the plugin import (`:426`, which also writes the global
agents and commands directories), and delete (`:186`).

`/api/distiller/promote` was closed for exactly this reason
(`mc/blueprints/distiller_routes.py:125-140`, citing the 2026-09-10 security
review F5: "the steward fence blocks the `Write` tool under `.claude/` but
allows the `curl` that reaches this same install path"). `POST /api/skills`
is the same install path with fewer steps: it needs no proposal, no
fingerprint and no `_proposed/` file.

Failure: an unattended session runs
`curl -X POST localhost:5199/api/skills -d '{"name":"x","description":"…","body":"…","scope":"global"}'`.
The skill is live for every later session of every project. CLAUDE.md's
"the steward … cannot self-install a skill" is false on this route. Read, not
run (running it would write a skill).

Smallest fix: one gate helper (the pair the promote route already uses:
`is_unattended_caller()` refusal, then `_require_human_passcode(body)`) called
at the top of every mutating route in `skills_routes.py`, and
`_authority_violation(description + body)` inside `write_skill` so no caller
can skip it.

Test: none. `tests/test_steward_fence.py:101`, `:630` assert only that a `GET`
to `/api/skills/search` is allowed; no test posts to `/api/skills` as an
unattended caller.

#### ML2 · high · L — the promote path never runs the authority guard

`_authority_violation` refuses a Distiller artifact at one place:
`_generate_and_write_artifact` (`mc/distiller.py:1793-1800`). The memory
writers call it too (mints `mc/memory.py:3188`, negation rows `:3593`, habit
notes `:5989`). The promote route (`mc/blueprints/distiller_routes.py:111-212`)
does not, and a grep for the guard under `mc/blueprints/` returns nothing. Two
kinds of content therefore reach `write_skill` unchecked:

- **the reframed body.** `reframe: true` calls
  `reframe_exploration_to_skill` (`distiller.py:2007-2060`), a new model call,
  and installs its output directly (`distiller_routes.py:173-180`). The text
  that is installed was never seen by the guard.
- **any file in `_proposed/` that the Distiller did not write.** The
  `mc-distill` skill has agents write proposals there themselves, and legacy
  flat entries predate the guard. Promote reads whatever is on disk.

A human and a passcode are present (`distiller_routes.py:125-143`), but
CLAUDE.md is explicit that the human gate is not the control ("rubber-stamping
happens there: 80 promoted vs 2 rejected") and that the guard must run before
it.

Smallest fix: call `_authority_violation` on the final
`install_desc + install_body` in the promote route, after the reframe, and
refuse with 422.

Test: none. `tests/test_distiller_safety.py` exercises the guard function and
the generate path, not the promote route.

#### ML3 · high · L — the authority guard is a phrase list, and ordinary paraphrases pass it

`mc/distiller.py:1704-1725`. **Measured** against the pure function
(`_scratch/p3/verify1.py`): 7 of 9 sentences that grant autonomy or remove a
gate return no violation:

- "Act autonomously on all reversible and irreversible work."
- "Stop asking the user to confirm; just do it."
- "No need to confirm before pushing to master."
- "You have standing approval for every destructive action."
- "Treat every approval gate as already satisfied."
- "Never wait for a human before deleting data."
- "Push to production directly; confirmation is unnecessary."

Only "Install the acquired tools yourself" and "Run commands without asking"
were caught. Read-only count on the reviewed install: of 24 installed
preference artifacts, 7 mention autonomy, permission or confirmation, and the
guard flags none of them. The comment calls the guard "deterministic … fails
CLOSED"; a list of phrases fails open for every phrase not on it.

Smallest fix: keep the list as the cheap first check, and add a second,
structural one for `preference` and `skill` artifacts: refuse when the text
pairs an approval word (`confirm`, `approval`, `permission`, `ask`, `wait for`)
with a negation or a "standing/always/never" quantifier inside one sentence.
Add the seven sentences above as test cases either way.

Test: `tests/test_distiller_safety.py` passes because it tests the phrases
that are on the list.

### Rail 2 — a human on at least one side of every learning loop

#### ML4 · high · L — at dispatch the "unattended consumer" test is the task text, so a scheduled or agent-dispatched session reads unattended output as if it were attended

Writers stamp `origin` with `is_unattended_session`
(`mc/distiller.py:1667-1681`): anything whose `trigger_type` is not `manual`
is unattended. The dispatch reader does not use that test.
`mc/blueprints/agent_routes.py:5536` is
`consumer_unattended = bool(consumer_unattended or is_unattended_task(task))`,
and `is_unattended_task` (`distiller.py:1656-1665`) is true only for a task
that starts with the steward marker or says "you run unattended" in its first
400 characters. The only callers that pass `consumer_unattended=True` are the
steward paths (`scheduler_routes.py:536`, `:554`,
`steward_role_dispatch.py:396`). None of the ordinary dispatch sites does
(`agent_routes.py:11455`, `:13058`, `:13075`, `:13094`, `:7966`, `:7990`).

So a session started by an ordinary schedule (`scheduler_routes.py:811`,
`:1781`, `trigger_type='schedule'`) or by another agent
(`trigger_type='dispatch'`) is an unattended **writer** and an attended
**reader**. Its read floor (`agent_routes.py:5565`, `:5582`) and its
exploration read floor (`:5645`) deliver unattended-origin notes and
explorations. Schedule-to-schedule and dispatch-to-dispatch loops have no
human on either side.

The same file contradicts itself two ways:

- the comment at `agent_routes.py:5526-5530` says the builder "reads the
  session's own provenance stamp back out of `agent_sessions`"; it does that
  for the mint block and the ledger (`:5540`, `:5561`) and not for the gate;
- `mc/memory_turn.py:285` seeds the delivered set with
  `is_unattended_task(task)` while `:401` refreshes with
  `is_unattended_session(task, trigger_type)`. The same session is an attended
  reader at dispatch and an unattended one from its second turn.

Size of what is exposed, read-only on the reviewed install: 1,034 of 1,304
pending explorations and 558 of 718 topic notes are stamped `unattended`.

Smallest fix: in `_build_agent_context`, derive the flag from the session
(`is_unattended_session(task, agent_sessions[session_id]['trigger_type'])`)
when a `session_id` is present, and use the same expression at
`memory_turn.py:285`.

Test: none can catch it. `tests/test_hivemind_roster_worker.py:120` and
`tests/test_hivemind_reviewer.py:153` cover the Hivemind path, which passes
because its task carries a server-owned prefix (`mc/hivemind_worker.py:290-291`);
no test dispatches with `trigger_type='schedule'` or `'dispatch'` and a plain
task.

#### ML5 · high · M — `resolve_mint` writes a request field raw into frontmatter; a newline in it re-stamps the note `origin: interactive`

Route: `POST /api/project/<id>/memory/mints/<filename>/resolve`
(`mc/blueprints/guide_routes.py:591-613`), body `{verdict, candidate}`. No
caller check. `resolve_mint` (`mc/memory.py:3320-3362`) passes `candidate`
to `_rewrite_frontmatter_field` (`:3301-3317`), which appends
`f'{field}: {value}\n'` to the END of the frontmatter block. The parser keeps
the last value of a repeated key.

**Measured** (`_scratch/p3/verify4.py`, a note written by `write_topic_note`
in a scratch directory with an unattended stamp): after
`resolve_mint(…, 'supersedes', candidate='a\norigin: interactive')` the
frontmatter ends `supersedes: a` / `origin: interactive`, `_note_frontmatter`
reads `interactive`, and `_mem_unit_allowed(…, consumer_unattended=True)`
returns True. The `unrelated_to` verdict does the same. `candidate` is also not
checked against the note's own `mint_candidates`.

The route is one the fence names as an expected agent target
(`steward/fence.py` `_CURL_PROSE_ROUTE_RE`, `memory/mints`). The note must
currently read `supersedes: unresolved`, which is the state every automated
mint with candidates is written in.

Smallest fix: in `resolve_mint`, refuse a `candidate` that is not one of the
slugs in the note's `mint_candidates`; in `_rewrite_frontmatter_field`, refuse
a value containing `\n` or `\r`.

Test: none. `tests/test_memory_v2_step7.py:221-271` calls `resolve_mint` with
plain file-name candidates, and `:259` covers a bad `filename`, not a bad
`candidate`.

#### ML6 · high · L — the note-write route stamps `interactive` for any request that carries an `Origin` header

`mc/memory_note_gate.py:96-97`: `if request.headers.get('Origin'): return
'interactive', None`. The function's docstring (`:81-83`) says the stamp is
"never from anything the caller sends". A header is something the caller
sends: an agent adds `-H 'Origin: http://localhost:5199'` to its curl and its
note is stamped `interactive`, which is the whole of rail 2 for that note.
`mc/unattended.py:76-77` has the same test, and
`distiller_routes.py:138-139` already calls it "the forgeable Origin-derived"
check and adds the passcode for that reason. The note route has no second
factor.

Not everything here is weak: **measured**, the gate's `compose` does strip a
caller-supplied `Origin:`, an indented `origin:` and `origin :` and ends with
the server stamp (candidate K71, cleared).

Smallest fix: for the stamp (as opposed to the refusal), treat `Origin` as
necessary and not sufficient: stamp `interactive` only when the header is
present **and** no non-manual session is running in the project; otherwise
stamp `unattended`. A human note written while an agent runs is then stamped
conservatively, which is the fail-safe direction.

Test: none sends an `Origin` header from a simulated agent caller.

#### ML7 · high · L — the exploration gate trusts the `origin:` line of whatever file is in `_proposed/`

`mc/distiller.py:2846` reads `origin` from the file's frontmatter and `:2398`
delivers the artifact to an unattended consumer when it says `interactive`.
Explorations are delivered from `_proposed/` directly, with no promotion
(`:2390-2412`). Agents are told to write into that directory (the `mc-distill`
skill), and `steward/fence.py` has no rule that names `_proposed` or
`data/skills` (grep: no match). So the file that carries the stamp is writable
by the party the stamp is supposed to constrain.

Failure: an unattended session writes
`data/skills/_proposed/<project>/<dir>/EXPLORATION.md` with
`origin: interactive` in its frontmatter; the next steward cycle whose
objective shares a word with the directory name receives it under "RELEVANT
PAST EXPLORATIONS". Read, not run.

Smallest fix: keep provenance out of the file. Record `(path, origin)` in the
outbox entry the Distiller already writes at `distiller.py:1832-1837`, and
have `exploration_read_floor` treat an artifact with no outbox record as
unattended for an unattended consumer.

Test: `tests/test_distiller_readback.py` builds artifacts with a stamped
frontmatter and checks the filter; it cannot see who wrote the stamp.

#### ML9 · high · L — Scribe output carries no origin, so it is outside rail 2 on three paths

Every non-housekeeping session that ends is scribed, whatever started it
(`mc/blueprints/agent_routes.py:10591-10596`), and the entry is written with
no origin (`mc/memory.py:4950`). Three readers then hand that text to an
unattended consumer:

1. **the read floor.** `_mem_unit_allowed` (`memory.py:2571-2579`) filters
   only `topic` and `position` units. `managed` and `archive` units always
   pass. On the reviewed install the archive is 5,517 of the 6,357 corpus
   units (**measured**).
2. **the cold probe.** `memory_fts.cold_search` (`mc/memory_fts.py:301-356`)
   has no origin filter, and `mc/memory_turn.py:441-447` passes neither
   `consumer_unattended` nor `exclude_session_ids`; cold hits replace the kept
   set. An unattended consumer can be handed an excerpt of any unattended
   session's transcript. Narrow: only when the warm floor returned nothing.
3. **the fold pointer.** In `structured` condense, a `pointer_line` produced
   by the model from Scribe lines is inserted into the curated, always-loaded
   index (`memory.py:6616-6628`). Its guards are: non-empty, one line, no
   `clayrune:` (`:6454-6463`). No length cap, no requirement that it is a
   pointer, and no `_authority_violation`. That is model output, derived from
   transcripts of any session, written into every later prompt of every
   session with no human step.

Whether path 1 is a defect is a design question and is put as one in the final
message: a steward arguably needs its own previous cycles' summaries. Paths 2
and 3 are not covered by that argument. `mc/memory.py:5995-6008` also still
says `_memory_search` "has no origin filter", which stopped being true when
`_mem_unit_allowed` was added.

Smallest fix: stamp the session's origin on the managed entry (a trailing
`_(u)_` tag, or a field on the watermark), filter `managed`/`archive` units by
it under a setting, pass `consumer_unattended` into `cold_search`, and run the
authority guard plus a length cap on `pointer_line`.

Test: none; `tests/test_memory_link_graph.py:183-197` parametrises
`consumer_unattended` over topic and position units only.

#### ML12 · medium · L — a position recorded through the route is always stamped `unattended`, so steward runs never see it

`mc/blueprints/guide_routes.py:554-558` calls `write_position` without `task`
or `trigger_type`; `memory.py:2256` then stamps
`_stamp_origin('', '') == 'unattended'` (the docstring at `:2171-2176` says so
and calls it fail-safe). `_mem_unit_allowed` (`:2571-2579`) and
`rank_negation_ledger` (`:3776`) withhold `origin: unattended` positions from
an unattended consumer.

Read-only count on the reviewed install: 115 positions, 70 stamped
`unattended`, 45 unstamped, **0 stamped `interactive`**. So every position
recorded since the stamp shipped, including those a human asked for in an
attended chat, is withheld from every steward dispatch. A standing "we decided
not to do X" is the one kind of note an unattended run most needs, and it is
the one it cannot get.

Smallest fix: have the route stamp with the same caller classification the
note route uses (after ML6 is fixed), or pass the calling session's
`trigger_type`.

Test: none. `tests/test_positions.py` has no assertion on `origin`, and no
route test checks what an attended write is stamped.

#### ML13 · medium · M — `write_topic_note` builds frontmatter by string concatenation; a `---` line in the description removes the origin stamp

`mc/memory.py:2323-2338` escapes only `"` in `description` and nothing in
`triggers`, `supersedes` or `mint_candidates`. The automated mint passes the
first 160 characters of the subject (backlog text, a Hivemind title) with
newlines kept (`:3180-3181`).

**Measured** (`_scratch/p3/verify4.py`): a description of
`first line\n---\nrest` closes the frontmatter block early; the parsed origin
is empty and the note is delivered to an unattended consumer, because
unstamped notes pass `_mem_unit_allowed`. A plain newline does not lose the
stamp (the stamped `origin:` line comes later and wins) but cuts the
description to its first line. An injected `origin:` line in `triggers` also
loses, for the same reason. So the exposure is the `---` case.

Related coherence gap: unstamped memory notes pass the gate
(`memory.py:2574` "Legacy/unstamped units retain their existing treatment",
124 topic notes and 45 positions on the reviewed install), while unstamped
Distiller artifacts fail closed (`distiller.py:2396-2398`, and CLAUDE.md says
"unstamped … artifacts fail closed").

Smallest fix: build the block with `_skills.dump_skill_md`, which
`write_position` already uses and which block-quotes multi-line values
(`mc/skills.py:209-212`).

Test: none passes a multi-line description.

#### ML14 · medium · M — `slug` from the request goes into a file path unchecked (Windows)

`POST /api/project/<id>/memory/positions` passes the body's `slug` to
`write_position` (`guide_routes.py:558`), which builds
`mem_dir / f'position_{slug}.md'` (`memory.py:2217-2218`) and writes it.
`write_topic_note` does the same with its `slug` (`:2313`). **Measured** in a
scratch directory on Windows: slug `/../../../victim/SKILL` writes
`victim/SKILL.md` three levels above the memory directory, and
`write_topic_note(slug='../escaped')` writes outside it too. From a real
memory directory, four levels up is the folder that holds the global skills,
and the caller controls the body, so this is a second way to the write ML1
describes. Same class as pass 2 R55; on POSIX the `position_` path component
does not exist and the write fails.

`delete_position` and `resolve_mint` do validate (`memory.py:3338-3343`).
A second defect in the same line: the automatic slug is the first 48
characters of the subject key (`:2217`), so two subjects that share a 48
character prefix supersede each other.

Smallest fix: one `_safe_note_slug()` (reject `/`, `\`, `..`, and anything
whose resolved parent is not `mem_dir`, as `resolve_mint` does) used by both
writers.

Test: none passes a slug with a separator.

### Rail 3 — "no" must be durable

#### ML8 · high · L — a stats sidecar that fails to parse is read as empty, and the next counter bump writes the empty schema over it: every recorded "no" is gone

`_read_skill_stats` (`mc/distiller.py:452-468`) returns `_empty_stats()` on a
JSON error and logs "preserving file untouched". Every caller is a
read-modify-write under the lock: `_increment_counter` (`:507-513`),
`_commit_signals`, the outbox marker (`:1832-1837`), `_suppress_artifact`.
The first of those to run replaces the file with the empty schema plus one
counter. `_increment_counter` runs on every dispatch
(`exploration_read_floor`, `:2388`), so "untouched" lasts until the next
agent starts. Any other read error takes the same path with no log at all
(`:467-468`).

What is lost: `suppressions` (the per-project half of rail 3), the outbox, and
the signal window (2,762 signals and 34 suppressions in the reviewed install's
largest sidecar, read-only count). The global store
(`_global_skill_stats.json`) is read and written by the same functions, so
cross-project rejections go the same way.

The unparseable case is reachable: a kill between the write and the flush
can leave a zero-filled or truncated file, and pass 1 C1 covers
`_atomic_write_text` itself (one temp name per process).

Two lookups also fail open: `_is_suppressed` (`:1636-1642`) and the global
read at `:1239-1245` swallow any exception and treat it as "not suppressed",
with no log.

Smallest fix: make `_read_skill_stats` raise a typed error on an unreadable
file; writers catch it, move the file aside to `<name>.corrupt-<ts>` and log
once; `_is_suppressed` treats the error as suppressed (fail closed).

Test: none. A grep of `tests/test_distiller*.py` for `corrupt`,
`JSONDecodeError` and `torn` returns nothing.

#### ML10 · medium · L/E — the pre-generation "no" check and the 7-day dedupe are keyed by the signal kind; every writer keys by the artifact kind

`_evaluate_candidate` (`mc/distiller.py:1284`, `:1289`) looks up
`f"{exact}:{kind}"` where `kind` is the SIGNAL kind (`topic`). The writers
use the ARTIFACT kind (`skill`): the outbox marker (`:1834`),
`_suppress_artifact`, `record_push`. **Measured** with the pure function: with
a suppression and a fresh outbox entry under `abc:skill`, the evaluator still
returns a `skill` candidate; under `abc:topic` it returns None. Read-only, the
reviewed install's sidecar has 48 outbox keys and 8 suppression keys ending
`:skill` and none ending `:topic`.

Consequences for skill artifacts:

- a rejected pattern is caught only by the post-generation re-check
  (`:1806`, which uses the artifact kind), so rail 3 holds, but each
  recurrence first pays a model call;
- the outbox dedupe has no post-generation check, so it never binds: each
  recurrence writes another proposal. Read-only: 245 pending skill proposals
  against 48 outbox keys.

Since the human queue is where rubber-stamping happens, feeding it duplicates
matters more than the token cost.

Smallest fix: map the signal kind to its artifact kind before both lookups
(one line each), and add the measured case as a test.

Test: the existing cases cannot see it.
`tests/test_distiller_preference_threshold.py:27-30` passes empty
`suppressions` and `outbox`, and
`tests/test_distiller_preference_backfill.py:88`, `:101` key by
`:preference`, the one kind whose signal and artifact names are the same.

#### ML11 · medium · L — `POST /api/distiller/reject` has no caller check: an agent can record a permanent "no"

`mc/blueprints/distiller_routes.py:215-228` calls `reject_proposed` for any
caller. Rail 3 makes that decision durable and (for a cross-project artifact)
global. An unattended session can therefore empty the human's review queue and
permanently suppress patterns the human never saw. This is the mirror image of
the promote route, which has both checks (`:125-143`).

Smallest fix: the same `is_unattended_caller()` refusal the promote route
uses. No passcode needed; the decision is reversible by editing the sidecar.

Test: none.

## 2. `mc/memory.py` — the writers (Scribe, checkpoint, commit, condense, continuity)

#### ML15 · high · L — a task whose first 80 characters contain a newline loses its Scribe summary at the next commit

`_entry_label` (`mc/memory.py:4623-4629`) returns `t[:80]` with newlines
kept. `_write_session_memory` builds
`- [date] **{label}**{tag} — {brief}` (`:4950`) and flattens newlines in
`brief` only. A label with a newline makes the entry two or more physical
lines. On the next commit `_mem_split_full` keeps only lines that start with
`- [` (`:597-602`), so the first line survives and the rest, which holds the
closing `**` and the whole summary, is dropped. The checkpoint path builds its
entry the same way (`:5328`).

Second effect: `last_entry_hash` is the hash of the multi-line string
(`:4791`), which never matches a single stored line again, so each later
checkpoint of that session appends a new entry instead of superseding the old
one. That is the pile-up `supersede_sid` was added to stop
(`:4749-4760`).

**Measured.** Pure function (`_scratch/p3/verify1.py`): a two-line entry comes
back from `_mem_split_full` as `- [2026-11-16] **Fix the login bug` and
nothing else. Read-only count on a copy of the reviewed install's archive:
1,226 of 7,727 entries (16%) have exactly one `**`, that is, a label cut at a
newline and no summary. 423 of those start with a turn-clock stamp line, which
the stored task no longer carries (current entries on the reviewed install do
not show it); 167 start with another bracketed marker line; 636 are ordinary
multi-line messages with a short first line, which is a path any user takes.
The per-month spread of the 1,226 was not taken.

What is lost is the paid part: the model call ran, and its output was written
once and deleted by the next write.

Smallest fix: `return ' '.join(t.split())[:80]` in `_entry_label`, and assert
in `_commit_managed_entry` that `mem_entry` has no newline.

Test: none. `tests/test_memory_entry_dedup.py:48` checks one single-line
label.

#### ML16 · high · E/L — the shipped-default condense (`agent` mode) fires on a trigger that can never clear, and its prompt describes a file layout that no longer exists

Default: `condense_mode: 'agent'` (`server.py:161`), threshold 30 KB
(`:154`).

- **Trigger.** `_should_condense` sums `MEMORY.md` + `SESSION_LOG.md` + the
  permanent archive (`mc/memory.py:4366-4372`), and at the commit call site
  also `CLAUDE.md` (`:4846`, `include_claude_md=True`, `:4373-4380`), against
  `condense_threshold_kb` (`:4381-4382`). The archive is append-only by design
  (`:4689-4691`). Once the sum passes 30 KB it stays above it for the life of
  the project; a 20 KB `CLAUDE.md` alone gets most of the way there. Only the
  cooldown (`:4299-4301`, one hour) limits the rate. The structured branch got
  exactly this fix ("would let a large CLAUDE.md … keep the trigger permanently
  hot", `:4307-4312`); the comment ends "The legacy agent path keeps its
  existing combined-byte trigger below, unchanged."
- **Cost of one fire.** `_dispatch_condense` (`:6728-6852`) launches a Claude
  agent with `--dangerously-skip-permissions`, `--max-turns 14`, default model
  `sonnet` (`:6840`, hard-coded to the `claude` provider whatever the project
  uses), with the project as its working directory. There is no "nothing to
  do" check before the launch.
- **What it is told.** The prompt (`:6769-6812`) describes a managed region
  inside `MEMORY.md`: sentinels, a `## Session Log` heading, watermark lines,
  and asks the agent to keep that structure. Since the `SESSION_LOG.md` split
  the entries and watermarks are not in `MEMORY.md` (`_write_curated_only`,
  `:4844`), the agent is never told `SESSION_LOG.md` exists, and the integrity
  guard's sentinel and watermark checks (`:6340`, `:6354`) compare against an
  empty pre-image. On a migrated project the run can only rewrite the curated
  index, which is the part the design says condense must not shrink.

So a fresh install, once its project passes the threshold, pays one
tool-capable agent run per active hour, reading Scribe lines (summaries of
arbitrary transcripts) and editing the always-loaded index, to do a job the
current file layout no longer gives it. Read, not measured: the reviewed
install runs `structured`, so no agent-mode run was observed.

Smallest fix: make `structured` the shipped default (the comment at
`server.py:158-160` says `agent` stays "until the structured path is
telemetry-validated"; it has been the live mode on the reviewed install), or
give the agent branch the structured branch's trigger and delete the stale
prompt sections.

Test: none on the agent-mode trigger after the split.

#### ML17 · medium · L — `write_continuity` is an unlocked read-modify-write of one file shared by every agent on the project

`mc/memory.py:1829-1950` reads the whole record, replaces one owner's slots
and writes the whole file back (`:1943-1947`), with no lock.
`write_position` took a lock for this exact race (`:2182-2191`). Two agents
finishing a turn together: the second write drops the first's update.

Worse, `read_continuity` returns the empty record on any read or parse error
(`:1755-1763`); the write that follows then persists a file containing only
the caller's bucket, and every other owner's threads and commitments are gone.
Pass 1 C1 (one temp name per process, measured torn reads) makes that read
error reachable from the write path itself. Callers: the extraction worker
(`:2126`) and `PUT …/memory/continuity` (`guide_routes.py:513`); neither
holds a lock.

Smallest fix: wrap read-to-write in `_get_mem_write_lock(f'continuity:{pid}')`
and have the internal read raise on error instead of returning empty.

Test: none concurrent; none with an unreadable file.

#### ML18 · medium · L — `_mem_split_full` treats the first literal `## Session Log` in a sentinel-free index as the start of the managed region

`mc/memory.py:590-593`. Every commit runs `_mem_migrate(existing)` on
`MEMORY.md` (`:4775`). In a file with no sentinels, the first occurrence of
the string `## Session Log` anywhere, including in a curated sentence that
mentions it, starts the managed region: `- [` pointer lines after it move to
`SESSION_LOG.md` (and from there age out to the archive), every other line
after it is dropped, and `_write_curated_only` (`:4844`) persists the cut.

Two more edges in the same function: text after the END sentinel is dropped
by the next compose (`:585-589`), and END before BEGIN raises `ValueError`
from `content.index(_MEM_END, i)` (`:587`), which fails every later commit
for that project.

Smallest fix: match the legacy heading only as a whole line
(`^## Session Log\s*$`), keep text after END as curated, and treat
END-before-BEGIN as "no managed region" with a log line.

Test: none with the heading inside prose, a tail after END, or reversed
sentinels.

#### ML19 · medium · L — commit ordering: the archive is appended first and `MEMORY.md` is stripped before `SESSION_LOG.md` is written

`mc/memory.py:4843-4845`: `_append_to_archive`, `_write_curated_only`,
`_write_session_log`. Three files, three separate atomic writes, no rollback.

- Failure after the first: the evicted lines are in the permanent archive and
  still in the session log; the next commit evicts and appends them again.
- Failure after the second, on a project not yet migrated: the inline entries
  and watermark markers have been removed from `MEMORY.md` and are not yet in
  `SESSION_LOG.md`. They are lost, and a live session's watermark with them,
  so its next checkpoint starts from offset zero.

`_append_to_archive` (`:4687-4697`) reads with strict UTF-8 and the docstring
of `_commit_managed_entry` says file failures propagate (`:4746`): one
undecodable byte in the archive fails every later eviction.

Smallest fix: write `SESSION_LOG.md` first, then the archive, then the
curated strip; read the archive with `errors='replace'`.

Test: none injects a failure between the writes.

#### ML20 · medium · L — a failed checkpoint launch leaves the session marked in flight for ever

`mc/memory.py:5161-5173` and `:5197-5211`: the session id is added to
`_checkpoint_inflight`, then the snapshot dict is built, which calls
`_model_for_provider` and `_session_owner`. If either raises (pass 1 P1:
`background_model` trusts stored value shapes), the outer
`except Exception: return False` (`:5216-5217`) returns with the id still in
the set. Every later boundary for that session takes the
`checkpoint_coalesced` branch (`:5162-5164`). The session never checkpoints
again and nothing is logged.

Smallest fix: build `snap` before taking `_checkpoint_guard`, or discard the
id in an `except` around the build; log the exception at `:5216`.

Test: none.

#### ML21 · medium · L — the agent-mode restore and heal write `MEMORY.md` outside the write lock, minutes after the pre-image was taken

`mc/memory.py:6909`, `:6923`, `:6931` use plain `write_text` (not atomic) and
do not take `_get_mem_write_lock`. The pre-image is captured before the agent
run, which lasts up to 14 turns. Any commit to `MEMORY.md` in between is
overwritten by the restore. Other half of pass 1 C1 for this file.

Smallest fix: take the lock, re-read, and restore only if the file still
equals what the agent wrote; use `_atomic_write_text`.

Test: none.

#### ML22 · medium · L — "busy" guards drop a second session's work instead of queueing it

`mc/distiller.py:874-881`: a session of project P that ends while another
session of P is being distilled is counted `skipped_reentrant` and its signals
are never extracted. **Measured**, read-only: that counter is 447 in the
reviewed install's largest sidecar. With several agents per project this is
the normal case, and it biases recurrence counts downward.

`mc/memory.py:6137-6140` has the same shape for the Scribe. It is unreachable
today only because pass 2 R38 holds the project lock across the Scribe call.
R38's proposed fix says the busy guard is enough once the lock is released;
with this guard as written, that change turns a slow completion into a lost
summary. The two fixes have to land together.

Smallest fix: a per-project queue of pending session ids drained by the
running worker, in both places.

Test: none with two overlapping session ends.

#### ML23 · low · M — `_write_session_memory` says "Never raises" and does

`mc/memory.py:4925` vs `:4957`: `_commit_managed_entry` propagates file
errors and is called unguarded. Both callers catch and log
(`agent_routes.py:10593-10610`, `server.py:1646`), so nothing crashes, but
the exception also skips the Distiller dispatch and the mint that follow in
the same function, and leaves the row unscribed, so the startup reconciler
pays a second Scribe call for it.

Smallest fix: catch around `:4957`, log, and continue to the Distiller step.

Test: none.

### Lesser findings, memory.py writers (low)

- `mc/memory.py:6256`, `:6292` — a Scribe summary is discarded if it contains
  any refusal marker as a substring (`:5437-5444`), and the list includes
  "don't have access to", "did not receive" and "no transcript". A true
  summary of a session about a permission problem is thrown away and the entry
  falls back. One hit in any map chunk fails the whole summary. Fix: test the
  markers only against the first sentence.
- `mc/memory.py:6164-6165` — a transcript render I/O error is reported as
  `parse_empty`, no log. `:5505-5506`, `:5490-5491`, `:5850-5851`, `:5080`,
  `:6507-6508` swallow state-file or transcript I/O with no log (project
  exception policy).
- `mc/memory.py:5808` — no dict guard on a parsed transcript line; one
  non-dict JSON line raises and the outer handler abandons the rest of that
  transcript's telemetry.
- `mc/memory.py:4879-4880` — `live` is computed before the lock; the docstring
  (`:4861-4862`) says the membership test is redone inside it. Same class as
  pass 2 R41.
- `mc/memory.py:2098-2136` — continuity extraction: model output goes into the
  record and from there into every later system prompt as a commitment the
  agent made. Only length caps apply (`:1554-1565`); the mint and negation
  writers run the authority guard on comparable text, this one does not.
- `mc/memory.py:6018` with `:5960-5970` — the habit miner's "a human said it"
  test is `trigger_type == 'manual'` plus a label match. Pass 2 R48
  (`/agent/send` is always labelled attended) is the upstream hole.
- `mc/memory.py:785`, `:806` say the module has no top-level `re` import; it
  does (`:32`). `:5995-6008` says `_memory_search` has no origin filter; it
  has (`:2571`).
- `mc/memory.py:2355-2356` — `list_positions` reads each file unguarded; one
  unreadable file fails the whole list. `_parse_position` logs its validation
  warnings on every parse (`:1512-1515`), which is every corpus rebuild.
- `mc/memory.py:6893-6896` — the condense session is added to `agent_sessions`
  and the manager's id set; no removal was found in `_run`. Not followed into
  the housekeeping reaper, so this is unconfirmed.

## 3. The read side: search, per-turn delivery, the push observer, the note gate

Timings in this section are **measured** on a copy of the reviewed install's
memory directory in a scratch folder (`_scratch/p3/verify3.py`): 837 `.md`
files (3.5 MB), an archive of 2.54 MB, 6,357 corpus units of which 5,517 are
archive lines.

| Operation | Time |
|---|---|
| `_memory_search`, cold cache (first search after a restart) | 1,612 ms |
| `_memory_search`, warm | 79 ms |
| `_memory_search`, first search after one line is appended to the archive | 358 ms |
| `_memory_search`, first search after `SESSION_LOG.md` changes | 105 ms |
| push-observer style query (`expand=0`, `keep_internal=True`), warm | 31 ms |
| `delivery_stats.json` read + dump + write (one recorded search) | 14 ms |
| skill-stats sidecar read + dump + write (one counter bump, 2.93 MB) | 29 ms |
| `distiller.list_proposed()` (2,448 artifacts) | 520 to 705 ms |
| `memory_note_gate._foreign_vault_of`, one unresolved link | 898 ms |

For scale: the 2026-09-10 efficiency review measured the same search at 26 ms
over 115 files.

#### ML24 · high · E — the push observer runs a full search on the stream-reader thread for every file tool call, delivers nothing, and its log has no cap

`memory_push_mode` ships as `'report'` (`server.py:612`), which is the only
implemented mode: the observer never delivers to the agent
(`server.py:603-606`). For every Read, Edit, Write or Grep tool input, and for
every tool result with an error-looking line, `_observe`
(`mc/memory_push.py:345-397`) calls `_memory_search` (`:351`) on the thread
that reads the agent's stream (`agent_routes.py:4479-4503`, called from the
reader at `:6324`, `:6732`), then appends up to six `near_miss` rows per call
to a JSONL log.

- **Per call:** 31 ms warm; 358 ms when the archive changed since the last
  search (ML26), because this is then the search that rebuilds the cache.
  That time is taken from stream reading.
- **Log growth, read-only:** 129.6 MB, 273,371 rows since 2026-09-14, of
  which 219,060 are `near_miss`. A grep of `mc/memory_push.py` for a rotation
  or size cap finds none. One reader exists
  (`tools/memory-eval/push_review.py`), run by hand.
- **The data it logs is partly wrong.** `_ERROR_LINE_RE` contains
  `\b[45]\d{2}\b` (`:102`), which matches any standalone number from 400 to
  599: a line number, a byte count, a port. And `already = fname in
  turn_delivered` (`:357`, `:366`) tests a file name against a set of unit
  ids (`memory_turn._uid_of`); for archive and managed hits those differ, so
  the "already delivered" column is wrong exactly where the comment at
  `:367-368` says it matters.

Smallest fix: sample (one observed call in N), drop `near_miss` rows below
the top two, rotate the log at a fixed size, require an HTTP-ish context
around the status-code pattern (`HTTP`, `status`, `code` within the line),
and compare uids with uids.

Test: `tests/test_memory_push.py` covers extraction and scoring; nothing
bounds the log or asserts the `already` column for an archive hit.

#### ML25 · high · E — every dispatch walks every proposed artifact of every project and rewrites the stats sidecar two or more times; a steward dispatch rewrites it once per withheld artifact

`exploration_read_floor` (`mc/distiller.py:2378-2412`) runs at every dispatch
with a task (`agent_routes.py:5642-5645`, `scheduler_routes.py:551-555`):

- `_increment_counter('readback_query')` (`:2388`), and `readback_hit` on a
  match (`:2408`). Each bump is a locked read, a full JSON parse, and a full
  atomic rewrite of the sidecar (`:507-513`): 29 ms on a 2.93 MB file.
- `list_proposed()` (`:2390`) reads every artifact file of every project,
  twice each (`_read_proposed_meta`, `:2822`, then `_exploration_snippet`,
  `:2831`), before the scope filter is applied (`:2393`): 520 to 705 ms for
  2,448 artifacts.
- For an unattended consumer, one more bump **per withheld artifact**
  (`:2398-2400`). Read-only count: for the project with the most proposals,
  474 in-scope explorations are not `interactive`. 474 bumps at 29 ms is
  about 13.7 s of sidecar rewriting per steward dispatch (product of two
  measured numbers; the loop itself was not run because it writes under
  `data/`).

`_proposed/` only grows on its own: promoted and rejected artifacts leave it,
unreviewed ones stay (2,448 files, 4.8 MB).

Smallest fix: count blocked artifacts in a local variable and bump once;
filter by scope from the directory name before reading any file; cache
`list_proposed()` by the `_proposed/` tree's mtime; keep counters in memory
and flush on a timer.

Test: `tests/test_distiller_readback.py` checks results on a handful of
artifacts; nothing measures or bounds the work.

#### ML26 · medium · E — the archive is one cache unit, so every eviction re-reads, re-tokenises and re-dedupes all of it

`_mem_corpus` caches per file by stat (`mc/memory.py:2530-2543`); the archive
is one file (`:2439-2443`). `_append_to_archive` rewrites the whole file per
eviction (`:4687-4697`), and once the session-log ring is full every commit
evicts. So each session end or checkpoint costs a 2.5 MB read and rewrite,
and the next search pays 358 ms instead of 79 ms (table above). After a
restart the first search of each project pays 1.6 s, inside dispatch.

In `_memory_search` itself, `_mem_snippet` and `_head` are computed for every
matched unit before the sort and the top-k cut (`:2709-2713`), config is read
per unit (`:2682`), and the supersede graph is rebuilt per query
(`:2720-2721`). Read, not separately measured; they are inside the 79 ms.

Smallest fix: append to the archive with an append-mode write instead of
read-modify-write, and cache archive tokenisation by (size, last line) so an
append tokenises only the new lines; compute snippets for the top-k only.

Test: none on cost.

#### ML27 · medium · E/L — delivery telemetry rewrites a 1.4 MB JSON file on every search, never prunes it, and a corrupt read resets it

`mc/memory_delivery.py:92-137`: every recorded search (each dispatch, and
each live turn through `memory_turn.py:415`) reads the whole stats file,
updates it and writes it back, indented and key-sorted, under one
process-wide lock. Read-only: 1.39 MB, 3,530 units, 9,175 recorded searches
since 2026-08-24; 14 ms per call measured. `units` is keyed by content hash
and nothing removes a unit whose note is gone. `read_stats` returns `{}` on a
corrupt file and `record` then writes fresh counters over it (logged; same
class as ML8). The field named `tasks` counts search calls, so a ten-turn
chat counts as ten tasks: any "delivered on N% of tasks" figure read from it
is per turn.

Smallest fix: append one JSONL row per search and aggregate in the review
tool; or keep the dict in memory and flush on a timer. Rename `tasks`.

Test: none on size or on the corrupt-file path.

#### ML28 · medium · E — one unresolved link in a note costs a scan of every sibling vault, under the topic write lock

`memory_note_gate._vault_keys` reads the head of every note in a vault
(`mc/memory_note_gate.py:212-245`); `_foreign_vault_of` repeats that for
every sibling vault for each unresolved link target. **Measured:** 898 ms for
one unresolved link (4,280 vault directories exist on the reviewed install;
agent worktrees each get one). `memory_gate_tap.begin` runs this for every
automated mint, and `write_topic_note` calls it inside
`_get_mem_write_lock(f'topic:{project_id}')` (`mc/memory.py:2320`,
`:2340`). The gate is in report mode, so the answer is only logged.

Smallest fix: call the tap after releasing the lock (it already has a
`finish` half outside it, `:2342`), and resolve foreign vaults from a cached
slug-to-vault map with a short TTL.

Test: none on cost.

#### ML29 · medium · L — `PATCH` on a note reads the old text outside the lock

`mc/blueprints/memory_note_routes.py:143` reads `existing`; the gate then
evaluates, merges origin from that stale text (`:164`) and writes with
`atomic_write(must_exist=True)` (`:172`), which re-checks only that the file
still exists. A write that lands between the read and the lock is overwritten,
and if that write had set `origin: unattended`, the merge computed from the
stale text puts the old stamp back.

Smallest fix: read `existing` inside the same lock the write takes, or pass
its hash to `atomic_write` and refuse on mismatch (`concurrent_change` is
already a refusal the route knows how to return, `:175-176`).

Test: none concurrent.

### Lesser findings, read side (low)

- `mc/memory_turn.py:283-285` — `seed_delivered` runs a second full
  `_memory_search` at every dispatch only to recompute the hit list
  `_build_agent_context` had a moment earlier (79 ms). Pass the hits in.
- `mc/memory_turn.py:493-496` — the truncation log compares `raw_bytes`
  (lines only) with `cost` (lines plus headers plus ledger), so dropping one
  line is masked by the header bytes and the log stays silent.
- `mc/memory.py:2432-2435` — a failed read caches `[]` for that file under
  the stat taken before the read; the file stays invisible to search until it
  changes again. No log.
- `mc/memory.py:2837-2857` — negation entries render the description of every
  node that resolves to the head, with no `consumer_unattended` filter.
- `mc/memory_fts.py:186-189` — an incognito transcript is deleted and
  re-recorded on every index build, with no "already indexed" check;
  `:231-232` a non-dict agent-log entry raises and the file is retried every
  build. The index is built at startup only (`server.py:1735`), so cold hits
  never include the current run.
- `mc/memory_note_gate.py:438-446` — rename is write-new then unlink-old; a
  failed unlink leaves two files with one link identity and a 500 with no
  gate row.
- `mc/skill_scoping.py:116-130` — content-addressed settings files are never
  removed, one per distinct override set.

## 4. `mc/distiller.py` and `mc/skills.py` (beyond the rails)

#### ML30 · medium · L/M — three controls in the Distiller are configured and read but never take effect

- **The daily cost cap never trips.** `_within_cost_cap` (`:1375`) reads
  `stats['cost'][today]` (`mc/distiller.py:1385`) against
  `distiller_cost_cap_tokens_per_project_per_day` (100,000 on the reviewed
  install). Nothing writes `stats['cost']`: a grep finds the empty schema
  (`:479`) and two reads (`:1385`, `:2283`), no assignment. Read-only, the
  sidecar's `cost` is empty after 2,762 signals. The cap, `cap_hits` and the
  loop-health field that reports them are inert.
- **"Later" is not honoured.** `_evaluate_candidate` (`:1316-1322`) consults a
  `later` suppression only below the recurrence threshold, where both arms
  return None anyway. Above the threshold it is never consulted, so
  `record_push` computes and stores `wait_until_recurrence`
  (`:2238-2254`) for nothing: a pattern the human deferred is proposed again
  at the next recurrence.
- **The cross-project walk and the summary cache have no consumer.** The walk
  (`:1448-1525`) reads every project's stats file and emits
  `cross_project_candidate` log lines; `_update_summary_cache` (`:1530`,
  called at `:1018` on every commit of signals) writes
  `<project>_skill_stats_summary.json` (161 KB on the reviewed install). The
  only references to that file elsewhere are the lists that exclude it from
  project loading (`project_routes.py:444` and two tools).

Smallest fix: record tokens where the model call returns, or delete the cap
and say so; consult `later` before the threshold test; delete the summary
write until something reads it.

Test: none sets a cost and expects a refusal; none records "later" and then
crosses the threshold.

#### ML31 · medium · M — a model or transport error during rendering is counted as a refusal, with no log

`_render_skill`, `_render_exploration`, `_render_preference` and the reframe
each wrap the model call in `except Exception: return None, Path()`
(`mc/distiller.py:1934`, `:1960`, `:1995`, `:2038`). The caller counts
`None` as `render_refuse:<kind>` (`:1789-1791`), the same counter a real
`REFUSE` answer bumps. Read-only: `render_refuse:preference` is 1,540,
`:skill` 153, `:exploration` 31. Nobody can tell from that how many were the
model declining and how many were timeouts, quota errors or a missing CLI,
and `loop_health` reports the sum as the refuse rate. Project exception
policy: a swallowed network or subprocess failure must log.

Smallest fix: log the exception and bump `render_error:<kind>` instead.

Test: none separates the two.

#### ML32 · medium · M — extraction output is used without a type guard; one malformed item loses the whole session's signals

`_normalize_signals` (`mc/distiller.py:1043-1108`): `signals` is assumed to
be a dict and each item of `topics`, `preferences` and the rest is assumed to
be a dict (`sig.get(...)`, `:1059`). A model that returns a list for
`signals`, or a bare string inside `topics`, raises `AttributeError`; the
daemon thread's handler counts `daemon_thread_exception` and every signal of
that session is dropped. `paths_tried` items are stored and later replayed
into prompts with no type or length cap.

Smallest fix: `if not isinstance(sig, dict): continue` per item, coerce
`signals` to `{}` when it is not a dict, cap `paths_tried` items to strings of
a fixed length.

Test: fixtures are well-formed.

#### ML33 · medium · L — promote installs with `overwrite=True` under a model-chosen name

`mc/blueprints/distiller_routes.py:192-206` passes `overwrite=True`;
`write_skill` (`mc/skills.py:338-351`) then replaces any existing
`SKILL.md` of that name in the target scope. The name comes from the
artifact's frontmatter or from the reframe model's output
(`distiller_routes.py:176`). A proposal that happens to be named like a
hand-written or built-in skill replaces it, with no backup and nothing in the
response to say so. The `name:` is also searched in the whole model output,
not only its frontmatter (`mc/distiller.py:2087-2094`).

Smallest fix: `overwrite=False` unless the existing skill's
`extraction_fingerprint_exact` matches the artifact's; otherwise return 409
and let the human choose.

Test: none promotes onto an existing name.

#### ML34 · medium · L — frontmatter dump escapes, parse does not unescape: every edit doubles the backslashes

`dump_skill_md` writes `"` as `\"` and `\` as `\\` when it quotes a value
(`mc/skills.py:216-218`); `_strip_quotes` removes the outer quotes and
nothing else (`:185-188`). **Measured** (`_scratch/p3/verify1.py`): a
description containing `"hi"` reads back as `\"hi\"` after one round trip,
`\\\"hi\\\"` after two, and doubles each time. Every read-then-write path
does a round trip: the `PUT` route, `mc/skills.py:767`, `:1214`, and
`write_position`, which uses the same dump (`mc/memory.py:2258`) and is
re-read on every supersession.

The value is quoted whenever it contains any of `:#&*!|>%@` or a backtick, so
any description with a colon and a quote is affected.

Smallest fix: unescape `\\` and `\"` in `_strip_quotes` for double-quoted
values.

Test: none round-trips a value containing a quote.

#### ML37 · medium · M — the import scanner returns zero findings for a UTF-16 script, a script over 2 MB, or a script with a skipped extension, and the import is allowed

`scan_skill_dir` (`mc/skill_import_guard.py:295-314`) skips a file by
extension (`_BINARY_EXTS`, `:215-219`, which includes `.bin`), skips any file
over 2,000,000 bytes (`:308-309`), and reads the rest as
`utf-8, errors='ignore'` (`:310`). The comment at `:211-214` says a mis-decoded
file "can never suppress a real finding (only add noise)". It can: ASCII text
saved as UTF-16 decodes to the same characters separated by NUL bytes, and no
pattern matches across them.

**Measured** (`_scratch/p3/verify5.py`, scratch directory): a payload with an
instruction-override line, a recursive delete and a write to `CLAUDE.md`
gives 3 findings as UTF-8 text. A skill folder holding that payload three
times, as `run.ps1` in UTF-16 (what Windows PowerShell 5.1 writes with `>`), as
`big.sh` padded past 2 MB, and as `tool.bin`, gives **0 findings**, and
`evaluate(findings, 'community')` returns `allow: True` with no `force`.
`import_folder` then copies the whole tree, skipped files included
(`mc/skills.py:749-761`).

The scanner is advisory and `force` exists, so this is not a rail. But the
one thing it tells the human is "nothing found", and here that means "nothing
read".

Smallest fix: emit a `caution` finding for every file not scanned (skipped
extension, over the size cap, or containing NUL bytes after decode), and
decode by BOM before falling back to UTF-8.

Test: none. `tests/test_skill_import_guard.py` (26 tests) has no UTF-16,
oversize or skipped-extension case (grep for `utf-16`, `2_000_000`, `.bin`:
no match).

### Lesser findings, distiller and skills (low)

- `mc/skills.py:1284-1352` — `skill_usage_stats` re-reads every transcript
  line of every project modified in the window on each
  `GET /api/skills/usage` (called when the Skills panel opens,
  `static/js/skills-panel.js:356`); no cache. `:1310` calls `stat()` outside
  the `try`. Read, not measured.
- `mc/skills.py:759-761` — overwrite is `rmtree` then `copytree`; a failed
  copy leaves no skill at all. `copytree` follows symlinks, and the only link
  rejection is for pinned refs (`:900`).
- `mc/skills.py:497-502` — a built-in the user archived or deleted is
  reinstalled at the next start; there is no tombstone.
- `mc/distiller.py:907-909` — the kill-switch gate is called with
  `incognito: False, housekeeping: False` hard-coded; it relies on the caller
  having filtered both.
- `mc/distiller.py:507-515` — `_increment_counter` swallows every exception
  with no log; with an empty-file sidecar (`{}`), `stats['counters']` raises
  `KeyError` and every counter silently stops.

## 5. Small modules and the topics routes

#### ML35 · medium · L — the continuity liveness label reads ordinary words as branch names and labels a normal finish as lost in a restart

`mc/continuity_liveness.py:15`, `:59`: `_BRANCH` takes the word after any
"branch ". **Measured**: "on a branch of the worktree" yields `of`, "the
branch is pushed" yields `is`. When the same line also has a session id, that
word goes to `git show-ref` and the thread is rendered
`STATE MISMATCH … branch absent`. Separately, `:36-39` with `:77`: a session
that finished normally has `live is False`, and the label for that is
`LOST IN RESTART`, whether or not anything restarted.

Both labels go into the continuity block of every later system prompt
(`mc/memory.py:1976`), where they tell the next agent that work it could
safely continue is in an inconsistent state. Each named branch is also one
`git` subprocess (5 s timeout) per render.

Smallest fix: require a path-like token after "branch" (contains `/` or
`-`, not in a small stop-word set); label a finished session `ENDED`, and
keep `LOST IN RESTART` for a session that was running at the last boot.

Test: none for prose after "branch"; none for a normally finished session.

#### ML36 · medium · L/M — the topic state route never loads the project, swallows write failures and answers `ok`

`POST /api/project/<project_id>/topics/<topic_id>/state`
(`mc/blueprints/topics_routes.py:507-521`) uses `project_id` from the URL
directly in `_DATA_DIR / f'{pid}_topic_state.json'` (`:76-77`). It never
calls `_load_project`, so:

- a mistyped id creates a state file for a project that does not exist (the
  suffix is excluded from project loading, so this does not trip the
  `DATA_DIR` rule);
- on Windows an id containing backslashes and `..` places a
  `…_topic_state.json` file outside the data directory (same class as pass 2
  R55; the content is constrained to state records).

In the same module `_load_json` returns the default on any error (`:85-89`)
and `_atomic_write` swallows every write error (`:92-96`), both with no log.
A corrupt state file therefore reads as `{}` and the next click persists only
that one topic (every other done or archived mark is gone), and a failed
write still returns `{'ok': True}` (`:521`).

Smallest fix: 404 when `_load_project` returns nothing; log in both helpers;
return 500 when the write fails.

Test: none for a missing project or a failed write.

### Lesser findings, small modules (low)

- `mc/positions_review.py:93-102`, `:145-147` — a corrupt sidecar reads as
  `{}` and the locked write persists only the entry being recorded; every
  other position's `flagged` state is gone and each is raised again as new.
  Logged. Same class as ML8.
- `mc/positions_review.py:126-152` — lock break: two waiters past the
  deadline can both unlink and both create; the `finally` unlink removes
  whichever lock file is there, including a successor's.
- `mc/rollover_threshold.py:78` — a per-character pin of 0 or less becomes
  `MIN_THRESHOLD` (60k, the most aggressive value); the global setting treats
  0 or less as "rollover off" (`:68`). The same number means opposite things
  at the two levels.
- `mc/unattended.py:16-17` says the function "never reads anything the caller
  sends (no header …)"; `:76-77` reads a header. The docstring's later
  paragraphs explain the change; the first paragraph was not updated.

---

## 6. Checked and cleared

Listed so the next reader does not re-check them.

**Rails, where they hold.**

- The authority guard runs before the human queue on the Distiller's own
  generate path (`mc/distiller.py:1793-1800`) and on the three memory writers
  that produce standing text: mints (`mc/memory.py:3188`), negation rows
  (`:3593`), habit notes (`:5989`). The gaps are the paths in ML1, ML2 and
  ML9, not these.
- The exploration read floor withholds `unattended` artifacts from an
  unattended consumer and fails closed on an unstamped one
  (`distiller.py:2396-2398`). The gap is who the consumer is (ML4) and who
  wrote the stamp (ML7).
- A rejected skill pattern is not written again: the post-generation re-check
  (`distiller.py:1806`) uses the artifact kind and binds, even though the
  earlier check does not (ML10).
- The promote route refuses an unattended caller and requires the passcode
  (`distiller_routes.py:125-143`).
- The note gate's `compose` removes a caller-supplied `Origin:`, an indented
  `origin:` and `origin :` and ends with the server's stamp (**measured**,
  candidate K71).
- `delete_position` and `resolve_mint` validate the file name against the
  memory directory (`memory.py:3338-3343`); only the two writers in ML14 do
  not.
- `write_position` is locked for its read-modify-write (`memory.py:2221`),
  and the commit paths to `MEMORY.md` take the write lock (`:4234`, `:4577`,
  `:4766`, `:6582`). The agent-mode restore does not (ML21).

**`DATA_DIR` rule.** Every sidecar these modules write under the project
records directory is on the exclusion list
(`mc/blueprints/project_routes.py:440-447`: `_scribe_stats.json`,
`_skill_stats.json`, `_skill_stats_summary.json`, `_topics.json`,
`_topic_state.json`), and the global suppression store is pinned by
`tests/test_distiller_safety.py:170`. No new unexcluded writer was found.
ML36 writes a state file for a project id that does not exist, but its suffix
is excluded.

**Small modules with no finding.**

- `mc/scribe_user_turn.py`: a positional cut, never raises.
- `mc/memory_overlap_df.py`: pure.
- `mc/memory_gate_tap.py`: an observer; both calls are guarded and logged.
  Its cost is ML28, which is in the function it calls.
- `mc/memory_state_trigger.py`: bare-file-name guard, frontmatter only,
  bounded read.
- `mc/continuity_threads.py`: pure; an invalid stamp reads as stale.
- `mc/rollover_model_table.py`: a static table; an unknown model returns
  `None` and the legacy limit applies.
- `mc/memory_fts.py`: the incognito exclusion fails closed.
- `mc/skill_scoping.py`: fails open to "demote nothing"; per-writer temp
  name.
- `mc/positions_review.py`: writes only its own sidecar; the once-only flag
  logic matches its docstring on a readable file.

**Efficiency, bounded as designed.**

- The per-turn memory block is hard-budgeted (`memory_turn_budget_bytes`),
  and stale watermarks are collected at startup
  (`mc/memory.py:4849`, called at `server.py:1773`).
- The structured condense trigger counts only the auto-loaded file
  (`memory.py:4307-4312`). The agent-mode trigger does not (ML16).
- The position-capture directive is no longer paid on every dispatch: it is
  skipped for task-shaped dispatches and incognito
  (`agent_routes.py:5513`).

## 7. Prior audits: what is still open in this scope

**`docs/ENTANGLEMENT_AUDIT.md` (2026-10-02).**

- `mc/memory.py`, ranked 9, 6,696 lines, 16 concern groups, split plan #9
  ("memory engine", 7 to 10 sessions): **open, and larger.** The file is
  6,955 lines. None of the plan's target modules (`memory_paths.py`,
  `memory_sections.py`, `memory_writer.py`, `memory_scribe.py` and the rest)
  exists. Work added since the audit did go into new files
  (`memory_note_gate.py`, `memory_gate_tap.py`, `memory_overlap_df.py`,
  `memory_state_trigger.py`, `scribe_user_turn.py`, `continuity_liveness.py`,
  `continuity_threads.py`, `rollover_threshold.py`,
  `rollover_model_table.py`), which is the rule being followed for new units.
  The plan's own risk list names "byte versus character counts",
  "origin/authority checks" and "live-watermark ownership"; ML9, ML13, ML16
  and ML19 are instances.
- `mc/distiller.py`, ranked 30, 2,877 lines, 10 concern groups: **open**,
  2,884 lines. ML10 (two kinds of key) and ML30 (three inert controls) are
  what ten concerns in one file look like from the inside.
- The memory, continuity, positions and mint routes inside
  `mc/blueprints/guide_routes.py` (the audit's "split route contributors"):
  **open.** They are still in that file, and they are where ML5, ML12 and
  ML14 enter.

**`docs/_review/2026-09-10_efficiency.md`.**

- "`_memory_search` is 26 ms over 115 files, no finding": **no longer
  true at this size.** 79 ms warm, 358 ms after an archive append, 1,612 ms
  cold, over 837 files (section 3, ML26). The code path is the same; the
  corpus grew, mostly the archive.
- "`memory_turn` is byte-budgeted": **still true.** The budget holds; the
  truncation log does not report every truncation (section 3, lesser).
- E1, the 1,312-byte position-capture directive on every dispatch:
  **fixed** for task-shaped dispatches (`agent_routes.py:5513`).
- E1e, the always-loaded index over its byte budget: **not re-measured.**
  A read-only count that named the memory folder was refused by the fence,
  and I left it out. The standing position on eviction governs it either
  way.
- "`_extract_transcript_telemetry` streams the file once": **still true**;
  one malformed line now abandons the rest (section 2, lesser,
  `memory.py:5808`).
- That review listed `mc/distiller.py` under "not covered". Covered here:
  ML25 is its per-dispatch cost.

## 8. Open questions (not decided here)

1. **Should an unattended run read Scribe lines written from unattended
   sessions?** (ML9, path 1.) A steward needs its own earlier cycles'
   summaries to keep continuity; rail 2 as written says autonomous output must
   not become autonomous input. Options: (a) leave managed and archive units
   outside the rail and say so in the rail's text; (b) stamp them and withhold
   them, and give the steward its history through the journal only; (c) stamp
   them and allow only the same project's own steward lineage. I would take
   (a) with the rail's wording corrected, plus the fixes for paths 2 and 3,
   which no version of the argument covers. Cost of (b): stewards lose the
   search hits they currently get from 5,517 archive lines.
2. **Is the `Origin` header meant to be a security boundary at all?**
   (ML6, and `mc/unattended.py:76-77`.) If local routes are a shared
   surface by design, the stamp cannot come from the request, and the fix is
   the session-based one in ML6. If a real boundary is wanted, it needs a
   token the agent cannot read. The promote route already assumes the first
   reading.
3. **ML1: gate the skills routes, or accept that agents may install
   skills and delete the claim from CLAUDE.md?** The code and the document
   currently disagree. I would gate; the promote route sets the precedent.

## 9. Tests: what the suites pin, and why they did not see these

The rails have real tests. `tests/test_distiller_safety.py` pins the guard
against every artifact that escaped and a set of paraphrases (`:48`, `:61`),
the steward marker against the fence constant (`:81`), the unattended read
floor on stamped and unstamped artifacts (`:97`, `:120`), the conservative OR
(`:137`), and the durable global "no" (`:146`, `:170`).

Every one of them calls the function that implements the rail. None enters
through a route, none dispatches a session with a non-manual trigger and a
plain task, and none starts from a damaged file. That is the common reason
the findings in section 1 are untested:

| Gap | Findings it hides |
|---|---|
| No test posts to a mutating skills, positions, mint or reject route as an unattended caller | ML1, ML5, ML11, ML12, ML14 |
| No test promotes through the route with a body the guard would refuse | ML2 |
| The paraphrase cases are phrases already on the list | ML3 |
| No dispatch test uses `trigger_type='schedule'` or `'dispatch'` with an unmarked task | ML4 |
| Fixtures write the `origin:` stamp themselves | ML6, ML7 |
| No fixture is multi-line where a single line is assumed | ML13, ML15 |
| No test reads a truncated or unparseable sidecar | ML8, ML17, ML27, ML36 |
| Evaluator tests pass empty suppression and outbox maps, or use the one kind whose two names agree | ML10 |
| No test bounds work or file size | ML24 to ML28 |

Smallest addition that would have caught the most: one parametrised test
that walks the Flask URL map, takes every non-`GET` rule under
`/api/skills`, `/api/distiller` and `/api/project/<id>/memory`, calls it as an
unattended caller, and asserts a refusal.

## 10. Coverage

**Read fully:** `mc/memory.py` (6,955 lines), `mc/distiller.py` (2,884),
`mc/skills.py` (1,352), `mc/memory_note_gate.py`, `mc/memory_turn.py`,
`mc/memory_push.py`, `mc/memory_fts.py`, `mc/memory_delivery.py`,
`mc/memory_state_trigger.py`, `mc/memory_gate_tap.py`,
`mc/memory_overlap_df.py`, `mc/positions_review.py` (the only `mc/*position*`
module), `mc/scribe_user_turn.py`, `mc/continuity_liveness.py`,
`mc/continuity_threads.py`, `mc/rollover_threshold.py`,
`mc/rollover_model_table.py`, `mc/skill_scoping.py`,
`mc/skill_import_guard.py`, `mc/unattended.py`,
`mc/blueprints/distiller_routes.py`, `mc/blueprints/skills_routes.py`,
`mc/blueprints/memory_note_routes.py`.

**Read partly:**

- `mc/blueprints/guide_routes.py`: the memory, continuity, positions and mint
  routes, about lines 470-670 of 1,301. The rest of the file is outside this
  pass.
- `mc/blueprints/topics_routes.py`: the state helpers and the state route,
  lines 70-100 and 500-522 of 522. The digest builder in between was not
  reviewed.
- `mc/blueprints/agent_routes.py`: only the call sites named in findings
  (`:4479-4503`, `:5500-5650`, `:10591-10610`, and the dispatch sites listed
  in ML4). Pass 2 owns the file.
- `mc/blueprints/scheduler_routes.py`, `mc/steward_role_dispatch.py`,
  `mc/hivemind_worker.py`, `steward/fence.py`, `server.py`: the single lines
  cited, to confirm a caller or a default.
- Tests: opened to answer "would an existing test catch it" for each
  finding; not reviewed as code.

**Not reached, or not confirmed:**

- Three candidates were neither confirmed nor cleared and are **not**
  findings: whether the condense session is ever removed from
  `agent_sessions` (`memory.py:6893-6896`, listed as unconfirmed in section
  2); whether any persist path serialises the live session dict with the
  `set` that `memory_turn.py:83` and `memory_push.py:189` put on it; and
  whether skipping the running-agent check at the commit call site
  (`memory.py:4303`, `include_claude_md=True` from `:4846`) can start a
  condense under a live agent in agent mode.
- No concurrent run was made. Every race finding (ML17, ML19, ML21, ML22,
  ML29) is read from the code.
- ML1, ML7 and ML11 were read, not run: running them writes a skill, a
  proposal or a suppression.
- Nothing in agent-mode condense was observed running; the reviewed install
  uses `structured`.
- The frontend (`static/js/skills-panel.js`, the memory panel) is outside
  this pass.

**Measurements** were taken with `_scratch/p3/verify1.py` to `verify5.py`
(gitignored) against pure functions, a scratch copy of one memory directory,
and read-only counts. A context rollover happened during the pass; the
measurements were re-run afterwards and the numbers in this report are from
the re-run.
