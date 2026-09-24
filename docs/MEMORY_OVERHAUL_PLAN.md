# Memory overhaul — diagnosis and plan (MC-964)

Status: PLAN. No production code changed by this document (MC-892 ruling:
memory work gets a written plan before code). Written 2026-09-24 by Bram
(dispatch on backlog item b2d85e51). Running log with every number:
`docs/_journal/b2d85e51-memory-overhaul.md` (gitignored, local).

Reproduction script, committed with this doc:
`tools/memory-eval/codex_miss_repro.py` — read-only; rebuilds the memory
corpus as it stood at Ron's complaint (2026-09-20T00:20:16Z) and runs the
same `_memory_search` the per-turn refresh calls, at the live signature.

---

## 1. What actually happened (timeline, all measured)

| When (UTC) | Event | Source |
|---|---|---|
| 2026-09-17 | Ron tops up Codex. The only surviving record is an ASSISTANT echo: archive line 4000, `[2026-09-17] **Where do we stand?** — Understood, Ron—the access problem cleared after adding Codex credits…` | `MEMORY_ARCHIVE.md:4000` |
| 2026-09-17 (later) | 86 more checkpoint lines of the same chat land under the same key `(2026-09-17, "Where do we stand?")`. The last one (line 4169) is about vendor-decoupling progress. | `MEMORY_ARCHIVE.md:3992-4169` |
| 2026-09-18 08:21–15:06 | Codex genuinely exhausted AGAIN: 9 real `You've hit your usage limit` task_complete errors. | `~/.codex/sessions/2026/09/18/*.jsonl` |
| 2026-09-18 | Archive gains W3 "ALLOWANCE AS A FIRST-CLASS STATE" lines and "await allowance reset 2026-09-24". `data/allowance_state.json` records Codex exhausted, resets Sep 24. | `MEMORY_ARCHIVE.md:4179-4184` |
| 09-18 15:06 → 09-20 00:28 | **Zero Codex runs.** The exhaustion record refused every dispatch, so no run could succeed and clear it. | Codex rollout dirs: nothing between those times |
| 2026-09-19 | `87989ac` "a stale exhaustion record is now escapable after a top-up" (re-probe). | git |
| 2026-09-20 00:20:16 | Ron: "We have plenty of codex token allowance, I added some more." 00:21: "I told you earlier already… The fact that you did not remember it is worrying." | session 4ae1b44f |
| 2026-09-20 ~00:22 | `project_codex_allowance_topped_up.md` created — 2 minutes AFTER the complaint (the post-hoc fix). | file birth time |
| 2026-09-20 00:28 | First successful Codex run since 09-18. `allowance_state.json` emptied at 00:56:46. | rollouts, file mtime |

Consequence for the framing: on 09-19 the 09-17 top-up event was not, by
itself, the right answer — Codex had run dry again after it. The knowledge
that should have won was the **pattern** ("Ron tops up instead of waiting,
so an exhaustion record is a question to re-probe, not a fact"). That
pattern existed nowhere in memory until 00:22 on 09-20.

## 2. Root causes, with proof

### RC1 — Read-time archive dedupe deleted the fact from every retrieval path (deterministic)

`mc/memory.py:871 _dedupe_archive_lines` (shipped `bbcfe3d`, 2026-08-23)
keeps only the LAST archive line per `(day, task[:120])`. The task key of an
archive line is the chat's first message, not the message the fact was in.
One long 09-17 chat titled "Where do we stand?" produced 87 lines; the
top-up line (4000) was superseded by line 4169, which says nothing about
Codex credits.

Proof:
```
$ grep -c '^- \[2026-09-17\] \*\*Where do we stand?\*\*' MEMORY_ARCHIVE.md
87
$ python tools/memory-eval/codex_miss_repro.py --project-path <main> --no-dedupe --current
live signature: topk=6 expand=2; per-turn asks for 12
===== as-of-cutoff =====
[oracle] 'Codex credits added access problem cleared'   top-up fact rank: None of 42
===== as-of-cutoff, archive dedupe BYPASSED =====
[oracle] ...                                              top-up fact rank: 2 of 42
```
An oracle query built from the fact's own words cannot reach it with dedupe
on, and reaches #2 with dedupe off. Nothing else differs between the arms.

Blast radius, archive-wide: **2,702 of 4,341 lines (62%) are invisible to
retrieval**. The dedupe's premise ("near-identical entries that supersede
each other") holds for a minority: **2,079 of the 2,702 hidden lines (77%)
have more than half their content words absent from the surviving line.**
The 2026-08-23 measurement that justified it (the `/goal` first-guess case)
is real, but the fix over-generalised from "same answer, revised" to "same
chat title".

### RC2 — Even visible, the fact does not rank for the words Ron used (lexical mismatch + wrong shape)

Dedupe bypassed, Ron's actual message "We have plenty of codex token
allowance, I added some more" → top-up line rank **None of 42**; the 12
delivered slots go to a vendor-agnostic position, and at **#2 the stale
09-18 "W3 ALLOWANCE AS A FIRST-CLASS STATE" line**. The stored fact says
"access problem cleared after adding Codex credits"; the query says "token
allowance… added some more". BM25 has "codex" in common and little else,
while the stale line shares "allowance".

This is a capture-shape failure as much as a ranking one: the fact was
stored as a dated event inside an assistant summary under a generic chat
title, never as a topic note, position, or pattern with triggers. A durable
operator habit was filed as an event.

### RC3 — A live state record overruled memory and blocked its own refutation

`data/allowance_state.json` held "Codex exhausted, resets Sep 24". Dispatch
refused Codex while it was set, so no run could succeed and call
`clear_exhaustion` (`mc/allowance_state.py:119`, "cleared at resets_at or on
the next successful run"). Measured: zero Codex runs 09-18 15:06Z → 09-20
00:28Z. Memory had no channel to contradict a gate; the gate had no reason
to re-probe. `87989ac` (09-19) added an escape; it did not add "memory
contradicts state → probe".

### RC4 — Observability: the telemetry could not have caught this

`data/memory_push_log/mission_control.jsonl` (37,702 rows) records archive
hits as `note: "MEMORY_ARCHIVE.md"` with no line id. 495 observations on
09-18/19 had codex/allowance in the query and 83 of those were archive
rows, but no log can say which archive line they were, so "was the top-up
fact ever near" is unanswerable from telemetry. Only a replay answers it.

## 3. Q2 — Does mid-turn rollover / auto-fresh drop facts stated in chat?

**Yes, and this dispatch is the measurement.** `midturn_rollover_enabled`
is now **true** (live `/api/config`; `465ae55` shipped it off, it was
flipped since). This session rolled at 2026-09-24T00:11:06Z at 201,857
tokens (`data/midturn_rollover_log/mission_control.jsonl`).

What the pre-roll transcript (782336a0) held vs what crossed:

| Content | Chars | Carried? |
|---|---|---|
| tool_result (55 results — every measurement) | 235,337 | **No** |
| tool_use inputs | 39,196 | last 10 only, 160-char previews |
| assistant text | 1,130 | yes |
| user text | 2,308 | yes (≤8,000-char window, `_HANDOFF_MAX_CHARS`) |

Mechanism: `agent_routes.py` handoff keeps `(role, text)` turns only;
`mc/midturn_rollover.py build_state_block` adds task, git state and the
last 10 tool-call previews. Findings that live only in tool output and were
never written to a file are lost; the successor must re-run them (this one
did). A fact Ron states in chat survives only if it is inside the last
~8,000 chars of user/assistant text, or the Scribe checkpointer (8 KB
trigger) already filed it — and when filed, it lands in the archive under
the chat's first-message key, i.e. straight into RC1.

Not measured: whether the Scribe checkpoint fires before a roll. There is no
call from `midturn_rollover.py` into the Scribe; the archive has no line for
this session's pre-roll work as of 00:20Z.

## 4. Q3 — Making a contradicting fact surface loudly

Three conditions must hold, none of which does today:

1. **State records carry an escape hatch that memory can pull.** A gate that
   refuses on a remembered state (allowance, auth, quota) must re-probe when
   (a) a user message asserts the opposite, or (b) a memory unit matching the
   gate's vendor/state is younger than the state record. `87989ac` covers
   the manual escape; neither automatic trigger exists.
2. **Memory can bind to live state.** `holds_while` shipped (`1705397`) with
   a grammar whose metrics are memory-internal only (`index_bytes`,
   `corpus_units`, `delivered`…). No external state metric exists; **0 notes
   use `holds_while`, 0 use `supersedes`**. A fact like "exhausted until Sep
   24" needs `holds_while: allowance(codex).exhausted` so it retires itself
   when the state clears, instead of outranking the correction.
3. **Contradiction is rendered, not ranked.** When the per-turn card holds a
   unit whose subject matches a live state record, render both side by side
   with dates and provenance ("memory 09-17: topped up / state 09-18:
   exhausted, resets 09-24 / last probe: never") instead of letting BM25 pick
   one. Silence is the failure; a visible conflict costs one line.

## 5. Reconciliation: MC-964 vs MC-944 / MC-892 / MC-953

They are one problem at four points of the same pipeline:

| Item | Point in pipeline | Built (on master) | Open | Verdict |
|---|---|---|---|---|
| MC-944 V2 (9adaef68) | record shape, triggers, delivery | Steps 1–5: `7259ead`, `1705397`, `18794f3`, `c39e1ee`, per-turn delivery `ba1e955` (09-06), push observer `476bdb5` (report mode), publication kernel `d92d51b` | Steps 6–10: supersession, minting, negation, measure, D4 | **Absorbs MC-964's RC2 and Q3.** Step 6 (supersession) is the direct fix for "stale line outranks correction"; step 2's `holds_while` needs an external-state metric. |
| MC-892 index eviction (dae8d6e7) | always-loaded index size | Retrieval half (S0–S6, S12, 2026-08-16) | Eviction + per-line delivery gate. Index is 19,227 B now (cap 24,576) | **Not the cause here and not urgent.** Keep the plan-before-code ruling; sequence after RC1 because the per-line delivery gate needs the archive visible to be honest. |
| MC-953 continuity (3db65948) | chat → successor chat | auto-fresh fix `7476f83`, context economy `93845ff`, mid-turn rollover `b5fa971`/`639adc0`/`465ae55`, now ON | Tool-result evidence does not cross a roll (Q2); no Scribe flush before roll | **Owns Q2.** |
| MC-964 (b2d85e51) | the incident | — | RC1, RC4 are new and unowned by any other item | Keep as the umbrella that sequences the fixes below. |

Dead: nothing in the V2 spec is contradicted by this incident. One shipped
change is now a known regression: `bbcfe3d`'s dedupe (RC1).

## 6. Build plan — ranked, sequenced, each with its acceptance test

Every step ships behind a flag or in report mode first, per the V2 spec's
"no gate refuses anything before it has been observed".

**Step A — Stop the archive dedupe from hiding distinct facts (RC1). Smallest change, largest measured effect.**
Change the dedupe from "last line per (day, task)" to "drop an earlier line
only if its content is substantially contained in the later one" (content
overlap on the part after `—`, threshold measured, not guessed).
- Acceptance: `codex_miss_repro.py` oracle arm reaches rank ≤ 12 with dedupe
  ON; archive-wide, hidden-but-novel lines drop from 2,079 to < 200; the
  2026-08-23 `/goal` first-guess case still collapses (add it as a fixture in
  `tests/`); `delivery_backfill.py` over the 188-task set shows no task losing
  its prior top hit.

**Step B — Line-identified telemetry (RC4).**
Push log and per-turn delivery record archive hits with the line's date +
first 80 chars (or line number at corpus build).
- Acceptance: re-running the 09-18/19 window from logs alone answers "did
  line X appear in the top-12 of any turn" without a replay.

**Step C — Carry evidence across a roll (Q2, MC-953).**
Before a mid-turn roll: (1) force a Scribe checkpoint of the outgoing
session; (2) append to the state block the session's own journal path and
any file the agent wrote under `docs/_journal/` this session; (3) carry the
last N tool_results' first lines, capped (e.g. 4 KB), not just call inputs.
- Acceptance: replay this session's roll — the successor state block
  contains the "87", "62%", "None of 42 / 2 of 42" measurements without
  re-running them; the rollover log row records `scribe_flushed: true`.

**Step D — State-contradiction surfacing (Q3).**
(1) Register external state metrics for `holds_while` — first one
`allowance(<vendor>).exhausted`, read from `allowance_state`; (2) when a
dispatch gate refuses on a state record, the refusal text carries the age of
the record and the date of the last probe, and any newer memory unit on the
same vendor triggers one re-probe; (3) per-turn card renders a two-line
conflict block when a delivered unit's subject matches a live state record.
- Acceptance: a fixture with state "codex exhausted, resets +5d" plus a
  newer memory unit "Ron topped up" produces one probe, not a refusal; the
  per-turn card for Ron's 00:20Z message shows the conflict block; zero
  change on turns with no matching state record (report-mode run over one
  day of real turns, false-positive count logged).

**Step E — Capture shape: habits become notes, not archive events (RC2).**
The Scribe's classifier flags user statements of standing operator
behaviour ("I topped up", "I always…", "I buy more when…") for a topic note
or position with triggers, rather than only an archive echo. This is V2 §7
(default triggers) applied to user statements; no new store.
- Acceptance: replaying the 09-17 chat through the Scribe yields a topic
  note that ranks ≤ 6 for Ron's 09-20 wording.

**Step F — Resume MC-944 at step 6 (supersession), then 7–10 as specced.**
Supersession is the general fix for "stale line outranks the correction".
- Acceptance: as in `docs/MEMORY_DESIGN_V2_SPEC.md` §16, plus the §2
  acceptance test and this incident's replay.

**Step G — MC-892 eviction, per its own plan.** After A (the per-line
delivery gate must measure against an archive that is actually visible).

Order rationale: A is deterministic and alone explains the oracle miss;
B makes every later step measurable; C and D close the two failure modes
this incident exposed that V2 does not cover; E and F are the V2 work
already specced; G is not implicated.

## 7. Decisions for Ron

1. **Dedupe replacement semantics (Step A).** Recommend: content-containment
   dedupe (drop an earlier line only when the later one carries its content).
   Alternative: turn dedupe off entirely — restores 2,702 lines to the corpus
   and brings back the `/goal` stale-first-guess failure it was built for.
2. **Should a user's chat assertion be allowed to trigger a re-probe of a
   gate (Step D.2)?** Recommend yes: a probe is one cheap vendor call and the
   alternative is the 33-hour Codex blackout measured here (09-18 15:06Z to 09-20 00:28Z). It does not expand what
   any agent may do; it only re-reads state.
3. **Rollover carry budget (Step C).** Recommend ≤ 4 KB of tool-result heads
   plus a forced Scribe flush per roll. Larger carries raise the fresh
   session's floor on every roll.

## 8. Not verified

- Ron's own 09-17 wording of the top-up: not found in Claude or Codex user
  messages 09-17..20 by regex; only the assistant echo survives. Possibly
  inside the Codex rollout that line 4168 says was being repaired.
- Whether a second top-up happened before 00:20Z on 09-20: unknowable; no
  Codex run was attempted in the window, which is RC3 itself.
- Whether the Scribe fires on a mid-turn roll: no call path found; not
  exercised.
- The as-of-cutoff snapshot keeps later CONTENT of older topic files (bias
  toward reachability, never against it), so the "None of 42" ranks are an
  upper bound on how reachable the fact was.
