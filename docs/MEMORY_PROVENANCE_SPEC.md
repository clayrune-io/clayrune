# Memory provenance: what a volatile claim was checked against

> **Status: DRAFT v1, 2026-10-29. Docs only, nothing built.** Backlog item
> `fa57aa82`. Base `master` 1c9d2fb4. Open questions are in §16, each with a
> recommendation; Dave decides them before U1 starts.
>
> Companion to `MEMORY_DESIGN_V2_SPEC.md` (the record schema this extends,
> §4.1-4.5) and `MEMORY_SYSTEM.md` (the map of what writes where). Nothing
> here replaces either. This is a pure superset: every new field is optional,
> every existing unit stays a valid unit on day one.

## 0. One paragraph

The Scribe records conclusions. It has no way to tell a conclusion the agent
checked against the live system from one the agent read off a stale doc, so
both enter memory with the same confidence and are recited back to every
later session. This spec makes the difference visible. A memory claim about
**volatile operational state** (a flag's value, whether something is installed,
running, merged, pushed or signed in) carries a record of **what it was checked
against**: the tool action, its result hash, when, and what kind of source it
was. The server derives that record from the session transcript; the model
never asserts it. A volatile claim with no such record is labelled
`unverified` wherever memory is shown to an agent, and every record ages.
Durable claims (decisions, causes, how code works) are left alone.

## 1. The problem, measured

### 1.1 The incident that named it (2026-05-18)

A session read an old CHANGELOG title ("Step 6 ... default-OFF", true as
history) and an index line ("live-on, validated", true as current state), hit
the contradiction, guessed instead of calling `GET /api/config`, and asserted
"reality is default-OFF". The Scribe wrote that into `_(live)_` entries and a
watermark summary. Every later session loaded it at startup and repeated it.
The fix was a hand clean-up plus a behavioural rule (memory
`feedback-verify-volatile-state`). `MEMORY_SYSTEM.md` states the gap
directly: "Mitigation is behavioral, not structural."

### 1.2 It is still happening (measured 2026-10-29, reference install)

The curated `MEMORY.md` index, loaded into every prompt on this project,
carries three lines dated 2026-10-07 that say a commit is "local only" with
"no push/merge": `cb955b32`, `c3766e06`, `c33481a5`. All three are ancestors
of `origin/master` today (`git merge-base --is-ancestor <sha> origin/master`,
rc=0 for each). Each line was true when written and is false now. Nothing
marks it as either.

### 1.3 How much of the vault is volatile

A throwaway census over the reference vault (narrow lexicon: config-key
names from `GET /api/config`, present-tense state predicates, SHA plus
merge/push words; unit-level, so a topic file counts once if any sentence
matches):

| corpus | units | flagged volatile | share |
| --- | ---: | ---: | ---: |
| `MEMORY_ARCHIVE.md` entries | 6,297 | 1,600 | 25% |
| `SESSION_LOG.md` entries | 7 | 5 | 71% |
| `MEMORY.md` index bullets | 116 | 6 | 5% |
| topic notes (non-mint, non-position) | 114 | 77 | 68% |
| `mint_*` notes | 379 | 86 | 23% |
| `position_*` notes | 99 | 18 | 18% |

Git/branch state dominates the archive hits (1,301), then present-tense
state (456), then named config keys (96). A first pass with a looser lexicon
flagged 67% of the archive because a case-insensitive `ON\b` matched the word
"on"; that is the measured reason U0 (§15) is a gate and not a formality.

### 1.4 What already exists, and what does not

Provenance of the **writer** is solved: `origin` and `generated` are
server-stamped (`_stamp_origin`, `_stamp_generated`), `verified[]` is derived
from a human follow-up (`_derive_verified`), and the unattended read filter
exists (`_memory_search(consumer_unattended=True)`). `holds_while` (V2 §4.5)
lets a note declare a machine-checkable expiry, and
`mc/memory_state_trigger.py` re-probes vendor-allowance records when newer
memory names the vendor. V2 §4.2 gives positions a human-authored
`evidence:` list.

Provenance of the **claim** does not exist. Nothing records what a statement
was checked against, nothing separates "the agent ran `git rev-list`" from
"the agent read a doc that said so", and nothing in any read path tells the
consuming agent which kind of statement it is looking at.

## 2. Goals and non-goals

**Goals**

- G1. Every volatile claim the Scribe, the checkpointer or a mint writes from
  now on carries a server-derived check record, or is marked `unverified`.
- G2. Every surface that shows memory to an agent (dispatch read floor,
  per-turn refresh, steward charter floor, `/memory/search`) shows that
  status, with the check's age.
- G3. Existing unstamped memory is labelled the same way at read time,
  without rewriting it.
- G4. Zero extra model calls per turn in v1.

**Non-goals**

- Not a truth oracle. A check record proves the session *observed* a result
  that mentions the claim's anchor. It does not prove the conclusion follows
  from it. It turns "a conclusion" into "a conclusion plus what it was checked
  against", which is what the reader needs to re-check it.
- Not a write gate. Nothing is refused (V2 P7, report-only until measured).
- Not a re-prober. v1 never re-runs a command to refresh a stale record
  (§16 Q4).
- Not engram. engram is the operator-session plugin layer and is out of
  scope (`MEMORY_SYSTEM.md` "Memory layers & audiences").
- Does not touch `origin`, `generated` or `verified[]`. A check is not a human
  witness (§11).

## 3. Principles

Inherited from V2 §3 and binding here: **P2** fail-open with a log where no
caller waits, **P3** deterministic rules first and a model may only add,
**P5** a provenance field the actor supplies is a field the actor can forge,
**P7** report-only until measured, **P8** relocate, never delete.

Four new ones, each from a specific failure:

- **PV1. Evidence is derived from the transcript, never asserted.** The model
  may point at an action; the server decides whether the action exists and
  what it shows. (P5 applied to claims.)
- **PV2. A document is not a check.** Reading a doc, a changelog, a memory
  file or a backlog note about state is reading someone's earlier conclusion.
  The 2026-05-18 incident *was* a doc read. Such evidence is recorded as
  `doc-sourced` and shown as unverified-equivalent.
- **PV3. Result text never re-enters a prompt.** Tool results include web
  pages, email digests and API bodies, which are untrusted input
  (`docs/UNTRUSTED_INPUT_SURFACE.md`). A record stores a hash and a
  short excerpt for humans; what agents see is the probe kind, the command
  summary and the date. Copying result text into memory would turn every
  fetched page into a standing instruction in every future prompt.
- **PV4. Label, never hide.** An unverified claim is still delivered, with a
  label. Hiding it is the V2 §4.3 measured trap (fail-closed would have made
  ~81% of that vault invisible to stewards).

## 4. Claim classification

### 4.1 Unit of classification: the claim, not the file

A topic note is one retrieval unit but many statements. Classification splits
a unit into sentences (Scribe lines are already one to three sentences) and
classifies each. A unit's status is the worst status among its volatile
claims, with a count (`2 of 3 volatile claims checked`).

### 4.2 Event versus state: the rule that keeps the numbers honest

"Committed `abc123` on branch X" is an **event**. It is durable history: the
commit exists forever. "`abc123` is local only" is **state**: true until
someone pushes. The census shows most git-word hits in the archive are
events. Only state is volatile.

Deterministic rule: a claim is state when it has a present-tense or
unqualified state predicate (`is`, `are`, `now`, `currently`, `still`,
`stays`, `left`, `set to`, `not yet`, a bare `ON`/`OFF`, `local only`,
`branch-only`, `unpushed`, `live`, `default-on`), and event when its verb is
past tense with an object (`committed`, `merged X into`, `pushed`,
`shipped in`, `fixed in`). A dated Scribe entry's state claims are true
*as of that date*; that date is the floor for decay (§9.3) even when no check
exists.

### 4.3 Volatile classes

| class | examples | anchor tokens | default TTL |
| --- | --- | --- | ---: |
| `config` | "scribe_checkpoint_enabled is on", "condense_mode=structured" | a key from the config registry, plus a value | 14 d |
| `vcs` | "local only", "not pushed", "master is 3 ahead", "branch merged" | SHA, branch name, `origin/<x>` | 3 d |
| `runtime` | "server is running old code", ":5199 is up", "process 4512 holds the lock" | port, PID, process/service name, route | 1 d |
| `install` | "memsearch is installed", "codex CLI is 0.158.0", "Playwright missing" | tool/plugin/package name, version string | 14 d |
| `account` | "X token expired", "Codex allowance exhausted", "signed in to reddit" | vendor/service name, profile name | 2 d |
| `deploy` | "v2.4.6 is the latest release", "site shows old pricing" | version tag, URL host | 7 d |

TTL values are proposals (§16 Q7). Everything else is **durable**: decisions,
positions' reasons, causes (`_why:_`), how code works, user preferences,
measured numbers with a date. Durable claims get no tag and no change.

### 4.4 How it classifies

- **Deterministic floor** (`mc/memory_claims.py`, pure functions):
  config-key registry match (the server's own defaults dict, injected, so the
  lexicon tracks the code), the state-predicate lexicon above, SHA/branch/port/
  version token patterns. Every rule is a line someone can read and fix (P3).
- **The model may add, never remove.** When Tier B (§6.4) runs, the Scribe
  may tag an extra sentence volatile. It cannot mark a deterministically
  volatile sentence durable.
- **Same function at write and read.** The read path (§9) runs the identical
  classifier over units that carry no record, which is how legacy memory gets
  labelled (§10) without a migration.

## 5. The evidence index

`mc/memory_evidence.py` turns one session's transcript into an ordered list
of **observations**. Built from the raw provider transcript (Claude `.jsonl`
first; other providers through the same runtime boundary the Scribe already
uses, `render_transcript_for_scribe`), never from the Scribe's rendered view:
that view caps results at `_SCRIBE_RESULT_CAP` (2,000 chars) and drops
`tool_use` ids, so a hash over it would not be a hash of what the agent saw.

Each observation:

```json
{"n": 41, "tool_use_id": "toolu_...", "at": "2026-10-29T14:02:11Z",
 "tool": "Bash", "input_summary": "git merge-base --is-ancestor cb955b32 origin/master",
 "kind": "live", "result_sha256": "3f9a1c2e...", "result_len": 0, "rc": 0}
```

`kind` is decided by the tool and its input, deterministically:

| kind | what produces it | counts as a check for |
| --- | --- | --- |
| `live` | Bash/PowerShell matching a read-probe allowlist (`git` read verbs, `gh api` GET, `curl` GET to the local server, `--version`, process/port listing); MCP read tools | all classes |
| `file-state` | Read of a runtime state or config file (`config.json`, `settings.json`, a lockfile, a `.mcp.json`) | `config`, `install` |
| `code` | Read/Grep of source | durable code claims only; never a volatile class |
| `doc` | Read/Grep of `*.md`, `docs/`, `CHANGELOG`, a backlog item, a journal | nothing (PV2): yields `doc-sourced` |
| `memory` | any read of the memory dir or the `/memory/search` route | nothing: memory citing memory is circular |
| `self-write` | Write/Edit/NotebookEdit, any mutating command | nothing: writing state is not observing it |
| `external` | browser read, WebFetch, mail digest | `account` and `deploy` only, rendered "per external page" |
| `server-event` | not from the transcript: a server-witnessed transition (backlog item set done, hivemind closed, merge recorded) | the event it witnessed |

A mutating command (`git push`, `git merge`) followed by its own output is
`self-write`, not `live`: "I pushed" is an event the agent caused, and its
claim class is an event (§4.2), which needs no check. A *state* claim after a
push ("master is now in sync") needs a read (`git rev-list --count`).

The allowlist is closed and lives in the module. Unknown commands default to
`self-write` (fail toward "not a check").

## 6. Matching claims to evidence

### 6.1 Tier A: anchor match, deterministic, v1

For each volatile claim, take its anchor tokens (§4.3). Scan observations
newest first, restricted to kinds that count for the claim's class. A match
needs the anchor in the observation's input or its raw result; a `config`
match additionally needs the claimed value within the same JSON member or
line as the key. The first match wins.

### 6.2 Status values

| status | meaning | shown as |
| --- | --- | --- |
| `checked` | a counting observation mentions the anchor (and value, for `config`) | `(checked <date> via <kind>: <command summary>)` |
| `contradicted` | a counting observation shows the anchor with a *different* value | `(CONTRADICTED by <date> <kind> check; do not rely on this)` |
| `doc-sourced` | the only mention is in a `doc`/`memory` observation | `(from a doc, not a live check)` |
| `unverified` | no observation mentions the anchor | `(unverified)` |
| `cited-unmatched` | Tier B cited an action that does not support it | rendered as `unverified` |

`contradicted` is written, not dropped (§16 Q8): a session that read
`true` and then wrote "flag is off" is exactly the 2026-05-18 shape, and
the next reader needs to see that the session had the right answer in hand.

### 6.3 Ordering inside a session

If a session checked `X=true` and later wrote "X is off", the match uses the
**latest** observation before the claim's own position in the transcript
(checkpoint deltas know their offset; terminal entries use the session end).
A check that happened after the claim was formed still counts, newest first,
because the terminal Scribe line is written at session end.

### 6.4 Tier B: model citations (deferred, §16 Q5)

The observations get ordinal ids (`E41`) in a provenance-only rendering. A
suffix on the terminal Scribe prompt (same pattern as `_SCRIBE_WHY_SUFFIX`)
asks the model to append `[E41]` after each state claim. The server then
applies §6.1 to the cited observation only: it must exist, be a counting kind,
and share an anchor. Otherwise `cited-unmatched`. This is the grounded-
citations idea (Hermes `skills/research/grounded-citations`, per
`docs/research/HERMES_AGENT_COMPETITIVE_READ.md` §5):
the model points, the server checks the quote exists. It adds coverage for
claims with weak anchors ("the pane renders black") at about 150 extra
output tokens once per session. Build only after U0 and U4 report how many
volatile claims Tier A leaves `unverified`.

## 7. The record

### 7.1 Inline tag (in the memory text itself)

The CLI auto-loads `MEMORY.md` raw, so a status that lives only in a sidecar
is invisible in the one place the 2026-05-18 poison lived. The tag is plain
words, so a model reads it without a legend:

```
- [2026-10-29] **MC-1121 merge** — 1c9d2fb4 is on origin/master (checked 10-29 via git p:3f9a1c2e); Desk smoke still red (unverified)
```

Grammar: `(checked MM-DD via <kind> p:<id8>)`, `(CONTRADICTED MM-DD p:<id8>)`,
`(from a doc p:<id8>)`, `(unverified)`. Placed after the sentence it governs.
~30 bytes checked, 13 bytes unverified.

### 7.2 Sidecar record

Append-only `provenance.jsonl` in the memory dir: outside `DATA_DIR` (the
load-bearing `load_projects()` rule), not `*.md` so `_mem_corpus` ignores it
(the `delivery_stats.json` precedent), covered by backup like the rest of the
vault.

```json
{"id": "3f9a1c2e", "v": 1, "at": "2026-10-29T14:05:00Z",
 "project": "mission_control", "session": "4d8c7a726af2", "writer": "scribe|checkpoint|mint|note",
 "claim": "1c9d2fb4 is on origin/master", "claim_sha256": "...", "class": "vcs",
 "anchors": ["1c9d2fb4", "origin/master"], "status": "checked",
 "evidence": {"kind": "live", "tool": "Bash", "n": 41, "tool_use_id": "toolu_...",
              "input_summary": "git merge-base --is-ancestor 1c9d2fb4 origin/master",
              "result_sha256": "...", "observed_at": "2026-10-29T14:02:11Z",
              "excerpt": "<=200 chars, secrets_store.redact()ed, never rendered to agents>"},
 "repo_head": "1c9d2fb4", "ttl_days": 3}
```

`id` = first 8 hex of sha256(session, claim_sha256, evidence.result_sha256),
so a replay of the same write produces the same id (idempotent, matching the
publication contract's "same operation, same receipt" rule).
`input_summary` and `excerpt` pass `mc.secrets_store.redact()` and the
authority guard before they are stored; `input_summary` is capped at 80 chars
and is the only evidence text ever rendered to an agent.

### 7.3 Topic notes and positions

Topic notes get an optional frontmatter list, written by `write_topic_note`
callers only, never by the model:

```yaml
checked:
  - {claim: "<=120 chars", status: checked, p: 3f9a1c2e, at: 2026-10-29}
```

Positions keep V2's `evidence:` list as the human-authored citation and gain
nothing new; their `holds_while` already is the machine expiry.

### 7.4 Forgery

An agent can type `(checked 10-29 via git p:00000000)` into its reply and the
Scribe can copy it. Three rules close that:

1. **The writer strips tag-shaped text** from model output before adding its
   own tags (`_commit_managed_entry` call path, and the note route).
2. **A tag whose `p:` id does not resolve** in the sidecar, or resolves to a
   record whose `claim_sha256` does not match the sentence it sits after, is
   rendered as `(unverified; tag did not resolve)`.
3. **Condense may move tags, never mint them.** Structured condense
   (`_validate_condense_payload`) rejects a plan whose output carries a tag
   id absent from its input lines, and keeps the tag with its sentence when it
   folds a line. Agent-mode condense cannot be constrained the same way; the
   post-condense integrity check strips unresolvable tags (§16 Q6). Live value
   on the reference install today: `condense_mode=structured`.

## 8. Where it is captured

| writer | hook point | what it does |
| --- | --- | --- |
| Session-end Scribe | `_write_session_memory`, after `_scribe_extract` returns, before `_commit_managed_entry` | build the evidence index from the full transcript, classify the line, match, tag, append records. Outside the leaf lock, like the model call |
| Step 6 checkpointer | `_checkpoint_worker`, on the delta | index only the delta's observations; carry earlier records forward by id. `_SCRIBE_CHECKPOINT_REDUCE` merges free text, so tags are re-applied to the reduced line after the model call, from the union of carried and new records, never left to the model |
| Deterministic mints | `mint_topic_node` | evidence is `server-event` (the trigger itself: backlog item → done at T, hivemind closed at T). A mint's subject is caller text, so a state claim inside it ("deployed to prod") still classifies and goes `unverified` |
| Habit classifier | `_scribe_classify_habits` | none: habits are observations about the user, durable by class |
| Attended note route | `POST/PATCH /memory/note` via `mc/memory_note_gate.py` | classify, and when a session id is in hand, match against that session's transcript |
| Direct agent `Write` into the memory dir | not reachable server-side today | labelled at read time (§10). A G2 PreToolUse stamp is possible later (§16 Q9) |
| Human UI edits (`save_memory`, `append_memory`) | none | human-typed; labelled at read time like legacy |

Each hook is a call into a new module; `mc/memory.py` (a listed monolith,
`docs/ENTANGLEMENT_AUDIT.md` row 9) grows by the call lines only. Every hook
is fail-open: any exception is `_log`'d with the project id and the write
proceeds untagged exactly as today (P2; the "never breaks completion" Scribe
contract).

## 9. How it is shown, and how it decays

### 9.1 One renderer for every agent-facing surface

`mc/memory_provenance_render.py` exposes `annotate(hit, now) -> str`. Today
the `• [file] snippet` line is formatted separately at three sites:
`agent_routes.py:5401` (dispatch floor), `scheduler_routes.py:525` (steward
charter floor) and `memory_turn.py:467` (per-turn refresh, which already
appends a `[cold tier ...]` tag the same way). All three call the renderer;
the `/memory/search` JSON gains a `provenance` field per hit with the same
data.

```
--- RELEVANT MEMORY (auto-surfaced for this task; ...) ---
  Tags: "checked" names the live check and its date; "unverified" means no check was recorded; re-check volatile state before you assert it.
  • [MEMORY_ARCHIVE.md] [2026-10-07] ... cb955b32 local only (STALE: checked 22 d ago, vcs TTL 3 d)
  • [feedback_x.md] scribe_checkpoint_enabled is on (unverified)
```

The legend line (~150 bytes) appears only when at least one hit is tagged.

### 9.2 What the renderer decides

1. Record present and resolves: show its status and age.
2. No record: run the §4 classifier on the snippet. Volatile → `(unverified)`,
   or `(as of <entry date>, unverified)` for a dated log entry. Durable → no tag.
3. Two hits in the same floor share an anchor: the newer `checked` one is
   listed first and the older is suffixed `(superseded by a newer check)`.
   A `checked` hit never outranks a hit carrying human `verified[]` (V2 C2).

### 9.3 Decay

Age = now − `evidence.observed_at` (or the entry date when unchecked). Past
the class TTL the tag becomes `(STALE: checked N d ago)`. Nothing is deleted,
demoted or rewritten by decay (P8); it is a render-time computation, so it
costs nothing at rest and cannot drift. `unverified` never improves with
age. A newer `checked` record on the same anchor is the only thing that
replaces an old one, and only in rendering.

### 9.4 The always-loaded index

`MEMORY.md`'s curated region is byte-preserved; only condense and humans
rewrite it. So inline tags reach it only by condense carrying them in from
the managed region (§7.4 rule 3). Legacy curated lines get no tag (§10).
Budget: the index is at its 24 KB budget on the reference install, so every
tag byte there evicts a byte of something else. Six volatile index lines ×
~30 bytes ≈ 180 bytes, under 1% of the budget.

## 10. Existing unstamped memory

Three options:

| option | effect on the 1,600 volatile archive entries and 6 index lines | risk |
| --- | --- | --- |
| A. Grandfather: no label | none | the two live examples (§1.1, §1.2) stay unlabelled forever |
| B. Fail closed: withhold unstamped volatile claims | 25% of the archive and most topic notes vanish from floors | V2 §4.3's measured trap: correctness that delivers worse memory |
| **C. Label at read time (recommended)** | delivered as today, tagged `(as of <date>, unverified)` by the §9.2 step 2 classifier | a false-positive classification adds a wrong label; bounded by U0's measured precision |

Recommend **C**, plus a one-off **report-only** listing of volatile lines in
the curated `MEMORY.md` index (`tools/memory-eval/provenance_census.py
--index`), because the renderer cannot reach the raw auto-load. A human
fixes those six lines; nothing rewrites curated text automatically. No
backfill of sidecar records for old sessions: transcripts older than the
agent log's 500-entry window often no longer join (V2 measured 19%), so a
backfill would be partial and look complete.

## 11. Safety rails: what this must not weaken

| rail (CLAUDE.md, load-bearing) | how this spec relates |
| --- | --- |
| Authority guard | untouched and still first. Provenance never makes text more authoritative as an *instruction*. `input_summary` and `excerpt` go through `_authority_violation` before storage; a hit is dropped from the record, not from the memory line |
| A human on one side of every loop | provenance is orthogonal to `origin`. A `checked` tag on an unattended-origin unit does not make it eligible for steward consumption, and `consumer_unattended` filtering is unchanged. `checked` is never written into `verified[]` and never counts as a human witness |
| Durable "no" | untouched. A rejected/suppressed artifact gains nothing from a check |
| Vault secrets stay out of the repo | records live in the vault dir under `~/.claude/projects/...`, never under the repo; excerpts are redacted |
| DATA_DIR pollution | `provenance.jsonl` lives in the memory dir, never in `data/projects/` |

## 12. Cost

| where | v1 (Tier A) | with Tier B |
| --- | --- | --- |
| model calls per turn | 0 | 0 |
| per session end | 1 transcript parse (the Scribe already reads it), regex over one ≤300-char line, sidecar append | +~150 input / ~150 output tokens on the existing Scribe call |
| per checkpoint | parse of the delta only (already read for the reduce) | none (Tier B is terminal-only) |
| per dispatch / per-turn floor | ≤8 hits × ≤40 bytes + 150-byte legend ≈ 470 bytes (~120 tokens) worst case, 0 when no hit is volatile | same |
| always-loaded index | ~180 bytes on the reference install | same |
| disk | ~600 bytes/record; at ~25% volatile of ~30 entries/day ≈ 4.5 KB/day | same |

The read-side figure is a ceiling: `memory_turn` already enforces a byte
budget for its block, and tags count inside that budget rather than on top.

## 13. Config keys

All registered in both the defaults dict and `_CONFIG_EDITABLE_KEYS` in the
change that introduces them (V2 Condition 9: four keys were silently
unconfigurable because they were in neither).

| key | default | governs |
| --- | --- | --- |
| `memory_provenance_mode` | `report` | `off` / `report` (compute + log + sidecar, no text change) / `tag` (inline tags written) |
| `memory_provenance_render` | `false` | read-time labels on the floors; separate so read can ship before write |
| `memory_provenance_ttl_days` | the §4.3 table | per-class TTL map |
| `memory_provenance_cite` | `false` | Tier B prompt suffix |

## 14. Tests

Each unit's tests live next to it (`tests/test_memory_<unit>.py`):

- **Classifier:** event vs state pairs ("committed abc on X" durable, "abc is
  local only" volatile); every config key from the live defaults dict
  matches; "on" in prose does not trigger (the census bug, pinned); model
  additions cannot demote.
- **Evidence index:** `kind` per tool and input across the allowlist; unknown
  command → `self-write`; a doc Read → `doc`; memory-dir Read → `memory`;
  result hash computed on raw result, not the 2,000-char render; Codex
  transcript via the runtime adapter.
- **Matcher:** the 2026-05-18 replay (session reads `"scribe_checkpoint_enabled": true`,
  writes "is off" → `contradicted`); doc-only mention → `doc-sourced`;
  `git push` output alone does not check "in sync"; newest-before-claim
  ordering.
- **Forgery:** a typed tag is stripped; an unresolvable id renders
  `unverified`; structured condense refuses a plan that invents an id.
- **Rails:** an excerpt containing a vault-dispensed value is redacted; an
  authority-phrase command summary is dropped from the record; a `checked`
  unattended-origin unit is still withheld when `consumer_unattended=True`;
  `verified[]` unchanged after any provenance write; no file appears under
  `data/projects/`.
- **Fail-open:** a raise inside every hook leaves the memory entry
  byte-identical to `memory_provenance_mode=off`.
- **Renderer:** all three sites produce identical tags for the same hit; decay
  boundary per class; supersession ordering; legend only when tagged;
  `memory_turn` byte budget respected with tags.
- **Smokes:** none needed until a UI surface shows provenance; the floors are
  server-side text.

## 15. Build order

Each unit is its own module (AGENT_RULES "one unit, one file") and lands
behind its key. Stop after any unit if its measurement says the next one is
not worth it.

| unit | module | depends on | lands as |
| --- | --- | --- | --- |
| **U0** census + precision gate | `tools/memory-eval/provenance_census.py` | none | report: per-corpus volatile counts, 50-claim hand-graded sample, the curated-index listing. **Gate: classifier precision ≥ 0.9 on the sample before U5** |
| **U1** classifier | `mc/memory_claims.py` | U0's lexicon | pure functions + tests, no caller |
| **U2** evidence index | `mc/memory_evidence.py` | none | pure over transcript lines + tests, no caller |
| **U3** matcher + record store | `mc/memory_provenance.py` | U1, U2 | `match()`, tag grammar, strip, sidecar append/lookup |
| **U4** Scribe hook, report mode | 3-line call in `_write_session_memory` | U3 | records + stats (`provenance_checked`, `_unverified`, `_contradicted`, `_doc_sourced`) in `/scribe-stats`, **no text change**. Soak a week |
| **U5** read renderer | `mc/memory_provenance_render.py` + three call sites | U1, U3 | `memory_provenance_render=true` labels legacy and new units. Read ships before write tags |
| **U6** condense preservation | `_validate_condense_payload` rule + integrity-check strip | U3 | must land before U7 |
| **U7** write tags | flip `memory_provenance_mode=tag` | U4 soak, U6 | tags in new managed entries |
| **U8** checkpointer | call in `_checkpoint_worker` | U7 | carry-forward by id |
| **U9** mints + note route | calls in `mint_topic_node`, `memory_note_gate` | U3 | `server-event` evidence, `checked:` frontmatter |
| **U10** Tier B citations | prompt suffix + provenance rendering | U4's unverified rate | only if Tier A leaves a large `unverified` share |

## 16. Open questions (Dave decides)

| # | question | options | recommendation |
| --- | --- | --- | --- |
| Q1 | Existing unstamped memory | A grandfather / B fail closed / C label at read | **C** (§10). B repeats V2's measured 81% trap; A leaves both live examples dark |
| Q2 | An unverified volatile claim at write time | write with label / strip the sentence / refuse the entry | **write with label**. Stripping is editing a summary the model wrote; refusing breaks "never breaks completion" |
| Q3 | Does a doc read count as evidence? | yes / no | **no** (PV2). It is the incident |
| Q4 | Re-probe stale records automatically? | none / server-run allowlisted read-only probes (git, local GET, file hash) | **none in v1**. A server that re-runs agent-chosen commands is a new capability; revisit with its own review if STALE tags prove noisy |
| Q5 | Tier B model citations | build with v1 / defer behind U4 | **defer**. Measure what Tier A leaves first |
| Q6 | Agent-mode condense can invent tags | require `structured` for `tag` mode / accept with integrity-check strip | **integrity-check strip** (unresolvable → `unverified`). It fails safe without forcing a mode switch on installs still on `agent` |
| Q7 | TTLs | §4.3 table / one flat TTL | **the table**; branch state goes stale in days, config in weeks. Tune from U4 data |
| Q8 | A contradicted claim | write labelled / drop the sentence | **write labelled**. The next reader needs to see the session had the answer in hand |
| Q9 | Direct agent `Write` into the memory dir | read-time label only / G2 PreToolUse stamp | **read-time label only** for now; G2 is V2's open item and covers Write/Edit only |

## 17. What this does not claim

- A `checked` tag means "the session saw a result mentioning this anchor",
  not "this is true". A misread of a correct result still gets `checked`.
  The tag names the command so the reader can re-run it.
- Anchor matching misses claims with no anchor ("the pane is black"). Those
  go `unverified`, which is the honest label, until Tier B.
- Read-time labels cannot reach the raw `MEMORY.md` auto-load for legacy
  lines; §10's report and a human fix are the only path there.
- Census numbers are from one install's vault on 2026-10-29 with a throwaway
  lexicon; U0 replaces them with a graded sample before anything ships.

## Appendix: adjacent finding (out of scope, reported separately)

`_memory_search(consumer_unattended=...)` exists, but none of the three
RELEVANT MEMORY delivery calls pass it: `agent_routes.py:5369` (dispatch),
`scheduler_routes.py:518` (steward charter floor) and `memory_turn.py:408`
(per-turn). `memory_turn.py:282` (`seed_delivered`) mirrors the dispatch call
and must change with it. Only the negation ledger and the exploration floor
pass the filter. So unattended-origin topic notes, 419 of 433 stamped notes on the
reference vault, reach steward cycles through RELEVANT MEMORY. This is V2
§4.3's C1 hole, still open on 1c9d2fb4. Provenance does not depend on it and
does not fix it.
