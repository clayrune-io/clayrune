# Agent types — design doc (MC-895)

Status: **design, not built.** 2026-08-22.
Backs `MC-895`; subsumes `MC-868`; unblocks `MC-871`.
External prior art: OpenClaw multi-agent (§6b). Internal prior art:
`docs/PROMPT_BUILDER_DESIGN.md` (characters, Phases 1–2 shipped),
`docs/DISPATCH_AND_ROUTING_ANALYSIS.md` (the auto-model router),
`docs/COORDINATION_LAYER_DESIGN.md` (MC-887, Phases 0–1 shipped).

---

## 1. What Ron asked for, and what is actually missing

> "Today the agents are not personalized… we have personas but these are unique
> to the specific chat and the user has to remember to bring it on… allow
> creating agent types… at project level or uber level (need to think about
> memory impacts)… and allow setting agent models for the different tasks
> (Fable for PRD, Opus for market research, Grok for coding)."

Three asks, and they are **not the same size**. Two of the three already have
their machinery built; what is missing is the wiring, not the parts.

| Ask | What exists today | What is missing |
|---|---|---|
| Agent types you can define | **Characters** — CC subagent `.md` files, dual-scope CRUD, injected at spawn (`mc/characters.py`, `_build_agent_context(character_body=…)`) | Nothing. This is done. |
| A type that *sticks* to a project | Per-chat, opt-in, chosen in the new-chat composer | A **project default** the chats inherit |
| The right model for the kind of work | A complexity classifier (H/S/O) + per-project `agent_model` + 7 providers | Routing by **kind of work**, and a model that travels **with the type** |

The Phase-2 design doc named the first gap itself and shelved it:

> *"Optional later layer, NOT in scope: a project-level default character that
> new chats inherit."*

That deferral is exactly the complaint. It is the cheapest item here and it is
most of the felt pain.

## 2. The one idea this design turns on

**Route to an agent, not to a model.**

Today's router answers *"how hard is this?"* and returns `haiku|sonnet|opus`.
It has no idea who is working or what kind of work it is. Ron's examples —
Fable for a PRD, Opus for market research, Grok for coding — are not three
difficulty levels. They are three **roles**, each of which happens to have a
preferred engine.

So the routing target becomes the **character**, and the character carries its
own engine:

```
prompt ──► classifier ──► character ──► (provider, model, effort, prompt body)
```

This subsumes the existing router rather than replacing it: a character with no
model pinned falls through to exactly today's behaviour, complexity classifier
included. And it makes `MC-868` (per-turn model switching) a re-use of the same
primitive — "hand this sub-step to a different character" — instead of a second
mechanism that also picks models.

It also fixes the token-economy angle. Savings do not come from picking Haiku
more often; they come from a *cheap specialist* handling cheap work with a
short prompt, and the per-bubble model pill already shows the user which one ran.

## 3. Data model

A character is already a file with YAML frontmatter. Extend the frontmatter —
nothing new to store, and the file stays a valid CC subagent so `@`-mention and
auto-delegate keep working:

```yaml
---
name: prd-writer
description: Turns a rough idea into a product requirements doc
provider: claude          # optional — else project/global default
model: claude-fable-5     # optional — else the complexity router decides
effort: high              # optional
handles: [prd, spec, product]   # optional — the routing vocabulary
autonomy: bounded         # optional — see §6
---
You are a product writer who…
```

Every added key is **optional**, and every one absent means "behave exactly as
today". That is the migration story: there isn't one.

Project record gains one field:

```jsonc
{ "default_character": "project:prd-writer" }   // scope:name, or absent
```

## 4. Precedence — one chain, no special cases

Resolved once, at spawn, in `_build_agent_context` / `_build_claude_flags`:

```
per-chat pick  ►  routed character  ►  project default  ►  no character
```

and for the engine, independently:

```
explicit per-chat model  ►  character's model  ►  project agent_model
                        ►  complexity router  ►  global default
```

Two properties worth keeping:

- **A character is still immutable for a chat's lifetime.** That is what makes
  the `claude -r` limitation a non-issue by construction (see
  `discovery_claude_resume_ignores_append` — resume restores the *original*
  system prompt, so a mid-chat persona swap silently would not take). Switching
  = new chat. Unchanged from Phase 2.
- **Visibility is a requirement, not a nicety** (Ron, 2026-06-12). The header
  pill already exists; a routed or inherited character must show in it the same
  way a hand-picked one does, with the source distinguishable (picked / routed
  / project default). An agent whose identity you cannot see is worse than no
  agent types at all.

## 5. Uber-level agents and memory — the question Ron flagged

> *"need to think about memory impacts when an agent is an uber agent but being
> asked to work on two different projects"*

**Recommendation: a character never owns memory. Behaviour is global; memory is
per-project. No exceptions.**

This is already how it works and it should be made an explicit invariant rather
than an accident. `_build_agent_context` assembles the project's rules, the
project's memory read-floor, and the project's API reference *regardless* of
which character is active; the character body is layered on top as voice and
method. A global `code-reviewer` used on Mission Control and on MarketReplay
reads each project's own memory and nothing of the other's.

The failure mode this forecloses is the one that would actually hurt: if a
character carried memory, a global character would become a **cross-project
leak channel** — client A's context surfacing in client B's session, with no
UI anywhere showing that it happened. The learning system already has a
structurally identical rail (`origin: interactive|unattended`, one human on
every loop) for the same reason: an artifact stream that silently crosses a
boundary is not recoverable after the fact.

Practical consequence: "uber level" needs no new storage concept. It is just
`scope: global` on the character file, which already exists.

## 6. Completion-drive (`MC-885`) as a character property

`MC-885` asks for agents that exhaust the reversible, obvious steps before
handing the turn back. Today that behaviour is prose in `SHARED_RULES.md`
("Pursue the better path by default…"), which means it applies uniformly to
every agent on every project, and its strength is untunable.

An `autonomy:` key makes it a per-type dial with the same three-way shape the
steward already runs on:

| value | means |
|---|---|
| `ask` | today's cautious default — check in at each decision point |
| `bounded` | do everything reversible; stop only for irreversible / genuinely ambiguous (the steward's contract) |
| `steward` | bounded, plus sets its own next goal |

This is a *presentation* of MC-885, not a substitute for it — the hard part of
MC-885 is the behaviour itself, not where the knob lives. But it is the right
place for the knob, and it means "a careful reviewer" and "a fire-and-forget
migration agent" can coexist on one project.

### 6a. Delegation — the steward runs the same flow (Ron, 2026-08-22)

> *"steward should follow same flow, so that it can also trigger other agents
> as per his authority."*

So there is no steward special case in routing: an unattended cycle resolves a
character through the same chain as a manual dispatch (§7), and it may dispatch
*to* other characters. `autonomy` stops being a private property of one agent
and becomes a **delegation budget**. Two invariants follow, and neither is
optional.

**1. Authority never escalates through a hand-off.** A delegated agent runs at
`min(caller.autonomy, callee.autonomy)` on the `ask < bounded < steward` order.
A `bounded` agent that dispatches a `steward`-level character gets a `bounded`
run, not a promotion. Without this, `autonomy` is not a limit at all — it is a
suggestion any agent can launder away by calling something with a bigger number,
and the constitutional line in the learning rails ("machinery must never expand
the agent's own capability set") would be one dispatch call wide.

**2. Unattended origin is inherited, never reset.** A steward-triggered agent is
itself unattended, and must be stamped `origin: unattended` even though it is a
fresh session with no steward marker in its own task text. The read floor
already withholds unattended-origin artifacts from unattended consumers
(`exploration_read_floor(consumer_unattended=True)`) — the whole point being
that a human sits on one side of every learning loop. A delegated agent that
counted as "interactive" because nobody typed its prompt would close that loop
silently, which is precisely the failure the rail exists to prevent.
`distiller.STEWARD_TASK_MARKER` is currently how unattendedness is detected;
delegation means detection can no longer rest on the task text alone — the
spawning session's origin has to travel with the dispatch.

Both are cheap to build and expensive to retrofit, so they belong in Phase 4
with the `autonomy` dial itself, not after it.

**Guard rail:** `autonomy` must NOT be settable by the learning system. The
authority guard (`_authority_violation`, `distiller.py`) exists precisely to
stop learned artifacts from expanding what an agent may do; a character
frontmatter key that grants autonomy is exactly the artifact it refuses, and
the Distiller must refuse it here too. Humans set autonomy. Learning never does.

## 6b. Prior art — how OpenClaw actually does it (researched 2026-08-22)

Ron asked us to check OpenClaw before deciding the routing question. The answer
is unambiguous, and it moves our recommendation.

**Agents are declarative config, one entry each.** `~/.openclaw/config.yaml`
holds `agents.entries`, and every entry carries its own `agentId`, `workspace`
(where its `SOUL.md` / `AGENTS.md` / `USER.md` live), `agentDir` (auth, model
registry, session store), `model` (primary + fallback, e.g.
`anthropic/claude-opus-4-6`), an optional `identity` persona, and optional
per-agent `sandbox` / `tools` restrictions. Exactly one entry is
`default: true` — the fallback that catches anything unmatched.

**Routing is 100% deterministic. There is no classifier.** `bindings` map an
`agentId` to a `match` block on structural facts — channel, accountId, peer
kind/id, guild/team id — with AND across fields, specificity tiers, and
first-in-config-order winning a tie. No model is ever asked "who should handle
this?".

Two things to take from that, and one not to:

- **Take: the routing key is a structural fact, not an inferred intent.**
  OpenClaw can be fully deterministic because its inputs arrive *from
  somewhere* — a WhatsApp business account, a Slack channel — and "where it
  came from" is a reliable proxy for "who should handle it". That beats a
  classifier's guess, and it never misroutes twice for the same reason.
- **Take: `default: true`.** Their fallback agent is precisely our proposed
  project-default character, and they ship it as part of the same object as the
  per-agent model — not as a later phase.
- **Do NOT take: `agentDir` as per-agent memory.** OpenClaw isolates memory
  *per agent* because its axis is account identity — the work WhatsApp and the
  personal WhatsApp genuinely must not see each other's history. Clayrune's
  axis is the **project**, and a type here is meant to be reusable across
  projects. Per-character memory would build exactly the cross-project leak
  channel §5 rules out. Same mechanism, opposite conclusion, because the
  boundary being protected is a different one.

**What this means for us.** Clayrune has no "channel" — every dispatch comes
from a project modal. But it has structural facts of the same quality: the
**project**, and the **trigger** (manual / scheduled / steward / hivemind /
backlog-dispatch). Those should bind deterministically, OpenClaw-style. Only
the genuinely-inferred part — *what kind of work is this prompt* — should reach
a classifier, and only after the structural rules have had their say.

## 7. Routing — the fork worth deciding deliberately

Three ways to choose the character for a dispatch. They differ in who is
accountable when it picks wrong.

**(a) Classifier picks freely.** Extend the H/S/O prompt to emit a character
name. Zero config. But it is a black box over a set that changes as the user
adds characters, and every misroute looks like the agent "went weird".

**(b) User-owned mapping table, classifier picks the row.** The character
declares `handles: [prd, spec]`; a small classifier emits a *work-kind* token;
the token maps to a character. Deterministic, inspectable, and a misroute is a
table entry the user can fix. The classifier's job stays tiny — the thing
Haiku is good at.

**(c) Explicit only.** A picker at dispatch, no automation. Honest, and Ron's
complaint is precisely that he has to remember to do this.

**Decision (Ron, 2026-08-22, after the OpenClaw check): (b), but structural
rules first.** The resolution order becomes:

```
1. explicit per-chat pick                 (the user said so)
2. binding match { project, trigger }     deterministic, OpenClaw-style
3. handles: + work-kind classifier        the only inferred step
4. project default character              the `default: true` fallback
5. no character                           exactly today's behaviour
```

Steps 1, 2, 4 and 5 involve no model at all. A misroute is therefore almost
always traceable to a rule you can read, and when it *is* the classifier, step
3 is the only place it can have happened. That is the property (a) can never
have: with a free-choice classifier every wrong answer looks like every other
one, and all of them look like the agent simply went weird.

(a) survives later as a *suggestion* in the picker — "looks like a PRD, use
prd-writer?" — where a wrong guess costs a click, not a turn.

## 8. Phasing — smallest useful thing first

| Phase | Scope | Size |
|---|---|---|
| **1** | `default_character` on the project **+** `provider`/`model`/`effort` in character frontmatter **+** the precedence chain; header pill shows the type and where the engine came from | medium — merged, see below |
| **2** | `bindings` — deterministic `{project, trigger} → character` | small once Phase 1 lands |
| **3** | `handles:` + work-kind classifier as the last-resort inference step | medium |
| **4** | `autonomy:` dial + non-escalating delegation + inherited unattended origin (§6a), with the Distiller refusal | small code, **needs a review** — it touches the authority bright line |
| **5** | `MC-868` per-turn hand-off between characters | large, only sensible after 1–3 |

**The original Phases 1 and 2 are merged (Ron, 2026-08-22).** The OpenClaw
check is the reason: their agent entry carries `identity` *and* `model` *and*
`default: true` as one object, and that is right. A "type" whose engine still
comes from a project-wide setting is only half a type, and Ron's own example
("Fable for PRD, Opus for market research, Grok for coding") is entirely about
the engine. Shipping identity without the model would answer the smaller half
of MC-895 and leave out the half he actually described.

Merged Phase 1 still answers "the user has to remember to bring it on", which
is the part that bites daily.

## 9. What this does NOT cover

- `MC-897` (visual project-interaction graph) — a view, not an agent concept.
  Shares only the source (the same YouTube reference Ron attached to both).
- `MC-887` / `MC-28` (cross-agent coordination) — substrate already shipped
  (`coordination_enabled: true`, project-scoped bus). Agent types make the
  roster more legible; they do not change the mechanism.
- `MC-898` (daily competitive-research agent) — a scheduled job. It *wants* a
  character (`market-researcher`, Opus) and is a good first customer of Phase 2,
  but it is not blocked on any of this.

## 10. Decisions, and what is still open

**Decided 2026-08-22 (Ron, via the OpenClaw check):**

1. **Routing** — deterministic bindings first, classifier last (§7). Structural
   facts (`project`, `trigger`) bind the way OpenClaw matches channel/account;
   only work-kind inference reaches a model.
2. **First cut** — identity *and* engine together (§8, merged Phase 1).

3. **Unattended cycles use the same flow.** No steward special case in routing,
   and a steward may dispatch other characters within its own authority
   (§6a). `trigger` stays a *binding key* — you can still bind a scheduled run
   to a different character — but it is not an escape hatch out of the chain.

**Still open:**

4. **Non-Claude providers in a character.** Deferred by Ron ("Grok — later").
   Seven are wired (Claude Code, Gemini, Codex, OpenCode, Goose, Aider, Kiro);
   Codex reports `auth: unknown` and four are not installed, so "Grok for
   coding" needs a provider that does not exist on this box. Standing
   recommendation for when it comes back: design the field now, validate
   against the live provider list at save time, and fail loudly on an unknown
   one rather than falling back to Claude — a character quietly running on the
   wrong engine is worse than one that refuses to save.
5. **Next item to design** — Ron: "not sure". MC-897 / MC-885 / MC-871 / MC-898
   all sit behind this. MC-871 genuinely wants MC-895 first; the others do not.
