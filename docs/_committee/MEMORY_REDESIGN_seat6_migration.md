# Seat 6 — The migration: what I measured, and where I changed seat 5's sketch

**Workstream:** `ws_006` · **Date:** 2026-08-15 · **Backlog item:** `dae8d6e7`
**Scope:** written plan only. `mc/memory.py`, `MEMORY.md`, the memory dir and all
production code were **not modified**. Artifacts in `_scratch/seat6/`
(gitignored).

**The plan itself is `docs/MEMORY_REDESIGN_2026-08.md` §14.** This file is the
derivation: what I measured on this box, the three places where I changed seat
5's §10 rather than elaborating it, and the reasoning that does not belong in the
deliverable.

---

## 1. What I took as settled and did not re-derive

Per the two-phase protocol.

| taken as given | source |
|---|---|
| pull path is dead (5% / 3%, 66 of 76 never opened) | brief; seats 4, 5 |
| live dark set is 15, not 30 — the probe omits `expand=` | seats 1, 4, independently |
| 14 of 15 dark files already carry a resident curated pointer; exactly 1 note reachable by neither path | seat 4 §3 |
| the archive filter is `- [`, and 28 of 67 evicted lines would be silently dropped | seat 5 §3.8 |
| T1/T2/T3 = 17/12/67 at `charter_byte_cap = 8192` | seat 5 M7 |
| bitemporal rejected; ranking before rewriting (the §5.3 ordering constraint) | seats 4, 5 |
| rerank measured worse locally (46 → 41) | seat 4 |
| `charter_byte_cap = 8192` is a proposal, not a measurement | seat 5 §3.7 |

---

## 2. What I measured myself

All on this box, 2026-08-15. Methods stated so they can be re-run.

| # | measurement | method | result |
|---|---|---|---|
| **N1** | the memory dir is **outside the git repo**, not merely untracked | `git check-ignore -v <MEMORY.md>` | `fatal: … is outside repository at 'C:/Users/…/mission-control'` |
| **N2** | `scorer_ab.py` **reaps live cloudflared connectors** | run it, `grep -c cloudflared` on combined output | **10** reap lines in one 42 s run. `retrieval_probe.py`: **0** |
| **N3** | baseline drift on the unpinned probe | rerun `scorer_ab.py`, compare to the brief | 46→**48** reachable, 30→**28** dark, 170→**177** tasks, same day, no code change |
| **N4** | a **server-free eval harness works** | `mc.memory.wire()` + `state.CONFIG` from `config.json`, no-op dispatch callables | `_mem_corpus` = 2,565 units; `_memory_search` returns at all three signatures; **0** supervisor side effects |
| **N5** | live index state | N4's harness, production `_mem_split_full` | `MEMORY.md` 22,662 B · curated 16,820 B (74.2%) · 15 managed entries · **1** watermark · archive 793,414 B |
| **N6** | corpus composition | `_mem_corpus` | 2,565 units — topic 74, managed 15, archive 2,476 |
| **N7** | `read_floor_topk` needs **no restart** | `mc/blueprints/agent_routes.py:1917-1920`; `_CONFIG_EDITABLE_KEYS` (58 keys, contains it); `_RESPAWN_TRIGGER_KEYS` (9 keys, does not) | read live per call; `PUT /api/config` mutates `state.CONFIG` in place and persists |
| **N8** | the silent-no-op trap for **new** keys | `update_config`: `if k in _CONFIG_EDITABLE_KEYS` | a `PUT` of an unlisted key returns **200 with `updated={}`** |
| **N9** | `data/memory-eval/` is **not** gitignored today | `git check-ignore -v data/memory-eval/x` → no match | must be added before S1 writes verbatim operator text |
| **N10** | `MEMORY.md` is volatile within hours | journal 18:41 vs my two reads | 23,547 → 22,795 → 22,662 B |
| **N11** | live config, from the API not a doc | `GET /api/config` | `read_floor_topk 3` · `read_floor_link_expand 2` · `index_byte_budget 24576` · `condense_threshold_kb 20` |

---

## 3. Where I changed seat 5's §10, and why

### 3.1 §10 Stage 0 is not "fix the instruments". It is "de-fang" them. (N2)

This is the finding that reorders the plan, and I found it by accident — by
capturing a baseline rather than quoting one.

`scorer_ab.py:130` calls `importlib.import_module("server")`. That boots the
remote-access supervisor **in the probe process**.
`mc_remote/cloudflared.py:317`:

```python
def reap_orphans(*, keep_pid: Optional[int] = None) -> int:
    for pid in _ledger_read():
        if keep_pid is not None and pid == keep_pid:  # keep_pid is None here
            ...
        log.info("reaping orphaned cloudflared connector pid=%s", pid)
```

The ledger is a **shared file**. `keep_pid` is `None` at the `CloudflaredProcess.start()`
call site. So a second process reading that ledger cannot tell the live server's
connector from a genuine orphan, and kills it.

**This is the same bug class `CLAUDE.md` already documents**, for a different
subsystem:

> `sweep_orphan_profiles()` may only run in the server process
> (`browser_routes.SWEEP_ENABLED`) … it calls any dir in the throwaway root that
> `browser_sessions` doesn't know about an orphan, and only the server's registry
> knows what is live — so importing this module in a test or debug script and
> launching a browser used to delete running panes' profiles out from under them.

The browser path got a guard. The cloudflared path did not. Every seat in this
hivemind that ran `scorer_ab.py` reaped connectors; the tunnel needing a
Reconnect fix earlier today is consistent with that, though I did not prove the
causal link and do not claim it.

**Why it reorders the plan rather than being a footnote:** every stage in this
migration is verified by running the probe. §10's stage 10 puts the probe on a
nightly schedule. Shipping that on top of this defect would silently take down
Ron's remote access once a night — and the tunnel watchdog fix from `5d8acb9`
would paper over it, so nobody would notice.

**And the fix is cheap, which I verified rather than assumed (N4).**
`mc.memory.wire()` needs `data_dir`, `memory_dir`, `claude_home` and six dispatch
callables. Read-only work never calls the six. No-op lambdas are sufficient:

```
corpus units 2565  {'topic': 74, 'managed': 15, 'archive': 2476}
probe topk3 expand0  [discovery_index_byte_cap_curated_bloat.md, project_memory_system_redesign.md, arch_memory_link_layer.md]
LIVE  topk3 expand2  [... + arch_memsearch.md, decision_step7_semantic_search_deferral.md]
      topk6 expand2  [... 8 hits]
```

I deliberately did **not** file or fix the `reap_orphans` defect. It is a
remote-access bug that this item merely tripped over, and folding it in would be
scope creep on an item whose journal already records three retractions from
overreach.

### 3.2 The proof-of-no-regression is a **paired run**, not a longitudinal comparison (N3)

Seat 5's §7.1 correctly identifies that the current probes' headline metric
"cannot go down" and prescribes a pinned suite. That is necessary and not
sufficient.

I measured the residual: same box, same day, **no code change**, the brief's
46/76 · 30 dark · 49% · 170 tasks became my 48/76 · 28 dark · 48% · 177 tasks.
Pinning the suite removes the task-set half of that drift. It does not remove the
corpus half — seat 2 measured the archive growing 2,691 B/day ≈ 8.5 units/day,
and every appended unit shifts the archive class's `avgdl` and the global IDF. So
replaying a pinned `suite-v1` next week against a larger archive still moves the
number with no code change.

Hence the rule I added, which is binding on every stage:

> **A stage is verified by running arm A (unchanged) and arm B (the change) in
> one process, against one frozen corpus snapshot.** The stage's number is the
> A-to-B delta. Longitudinal numbers are drift telemetry, never a gate.

**The payoff is that the noise floor becomes exactly 0**, because BM25 here is
deterministic and both arms see identical inputs. That is what lets §14.2 state
ranking gates as `delta >= 0` instead of seat 5's `delta >= -2`. I am not
tightening the tolerance by assertion — I am tightening it because the paired
design removes the source of the variance that `-2` was absorbing. On the
*unpinned* probe, `±2` remains exactly right, and I measured that it is; it is
just never used as a gate.

### 3.3 Rollback needs a snapshot, and §10 does not have one (N1)

§10's reversal column says "revert the edits" for its stage 3 and "copy the lines
back from the archive" for stage 8. The second is sound — the archive is
append-only. The first is not, and the reason is sharper than "memory files are
untracked": **the memory dir is not in the repository at all.** `git check-ignore`
does not return "ignored", it returns "outside repository". There is no git object
for `MEMORY.md` at any commit. Nothing to revert to.

So I added **S0b**, a sha256-manifested snapshot, and made it a hard prerequisite
for every stage from S5 on. Three details in it are not cosmetic:

1. **Location.** `~/.clayrune/memory-snapshots/`. Not `data/projects/` — the
   DATA_DIR pollution rule makes any stray file there a malformed "project" that
   500s both restart endpoints. Not `_scratch/` — `.gitignore:292` makes it
   invisible, and it is a scratch dir, which is the opposite of durable. Not the
   repo. `~/.clayrune/` is where this project already keeps durable operator
   state.
2. **Copy order.** `MEMORY.md` first, then the archive, then topic files. Floor
   eviction moves lines *out of* `MEMORY.md` and *into* the archive, so a
   concurrent eviction during a snapshot in that order yields at worst a
   **duplicated** line. Reverse the order and it yields a **lost** one. The
   per-project write lock is a `threading.Lock` inside the server process, so a
   snapshot taken by a separate process is not protected by it; `_atomic_write_text`
   (temp+replace) is what guarantees each individual file reads whole.
3. **Verification.** Restore into a temp dir and match 76/76 sha256s. A snapshot
   nobody has ever restored is not a rollback, it is a hope.

### 3.4 Two smaller changes

**S7 (watermarks out of `MEMORY.md`) demoted to optional.** Seat 2's 660 B per
watermark is right; the live count today is **1** (N5). That is 660 B against a
stage whose failure mode is breaking supersede, which is exactly the 2026-08-05
incident where 16 `_(live)_` entries filled the managed region. The July leak (67
markers, 37.8 KB) was already fixed by `_gc_stale_watermarks` at `49c09cc`. I put
a re-open trigger on it instead: revisit if the live count exceeds 5.

**S2 must reconcile the condense deadband rather than inherit it.** Seat 2's F4
says structured condense cannot fire because the floor is `cap-1024` and the
trigger sits past the cap. My read of live config gives
`condense_threshold_kb = 20` → **20,480 B**, against a floor of **23,552 B**, and
`mc/memory.py:875` computes the trigger over `MEMORY.md + CLAUDE.md` **combined**,
not `MEMORY.md` alone. Those are not obviously the same deadband. **I am not
adjudicating it** — seat 2 audited that path and I did not — but S10 depends on
the answer, so S2 publishes which is true before S10 builds on it. Given this
item's history, inheriting an unverified constant is the specific mistake to
avoid.

---

## 4. The ordering, and the one place it is genuinely load-bearing

The brief asked for cheapest-and-safest first, ranking before rewriting. §5.3
already establishes *why* (shrinking curated first converts benign dark files
into real losses 1:1). What the staging adds is a **hard boundary with a
different rollback mechanism on each side**:

- **S0 … S4** — instruments, config, ranking. Every one reverses with a single
  `PUT /api/config` or a `git revert`. **No note is touched.** A total rollback of
  everything up to here is a handful of API calls and one restart.
- **S5 … S12** — note content and resident text. Rollback is a snapshot restore.
  Git is irrelevant.

S3 (`read_floor_topk` 3 → 6) sits deliberately at the last position before that
boundary. It is the largest measured win in the whole hivemind (seat 2: dark
31 → 11), it costs one API call, it needs **no server restart** (N7), and it
undoes in one API call. **If Ron approves nothing else, approve S3.**

Two more properties of the ordering worth stating:

- **Every new config key defaults to today's behaviour.** Landing the code is a
  measurable no-op — S10's own success criterion is "`MEMORY.md` unchanged, delta
  0, across 20 simulated writes with the flag off". Behaviour changes only when a
  human flips a flag, and the flip is one call.
- **Exactly two restarts, both batched** (R1 after S4+S6, R2 after S10). Each
  needs Ron's explicit go-ahead per `feedback-server-restart-approval`. I
  deliberately routed S3 around the restart requirement by using `PUT /api/config`
  rather than editing `config.json` — hand-editing the file would have needed a
  restart, since `_load_config()` runs once at `server.py:295`, and that would
  have put an approval gate in front of the plan's cheapest win for no reason.

---

## 5. The human gates, collected

The brief asked what a human must decide at each gate. Five decisions, in the
order they arrive:

| gate | decision | why it cannot be automated |
|---|---|---|
| **S0b** | is `~/.clayrune/memory-snapshots/` acceptable, and never committed? | a location decision with a repo-hygiene history behind it |
| **S1** | may verbatim operator task text be written to disk? | 177 real prompts. Needs `.gitignore` **and** a `build-macos.spec` check — gitignoring alone was insufficient for `SHARED_RULES.md`, which the spec packaged anyway |
| **S3** | accept ~344 extra tokens per prompt | a spend, not a risk. Ron's budget |
| **S5** | approve the 21 wikilink targets | the only place a wrong automated choice creates a plausible-but-wrong retrieval edge |
| **S8** | read the 67-line eviction list and approve / edit / reject | seat 5 §6.3 states the prohibition lexicon's precision/recall was never measured against a hand label. This is where it gets human eyes, once |
| **S9** | after tranche 1, go/stop on the measured numbers | not on the plan's prediction |
| **S10** | set `charter_byte_cap` | a budget decision. Seat 5 proposes 8192 and labels it a proposal |

Plus the two restart approvals.

---

## 6. Safety rails — how the plan stays inside them

- **Authority guard.** The plan adds no capability the agent did not have. The
  one new automated write is charter eviction, which only ever **reduces what the
  agent is told**, never expands **what it may do** — and it ships behind
  `charter_eviction_enabled = false`. S8's dry run publishes the list before any
  eviction, so a human sees the eviction set before the machine acts on it.
- **A human on one side of every loop.** The nightly eval (S11) escalates and
  never acts. Hard gates fail a run; they do not repair anything.
- **Durable "no".** Nothing in the plan deletes a note. S9 moves lines into an
  append-only archive that is never truncated; the archive still holds them, and
  they become *more* retrievable than they are today, not less, because eviction
  makes them retrieval units for the first time (§5.4).
- **DATA_DIR pollution.** Nothing new under `data/projects/`. `data/memory-eval/`
  is a sibling, and it must be gitignored before S1 (N9). Snapshots live outside
  the repo entirely. Any new state file in the memory dir must carry a **non-`.md`**
  extension — the dir currently contains **zero** non-`.md` files (measured), so
  the plan's sidecar would be the first, and a `.md` there would silently become
  a retrieval unit and perturb class `avgdl` and global IDF.
- **Unattended agents never write backlog notes.** S11's output goes to
  `docs/_journal/dae8d6e7-*.md`. The note API truncates at 2,000 bytes and caps
  at 50 notes, both silently; that rule exists because it already destroyed 3,855
  notes.

---

## 7. Where I could be wrong

1. **The +5-file success criterion for S3 is a prediction, not a measurement I
   made.** It is seat 5's table (59→66 at the live signature) transposed onto a
   suite I have not yet pinned. If the pinned suite behaves differently, the
   criterion is wrong and the abort threshold (< +3) is what protects us. I chose
   an abort threshold well below the prediction for exactly that reason.
2. **The paired-run rule assumes both arms can be run in one process.** For
   config-only stages that is trivially true. For S9 (which mutates files) arm A
   is a snapshot and arm B is the post-eviction state, both replayed in one
   process — which works, but means the snapshot machinery from S0b is on the
   critical path for the *verification*, not just the rollback. If S0b is weak,
   S9's proof is weak. Stated rather than buried.
3. **Effort estimates are agent-hours and they are estimates.** I did not build
   any of it. S9's "1 h per tranche" is mostly measurement and waiting; S10's
   "8 h" is the one I would trust least, because it touches the write path under
   a lock that four writers share.
4. **I did not measure S4's three ranker constants myself.** They are seat 4's
   offline numbers. The plan's per-constant abort criterion ("fails to reproduce
   seat 4's offline number within 2 files") is designed so that a failure to
   reproduce kills the constant rather than propagating an unverified number.
5. **I did not prove the cloudflared reaping caused today's tunnel outage.** The
   timing is consistent and the mechanism is read from code, but "consistent" is
   not "caused" and this item's history is exactly that kind of overclaim. The
   finding stands on the 10 measured reap lines, which need no causal story.

---

## 8. Artifacts

`_scratch/seat6/` — gitignored:

| file | what it is |
|---|---|
| `baseline_scorer_ab_2026-08-15.txt` | B2 baseline, including the 10 cloudflared reap lines that are the N2 evidence |
| `baseline_retrieval_probe_2026-08-15.txt` | B1 baseline, 266 sessions |
| `section14.md` | the appended §14, kept so the append is re-runnable |

`mc/memory.py`, `MEMORY.md`, the memory directory, `config.json` and every
production file were **not modified**. No server restart was requested or
performed.
