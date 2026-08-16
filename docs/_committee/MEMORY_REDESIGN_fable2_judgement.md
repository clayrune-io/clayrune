# Independent judgement — memory redesign (fable2)

**Date:** 2026-08-16 · **Backlog item:** `dae8d6e7` · **Reviewer:** independent, different model from the authoring seats.
**Lens:** is the design right, and is the verdict right. Arithmetic is being re-checked separately and is not duplicated here.
**Read:** `docs/MEMORY_REDESIGN_2026-08.md` (all 1522 lines), seats 3, 5, 7, the item journal.
**Grounded directly:** `mc/memory.py` — confirmed `_mem_corpus` indexes only the managed half of `MEMORY.md` (curated region is not a retrieval unit), and archive units require the literal `- [` prefix. Both of the design's most load-bearing code claims hold. I did not run `scorer_ab.py` (seat 6's cloudflared-reaping finding; `retrieval_probe.py` results were reproduced by three seats already).

---

## Verdict in one paragraph

**I endorse "approve S0–S6 + S12, hold S8–S10, defer S7," and I endorse seat 7's
kill of the eviction — including against the strongest counter-argument, which I
test below and which does not survive.** But the recommendation should carry
three amendments: (1) Constraint P is over-stated as a law of nature when it is a
measured property of an unset harness policy — one cheap experiment settles it
and nobody proposed running it; (2) the approved set is a well-built deferral,
not a fix, and the document should say so with a number attached (~8 months of
fuse on thin data); (3) the plan silently dropped the market scan's two highest-
ranked *adoptions* (path-scoped push, entity matching) while faithfully carrying
its rejections — the cheapest genuinely new capability on the whole scan is not
in any stage.

---

## 1. Is seat 7 right to kill the eviction? — YES, and here is the counter-argument given its full weight

The counter: *the 67 lines are already not delivered by the ranker today; they
reach the agent only by being resident. So "delivered on 0 of 160 tasks" may not
show eviction is unsafe — it may show these lines never earned residency either.*

Three things are wrong with it, in increasing order of importance.

**First, its premise is vacuous.** The 67 lines are not "not delivered by the
ranker today" — they have *never been in the ranker's corpus* (verified in
`_mem_corpus`: curated is excluded by construction). The ranker has not tried
and failed; it has never competed for them. Seat 7's experiment is the first
time they entered the contest, and they lost it structurally (best-ever rank
min 7, median 109, out of 2,632 — a 170-byte summary is outranked by its own
topic file and by richer archive entries on every realistic query). So the
correct statement of today's state is not "ranker doesn't deliver them" but
"residency delivers them on 100% of prompts." Eviction changes delivery from
always to effectively-never. Whether that is a loss is a *value* question about
residency — which is the counter's real content.

**Second, the value question has local evidence, and it points the other way.**
Nobody measured whether resident lines change behaviour (see §5.2 — a real gap).
But the strongest single fact in this whole document cuts against the counter:
**the memsearch incident is direct evidence that agents obey always-loaded
lines.** For 80 days `CLAUDE.md` carried a wrong instruction ("query memsearch
for the topic before…") and the recorded failure is that agents *followed* it —
the instruction was wrong, not ignored. The design uses this incident to argue
about contradiction detection; it is equally evidence that residency delivery
*converts*. Against that: eviction-to-rank-109 measurably converts to nothing.
Asymmetric evidence on both channels, same direction: keep.

**Third — and this is the piece neither seat 7 nor the counter states — the
eviction set is precisely the content a dispatch-time ranker can never deliver,
by construction, not by tuning.** Look at what the 30 no-channel lines are: IPv6
localhost stall, PowerShell pipeline pollution, cmd.exe 8191-char limit,
Session-JWT fails-open, Android Keystore backup trap. These are "gotchas that
cost hours." Their relevance is a **mid-session event** — the moment a socket
stalls, a native command pollutes a return value, a login silently fails. The
task text at dispatch does not and cannot predict them; if it did, they would
not be gotchas. A ranker whose only query is the first user message is
structurally blind to exactly this class, at any topk, with any constants.
Residency is not their channel by historical accident; it is the *only possible*
channel for pre-emptive warnings under this retrieval architecture. Seat 7's
closing sentence — "the index is the delivery mechanism for facts too small to
win a ranking contest" — is right and slightly understated: too small, and *too
early*. That is why "0 of 160" is decisive rather than merely suggestive.

**The decision-theoretic frame settles the residue.** Cost of a false keep:
~2,700 tokens/prompt, bounded, visible, priced. Cost of a false evict: an
unbounded, silent, unattributable repeat of an hours-costing failure — you never
observe the counterfactual, so the loss cannot even be detected, which is the
exact failure shape this codebase keeps re-learning (backlog-note truncation,
watermark leak, the floor/trigger deadband). Under silent asymmetric loss the
burden falls entirely on the change, and the change's own safety gates (M5, M1)
were shown blind to it. **Hold is correct.**

**One caveat on seat 7's own remedy, so it is not adopted uncritically.** Its
proposed per-line delivery gate — "no line evicted unless its knowledge is
delivered on ≥ 1 suite task afterwards" — blocks the 30 but licenses evicting
lines whose channel fired on 1–2 of 160 *historical* tasks. That is a thin
guarantee, and it measures the wrong conditional: delivery on tasks in general,
not delivery *when the knowledge is needed*, which the suite cannot observe
(§5.1). The gate is strictly better than M5 and worth having; it is not yet a
safety case. If the lines are genuinely dead weight, the correct remover is the
one the design already names in §8.5 — the human charter editor, whose one
observed forced-choice event (2026-08-06) performed exactly this judgement well.

**Where seat 7 is additionally right and should be adopted verbatim:** the
variant (a)/(b) ambiguity (F3) is real and embarrassing — the doc leaves 37% of
its own cap empty against its own stated rule and its own §4.1 conclusion; the
T1-never-evict close on the authority guard; F4 (the snapshot restore is not a
rollback — the watermark-clobber path recreating the 2026-08-05 pile-up is a
genuinely good catch); F6 (the lexicon evicts entire front pages on 4 of 19
projects, including ones that fit inside the cap).

---

## 2. Is Constraint P over-applied? — YES, as stated. The 5% is a policy outcome, not a law

The design's own market scan contains the refutation of the design's framing.
Letta's best result required a harness-forced pull; Anthropic hard-wires "ALWAYS
VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE" server-side. Both
demonstrate the pull rate is **policy-elastic**. Our 5%/3% was measured under a
harness that has never once asked for a pull. Treating that number as a constant
of nature, and then writing the corollary "there is no third channel," is the
one place the synthesis argues past its own evidence: **harness policy is a
third channel, and two vendors ship it.**

And there is local evidence compliance would be high: the memsearch incident,
again. An always-loaded instruction to consult a memory subsystem was followed
for 80 days *even though the subsystem did not work*. A resident line "before
touching a subsystem, grep the memory dir for it" would very likely move the 3%
substantially.

**What the design gets right anyway:** for the content that matters most, forced
pull cannot carry the load. A prohibition needs *unconditional* delivery
(§4.2's asymmetric-loss argument, which is sound); a probabilistic
instruction-following channel can never be that. And today's design doesn't
*need* the pull path — the front page is already pushed, so a forced "view your
memory" would mostly re-read what is already in context. So over-application is
currently cheap. Where it bites is the future: Constraint P as written
permanently forecloses both the item's two-level proposal and any eviction
escape hatch, on a measurement taken under a policy nobody attempted to change.

**The fix costs one line and one probe run.** Add the forced-pull instruction to
the injected context, let N sessions accumulate, re-run `retrieval_probe.py`,
and watch whether 5% moves. If it moves to 40–60%, the eviction calculus of §1
reopens (paged-out lines get a real second channel); if it stays under 10%,
Constraint P is confirmed *as a law of this system* and the document's framing
becomes earned. Either result is worth more than the assertion. Downgrade P
from "law" to "measured property of the current harness policy; re-testable by
S-series experiment" — and keep it absolute for T1-class content regardless of
the result.

---

## 3. Does the approved set solve the filed problem? — NO. It is a deferral, and a good one, and it should say so plainly

The item is "the front page will hit the ceiling and no amount of trimming
fixes it." S0–S6 + S12:

- removes **zero bytes** from the always-loaded page;
- **adds** ~344 tokens/prompt (S3), so per-prompt cost goes *up*;
- leaves `fold` pumping (117 lifetime inserts, no remover), curated growing
  ~28 B/day with the file measured 5 bytes under the eviction floor on
  2026-08-15;
- leaves the escalation tier in whatever state F5 shows it is actually in
  (the §3 "cannot fire" verdict is wrong per seat 7; S2's reconciliation is
  the right response).

On seat 2's adopted rate, the managed region is squeezed to zero around
2027-02 — call it **an ~8-month fuse, order-of-magnitude, on a 6-day growth
signal**. The approved set is the *prerequisite* work for any real fix
(instruments that can actually detect regression, ranker reach raised first per
the ordering constraint, injection paths repaired) and it is the right thing to
do first under either future. But the item's thesis — resident size is
O(topics) with no drain — is untouched, and the document's own §4.1 result says
no amount of S0–S6 changes that.

**So the honest label is: approve S0–S6+S12 as foundation, keep the item open
at high priority, and name the decision that remains.** The real menu for the
actual fix, none of which is in the approved set:

1. **Human-executed cut, mechanically assisted** — run S8's dry run (read-only,
   already specced), fix the escalation so "curated needs human curation"
   reaches Ron as an actionable item with the tiering list attached, and let
   the human cut. The 2026-08-06 `CLAUDE.md` event proves this works and is the
   only remover with an observed track record. This is the default I would
   recommend: it is S8 + escalation, with S9/S10's machinery never built.
2. **Machine eviction under variant (b) + a genuine value gate** — seat 7's
   route, blocked today on §1's grounds until a residency-value measurement
   exists (§5.2).
3. **Pay the tax** — `index_byte_budget` is a chosen budget, not a harness cap
   (Ron corrected exactly this framing on 2026-08-05). Raising it is a
   legitimate option nobody priced in dollars or latency, and prompt caching
   (§5.4) may make the true marginal cost of resident bytes much lower than the
   token count suggests.
4. **Throttle the writer** — see §5.3. Nobody across seven seats proposed
   reducing inflow.

---

## 4. Market scan sanity-check — verdict sound; three of its own steals were left on the table

**The no-purchase verdict is right, and right for the right reason.** The
upgrade from the scale argument ("1 MB is small") to the corpus-type argument
("token-exact corpora are where BM25 *beats* dense retrieval, and this box has
no ML stack at all") is the scan's best work — the scale argument flips when the
corpus grows; the type argument does not. The Zep/Graphiti rejection is
well-evidenced (per-write LLM+embedding cost for ~12 superseded facts, and the
reference implementation's own valid-time bugs #1489); §6's replacement
(tombstone convention + nightly cross-surface contradiction grep + `modified:`
frontmatter) captures essentially all the value at ~zero cost — the memsearch
inversion (the store was right; `CLAUDE.md` was stale for 80 days) is the
cleanest piece of analysis in the whole document. RAPTOR's collapsed-tree
result being *evidence against the item's own two-level design* is a genuinely
good find. The rerank rejection is backed by a local measurement (46 → 41,
worse), which is worth more than any vendor benchmark. The df ≤ 3
vocabulary-overlap experiment as the gate before ever revisiting embeddings is
exactly how a deferral should be written.

**But the plan inherited the scan's rejections and dropped its adoptions.**
Seat 3 ranked eight steals by value-per-cost. Of the top four, two appear
nowhere in S0–S12:

- **Path-scoped push (seat 3's #1)** — `paths:` globs in topic-file
  frontmatter, fired when the agent *reads a matching file* mid-session. This
  is the only mechanism on the entire scan that is (a) a push path, (b)
  orthogonal to task text, and (c) mid-session — which makes it the cheap
  partial answer to *both* open problems the design names but defers: the dark
  files whose vocabulary never appears in task text, and the 84%-stale read
  floor (it refreshes context exactly when relevance becomes knowable, without
  the per-turn re-injection build). 74/76 files already have frontmatter. It
  also directly serves §1's gotcha class: the IPv6 note fires when the agent
  opens `server.py`, not when the task mentions IPv6. **This is the single
  cheapest new capability available and it fell between seat 3 and seat 5.**
- **Entity matching (seat 3's #4)** — a third lexical signal beside BM25, no
  model, aimed at a corpus whose entities are exact tokens. Cheap, targeted at
  the residual dark set, unmentioned in the plan.
- Smaller: **Claude Code's write-time backpressure loop** (§9a — near-limit
  writes trigger a shorten-instruction; over-limit writes return an error
  demanding a rewrite). This is a five-line version of "the pump gets a drain"
  that needs none of S9/S10's machinery, and it was quoted in the scan and then
  never used.

The forced first-turn pull (steal #5) is §2's experiment. None of these
requires holding anything; path-scoped push and entity matching belong in the
approved series as new stages with the same paired-run gates.

---

## 5. What seven seats sharing one brief all missed

**5.1 The entire eval methodology defines relevance at dispatch time — and then
adjudicates a question about mid-session value with it.** Every number in the
document — reachable, dark, delivered, suite-v1, all of M1–M7 — is computed
over the *first user message* of a session. The moment a gotcha-class line
matters is mid-session, when a file is opened or a command fails; that moment
appears in no metric and cannot, because transcripts are only mined for their
opening task. Seat 7 grazed this ("never delivered across 160 historical tasks
is not never deliverable") but nobody named the structural version: **the
instrument suite is blind, by construction, to the value of exactly the content
class the eviction debate is about.** Pinning suite-v1 institutionalizes the
blind spot as the permanent regression gate. This does not invalidate the gates
(they measure the push path honestly); it bounds what they can ever license.
Any future eviction argument made *from these instruments alone* should be
rejected on methodology, whatever the numbers say.

**5.2 Nobody measured — or proposed measuring — whether resident lines change
behaviour.** The cost side of residency is measured to the byte
(~2,712 tokens/prompt); the benefit side has zero measurements anywhere in
~7,000 lines of committee output. Seat 4's behavioural gold set covers the pull
path only (file opens, n=9). The counterfactual question — "did an agent avoid
a documented trap because the line was resident?" — is hard but not
unmeasurable: (a) grep transcripts for agents citing index-line content they
never read from a file (e.g. dual-stack sockets, `--append-system-prompt-file`
on Windows) — attribution to the resident line is then near-certain; (b) count
recurrences of documented gotchas *after* their line became resident vs before.
Either is a weekend probe. Until one exists, every eviction argument divides a
measured cost by an unmeasured benefit.

**5.3 Every seat accepted the pump and designed a drain; nobody proposed
throttling the pump.** Seat 2's F3 ("insert-only pump, no mechanical remover")
was answered exclusively on the removal side — eviction machinery, caps,
tranches. The symmetric fix is cheaper and safer: make `fold` propose rather
than insert (pending region or human-approved diff, exactly the shape the
learning system already uses for skill promotion), and/or adopt the write-time
backpressure loop from §4. Growth of 0.167 pointers/day is ~1 pointer a week —
a human can approve that flow with seconds of effort, and inflow control has no
silent-deletion failure mode at all. It is telling that a committee asked "how
do we remove content safely" never asked "why is unattended machinery allowed
to append to the most expensive surface in the system without review" — on a
box whose standing rules exist precisely because unattended writers to
always-loaded surfaces caused incidents.

**5.4 Prompt caching is absent from the entire cost model.** The headline —
"~2,712 tokens saved on every prompt, forever" — prices every prompt at full
rate. If the harness caches the system-prompt prefix (the Claude CLI does),
a *stable* resident index costs a fraction of naive token math, and what
actually defeats caching is **churn**: watermark writes, floor evictions, and
per-session mutations that change the prefix every session. Two consequences
nobody drew: the real dollar case for eviction is weaker than stated, and
cache stability is an argument for a *frozen* charter that is independent of —
and possibly stronger than — the argument for a small one. Whether the injected
context actually lands in a cacheable position is a one-measurement question;
it should be answered before "tokens/prompt forever" is used as the headline
justification for any Phase-2 eviction.

**5.5 Minor.** The 84%-staleness repair and path-scoped push are the same
problem and should be evaluated together rather than the first deferred and the
second dropped. And S11's nightly eval writes findings to a journal whose read
rate is unmeasured (seat 7's O5) — if the contradiction check is to catch the
next memsearch in May rather than August, its escalation should land on a
surface with a measured read rate (the Inbox), not only in a file.

---

## 6. Recommendation

**Endorse: approve S0–S6 + S12, hold S8–S10, defer S7** — with S5 taken only
after seat 7's F4 fixes (restore drill, write-lock-aware restore, region-scoped
restore), and with these changes:

1. **Add two stages** from the scan's own top steals: path-scoped push
   (frontmatter `paths:` + hook; behind a default-off flag; gated on the paired
   suite) and entity matching. Cheapest new capability in the entire document.
2. **Run the Constraint-P experiment** — one forced-pull line, N sessions,
   re-probe. Reframe P as a measured property of the current policy, absolute
   only for T1-class content.
3. **Reframe the approved set honestly**: foundation, not fix. Item stays open;
   fuse ~8 months on thin data. Name the human-executed cut (S8 dry run +
   working escalation to Ron) as the default route to actually shrinking the
   page, with machine eviction (variant (b) + value gate) as the fallback that
   stays blocked until §5.2's measurement exists.
4. **Before any future eviction debate**: a residency-value probe (§5.2) and a
   caching measurement (§5.4). Both are small; both change the argument's
   denominator.
5. **Fold seat 7's corrections into the main doc** (its §8 list is right,
   especially: strike "no note becomes unreachable," demote M5 to a plumbing
   assertion, resolve variant (a)/(b), T1-never-evict, the F5 condense
   correction, cross-project lexicon rates).

**What the committee got right, on the record:** the diagnosis (resident-
because-residency-is-the-only-channel is the key insight and it is correct —
verified in code); the ordering constraint (reach first, shrink second); the
§5.4 archive-filter trap catch; the bitemporal rejection and the memsearch
inversion; the no-purchase verdict on corpus-type grounds; the paired-run rule;
the DATA_DIR/operator-data/authority-guard handling, which is exemplary; and
the process itself — a design whose own adversarial seat can kill its
centerpiece with a measurement, and whose synthesis records five retractions
instead of hiding them, is a process working exactly as intended.
