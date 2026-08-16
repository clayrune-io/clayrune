# Fable-3 — independent buildability review: can the approved set be built HERE, safely?

**Reviewer:** independent seat, different model from the authors · **2026-08-16**
**Target:** `docs/MEMORY_REDESIGN_2026-08.md` §14 (S0–S12), seats 2/6/7.
**Scope of this review:** the approved set S0–S6 + S12 only. Numbers and design
judgement are other reviewers' lanes; this file is only about whether the plan
survives contact with this codebase and this machine. Analysis only — no
production code, config, `MEMORY.md`, or memory-dir file was touched, no restart
was triggered, no schedule created.

Every claim below names the code or read-only command it was checked against.

---

## 1. ROLLBACK — stage by stage, against what each stage actually mutates

Seat 7's F4 ("the snapshot rollback is not a rollback") is **correct as stated
for `MEMORY.md` restores** — and it turns out to matter much less for the
approved set than its "blocking for S5" label implies, because of a fact
neither seat states outright:

> **No machinery writes topic files.** Every write site in `mc/memory.py`
> targets `MEMORY.md` or `MEMORY_ARCHIVE.md` only — `_append_to_archive`
> (`memory.py:1009`), the floor/commit path (`:1091`, `:1137`), condense apply
> (`:2189`), and the condense subprocess fallback (`:2419–2441`). Topic files
> have exactly one writer class: an agent or human editing a note in a session.

F4's severe failure (restore clobbers a live watermark → supersede fails
silently → the 2026-08-05 `_(live)_` pile-up) lives **inside `MEMORY.md`**
(`memory.py:1052–1060` reads `last_entry_hash` off the wm record). A restore
that never touches `MEMORY.md` cannot trigger it.

| stage | what it mutates | stated undo | verdict |
|---|---|---|---|
| **S0** | `tools/memory-eval/` — tracked files (`git ls-files` confirms `scorer_ab.py`, `retrieval_probe.py` are in git) + new `_harness.py` | `git revert` | **SOUND.** Fully tracked, nothing live changes. |
| **S0b** | creates `~/.clayrune/memory-snapshots/` only (dir family exists and already holds durable operator state incl. `cloudflared_pids.json`) | n/a | **SOUND as a create.** But the *restore* tool it ships is the approved set's one weak edge — see the S5 condition below. |
| **S1** | `data/memory-eval/suite-v1.jsonl` + a `.gitignore` line | `rm -r data/memory-eval/` | **SOUND.** The `rm` removes the suite; the leftover `.gitignore` line is harmless. `data/memory-eval` is a **sibling** of `DATA_DIR` (`server.py:444`: `DATA_DIR = _DATA_ROOT / 'data' / 'projects'`), not inside it. |
| **S2** | new standalone `eval.py`, read-only | `git revert` | **SOUND.** |
| **S3** | one key in `state.CONFIG` + `config.json` | `PUT {"read_floor_topk": 3}` | **SOUND — a true rollback.** `update_config` (`settings_routes.py:186–196`) mutates `state.CONFIG` in place and rewrites `config.json`; the reverse PUT restores both the live value and the persisted file. The value is read live per context build (`agent_routes.py` ~1917), so behaviour reverts at the next build with no residue. |
| **S4** | new code in `mc/memory.py` + `settings_routes.py` + 3 config keys | "flag → default" | **SOUND with a caveat.** The flag flip restores *behaviour* exactly (defaults = today's constants), not the code — acceptable because the code at default is a measured no-op (the plan's own S4 success gate). Full code rollback = `git revert` + a second restart. Note the flip only works **after** R1: a `PUT` of a key not yet in the running process's `_CONFIG_EDITABLE_KEYS` returns **200 with `updated=[]`** (verified in `update_config` — the plan's silent-no-op trap is real). |
| **S5** | 21 **topic files** (never `MEMORY.md`) | snapshot restore `--only <21 paths>` | **SOUND ONLY IF SCOPED — see condition F-A below.** Because topic files have no machine writer, a scoped restore races nothing mechanical; F4's watermark clobber cannot occur. The plan's "fresh `--label pre-S5` snapshot first" also fixes staleness (edits between S0b and S5 are not silently reverted). |
| **S6** | `mc/blueprints/agent_routes.py` call sites | `git revert` + restart | **SOUND.** Fully tracked code; the restart is the cost, and it is declared. |
| **S12** | `docs/MEMORY_SYSTEM.md` | `git revert` | **SOUND** (but its gate is not buildable as written — F-C below). |

**F-A (condition on S5, and the fix to S0b): the restore tool must be scoped by
construction.** `tools/memory-snapshot.py --restore` should (1) **require**
`--only`, and (2) **refuse** `MEMORY.md` and `MEMORY_ARCHIVE.md` as restore
targets unless an explicit `--unsafe-full-restore` flag is passed with the
server stopped. That single guard makes the approved set's only snapshot-based
rollback (S5) genuinely safe, and it is the same guard S9 will need anyway
(seat 7's correction #8). Cost: ~20 lines inside a stage already being built.
Without it, the tool ships with a full-restore mode whose known failure is the
2026-08-05 supersede pile-up, one typo away.

**F-B: S0b's stated verification ("restore into a temp dir, 76/76 sha256") is a
backup-integrity check, not a restore drill** — seat 7's F4.4 is right. For the
approved set this is acceptable *because* S5 only restores topic files; the
live-restore drill becomes mandatory before S9, not before S5.

---

## 2. RESTARTS — verified against how config is actually loaded

`CONFIG = _load_config()` runs **once at module import** (`server.py:295`).
After boot, the only mutation path is `PUT /api/config` → `update_config`,
which writes `state.CONFIG` in place. So a key is "live" iff its *reader* does
`state.CONFIG.get(...)` per call, and "boot-captured" iff read at import.

| claim | verdict | evidence |
|---|---|---|
| **S3 needs no restart** | **CORRECT** | `agent_routes.py` (~1917–1920, in `_build_agent_context`'s read floor): `int(state.CONFIG.get('read_floor_topk', 6) or 6)` and `read_floor_link_expand` read **live, per context build**. `read_floor_topk` ∈ `_CONFIG_EDITABLE_KEYS` (`settings_routes.py:102` block) and ∉ `_RESPAWN_TRIGGER_KEYS`. (That set holds **9** keys — seat 6 is right, seat 7's "8" is a miscount.) Nuance worth stating: "live" means *next fresh context build* — an in-flight Mode B session keeps its already-built context until its next dispatch/respawn. That is fine; it is not a silent no-op. |
| **S4 needs a restart** | **CORRECT, but the plan gives a partially wrong reason** | The plan says the restart is "to load the three new keys into `_load_config()`'s defaults." If the new code reads `state.CONFIG.get('bm25_b', 0.75)`, missing defaults would not matter. The *actual* forcing reason is simpler and harder: **the ranking code and the `_CONFIG_EDITABLE_KEYS` additions are new module code, and Python loads modules once at process start.** Either way the conclusion (restart R1, batched with S6) stands. |
| **S6 needs a restart** | **CORRECT** | `agent_routes.py` is imported at boot; edits to it are invisible until the process restarts. |
| S0/S0b/S1/S2/S12 need none | **CORRECT** | standalone tools and docs; nothing imported by the server changes. |

**The one real silent-no-op window in the approved set:** between landing S4's
code and R1, a `PUT` of `bm25_b` (or the other two keys) returns 200 and does
nothing, because the *running* process still has the old `_CONFIG_EDITABLE_KEYS`.
The plan knows this (the "TRAP, verified" note and
`tests/test_config_keys_editable.py`), and its sequencing (land → restart →
flip) avoids it. No wrong "no restart" claim exists in the approved set.

Per the review rules, no restart was performed; all of the above is from code,
not experiment.

---

## 3. BLAST RADIUS — cross-project and fresh-install exposure of the APPROVED set

`state.CONFIG` is one global dict; there is no per-project override for any
memory key. So:

- **S3 changes the read floor for all 19 projects and every agent at once.**
  Mitigating fact the plan should state louder: **6 is already the shipped code
  default** (`state.CONFIG.get('read_floor_topk', 6)`), running today on every
  install that never persisted the key. This box at 3 is the anomaly
  (`config.json` shadows the default, seat 2 F1). S3 is a *convergence to
  intended shipped behaviour*, not an experiment — the cross-project risk is
  the token spend (~344/prompt, on every project), which is exactly the human
  gate Ron is being asked to approve.
- **S4 is the real cross-project gap in the approved set.** `bm25_b`,
  `bm25_title_boost`, `read_floor_archive_quota` candidates were measured on
  mission_control's corpus only, and seat 7's F6 table shows other projects'
  curated regions have radically different shapes (18–172 pointer lines,
  different densities, different archive sizes). A constant can pass its
  paired gate on mission_control and degrade DayTrading the same minute it is
  flipped. **Condition F-D: before flipping any S4 constant, run the same
  paired A/B on 2–3 other projects' corpora with the S0 harness** — minutes of
  work once the harness exists, and the revert stays one `PUT` either way. Not
  blocking the build; blocking the *flip*.
- **S6 touches respawn/recovery context building for every project.** That is
  its purpose; the declared abort ("any currently-passing test in `tests/`
  breaks") is the right gate for a subsystem with a race history.
- **Fresh install: the approved set is clean.** All new keys default to
  today's behaviour, so a fresh `config.json` is generated correct
  (`_load_config`, `server.py:280–295`). `_memory_search` on an empty corpus is
  handled (`_mem_class_avgdl` → `{}`; seat 7 verified). The F7 `max()` crash
  lives in the **held** S8 tiering prototype, and nothing in S0–S6/S12 imports
  it. S2's `eval.py` runs only on this box, never on an install.
- **S1's verbatim prompts are mission_control-family only** — `scorer_ab.py`
  filters transcript dirs by `"mission-control" in x` under
  `~/.claude/projects/`. That bounds the exposure (no trading-project prompts
  in the suite) but they are still operator text; the S1 gate stands.

---

## 4. LOAD-BEARING RULES — checked, one at a time

| rule | verdict |
|---|---|
| **DATA_DIR pollution** | **CLEAN.** Nothing in the approved set writes under `data/projects/` (`DATA_DIR`, `server.py:444`). `data/memory-eval/` is a sibling; snapshots are outside the repo. The plan's own note that any new memory-dir state file must be non-`.md` is verified correct — `_mem_corpus` globs `mem_dir.glob('*.md')` (`memory.py:560`), so a stray `.md` there becomes a retrieval unit. |
| **Nothing operator-specific in the repo** | **HANDLED, with one ordering requirement.** `data/memory-eval/` is **not** gitignored today (verified: no match in `.gitignore`) and S1 writes 177 verbatim operator prompts there. `build-macos.spec` bundles only `data/agent_reference` from `data/` (spec line 46; verified — the `SHARED_RULES.md` bundling class does not recur here), and it is the **only** PyInstaller spec in the repo; `installer/` holds prebuilt binaries and scripts that do not bundle repo data at build time. **Condition F-E: the `.gitignore` line must land in the same commit that creates the extractor, before any suite file is written** — an ignore added after the first write is one `git add -A` (which the commit rules ban, but bans have failed here before) from publishing operator prompts. |
| **Learning-system safety rails** | **CLEAN for the approved set.** No stage touches `distiller.py` or the skills surface; no artifact reaches an agent without a human gate; the eval reports and never acts. The authority-guard caveat seat 7 raises (T1 eviction) belongs entirely to held S8–S10. |
| **Unattended agents never write backlog notes** | **RESPECTED** — S11 (held anyway) routes to `docs/_journal/`; nothing in S0–S6/S12 writes a note. |
| **Server restart requires approval** | **RESPECTED** — R1 is declared, batched, and gated on Ron. One wrinkle: S0's ABORT fallback ("add an `MC_EVAL=1` env guard to the supervisor start in `server.py`") is a *server code change requiring a restart* — if that branch fires, S0 stops being a no-restart stage. It is a fallback, but the plan should say its cost out loud. |

---

## 5. S11 / S0 — the tunnel-kill mechanism is real, and S0 genuinely de-fangs it

Verified end-to-end in code, each link:

1. `tools/memory-eval/scorer_ab.py` `main()` → `importlib.import_module("server")`.
2. `server.py` imports `mc.blueprints.remote_routes` at module level (~line 2040 stanza).
3. `remote_routes.py:65` does `import mc_remote` at import time (provider self-registration).
4. `mc_remote/__init__.py:63–75` spawns a `_bg_autostart` **daemon thread** →
   `tunnel_supervisor.maybe_start()` — which starts, because this box has an
   existing enrollment.
5. The supervisor attests, gets a token, calls `CloudflaredProcess.start()` →
   `cloudflared.py:385` calls `reap_orphans()` **with `keep_pid` unset** →
   `reap_orphans` (`cloudflared.py:317`) kills **every** PID in the shared
   ledger (`~/.clayrune/cloudflared_pids.json` — confirmed present), including
   the live server's connector. Seat 6's 10 measured reap lines corroborate.

So yes: an S11 nightly schedule running today's `scorer_ab.py` would kill the
tunnel nightly, and the `5d8acb9` watchdog would then mask it as a recovered
blip. **S0's fix is structural, not cosmetic** — a harness that never imports
`server` never reaches link 2, and both seat 6 (N4) and seat 7 (independent
harness) have already *run* server-free wiring successfully. S0 de-fangs it.

**Two buildability caveats on S0's gate:**

- **M8 (`grep -c cloudflared` = 0) is a racy gate on its own.** The autostart
  is a daemon thread gated on a network attestation round-trip; a fast probe
  run can exit before the reap fires and pass M8 while still importing
  `server`. The deterministic gate is the one already in §14.6:
  `tests/test_memory_eval_harness.py` **asserting no supervisor module is in
  `sys.modules`**. Make that the primary success criterion and demote M8 to a
  belt.
- **S2's `eval.py` and anything S11 later schedules must be built ON the S0
  harness** — the S2 spec should state "never imports `server`" explicitly, or
  S0's guarantee quietly erodes as new eval entry points accrue.

---

## 6. SEQUENCING — what is forced, what is parallel, what needs re-baselining

**Genuinely forced (serial):** S0 → S0b → S1 → S2 → S3. Each gate consumes the
previous stage's artifact: the paired-run rule needs the harness (S0) and a
frozen corpus (S0b); S3's gate replays `suite-v1` (S1) and checks M1/M2 (S2's
`eval.py`). No link in that chain is arbitrary.

**Not forced (parallelisable):** after S3, the three remaining approved stages
are mutually independent at the file level — S4 (`mc/memory.py` +
`settings_routes.py`), S5 (21 topic files), S6 (`agent_routes.py`). S4+S6 are
batched only to share restart R1, which is a scheduling choice, not a
dependency, and a sound one. S5 can be built and landed in parallel with S4/S6
development; it needs only S0b, S2 (for M1), and its own pre-S5 snapshot.

**No later approved stage invalidates an earlier stage's measurement** — the
paired-run rule (both arms, one process, one frozen corpus snapshot) is exactly
what buys that immunity, and it is the plan's best procedural idea.

**One gate needs re-baselining (F-F):** S4's abort criterion "fails to
reproduce seat 4's offline number within 2 files" compares against numbers
measured **before** S3 landed and before the suite was pinned. After S3, a
perfectly good constant can fail that reproduction spuriously (different
signature, different corpus). The reproduction check should be re-derived once
against the S0b snapshot at the post-S3 live signature, and *that* becomes the
reference. Same family of issue, minor: S3's "+5 files" success number is a
transposition from unpinned data — seat 6 says so himself, and the < +3 abort
is the actual protection.

**S12's gate is not buildable as written (F-C):** "0 config rows differ from
`GET /api/config`, checked by a test" — a repo test cannot read a live API in
CI, and live config is operator state (this box's values are not what the doc
should promise a stranger's install). The test should compare the doc's table
against the **defaults dict in `server.py`** (plus a note for keys this box
intentionally overrides). Two-line change to the stage; the stage itself is
fine.

---

## 7. Verdict

**Safe to start now, as written:** **S0** (with the `sys.modules` assertion as
primary gate), **S0b** (as a backup; restore-drill deferred to pre-S9), **S1**
(gitignore in the same commit as the extractor — F-E — plus Ron's existing
gate), **S2**, **S3** (after S0–S2; it is the plan's best value and its
rollback is the only *perfect* one in the set), **S12** (with the gate
re-pointed at the defaults dict — F-C).

**Safe to build, condition before the flip/land:**
- **S4** — build and land freely (defaults = no-op); before flipping any
  constant, cross-project spot-check (F-D) and re-baseline the reproduce-gate
  (F-F). Mind the PUT-before-restart no-op window; the plan already sequences
  around it.
- **S5** — one fix first: the snapshot tool's restore must require `--only` and
  refuse `MEMORY.md`/archive targets (F-A). With that, S5's rollback is sound
  — topic files have no machine writer, so seat 7's F4 does not actually bite
  this stage.
- **S6** — as written; R1 needs Ron's explicit approval.

**Showstoppers: none in the approved set.** The S11 tunnel-kill mechanism is
real and verified to the exact line; S0 genuinely de-fangs it; keep S11 held
until S0's import-assertion test exists. The F6/F7 cross-project and
fresh-install failures are confined to the held stages — nothing in S0–S6/S12
imports the tiering prototype or evicts anything.
