# Hermes Agent (Nous Research) — competitive read

**Researched 2026-08-30 by Quill.** Primary method: shallow clone of
`NousResearch/hermes-agent` at `main` (pushed `2026-08-31T00:42:24Z`) and direct
reads of the source tree; GitHub REST + Algolia HN APIs for numbers; their own
docs site for behaviour I could not confirm from code alone.

**Evidence classes used throughout.** Every claim is tagged:

- **[SRC]** — I read it in their source at the cited path.
- **[DOC]** — stated on `hermes-agent.nousresearch.com/docs` or a GitHub release body.
- **[API]** — read from an API (GitHub REST, HN Algolia) on the date given.
- **[2nd]** — secondary source, not independently confirmed. Treat as soft.
- **[UNVERIFIED]** — could not be established. Left as a hole on purpose.

> Note on precedent: prior competitor teardowns live flat in `docs/`
> (`COMPETITOR_OMNARA.md`, `RESEARCH_HYPERAGENTS.md`). This one went to
> `docs/research/` because the brief named that path. Worth picking one.

---

## 0. Bottom line up front

Hermes Agent is not a peer project. It is a **238,501-star, ~1.9M-line, ~1,200
commits-per-week, 650-contributor open-source platform** backed by a funded lab
with a paid inference portal behind it [API, DOC]. Clayrune is 85k lines of
Python and 30k of JS, 1,083 commits, effectively one contributor, 12 stars [SRC,
API].

It duplicates Clayrune's positioning almost exactly, and it duplicates several
things this repo's `CLAUDE.md` still treats as Clayrune-only. Specifically, three
of the four "structural weaknesses" in the brief are **wrong**: they have a web
dashboard, a native desktop app, and an approval-gate system substantially more
developed than ours.

What survives as genuinely ours is narrower and less exciting than
"agent-persistence", but it is real: **memory depth, multi-project supervision,
and safe-by-default learning**. Details in §5.

---

## 1. What it actually is

### Corrections to the framing in the brief

Five things in the brief are wrong or imprecise. Stating them first because the
rest of the read depends on them.

1. **"Released Feb 2026" is wrong.** The repo was created `2025-07-22T22:22:28Z`
   [API]. The first tagged release is `v2026.3.12` (v0.2.0), published
   `2026-03-12T10:07:34Z` [API]. There is no v0.1.0 release and no February
   release. Feb 2026 is when *OpenClaw* was in the news (see §4), which is
   probably where the date came from.
2. **"SQLite/FTS5 memory" conflates two different systems.** FTS5 is the
   *session search* index over `~/.hermes/state.db`, not the memory store.
   Persistent memory is two flat markdown files with hard character caps. See
   "Memory model" below. This distinction matters a lot for §5.
3. **"16+ messaging platforms" undercounts.** There are 22 platform plugins
   under `plugins/platforms/` plus roughly 8 built-in adapters under
   `gateway/platforms/` [SRC]. Their docs say "20+" [DOC].
4. **"7 execution backends" is right but the list in the brief is right too and
   their own docs contradict themselves.** `tools/environments/` contains
   `local`, `docker`, `ssh`, `singularity`, `modal` (+ `managed_modal`),
   `daytona`, `vercel_sandbox` [SRC] — seven. The README says seven [DOC]; the
   docs overview page says six and omits Vercel Sandbox [DOC, 2026-08-30]. Source
   wins.
5. **"Functional duplicate of Clayrune's positioning" is correct but
   understated.** They also ship a web dashboard, an Electron desktop app with a
   plugin SDK, real-time voice with barge-in, A2A v1.0, signed webhooks, a Kanban
   board, and a skill security scanner. See §2 and §3.

### Architecture

Three entry points funnel into one agent class [DOC, `/docs/developer-guide/architecture`]:

```
CLI (cli.py) ─┐
Gateway (gateway/run.py) ─┼─► AIAgent (run_agent.py) ─► prompt_builder
ACP adapter (acp_adapter/) ─┘        │                 ─► runtime_provider
                                     ▼
                       chat_completions | codex_responses | anthropic_messages
                                     │
                       tool_calls ─► model_tools.handle_function_call()
                                     │
                                     ▼
                          SessionDB (SQLite + FTS5)
```

Three API modes means they speak OpenAI-style, Codex-style and Anthropic
Messages natively rather than normalising everything to one shape [DOC]. That is
how they get 39 model-provider plugins [SRC, `plugins/model-providers/`, counted
2026-08-30] without per-provider special-casing leaking everywhere.

Scale, measured on the clone [SRC, 2026-08-30]:

| | Hermes | Clayrune |
|---|---|---|
| Python LOC | 1,919,734 | 85,048 |
| Python files | 4,777 | 231 |
| JS/frontend LOC | separate React/Electron apps | 29,860 (`static/`) |
| Test files (`test_*.py`) | 3,447 | 97 |
| Tool count | "70+ tools across ~28 toolsets" [DOC]; 34 toolset keys in `toolsets.py` [SRC] | n/a (different shape) |

### Profiles

A profile is a full isolation boundary, not a settings preset [DOC,
`/docs/developer-guide/architecture`]:

> "Each profile (`hermes -p <name>`) gets its own HERMES_HOME, config, memory,
> sessions, and gateway PID. Multiple profiles run concurrently."

Supporting machinery exists in `hermes_constants.py`: `named_profile_home()`,
profile tombstones, `assert_named_profile_home_live()` [SRC]. Voice wake words
route per profile [DOC, v2026.8.3 release notes].

**This is the closest thing they have to Clayrune projects, and it is weaker in
one specific way:** profiles are parallel silos with no cross-profile view. There
is no board showing you four profiles' states at once. See §3.

### Memory model — the most important finding in this section

Persistent memory is **two capped markdown files** [DOC,
`/docs/user-guide/features/memory`, read 2026-08-30]:

| File | Cap | Purpose |
|---|---|---|
| `~/.hermes/memories/MEMORY.md` | **2,200 chars (~800 tokens)** | agent notes: OS quirks, project conventions, work diary |
| `~/.hermes/memories/USER.md` | **1,375 chars (~500 tokens)** | user profile: name, timezone, style preferences |

Both are "injected into the system prompt as a frozen snapshot at session start"
so the prefix cache stays warm; edits land on disk but do not reach the prompt
until the next session [DOC]. When the cap is hit the agent gets an error telling
it to consolidate before retrying [DOC].

Separately, **all CLI and gateway sessions go to SQLite `~/.hermes/state.db`
with FTS5**, queried on demand by a `session_search` tool that returns real
messages rather than summaries [DOC].

Eight external memory providers ship as plugins and run *alongside* the built-in
memory, never replacing it: `honcho`, `openviking`, `mem0`, `hindsight`,
`holographic`, `retaindb`, `byterover`, `supermemory` [SRC,
`plugins/memory/`, counted 2026-08-30]. Only one may be active at a time [DOC].

**Read this against Clayrune.** Our always-loaded index alone has a ~24KB byte
budget, and it sits on top of topic files, an archive, standing positions and a
continuity block. Hermes's entire always-on memory is ~3,575 characters, roughly
**1,300 tokens fixed cost** [DOC]. They chose a small hot set plus on-demand
search; we chose a large curated hot set. That is a genuine architectural fork,
and it is the single clearest place we are ahead.

### Skill generation

- The agent creates, edits and deletes its own skills through a `skill_manage`
  tool [DOC, `/docs/user-guide/features/skills`].
- A **background self-improvement review** forks after each turn and can save
  memory entries or write skills [SRC, `run_agent.py:1902 _spawn_background_review`;
  `agent/background_review.py`]. It is controlled by
  `auxiliary.background_review.enabled`, **default `true`** [SRC,
  `agent/background_review.py:296`].
- `tools/skill_provenance.py` sets a ContextVar so the curator can tell
  agent-written skills from user-requested ones, and only auto-curates its own
  [SRC].
- Skills follow the `agentskills.io` standard; the hub pulls from skills.sh,
  `.well-known/skills/index.json`, `openai/skills`, `anthropics/skills`,
  `huggingface/skills`, `NVIDIA/skills`, ClawHub, LobeHub [DOC].
- `tools/skills_guard.py` (1,360 lines) statically scans externally-sourced
  skills for exfiltration, prompt injection, destructive commands and
  persistence, with a three-level trust policy: `builtin` never scanned,
  `trusted` = `openai/skills` and `anthropics/skills` only, `community` blocked
  on any finding [SRC].

**The approval gate exists but is off by default.** With
`skills.write_approval: true`, every `skill_manage` write stages to
`~/.hermes/pending/skills/` for review. Their own docs: *"By default,
agent-created skills are written freely without review"* [DOC, read 2026-08-30].
Memory writes have the same shape: `memory.write_approval` stages writes for
`/memory pending` [DOC], off by default. Confirmed in source at
`tools/skill_manager_tool.py:1511-1558` (stage/replay) and
`gateway/slash_commands.py:4013` [SRC].

### Orchestration

Two distinct mechanisms, not one:

- **Subagent delegation** [SRC, `tools/delegate_tool.py`]: spawns child
  `AIAgent` instances with a fresh conversation, their own `task_id` and
  terminal session, the parent's toolsets minus child-blocked tools, and a
  focused prompt. Single-task and batch/parallel modes. The parent sees only the
  call and the summary, never the child's intermediate reasoning. There is also
  `tools/async_delegation.py`.
- **A2A orchestration** [SRC, `plugins/platforms/a2a/`]: `a2a_orchestrate`
  drives *external* agents over the wire.

Notably, delegation children are barred from writing shared `MEMORY.md`
(`DELEGATE_BLOCKED_TOOLS`) and are spawned `skip_memory=True`, and the post-turn
background review is suppressed inside them [SRC, `run_agent.py`]. That is a
deliberate blast-radius boundary on the learning loop, and it is the closest
thing they have to our unattended-loop rule (see §3).

### Execution backends

`tools/environments/`: `local.py`, `docker.py`, `ssh.py`, `singularity.py`,
`modal.py` + `managed_modal.py` + `modal_utils.py`, `daytona.py`,
`vercel_sandbox.py`, plus `base.py` and `file_sync.py` [SRC, 2026-08-30].
Daytona and Modal offer serverless persistence: the environment hibernates when
idle and wakes on demand [DOC, README]. Unified orchestration in
`tools/terminal_tool.py`.

### Monetization

`portal.nousresearch.com` is a credits model [DOC, read 2026-08-30]:

| Plan | Price | Monthly credits | Rollover cap |
|---|---|---|---|
| Free | $0 | $0 | n/a |
| Plus | $20/mo | $22 | $10 |
| Super | $100/mo | $110 | $50 |
| Ultra | $200/mo | $220 | $100 |

Covers 300+ models plus a Tool Gateway (Firecrawl web search, FAL image gen,
OpenAI TTS, Browser Use cloud browser) and cloud hosting for agents. Top-ups at
$10/$20/$50/$100/$200. Tools bill against the same credit balance as models.

**The harness is free and MIT; the inference and tooling around it are the
product.** Same open-core shape Clayrune has chosen, executed by a lab that
already sells inference.

---

## 2. Feature by feature

Verified 2026-08-30. "Ahead" is from Clayrune's point of view.

| Capability | Hermes | Clayrune | Who is ahead |
|---|---|---|---|
| **Remote/sandboxed execution** | 7 backends: local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox [SRC]. Modal/Daytona hibernate-and-wake [DOC] | local only; the hosted story lives in a separate project | **Hermes, decisively** |
| **Messaging channels** | 22 platform plugins + ~8 built-in adapters [SRC]: Telegram, Discord, Slack, WhatsApp (2 impls), Signal, Matrix, Teams, IRC, LINE, Feishu, DingTalk, WeCom, Weixin, QQ, Google Chat, Mattermost, SimpleX, SMS, email, ntfy, Home Assistant, BlueBubbles/iMessage, Yuanbao, Buzz, Photon, Raft | web push + mobile app over a Cloudflare tunnel; email for decisions | **Hermes, decisively** |
| **Provider-agnosticism** | 39 model-provider plugins [SRC] incl. Anthropic, OpenAI, Codex, Copilot, Bedrock, Gemini, Ollama, DeepSeek, Kimi, MiniMax, NVIDIA, Fireworks. Three native API modes [DOC] | `agent_runtime.py` drives claude-code, gemini, codex [SRC] | **Hermes, decisively** |
| **A2A v1.0** | Full Linux Foundation standard, both directions. Agent Card at `/.well-known/agent-card.json`, JSON-RPC, pure stdlib transport. Security: localhost-only without a token, per-peer bearer tokens, inbound prompt-injection filters, outbound credential redaction, append-only JSONL audit, trusted-peer allowlist, HMAC-SHA256 push auth with SSRF-safe callbacks [SRC, `plugins/platforms/a2a/security.py`] | none | **Hermes** |
| **Voice / TTS** | Streaming clause-by-clause TTS, barge-in interrupt, on-device open-vocabulary wake words, per-profile wake routing, voice notes transcribed on 7+ platforms, `tools/tts_*.py`, `voice_mode.py`, NeuTTS synth [SRC, DOC v2026.8.3] | none | **Hermes** |
| **Signed webhooks** | Inbound HMAC with V1/V2 signature schemes [SRC, `gateway/platforms/webhook.py`] plus outbound signed lifecycle events (session activity, turn completions, tool events) [DOC, v2026.8.3] | none | **Hermes** |
| **Grounded citations** | `skills/research/grounded-citations` [SRC]: quotes matched against actual page text, citations link to exact evidence, fact-check mode reports verified / failed / unverifiable [DOC, v2026.8.3] | none | **Hermes** |
| **Approval gates** | `tools/approval.py`, 5,961 lines: dangerous-command pattern detection, per-session thread-safe state, CLI + async gateway prompting, auxiliary-LLM smart approval, permanent allowlist, YOLO-mode frozen at import to block prompt-injection escalation, MCP elicitation consent, audit-board marks [SRC] | plan approval + `mc:question` + PreToolUse fence | **Hermes** |
| **Skill security scanning** | `tools/skills_guard.py`, 1,360 lines, trust-tiered, fail-closed quarantine on project skills [SRC] | none | **Hermes** |
| **Desktop app** | Electron, macOS/Windows/Linux. Artifacts with sandboxed live preview, plugin SDK, file browser, voice, multi-window, global-hotkey quick entry [SRC `apps/desktop/`, DOC] | Tauri launcher | **Hermes** |
| **Web dashboard** | `hermes dashboard` on port 9119: FastAPI + Vite/React 19/Tailwind SPA served from `hermes_cli/web_dist/` [SRC, `web/README.md`] | full SPA, the core product | **roughly even; see §3** |
| **Browser control** | `browser_tool.py`, `browser_cdp_tool.py` (raw CDP passthrough), `browser_camofox.py`, `browser_supervisor.py`, `browser_extension_router.py`; cloud providers browser-use / Browserbase / Firecrawl [SRC] | live Chromium rendered into the dashboard, drivable over HTTP, named persistent profiles | **Clayrune on the *pane*; Hermes on browser *tooling*** |
| **Scheduler** | `cron/` with jobs, executions, monitor, incidents, lifecycle guard, blueprint catalog, consent-first suggestions [SRC] | Clayrune scheduler + `/schedule` skill | **even** |
| **Persistent memory across sessions** | 2,200 + 1,375 chars hot, FTS5 search cold, 8 provider plugins [DOC, SRC] | Scribe, read-floor, topic files, archive, positions, continuity, ~24KB index budget | **Clayrune** |
| **Self-authored skills** | `skill_manage` + background review, default unreviewed [DOC] | Distiller four-artifact loop, human-promote gate | **even on capability; Clayrune on safety** |
| **Multi-project supervision** | profiles = parallel silos, no cross-profile board [DOC] | project grid, cross-project backlog, agent floor | **Clayrune** |
| **Multi-agent structure** | subagent delegation, `a2a_orchestrate` [SRC] | hivemind: orchestrator, workstreams, message bus, shared knowledge layer, escalation | **Clayrune on structure; Hermes on reach** |
| **Migration in** | `hermes claw migrate` from OpenClaw (settings, memories, skills, API keys, with `--dry-run`); `hermes import-agent` from Claude Code / Codex CLI [SRC, DOC] | none | **Hermes** |
| **Windows support** | native, no WSL, bundles MinGit/uv/Python/Node/ripgrep/ffmpeg [DOC] | native, primary dev platform | **even** |
| **License** | MIT [API] | (this repo) | even |

---

## 3. Where they are weak

The brief proposed five Clayrune-only areas. **Three of them are wrong.** I
checked each rather than assuming.

### Claimed and FALSE

**"Visual dashboard" — they have one.** `web/README.md` describes a Vite + React
19 + TypeScript + Tailwind v4 SPA for "managing Hermes Agent configuration, API
keys, and monitoring active sessions", served by FastAPI on port 9119 [SRC].
Plus a full Electron desktop app [SRC, `apps/desktop/`] and a dashboard plugin
API with auth plugins (`plugins/dashboard_auth/{basic,nous,self_hosted,drain}`)
[SRC].

**"Approval gates" — they have a better one than us.** 5,961 lines against our
plan-approval flow. It even hardens against a case we do not: YOLO mode is read
once at import, "Reading `os.environ` on every call would allow any skill running
inside the process to set this variable and instantly bypass all approval checks
— a prompt-injection escalation path" [SRC, `tools/approval.py:36`].

**"Terminal pop-out" — they have panes.** `tools/open_preview_tool.py`,
`preview_tool.py`, `focus_pane_tool.py`, `read_terminal_tool.py`,
`close_terminal_tool.py`, `annotate_preview_tool.py`, `drive_preview_tool.py`,
routed to the desktop renderer via `tools/desktop_ui.py` [SRC]. Different shape
from our pop-out, same job.

### Claimed and TRUE, but narrower than stated

**"Browser pane."** They have deeper browser *automation* than us (raw CDP
passthrough, anti-detection via camoufox, three cloud providers). What they do
not have is our specific thing: **a live Chromium rendered inside the dashboard
that the operator watches and takes over from a phone.** Their preview pane
renders artifacts and files in the desktop app [DOC]; it is not a co-driven
browser session reachable over a tunnel. Narrow, but real.

**"Learning safety rails."** This is the one that holds up, with an honest
caveat. Comparing the three rails in `CLAUDE.md` against their source:

| Rail | Clayrune | Hermes | Verdict |
|---|---|---|---|
| **Authority guard** — learning may never expand the agent's own permissions | `_authority_violation()` at `distiller.py:1636`, called in `_generate_and_write_artifact` at `:1674`, *before* the human queue. Semantic: refuses artifacts granting autonomy or removing approval gates [SRC] | `skills_guard.py` has `agent_config_mod` / `agent_config_mod_shell` / `other_agent_config_mod` at **critical** severity, targeting skills that write AGENTS.md / CLAUDE.md / config.yaml [SRC:583-607]. But it is a **regex scanner on imported and project-tier skills**, and I found **no `scan_skill` call on the `skill_manage` write path** the agent uses for its own skills [SRC, callers enumerated 2026-08-30]. Their own docstring concedes the mechanism misses Python `open(...,'w')` / `pathlib.write_text` / Node `fs.writeFileSync` aimed at config files [SRC:22-36] | **Clayrune. Ours guards the self-authored path; theirs guards the import path.** |
| **Human on one side of every loop** | `_UNATTENDED_LOOP_RULE` at `distiller.py:1554`; artifacts carry `origin: interactive\|unattended`; `exploration_read_floor(consumer_unattended=True)` withholds unattended-origin artifacts from steward cycles; unstamped artifacts fail closed [SRC] | Partial and by-default absent. `background_review.enabled` defaults **true** [SRC:296] and agent-created skills are "written freely without review" by default [DOC]. They *do* block delegation children from the shared memory and from the post-turn review [SRC] — a blast-radius boundary, not a human-in-loop rule | **Clayrune, on defaults and on fail-closed design. They ship the gate; they just leave it open.** |
| **Durable no** | `_GLOBAL_SUPPRESSION_PID` at `distiller.py:1478`, global rejections bind every project via `_is_suppressed` [SRC] | Cron *suggestions* have exactly this: dismissal is "latched so it is never re-offered" [SRC, `cron/suggestions.py`]. I found no equivalent for rejected skills or memory writes | **Even on cron, Clayrune on learning artifacts** |

Read plainly: **we are not ahead because they lack the machinery. We are ahead
because our defaults are closed and theirs are open.** That is a defensible
position to hold, but it is one config commit away from evaporating, and it is
not a feature anyone will pick a product for.

### Genuinely absent from Hermes

Things I looked for and did not find:

- **A cross-project / cross-profile supervision board.** Profiles run
  concurrently but there is no view of N of them at once, no per-project backlog,
  no agent-floor equivalent [DOC, SRC].
- **A structured multi-agent layer.** Delegation is per-turn fan-out with
  summary-only return [SRC]. There is no persistent workstream state, no
  inter-worker message bus, no shared knowledge/decisions/findings layer, no
  escalation path. Hivemind has no counterpart.
- **A fire-and-forget self-goal agent.** Their nearest analogue is cron plus
  suggestions, and suggestions are explicitly consent-first: "Suggestions never
  auto-create jobs; acceptance is always explicit" [SRC, `cron/suggestions.py`].
  Nothing sets its own next goal over a standing field of responsibility the way
  the steward does.
- **Deep curated memory.** Covered above. ~1,300 tokens of always-on memory
  against our topic-file corpus.
- **A built-in tunnel / remote-access story for the dashboard.** Their remote
  answer is "reach the agent through a messaging platform"; the only tunnel
  references in source are an SSH-tunnel *hint* for loopback OAuth on remote
  boxes and a `cache_exempt_hosts` comment [SRC]. Our Cloudflare tunnel plus
  mobile app plus web push is a different and, for a dashboard, better answer.
- **Standing positions / continuity state.** No equivalent to "we decided not to
  do X, and here is why" surviving into future sessions.

---

## 4. Adoption reality

All figures read **2026-08-30** unless dated otherwise.

### GitHub [API]

| Metric | Value |
|---|---|
| Stars | **238,501** |
| Forks | 48,570 |
| Watchers | 929 |
| Open issues | 12,838 |
| Open PRs | **25,102** |
| Closed issues | 12,144 |
| Merged PRs | 12,207 |
| Contributors (API pagination) | ~395 |
| Contributors (their claim, v0.20.0) | "650+" [DOC] |
| Created | 2025-07-22 |
| Last push | 2026-08-31T00:42:24Z |
| License | MIT |

Commit velocity, last 12 weeks [API, `stats/participation`]:
`845, 826, 1032, 1201, 847, 1271, 1619, 1907, 1287, 1412, 1550, 1144`.
Mean ~1,244 commits/week. Search API reports **6,572 commits since 2026-07-31**.

Release cadence: **30 releases from 2026-03-12 to 2026-08-27**, roughly one per
5-6 days, never a gap over 3 weeks [API]. Latest is `v2026.8.27` (v0.20.6),
published 2026-08-27.

The Aug 3 release (`v2026.8.3`, v0.20.0, "The Herald Release") self-reports
"~3,650 commits · ~1,400 merged PRs · ~5,200 files changed · ~559,000 insertions
· ~405,000 deletions · ~1,200 issues closed · 650+ contributors" since v0.19.0
[DOC, release body].

**The 25,102 open PRs are the number to stare at.** Twice the open-issue count
and more than the 12,207 they have ever merged. Combined with 48,570 forks, that
is a project taking contribution at a rate it cannot triage. Whether that reads
as vitality or as drowning, it is a real operational fact about the project.

### Hacker News [API, Algolia, queried 2026-08-30]

This is the most surprising result in the whole read.

**Hermes Agent itself has almost no HN traction.** Best-scoring story with
"Hermes Agent" in the title:

| Points | Comments | Date | Title |
|---|---|---|---|
| 52 | 42 | 2026-06-05 | Hermes Agent — Open-source AI agent with persistent memory |
| 28 | 0 | 2026-08-16 | Show HN: Grafana agent observability for Hermes Agent |
| 9 | 0 | 2026-04-29 | Tooling Up Hermes Agent |
| 8 | 1 | 2026-05-19 | Nous Research edits GitHub issue to remove plagiarism claims about Hermes Agent |
| 6 | 1 | 2026-06-24 | Hermes Agent can now /learn from anything |
| 5 | 1 | 2026-06-01 | Hermes Agent is now natively supported on Windows |

**The [2nd] claim that "the Hacker News thread about Hermes reached #1 with 1,064
points and 811 comments" in April 2026 is not supported.** No such story exists
in Algolia. What does exist, and what the claim appears to be a garbled memory of:

| Points | Comments | Date | Title |
|---|---|---|---|
| 1,349 | 720 | 2026-04-30 | Claude Code refuses requests or charges extra if your commits mention "OpenClaw" |
| **1,251** | **531** | **2026-04-29** | **HERMES.md in commit messages causes requests to route to extra usage billing** (→ `anthropics/claude-code#53262`) |
| 1,099 | 827 | 2026-04-03 | Tell HN: Anthropic no longer allowing Claude Code subscriptions to use OpenClaw |
| 802 | 705 | 2026-02-22 | Google restricting Google AI Pro/Ultra subscribers for using OpenClaw |
| 511 | 293 | 2026-04-21 | Anthropic says OpenClaw-style Claude CLI usage is allowed again |

**Read that carefully, because it is strategically important.** The largest
Hermes-related HN thread by a factor of 24 is not about Hermes being good. It is
about **Anthropic routing requests to extra billing when `HERMES.md` appears in
commit messages.** The category's attention in 2026 went to OpenClaw and to the
model vendors' countermeasures against third-party harnesses. Hermes is the
beneficiary of that wave (hence `hermes claw migrate` in the CLI [SRC]), not its
cause.

For Clayrune this cuts both ways. It says GitHub stars in this category are not
coming from HN and the HN launch playbook in `research-competitor-gtm-channels`
may be aimed at a channel that has moved on. It also says any harness that wraps
a vendor CLI is exposed to that vendor's policy, and Clayrune wraps Claude Code.

### Reddit / X

**[UNVERIFIED].** `reddit.com` returns a block page to our HTTP client and is
excluded from the search crawler's allowed domains, so I have no Reddit numbers
and will not guess at them. Same for X. Closing this gap needs a browser-pane
session or an API key; estimated 1-2 hours.

### Install numbers

**[UNVERIFIED].** No published install or DAU figure found. PyPI download stats
were not checked; that would be the cheapest proxy if it matters.

### Secondary sources — flagged, not used

The search results for this topic are heavily polluted. `hermesatlas.com`,
`hermes-ai.net` (note: **not** the official domain, which is
`hermes-agent.nousresearch.com`) and a MEXC crypto-news page all rank highly.
`hermesatlas.com` sourced the "57,200 stars in 6 weeks" and "crossed 100,000
stars" claims [2nd]; both are plausible given the current 238,501 but neither is
confirmed and I could not verify them from GitHub because the stargazers
endpoint returns 404 for this repo. One of those pages also mis-dated the Aug 3
Herald release as v0.20.2 (actually v0.20.0; v0.20.2 shipped 2026-08-16 [API]),
which is a good reason to discount the rest of it. **Star trajectory: unverified.**

### Comparison anchor

`ronle/clayrune`: **12 stars**, last push 2026-08-29 [API]. 1,083 commits, 2
git authors [SRC]. This is the ratio the positioning has to survive:
**~20,000:1 on stars.**

---

## 5. The positioning verdict

### Is "agent-persistence is the hook" still defensible?

**No. It is table stakes, and it has been for months.**

The locked positioning says the hook is agent-persistence via scheduler and
steward. Against Hermes:

- **Persistence**: their whole pitch is "an autonomous agent that gets more
  capable the longer it runs", with a gateway daemon, session lineage across
  compressions, and cross-session recall [DOC]. Shipped since at least March 2026.
- **Scheduler**: `cron/` with jobs, executions, monitoring, incidents, lifecycle
  guard and a blueprint catalog [SRC]. Delivery to any of 20+ platforms [DOC].
- **Unattended work**: background self-improvement review running by default
  after every turn [SRC].

This is not a new conclusion for this project. The 2026-08-29 memory
`research-anthropic-closed-the-category` already found that Anthropic shipped
remote control, `/goal`, self-paced `/loop` and Routines inside Claude Code
itself, and that both candidate launch hooks were now vendor features. **Hermes
is the second, independent confirmation of the same finding, from the
open-source side rather than the vendor side.** Two sources, one conclusion:
the hook is gone.

### The honest remaining differentiator

Three things survive scrutiny. Only the first is worth leading with.

**1. Clayrune supervises many agents; Hermes is one agent you talk to.**

This is the real difference and it is architectural, not a feature gap. Hermes
is a *personal* agent: one entity, one memory, one relationship, reachable from
wherever you are. Its profiles are parallel copies of that, not a fleet. Every
surface it has — CLI, TUI, desktop, 22 messaging platforms — is a way for **one
human to talk to one agent**.

Clayrune is the opposite shape. A project grid, a cross-project backlog, a
roster of named agent types, an agent floor, hivemind workstreams with a message
bus, per-project memory and standing positions. Every surface is a way for **one
human to oversee many agents across many projects**.

They will not close this by adding a feature; it would mean rebuilding around a
different noun. And it is the honest description of what Clayrune already is,
which makes it cheap to say and hard to dispute.

**2. Memory depth.** ~1,300 tokens of always-on memory against a curated corpus
with topic files, an archive, positions and continuity. If someone wants an
agent that *remembers the project* rather than *remembers the user*, we win on
the merits. This is provable in a demo in about forty seconds.

**3. Safe-by-default learning.** Real, and worth documenting, but weak as
marketing: it is a defaults argument, one config commit from parity, and nobody
adopts a tool for its fail-closed design. Keep it as an engineering commitment
and a trust signal, not a headline.

### Recommendation

**Re-point the launch from "agent-persistence" to "multi-agent, multi-project
supervision", and do it before any launch asset is cut.**

Concretely:

- Treat the hook change as a **blocker on `docs/LAUNCH_PLAN.md` v2**, not an
  edit to make afterwards. Anything built on the old hook (`LAUNCH_COPY.md`
  verified-claims table, `MARKETING_CAMPAIGN_PLAN.md` segments, the demo video
  spec) inherits a claim that a 238k-star competitor and the vendor both
  falsify. That is the expensive kind of wrong.
- The demo already shoots the right thing. The product cut is
  grid → streaming → approval → phone. **Grid is the hook now, not a
  warm-up shot.** Reorder the beats so the multi-project board leads and the
  single-session detail follows.
- **Do not lead on HN.** The category's HN attention in 2026 went to OpenClaw
  and to vendor policy fights, and Hermes at 238k stars could only muster a
  52-point Hermes-specific thread. Whatever HN was worth in the
  2026-08-02 GTM research, this is evidence it is not the channel for a
  Claude Code dashboard now.
- **Name the vendor-policy risk internally.** Two of the five biggest threads in
  this space are about Anthropic penalising third-party harness usage, one of
  them keyed on the literal string `HERMES.md` in commit messages [API]. Clayrune
  wraps Claude Code. That is a live exposure worth a written position, separate
  from this report.
- **Do not adopt or fork Hermes.** Confirmed independently by the parallel
  session on 2026-08-31. Nothing here changes that: it is a different product
  shape with 22× our code, and the parts worth having are extractable as ideas.

---

## 6. What to steal

Ranked by value per unit of effort. **Not filed as backlog items** per the brief;
Dave triages. Effort: **S** < 1 day, **M** 2-5 days, **L** 1-3 weeks.

### 1. Consent-first automation suggestions — **S** [SRC, `cron/suggestions.py`]

A suggestion is a ready-to-run job spec surfaced to the user, who accepts (creates
the real job) or dismisses (**latched so it is never re-offered**). Four sources:
curated catalog, skill-carried blueprint, usage pattern noticed by the background
review, and account-connection defaults. No second job engine; acceptance calls
`create_job` with the stored spec.

Why it is the top pick: Clayrune already has every input (Distiller sees
recurring asks, the scheduler exists, `_is_suppressed` is the latch). This wires
existing parts into a visible loop where the agent proposes automations and the
human taps yes. It is also the *safe* shape of self-improvement, which fits our
rails rather than fighting them.

### 2. Skill security scanning for imported skills — **M** [SRC, `tools/skills_guard.py`]

Trust-tiered static scan: `builtin` never scanned, `trusted` (a named allowlist)
may pass on caution, `community` blocked on any finding unless forced.
Fail-closed: a scanner crash quarantines. Detects exfiltration, prompt injection,
destructive commands, persistence, agent-config modification.

Clayrune imports skills from paste, folder, git, and plugin sources with **no
scanner at all**. Our authority guard covers what the Distiller writes; it does
not cover what a user pastes in. This is a real hole and their design is directly
portable. Note their own documented limitation so we do not inherit it: static
regex misses language-level write APIs (`open(...,'w')`, `pathlib.write_text`,
`fs.writeFileSync`) aimed at config files.

### 3. Prompt-injection hardening of our own bypass switches — **S** [SRC, `tools/approval.py:36`]

> "Freeze YOLO mode at module import time. Reading `os.environ` on every call
> would allow any skill running inside the process to set this variable and
> instantly bypass all approval checks — a prompt-injection escalation path."

Audit every Clayrune env var or config flag that relaxes a gate (steward fence,
`allow_unattended`, auto-approve paths) and freeze it at import. Cheap, and it
closes the exact class of hole our authority guard exists to prevent, on a path
the guard does not cover.

### 4. Signed outbound lifecycle webhooks — **M** [DOC, v2026.8.3]

HMAC-signed POSTs on session activity, turn completion, tool events, to any
registered endpoint. Clayrune has SSE and web push but no way for an external
system to subscribe without polling. This is the integration primitive that lets
someone wire Clayrune into CI or their own dashboard, which is exactly the
ecosystem the free-tier decision is meant to buy.

### 5. Grounded citations as a built-in skill — **M** [SRC, `skills/research/grounded-citations`]

Quotes verified against actual fetched page text rather than trusted from the
model, citations resolving to exact evidence, and a fact-check mode that reports
verified / contradicted / unverifiable. Given how much Clayrune work is research
and steward reporting, and given that this report itself is an argument for the
value of provenance, this is well-aimed. Their implementation is a skill, so it
is portable in shape if not in code.

### 6. `import-agent` — one-command migration in — **M** [DOC, v2026.8.3]

`hermes import-agent` pulls a Claude Code or Codex CLI setup into Hermes;
`hermes claw migrate` does the same from OpenClaw with `--dry-run` and presets.
Every Clayrune user already has a `~/.claude` directory with skills, settings and
MCP config. Importing it on first run turns a cold install into a warm one, and
removes the largest single objection to trying a new dashboard.

### 7. Two-tier memory: small hot set + on-demand session search — **L** [DOC]

Their FTS5 `session_search` over every stored session, returning real messages
rather than summaries, is a good complement to what we do. We have the transcript
data already. **Do not copy their caps** — our depth is a differentiator per §5 —
but adding cold search *underneath* the curated index would let the index stay
small without losing recall. This overlaps the deferred Step-7 semantic-search
decision; the point here is that FTS5 keyword search is far cheaper than
embeddings and might close most of the gap.

### 8. Serverless execution backend (Modal or Daytona) — **L** [SRC, `tools/environments/`]

Hibernate-when-idle, wake-on-demand agent environments. Flagged for completeness
because it is their strongest capability lead, but it points at hosted compute,
which is `clayrune_cloud`'s scope by the 2026-07-11 split, not this project's.
Listed so it is recorded, not proposed.

### Considered and rejected

- **22 messaging platforms.** Wrong shape for a supervision dashboard, and an
  enormous maintenance surface. Our tunnel + mobile + push already solves "reply
  from anywhere", which is the actual job.
- **A2A v1.0.** Real standard, real implementation, but it serves
  agent-to-*external*-agent interop. Clayrune's coordination need is
  agent-to-agent *inside* one operator's fleet, which hivemind's bus already
  covers. Revisit only if someone asks to drive Clayrune agents from an external
  orchestrator.
- **Voice with barge-in.** Impressive; orthogonal to supervision.

---

## Appendix: what I could not verify

Listed so nobody mistakes a hole for a finding.

| Question | Status | Cost to close |
|---|---|---|
| Star growth curve (when it hit 10k / 50k / 100k) | **UNVERIFIED.** GitHub `stargazers` returns 404 for this repo; the only figures found are from an unreliable secondary site | ~1h with a star-history service |
| Reddit reception | **UNVERIFIED.** reddit.com blocks our HTTP client and is not in the search crawler's allowed domains | ~1-2h via browser pane |
| X / Twitter reception | **UNVERIFIED.** Not attempted | ~1h |
| Install / DAU numbers | **UNVERIFIED.** None published that I found | PyPI stats would be the cheapest proxy |
| Contributor count: 395 (API) vs "650+" (their release notes) | **Discrepancy unresolved.** GitHub's contributors listing caps out, so the API figure is a floor, not a count | ~30min via GraphQL or `git shortlog` on a full clone |
| The 2026-05-19 "plagiarism claims" story (`hermes-agent#10232`) | **Not investigated.** 8 points, 1 comment, below the noise floor | ~30min if reputational risk matters |
| Whether `skills.write_approval` defaults differ in a packaged release vs source | **Not checked.** Docs say off by default; I did not install | ~1h |
