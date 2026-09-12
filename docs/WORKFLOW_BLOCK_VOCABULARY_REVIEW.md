# The 15-block proposal, checked against what we actually built

Ron brought an outside agent's block vocabulary (2026-09-12). This maps each
item to the current implementation. Three are already shipped under different
names, one is refused by design, the rest are real gaps.

## Already built — under a different name

**Decision / Router** — called "probably the most important missing block". It
is not missing. R2-D6 decided branching is a property of the EDGE (`when`), not
a node: an agent step declares `outcomes`, each label gets its own output port,
plus a mandatory `otherwise` port. The routing is drawn on the canvas as edges.

This matters because the proposal's own closing design point —

> make Agent itself capable of returning a structured decision, but do not make
> the Agent block responsible for routing the graph. Keep routing visible on
> the canvas.

— is precisely the rule we already implement. The agent returns an outcome; the
graph routes on it. We did not arrive there by accident (R2-D6, and Q7 was
reversed to get the free canvas that makes it legible).

Their example flow already expresses today, minus the loop:
`Trigger -> Research Agent -> [outcome: good enough] -> Writer -> Approval -> Publish`

**Approval / Human Gate** — shipped. `approval` is one of three NODE_TYPES; its
`options` array behaves like an agent's outcomes, each option its own port.

**Parallel / Join** — structurally shipped, behaviourally not. The store is a
DAG, so fan-out and fan-in are expressible today, and R2-D2 defines real join
semantics: a node runs when EVERY parent is terminal (`completed` or `skipped`)
and at least one completed, with skip-propagation by reachability so a dead
branch cannot hang the join. What is missing is CONCURRENCY — the runner is
serial by design (single live run per workflow, fail-closed restart adoption).
So "launch multiple agents simultaneously" is a runner change, not a block.

## Refused by design — do not add without reopening the decision

**Loop / For Each.** The graph is acyclic and cycle detection refuses a closing
edge at three layers: at connect (drop refused with a toast naming both nodes),
at save, and at run. That is load-bearing for the frontier runner and for
skip-propagation. "Retry until condition" is reachable a different way (see
Error/Recovery); "for each item" is not, and bolting it on means reopening the
execution model, not adding a palette entry.

## Real gaps, ranked by value

1. **Error / Recovery** — already specced as R3-6 (`on_failure` edges plus a
   runner notify backstop), never built. Highest value of the list: a workflow
   runs unattended on a cadence, and today a failed step fails into a log nobody
   reads. The mockups already DRAW an "on failure" port; we deliberately left it
   out rather than ship a port that does nothing.
2. **End / Output** — what the workflow produced. Cheap, and it is what makes a
   run reviewable afterwards.
3. **Human Input** (distinct from approval — "ask me for a value, then resume").
   The approval gate already parks a run and waits; this is the same machinery
   with a free-text return instead of a chosen option.
4. **Wait** (delay / until a time). Small.
5. **Wait for Event** — suspend until a reply arrives, a PR merges, a build
   finishes. Needs an event source, so it lands with, not before, the trigger
   work below.
6. **Context / Data** — set variables, transform, extract fields. Partially
   covered by `{{steps.X.output}}` slots; a real transform step is new.
7. **Subworkflow.** Note the boundary: a workflow INVOKING another is fine; an
   agent AUTHORING or EDITING a workflow is refused (standing position, the
   authority guard). Sub-run via the API was ruled OUT in R3-1 for that reason,
   so this needs the invocation to be a first-class node, not an action verb.

## Triggers — where the scope caution bites

Proposed: webhook/event, file change, email, GitHub event, another workflow.
`TRIGGER_TYPES` is `('manual','schedule')` today. Another-workflow is reachable.
File change is reachable. Webhook, email and GitHub event are the third-party
integration layer — the same Zapier-shaped expectation already recorded as a
scope caution. That is a different product, not a block.

## The palette recommendation — agree

> I'd expose six primary blocks: Agent, Action, Decision, Human, Parallel, Wait.
> Then clicking + could reveal the more specialized variants. Trigger and End
> can remain structurally special.

Agree, with one correction: Decision is not a block in our model, it is what an
agent's outcomes plus edges already do. The palette could still SHOW it as a
concept if that reads better to a new user — but it must create ports and edges,
not a node, or the canvas stops matching the store.

Also note the palette today is the Bench: you drag PEOPLE, not primitives. That
was a deliberate product choice ("drag people, not primitives"). Six primary
blocks has to sit beside that, not replace it.

## Recommended order

1. `on_failure` + runner notify (R3-6) — specced, drawn in the mockups, unbuilt.
2. End / Output.
3. Human Input.
4. Concurrency in the runner, which turns the existing DAG fan-out into real
   Parallel.
5. Subworkflow.
6. Wait, then Wait-for-Event with a real event source.

Loop and the external triggers stay out until their own decisions are reopened.
