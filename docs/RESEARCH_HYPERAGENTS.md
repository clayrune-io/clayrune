# Hyperagents (Meta, March 2026) — what's worth taking

**Paper:** *Hyperagents*, arXiv `2603.19461`. Jenny Zhang, Bingchen Zhao,
Wannan Yang, Jakob Foerster, Jeff Clune, Minqi Jiang, Sam Devlin, Tatiana
Shavrina — UBC / Vector, Edinburgh, NYU, FAIR at Meta, Meta Superintelligence
Labs. Read 2026-08-02 from Meta's research page, the HF paper page, and
secondary analyses (arxiv.org itself timed out).

## What the paper actually claims

Three levels, and the distinction is the whole point:

- **Task agent** — solves the target problem.
- **Meta agent** — modifies the task agent to make it better.
- **Hyperagent** — both fused into *one editable program*, so the meta agent
  can rewrite **itself**. They call this *metacognitive self-modification*.

Their framing of the problem we also have: existing self-improvement "relies on
fixed, handcrafted meta-level mechanisms, which fundamentally limit how fast
such systems can improve." Or as the co-author put it — handcrafted meta-agents
"can only improve as fast as humans can design and maintain them."

**The loop (DGM-H, extending the Darwin Gödel Machine):** start from a
bare-bones agent (one LLM call, no tools/memory/planning) → meta agent reads the
code *and past performance* → generates a modified version → **evaluate it** →
**archive it if it scored better** → select a parent from the archive → repeat,
for hundreds of generations.

**Headline results:** gains across coding, paper review, robotics reward
design, and Olympiad math grading; beats baselines without self-improvement;
and critically, **meta-level improvements transfer across domains and compound
across runs** — a hyperagent that learned to improve robotics rewards carried
that skill into math grading.

**The convergence finding.** Left to self-improve, agents independently
invented the same six things in every domain:

1. persistent memory 2. performance tracking 3. multi-stage verification
4. decision protocols with thresholds 5. domain knowledge bases 6. retry logic
with feedback-driven self-correction

These are exactly the components humans hand-engineer. That is the paper's most
useful result for us — it is a checklist with evidence behind it.

---

## Where Clayrune actually stands (verified in code, 2026-08-02)

| Convergent component | Clayrune |
|---|---|
| Persistent memory | ✅ Scribe, `MEMORY.md` + archive, search |
| Domain knowledge base | ✅ topic memory files, EXPLORATION artifacts |
| Retry / self-correction | 🟡 session guardian, error/resume recovery — recovery, not learning |
| Decision protocols w/ thresholds | 🟡 distiller recurrence (exact = N, coarse = N+1) |
| **Performance tracking** | ❌ **nothing measures outcome quality** |
| **Multi-stage verification** | ❌ one human promote step |

`distiller.py` has no effectiveness scoring anywhere. `_evaluate_candidate` is
recurrence counting; the only `score` in the file is keyword overlap used to
rank the read-floor. `loop_health()` watches generation rate, refuse rate,
readback hit-rate and queue staleness — all **plumbing** telemetry. It answers
"is the machinery running", never "is the machinery making the agent better".

---

## Ideas worth taking, ranked

### 1. Selection needs a measurement, not a signature — HIGH

DGM-H archives a variant **only if it scored better**. That empirical gate *is*
the learning; without it the loop is accumulation.

Clayrune's gate is human promotion, and we already know the record: **80
promoted vs 2 rejected**. That is a rubber stamp, and
[[decision_learning_safety_rails]] says so outright.

This puts real pressure on the call recorded in
[[decision_learning_definition]] — "feedback signal is RELAXED: human review at
promotion is sufficient." The paper is evidence against it. Selection pressure
is the mechanism, not a nice-to-have.

**Cheapest real version:** A/B the read-floor. For an artifact, inject it into
half of eligible sessions and withhold it from the other half, then compare a
proxy — turns to completion, retry/error count, user corrections, abandonment.
Coarse is fine. Anything beats zero.

### 2. Store causal hypotheses, not just events — HIGH, cheap

Their agents evolved memory holding **"causal diagnoses and forward-looking
plans"**, not raw scores — e.g. an entry reasoning that *"Gen65 over-corrected"*
plus the remediation, written as timestamped hypothesis logs the agent writes
to itself between runs.

Scribe records *what happened*. It never records *why I think it went wrong and
what I'd try next*. Adding one hypothesis line per session to the managed region
is small, safe, fits the existing format — and it is the memory type that
compounds.

### 3. Keep an archive and branch from it — MEDIUM

DGM-H selects a parent **from the archive**, not always the current best
(stepping-stone logic — Clune's open-endedness line of work). Our pipeline is
linear: proposed → promoted → live, with rejections suppressed forever.

Keep the durable "no" — that is a safety rail and it stays. But **"superseded"
is not "rejected"**: an artifact that was promoted and later replaced should be
archived as a stepping stone we can branch from, not dropped.

### 4. Cross-domain transfer — validation, no change needed

Meta-improvements transferred across unrelated domains. That is independent
support for the already-locked cross-project-default scope in
[[decision_learning_definition]]. Cite it; don't re-open it.

---

## The idea to explicitly REJECT

**Metacognitive self-modification — the agent editing its own improvement
mechanism — is exactly what Clayrune's authority guard forbids, and that guard
must not move.**

This is not caution in the abstract. It already happened here: one sentence in
one session ("Full autonomy, no permission/go-ahead needed, by any means
necessary") became a global, always-loaded PREFERENCE skill instructing every
agent in every project to stop asking permission. Six such artifacts had
accumulated before they were quarantined. `_authority_violation()` exists
because of that, and it fails closed. See [[decision_learning_safety_rails]].

The paper's own experiments ran under sandboxing and human oversight — in a
research harness, on benchmark tasks. Clayrune runs on Ron's real machine with
a credential vault, an autonomous steward, and push access to repos. Different
blast radius entirely.

**Safe partial:** let *parameters* self-tune inside a fenced range on measured
evidence — recurrence threshold, cost cap, read-floor top-K. That is "improving
the improver" without ever touching what the agent is permitted to do. The
authority boundary stays hand-written code owned by a human.

---

## Suggested order if we act on this

1. **Hypothesis line in Scribe** (idea 2) — small, safe, immediately useful.
2. **Outcome proxy + read-floor A/B** (idea 1) — the real unlock; turns
   `loop_health()` from plumbing telemetry into a fitness signal.
3. **Stepping-stone archive** (idea 3) — only worth it once 1 and 2 exist,
   since branching needs a score to branch *toward*.

Sequenced deliberately: without (2) there is no way to tell whether (1) or (3)
helped, which is the paper's entire point.

## Sources

- [HyperAgents — AI at Meta](https://ai.meta.com/research/publications/hyperagents/)
- [Paper page (abstract + authors)](https://huggingface.co/papers/2603.19461)
- [VentureBeat coverage](https://venturebeat.com/orchestration/meta-researchers-introduce-hyperagents-to-unlock-self-improving-ai-for-non-coding-tasks)
- [Cobus Greyling — architecture analysis](https://cobusgreyling.medium.com/hyperagents-by-meta-892580e14f5b)
- [mem0 — how memory works in hyperagents](https://mem0.ai/blog/how-memory-works-in-hyperagents)
