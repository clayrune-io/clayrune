# Event triggers + per-action rules — exploration brief

**Status:** BRIEF, not a spec. No code. Written by Brainstorm for Dave, 2026-09-29,
at Ron's request to plan two features: (1) event triggers ("MCP Events") and
(2) per-action rules (allow / block / require approval for each agent action).
Research retrieved 2026-09-29. Every external claim carries its link; repo
claims carry a path.

**Headline:** build them in the reverse of the order they were asked.
Per-action rules first, because event triggers are the first feature that
lets *untrusted outside input start a run*, and today nothing but a
hard-coded deny list stands between that input and a skip-permissions agent.

---

## 0. What already exists (read, not re-derived)

| Piece | Where | What it gives us |
|---|---|---|
| Triggers are `manual` + `schedule` only; event triggers named, deferred, with 3 prerequisites (emit points, `{event_type, filter}` record, self-trigger suppression); "start with `agent_finished`, ship suppression day one" | `docs/WORKFLOW_BUILDER_SPEC.md` R3-5 | The shape of Feature 1 is already half-decided. |
| Schedule/workflow/trigger writing is OUT for workflows under the authority guard | same, R3-1/R3-3 | Machinery may never author triggers. |
| Outbound signed lifecycle webhooks, deferred (no ecosystem) | backlog `1f589738` | Reading (C) below is already parked. |
| The only interception: `steward/fence.py` PreToolUse hook, a hard-coded regex **deny** list, armed only for unattended trigger types; Codex gets the same hook via `mc/guardrail_hooks.py` `-c hooks.PreToolUse=[...]`; Codex unattended runs sandboxed `-s workspace-write` | `docs/UNATTENDED_AGENT_PERMISSIONS_AUDIT.md` §3-§8 | An enforcement point already sits on every Claude and Codex tool call. Feature 2 is a data layer on top of it, not a new mechanism. |
| Interactive chats: `--dangerously-skip-permissions` / Codex bypass, unconditional | `mc/agent_runtime.py` (audit §1) | "Allow" is today's default for everything. |
| "Allow once": a blocked unattended call can be let through ONCE after a human proves themselves with the dashboard passcode; in-memory, 10-min TTL, bound to the exact MC session | `mc/blueprints/agent_routes.py` ~6411-6620 | This is already the "require approval" verb, with only one rule behind it. |
| Human proof = dashboard passcode retyped in the body (Origin header is forgeable) | `docs/HUMAN_PROOF_GUARD_SPEC.md` §4, Option A | How a rule or trigger edit proves a human made it. |
| `mc:question` channel: attended → form in chat; unattended → email after a 45s grace; answer = follow-up | `docs/ASKUSERQUESTION_CHANNEL_PLAN.md`, `mc/question_channel.py` | The delivery path for "ask Ron" when nobody is watching. |
| Completion latch `_maybe_notify_spawner` | `mc/blueprints/agent_routes.py:7919` | The one near-real emit point (R3-5). |
| `agent_channels` config already passes `--channels` to Claude | `mc/agent_runtime.py:1950,1989` | Claude Code channels are plumbed but unused. |
| Configured MCP servers on this box: `tradingview`, `sequential-thinking`, `mail` (global), `filesystem`, `browser` (project) | `~/.claude.json`, `.mcp.json` (read 2026-09-29) | None was found to declare resource subscriptions (the `mail` server under `tools/mail-mcp` has no subscribe code). Clayrune's `mc/mcp.py` is a config manager, **not an MCP client**: the CLIs hold the connections, not the server. |

---

## 1. What "MCP Events" can mean — four readings

| # | Reading | Example | Fit for Ron |
|---|---|---|---|
| **A** | Clayrune reacts to events **from MCP servers** (resource subscriptions / notifications) | "mail server says inbox changed → run triage agent" | The literal reading. **Weak foundation today** (below). |
| **B** | Clayrune reacts to **its own** events | "agent finished → run reviewer"; "backlog item → `done` → run release notes"; "Desk item approved → publish step" | **Strongest fit.** It's what R3-5 already scoped, what the Desk pipeline needs, and every emit point is code we own. |
| **C** | Clayrune **publishes** its events to others (outbound webhooks, or Clayrune-as-MCP-server exposing events) | "tell my Slack/n8n when a run finishes" | Already deferred (`1f589738`: no ecosystem yet). Nothing new says reopen it. |
| **D** | **External** systems push into Clayrune (inbound webhook → run) | "GitHub PR opened → review agent" | That's Hermes's and Zapier's model. Useful, but it's the highest-risk surface (§4) and needs a public endpoint (the tunnel). |

**Why A is weak right now (evidence):**
- MCP resource subscriptions (`resources/subscribe` → `notifications/resources/updated`) carry **only a URI**. The client must re-read, and notifications only arrive **while the connection is open**. [MCP spec 2025-06-18, Resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)
- Revision **2026-07-28** *replaced* `resources/subscribe`/`unsubscribe` with `subscriptions/listen` (a long-lived POST stream) and removed protocol sessions. [2026-07-28 changelog](https://modelcontextprotocol.net/specification/2026-07-28/changelog)
- A **Triggers and Events Working Group** (chartered 2026-03-24, leads from AWS and Anthropic) exists because "today, clients learn about server-side updates by polling or holding an SSE connection open". Its "Events in MCP v1 RFC" SEP was listed as **Ideating**. [WG charter](https://modelcontextprotocol.io/community/working-groups/triggers-events), [incubation repo](https://github.com/modelcontextprotocol/experimental-ext-triggers-events) ("exploratory, not official").
- Claude Code **channels** (research preview) are the one shipping "MCP server pushes an event into a session" mechanism. But events only arrive while the session is open, only for allowlisted plugins, and Claude Code "doesn't register a channel server that negotiates protocol revision 2026-07-28". [Channels docs](https://code.claude.com/docs/en/channels)
- On this box, none of the configured MCP servers was found to expose subscriptions, and Clayrune would first have to become a resident MCP client holding N connections open.

So reading A means building on a primitive that changed two months ago and whose replacement is still being designed. **Inference:** by the time a v1 of reading B ships, the WG may have settled. So design the trigger record so that "MCP subscription" is just one more *source*, added later.

**My read of what Ron wants (hypothesis, needs his confirmation):** the thing he's after is "something happens → an agent runs without me typing". "MCP" is probably the word for "connected systems", not a commitment to the protocol primitive. **Recommendation:** call the feature **Event triggers**, build reading B first, and add *one* external source after that, picked by Ron's first real use case. If his first example is email, that's a **polling** source through the existing laundered IMAP path, not MCP. **This is question Q1 for Ron (§6), and it's genuinely open:** his first concrete example decides the first external source.

---

## 2. The field, per feature (retrieved 2026-09-29)

### Feature 1 — event triggers

| Who | How triggers work | Lesson for Clayrune |
|---|---|---|
| Zapier | Polling triggers every 15 min (Free) down to 1 min (Team/Enterprise); "instant" triggers are app-sent webhooks. [Zapier help: How Zap triggers work](https://help.zapier.com/hc/en-us/articles/8496244568589) | Most "events" in the field are honest polling. Polling a source is legitimate, as long as we say that's what it is. |
| n8n | "MCP Server Trigger" = n8n *exposes* a workflow as an MCP tool that a client calls (webhook-like URL); "MCP Client Tool" = agent calls external MCP tools mid-run. No MCP-notification trigger found. [dev.to, 2026](https://dev.to/david_hamilton/n8n-mcp-means-three-things-heres-each-one-2026-2ah9) | Even the automation leader's "MCP trigger" is reading C/D, not A. |
| Hermes Agent (Nous) | Inbound webhooks: HMAC per route (every route must have a secret), 30 req/min default, 1h delivery-ID dedup, 1 MB cap. Webhook runs get a **constrained default toolset**; "HMAC validation authenticates the sender, not the content"; the agent *can* create subscriptions via a skill, but "`hermes webhook subscribe` deliberately does not accept a toolsets flag". [Hermes webhooks docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks) | The closest peer already hit our exact problem. Event runs get a tighter default posture, and an agent can't widen its own tools. Clayrune goes one step further and blocks agent authorship of triggers entirely (authority guard). |
| Claude Code | Channels (above); hooks include `FileChanged`, `TaskCompleted`, `SubagentStop`, but those are in-session, not cross-run triggers. [Hooks docs](https://code.claude.com/docs/en/hooks) | No cross-run event triggers upstream; this slot is open for an orchestrator. |

**Where the gap is:** peers either trigger from the outside world (Zapier, Hermes) or push into one open session (channels). None found triggers **across a fleet of mixed-vendor agents from the orchestrator's own state** ("Codex reviewer runs when the Claude builder's run finishes and its backlog item moves to review"). That's reading B, and only an orchestrator can do it. **Claim level:** inference from the sources above, not an exhaustive market scan; Quill should run a proper one if this becomes a positioning claim.

### Feature 2 — per-action rules

| Who | Mechanism | Lesson |
|---|---|---|
| Claude Code | `permissions.allow/ask/deny` rules; "evaluated in order: deny, then ask, then allow… rule specificity doesn't change the order"; `mcp__server__tool` and `Bash(prefix *)` specifiers; modes incl. `dontAsk` (auto-deny anything that would prompt). PreToolUse hooks return `allow/deny/ask/defer`; "Hook decisions don't bypass permission rules"; `defer` "skips the tool call, saves it… on your next prompt, Claude Code offers to resume the deferred call" (also works resuming with `-p`). Channels can relay permission prompts to a phone. [Permissions](https://code.claude.com/docs/en/permissions), [Hooks](https://code.claude.com/docs/en/hooks) | Copy the verb set and the deny>ask>allow precedence exactly, so Ron doesn't have to learn two models. `defer` is a possible native primitive for a headless "ask". It's **untested** against Clayrune's Mode B stream-json process. |
| Codex CLI | `approval_policy` incl. `granular` (sandbox_approval, rules, mcp_elicitations, request_permissions, skill_approval; `false` = fail closed); Starlark execpolicy rules with prefix rules; an "auto-review" subagent that approves/denies/escalates; `untrusted` policy **retired in v0.149.0 (2026-08-20)**. [Vaughan, granular policies](https://codex.danielvaughan.com/2026/05/07/codex-cli-granular-approval-policies-auto-review-subagent-autonomous-secure-workflows/), [Crosley, untrusted retired](https://blakecrosley.com/blog/codex-untrusted-approval-policy-retired) (third-party write-ups, not OpenAI docs; verify before depending on them) | Each vendor has its own rules dialect, and they churn. |
| Hermes | Constrained toolset per trigger route (above) | The posture belongs to the *trigger*, not only to the agent. |
| Reddit ask (`00aeb477`, "hol guard") | Pre-tool-call interceptor, off by default | Real outside demand signal, n=1. |

**Where the gap is:** every vendor offers allow/ask/deny **inside its own CLI, for a human at that terminal**. What nobody provides, and Clayrune is positioned for:
1. **One rule set across vendors.** Claude and Codex both already run Clayrune's PreToolUse hook (`guardrail_hooks.py`), so one table can govern both.
2. **"Ask" that works when nobody is at the terminal:** park the call, ask Ron on his phone or by email, resume on a passcode-proven yes. Claude's `ask` needs a terminal; Codex's auto-review swaps the human for a model. Clayrune has every piece (question channel, Allow-once, follow-up resume), but no rule table feeds them.
3. **Posture per trigger:** a run started by an event is treated differently from Ron's own chat.

**Anomaly worth keeping in view:** Ron said "allow / block / require approval", but in Clayrune today everything is already *allowed* (skip-permissions). So a rule table where "allow" is meaningful implies a **default posture other than allow** for some runs. That's a real decision (Q5), not a detail.

---

## 3. Recommended v1 scopes

### Feature 2 v1 — Action rules (build FIRST)

**Rule record** (server-owned data, not code):
`{id, scope: global|project|character, tool: Bash|Edit|Write|WebFetch|mcp__<server>__<tool>|*, match: prefix/glob on the command or path, decision: block|ask|allow, when: unattended|always, note, created_by_proof}`

- **Precedence:** block > ask > allow; the most restrictive match wins (same as Claude Code). With no match, the launch's default posture applies (today: allow).
- **Floor:** `fence.py`'s hard-coded `_BLOCK_PATTERNS` stays an immovable floor that no rule can loosen in v1. A human can still get past it once through the existing Allow-once.
- **Enforcement:** extend `steward/fence.py`, which already runs on every Claude and Codex tool call and already does one HTTP round trip (`GET /api/session/trigger-type`). Return the effective rules for this session in the same response, so a rule adds no second lookup. `block` → exit 2 with the rule's note as the reason.
- **`ask` flow, reusing what exists:** the hook blocks the call with a pending-approval id and tells the agent to end its turn → server raises a question through `question_channel` (form if attended, email after the grace period if not) → Ron approves **with the passcode** → server grants an Allow-once pass bound to *that call's hash* (not the whole session) → server sends the follow-up that resumes the session. Claude's native `defer` may replace the "end your turn" step later. Test it against Mode B first; don't assume it works.
- **Engines without a hook** (Gemini, Qwen): rules can't be enforced there. Show that in the UI, and refuse event-fired runs on them (Q6).
- **Authoring:** a Settings table, create/edit/delete **passcode-gated** (HUMAN_PROOF Option A). Rules live server-side, loaded at start. If the persisted file changes on disk without going through the route, the server refuses to reload it and flags the change. That's needed because any same-user agent can edit any file; HUMAN_PROOF §2 names the ceiling honestly, and this only raises the cost.
- **Decision log:** every block/ask/allow-by-rule is appended to a log outside `DATA_DIR`, so "which rule fired, on what" is answerable.

**Explicitly OUT of v1:** an auto-review classifier model (Codex-style), matching on individual MCP parameters, agent-proposed rules that activate themselves, filesystem/network sandboxing, dropping `--dangerously-skip-permissions` (separate open item, audit §3b), per-rule time windows, rules for Gemini/Qwen.

### Feature 1 v1 — Event triggers (build SECOND, internal events only)

- **Emit points (3):** `agent_finished` (the `_maybe_notify_spawner` latch, as R3-5 prescribed), `backlog_status_changed` (the backlog PATCH), `workflow_run_finished`. One `emit_event(type, payload, provenance)` function.
- **Trigger record** on a *workflow* (one noun: a one-step workflow covers "run this agent"): `{type: 'event', event_type, filter: {field: value} equality only, rule_profile}`. **`rule_profile` is mandatory.** An event trigger can't be saved without naming the Feature 2 posture its runs get (Hermes lesson).
- **Suppression, day one:** (1) provenance: every event carries the chain of runs that caused it, and a trigger never fires for an event whose chain already contains its own workflow; (2) depth cap of 3; (3) per-trigger rate limit (suggest 6/hour; Hermes allows 30/min, which is far too loose for agent runs that cost tokens); (4) dedup by event id; (5) the existing one-live-run-per-workflow.
- **Authoring:** human only, passcode-proven. Agents may create an inert draft (matches the standing workflow-draft position); only a human enables it.
- **Payload into prompt:** only named, allowlisted fields go into the prompt, inside an untrusted-data envelope (the `/api/browser/read` pattern), never a raw dump. Internal events carry little untrusted text, but backlog titles can.

**Explicitly OUT of v1:** MCP subscriptions (reading A), inbound webhooks (D), outbound webhooks (C), file watchers (a new resident process), filters beyond equality, fan-out from one event to many runs, agent-authored or agent-enabled triggers.

**v1.1 (only if Q1 says so):** one external *polling* source. If it's mail, reuse `tools/mail-mcp/read_digest.py` (laundered through a toolless model) as the source, and call it polling in the UI, as Zapier does.

---

## 4. How the two interact + safety constraints

An event-fired run is the worst case for Feature 2: no human started it, no human is watching it, and the event that started it may carry text someone else wrote. So:

1. **Sequencing is a safety property.** Event triggers must not ship before action rules can enforce a posture on the runs they start. Today the only posture is the fence's deny list.
2. **Authority guard.** Neither rules nor triggers may be written by an agent, a workflow, a learning artifact or an event. They're all "what agents may do / when they run", the same class R3-3 already rejects for schedules. A workflow action that edits rules or triggers joins the R3-1 OUT list by name.
3. **Self-trigger loops:** handled by provenance + depth cap + rate limit + dedup (§3). R3-5 already noted that one-live-run "blunts the storm but does not prevent the cycle". Provenance is what prevents it.
4. **Approval proof:** an `ask` approval is spending authority, so it takes the passcode, like Allow-once. An email reply is a notification, not proof: the sender is unauthenticated (AGENT_RULES, the laundering section). Remote approval goes through the dashboard over the tunnel, which already works from a phone.
5. **Loosening vs tightening:** in v1 only a human can change rules in either direction. An agent adding a `block` would be safe on the authority axis, but it's a denial-of-service lever and adds a second authoring path for little benefit.
6. **Ask fatigue** is the failure mode that kills "require approval" (the human-promote gate went 80 promoted vs 2 rejected, per CLAUDE.md). If `ask` fires often, Ron rubber-stamps it and it stops being a gate. Hence the experiment in §7, which comes before the approval UI.

---

## 5. SWOT (Clayrune competes with orchestrators and peer agents here)

| | |
|---|---|
| **Strengths** | One PreToolUse enforcement point already covers Claude + Codex (evidence: `guardrail_hooks.py`, audit §8). Passcode proof, Allow-once, question channel and follow-up resume are all shipped (evidence). The authority-guard doctrine is already written down and tested (evidence). |
| **Weaknesses** | Interactive runs are skip-permissions by design (evidence). Gemini/Qwen have no hook (evidence). Same-user agents can edit any file, so rule integrity only raises the cost (evidence: HUMAN_PROOF §2). Clayrune is not an MCP client (evidence). |
| **Opportunities** | Cross-vendor rules plus remote "ask" is something no single vendor offers (inference from §2). Cross-run triggers from orchestrator state have no upstream equivalent found (inference, sparse search). The MCP Triggers WG will eventually give reading A a stable primitive to plug in as a source (evidence: charter). There's a trust signal for launch: the Reddit ask (user report, n=1). |
| **Threats** | Vendors churn their permission dialects (Codex retired `untrusted` 2026-08-20; MCP replaced subscribe 2026-07-28), so mirroring native rules is a treadmill (evidence). Claude Code channels + Remote Control move "events and approvals" upstream into the vendor (evidence; still a research preview). Ask fatigue could quietly neutralize the gate (hypothesis, backed by the 80:2 promote history). |

**What drives the recommendation:** Weaknesses + Threats. Event triggers raise the number of unattended runs, which makes the skip-permissions weakness worse. That forces rules first. The Opportunities decide *which* rules design: our own table enforced through our own hook, not a mirror of each vendor's dialect.

---

## 6. Decisions only Ron can make

| # | Question | Recommendation |
|---|---|---|
| **Q1** | What's the first event you want to fire a run? Something inside Clayrune (agent finished, backlog moved, Desk item approved), email arriving, a GitHub/webhook event, or an MCP server's notification specifically? | Internal events first (reading B), then one external source picked by your answer. Call it "Event triggers", not "MCP Events", until the MCP events spec settles. |
| **Q2** | Do rules apply to your interactive chats too, or only to unattended runs? | A per-rule `when` field, defaulting to unattended. `block` rules for interactive chats are cheap and useful; `ask` in an interactive chat is just a form in the chat. |
| **Q3** | Can a rule loosen the fence's built-in floor (e.g. "allow `git push` for this one job")? | No in v1. Use `ask` + Allow-once. Revisit if one recurring job needs it weekly. |
| **Q4** | Is an email reply enough to approve an `ask`, or must approval be passcode-proven in the dashboard (phone via the tunnel works)? | Passcode only; the email only notifies you, with a link. Email senders can be forged. |
| **Q5** | Default posture for event-fired runs when no rule matches: allow (today's behaviour), or ask on anything that writes outside the project or reaches out? | Every event trigger must name a rule profile. Default profile: ask on outward-facing actions and on writes outside the project, allow the rest. |
| **Q6** | Engines with no hook (Gemini, Qwen): refuse event-fired runs on them, or run them unenforced with a warning? | Refuse. Fail closed matches the Codex sandbox decision (audit §8). |

---

## 7. The cheap experiment (first action fits in a day)

**Shadow rules.** Add a log-only mode to `steward/fence.py`: load 5-8 candidate `ask`/`block` rules (e.g. any `WebFetch`, `Write` outside the project root, `mcp__mail__*`, `gh api`, `curl -X POST` to a non-local host, `Bash(pip install *)`), and record every call that *would* have matched, per launch type, without changing behaviour. Run it for 7 days.

- **Signal:** asks per unattended run. If it's under 1 a day, the park-ask-resume UX is worth building as specified. If it's dozens, the rule vocabulary is too coarse, and v1 needs better defaults (or an auto-review tier) before any approval UI, or Ron will rubber-stamp.
- **Keeps open:** every design choice in §3; nothing is enforced.
- **Closes:** guessing at ask frequency, the one number that decides whether "require approval" is a gate or a formality.
- **Second probe, same day, ~1 hour:** test Claude `permissionDecision: "defer"` inside a Mode B stream-json session, to see whether it parks and resumes cleanly. If it does, the `ask` flow loses its "tell the agent to end its turn" hack.

## 8. Side threads (named, not pursued)

1. **Posture per trigger for schedules too.** Once rule profiles exist, schedules and hivemind workers could name one as well; that's the Hermes model applied across the board.
2. **Claude channels as an approval path.** Clayrune already passes `--channels`; permission relay to Telegram could become a second approval transport. It inherits the "sender allowlist is the authority" weakness, so it would need passcode-equivalent proof.
3. **Reading C revisited.** Once event triggers exist internally, outbound webhooks (`1f589738`) are a small addition on the same `emit_event` bus. The bus is the expensive part; this brief builds it for internal use first.
