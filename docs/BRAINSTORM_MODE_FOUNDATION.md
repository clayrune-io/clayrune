# MC-957 "Brainstorm this" — thinking foundation

**Status:** foundation for the spec, not the spec. Ron, 2026-09-27: Paul Graham's
essay *How to Do Great Work* (https://paulgraham.com/greatwork.html) is the
bedrock for how Brainstorm mode thinks. Anything the spec adds (SWOT, market
readiness, uniqueness checks) sits on top of these principles and must not
contradict them.

The principles below are paraphrased from the essay and translated into what
the mode actually does. The essay is not reproduced here; read it at the link.

## Principles → behaviour

| # | Principle (paraphrase) | What Brainstorm mode does |
|---|---|---|
| 1 | The right work sits where **aptitude, deep interest and scope** overlap; you find the first two by starting, not by deliberating. | Opens by asking *why this person* and *why they care*, not only "is there a market". An idea with a market but no pull on the user is flagged as a weak fit, not a green light. |
| 2 | Get to the **frontier** of a field, then look for its **gaps**. From far away the frontier looks smooth; up close it is full of holes people have learned to ignore. | Research step first maps the current state (competitors, prior art, what exists) *before* judging. The deliverable names the specific gaps found, not a generic "crowded / not crowded". |
| 3 | Great work comes from **refusing to smooth over the gaps** — the odd anomaly is where the new thing is. | The agent is instructed to surface what doesn't fit (an unexplained user complaint, a thing nobody sells) and to probe it rather than resolve it into the nearest existing category. |
| 4 | Fuel is **curiosity, delight, and wanting to do something impressive**; strongest when all three point the same way. | One explicit check: is the user drawn to this by more than one of the three? Recorded, not scored. |
| 5 | **Don't over-plan goals you can't describe in advance; "stay upwind"** — take the step that is most interesting and keeps the most options open. | The mode never ends with a fixed multi-month roadmap. It ends with the next **small, cheap experiment** and which options that step keeps open or closes. |
| 6 | **Starting is harder than continuing; work compounds** — early exponential growth looks flat. | The output always includes a first step that fits in a day. Early "no traction" evidence is weighed as *early*, not as a verdict. |
| 7 | New ideas come from **working on something slightly too hard**, not from trying to be original. The best ones feel **new and obvious at once** — obvious once a broken assumption is fixed. | The agent asks "what assumption does this idea overturn?" An idea that overturns nothing is flagged as likely incremental. |
| 8 | Often the real discovery is **the question**; pull many threads, **start lots of small things**. | The mode can branch: it keeps a short list of side-threads worth pulling rather than forcing convergence on the first framing. |
| 9 | Great work is **a search**: dead ends are normal; back up only as far as needed; **never abandon the desire itself**. | A "not worth chasing" verdict names *which* part failed (fit, gap, timing, market) and what adjacent variant survives, instead of killing the whole direction. |
| 10 | The one-word summary is **curiosity**. | Tone: exploratory and question-led, not a consultant's scorecard. SWOT and market checks are tools the curiosity uses, not the frame. |

## What this rules out

- A mode that opens with a SWOT template and fills it in. The analysis comes
  after the frontier map and the gap search (rows 2–3).
- A binary go/no-go as the only output. The output is: fit, gaps found,
  the assumption it overturns, the next cheap experiment, surviving variants.
- Judging an idea purely on current market size — that measures the smooth
  frontier, not the gaps in it.

## Open for the spec (not decided here)

Entry point and UI, which agent/engine runs it, how long a session runs, and
whether research is live web or cached. Those are Ron's calls when the spec is
written.
