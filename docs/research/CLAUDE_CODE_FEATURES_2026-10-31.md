# Claude Code, 2026-08-29 to 2026-10-31: what shipped, and what is left for Clayrune

Quill, 2026-10-31. Window: Claude Code 2.1.252 (2026-08-31, first release after
the baseline) to 2.1.316 (2026-10-31). Baseline: the 2026-08-29 read in project
memory `research_anthropic_closed_the_category_2026-08.md`, which this report
updates.

Registers used throughout: **Found** (a source says it), **Reading** (my
inference), **Dark** (could not establish).

## Verdict

The 2026-08-29 conclusion does not hold as written. Its premise, that
Anthropic's autonomy is session-scoped, stopped being true on 2026-09-17, when
Projects made the project the unit: one coordinator conversation, parallel
threads, a project memory that every cloud thread reads, a scheduled-tasks tab,
and a conversation that "keeps going for as long as the project runs"
(code.claude.com/docs/en/claude-projects, read 2026-10-31).

A narrower version holds. Anthropic's continuity is cloud-side,
Anthropic-model-only and single-user. Local continuity is still thin: a local
Projects thread "starts with the project's instructions, but not with its memory
files loaded", and agent-view background sessions show as failed after a reboot.
No vendor surface I read has a persona roster, a backlog, standing rules that
govern unattended agents, a human approval step inside a workflow, or engines
other than Claude. That is Clayrune's remaining ground: governed, local,
multi-engine continuity. On the maintainer's install it is also the least-used
part of Clayrune.

## Top findings

1. **Projects (blog 2026-09-17) is the largest overlap in the window.**
   *Found:* "threads that do the work and a coordinator that directs them";
   "Every thread now adds to and draws from a shared memory"
   (claude.com/blog/projects-redesigned). The docs add an Overview pane with
   thread states (Ready for review, Waiting on you, Working, Landing, Idle,
   Resolved), PR auto-fix, a scheduled-tasks tab, and automatic context
   management: "You don't manage context windows in a project. Threads compact
   automatically, and the conversation works from recent messages, recent
   threads, and project memory rather than its full history, so it keeps going
   for as long as the project runs." *Reading:* that sentence is the
   vendor version of Clayrune's own 2026-09-18 continuity goal ("a new chat
   should already know what we did").

2. **Its gaps are documented, and they are exactly the local and governed
   half.** *Found (claude-projects docs):* public beta on Pro, Max and Ultra;
   "Projects aren't offered on Team or Enterprise plans yet"; cloud threads read
   each repository's `CLAUDE.md` "but not the repositories' skills,
   `.claude/settings.json`, or `.mcp.json`"; "Cloud threads don't pick up
   anything from the Claude Code setup on your own machine"; a local thread
   (via Remote Control, v2.1.280+) "Starts with the project's instructions, but
   not with its memory files loaded"; code must be on github.com; saved
   preferences "are instructions Claude keeps to, not enforced settings".
   *Found (agent-view docs):* after a reboot, "Within 48 hours, the session
   shows as failed".

3. **Clayrune's memory shares a file layer with Claude Code's auto memory, and
   agents get contradictory instructions about it.** *Found:* Clayrune's
   project memory file is Claude Code's native auto-memory path
   (`mc/memory.py:507`, `_native_memory_path` →
   `~/.claude/projects/<encoded>/memory/MEMORY.md`). Auto memory is on by
   default, uses the same four types (user, feedback, project, reference), and
   loads the first 200 lines or 25KB (code.claude.com/docs/en/memory, read
   2026-10-31). This session's own system prompt carries both Claude Code's
   instruction ("add a one-line pointer in `MEMORY.md`") and Clayrune's
   ("maintained for you by Clayrune ... do NOT hand-edit it"). The same prompt
   carried a loader warning, "MEMORY.md is 24.6KB (limit: 24.4KB) ... 1 of 143
   lines were cut off"; 24.4KB is 25,000 bytes, Claude Code's cap, and the
   string does not occur in Clayrune's code (grep of `mc/`, `server.py`,
   `static/`). *Reading:* the MEMORY.md header line "nothing truncates it,
   going over just costs tokens" is wrong; the vendor loader truncates it.

4. **Clayrune's Claude-side fence fails open when the hook itself fails.**
   *Found:* Claude Code 2.1.295 (2026-10-08): "Added `onFailure: "block"` for
   command and HTTP hooks: a hook that can't start, times out, or exits with an
   unexpected code blocks the action instead of letting it through." The
   steward fence hook Clayrune writes (`steward/core.py:285-288`) is
   `{"type": "command", "command": ..., "timeout": 10000}`, with no
   `onFailure`. *Reading:* if the fence's Python cannot start, unattended
   Claude runs proceed unfenced, the same failure class Clayrune already
   documented for Codex (`mc/guardrail_hooks.py:363-370`, where no config
   fixes it). On Claude Code a one-line setting does.

5. **All four rows of the README's "Bare Claude Code vs Clayrune" table are now
   contradicted by Anthropic's docs** (`README.md:141-144`). "One repo, one
   terminal": agent view and the Desktop sessions sidebar run many sessions.
   "You're at the keyboard": `--bg`, Desktop scheduled tasks, cloud scheduled
   tasks. "Session ends, context dies": auto memory, on by default since
   2.1.59 (2026-02-25), plus Projects. "Desk-bound": Remote Control with mobile
   push. Details and the other stale lines are in [Stale copy](#stale-copy).

6. **The surfaces that are still differentiated are the least used on the
   maintainer's install** (measured 2026-10-31 in the main checkout's `data/`).
   Clayrune workflows: 1 defined, 36 runs (31 by schedule). Steward: on in 1
   of 23 projects. Hivemind: 26 runs on disk, newest file activity 2026-09-30,
   none in October. Scheduler: 48 schedules, 15 enabled, last runs up to
   2026-10-31. *Reading:* one install and one user; this shows how Clayrune is
   used here, not how other users use it.

7. **Agent Teams has not moved.** *Found (agent-teams docs, read 2026-10-31):*
   still "experimental and disabled by default"
   (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`), interactive only, teammates not
   restored after resume. The 2026-08-29 rating stands.

## Recommendations

Ordered by value for cost. Kinds follow the brief: adopt or wrap the vendor
feature, drop or deprioritise ours, or double down.

| # | Kind | What | Cost | Evidence |
|---|---|---|---|---|
| 1 | Adopt | Add `"onFailure": "block"` to the Claude fence hook in `steward/core.py` and a test that a hook which cannot start blocks the call. | About 1 hour. Trade-off: a broken interpreter path then blocks every tool call in a fenced run, which is the intended direction. `steward/fence.py` is human-owned; this change is in `core.py`, not `fence.py`. | Finding 4 |
| 2 | Decide, then adopt or wrap | Give the memory index one owner. Either Claude Code writes and Scribe curates on top, with Clayrune's prompt saying so, or Clayrune-launched sessions turn auto memory off (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` or `autoMemoryEnabled: false`) and Clayrune delivers memory itself. Either way, size the index to the vendor's 25,000-byte load cap and correct the MEMORY.md header. | Half a day to decide; 1 to 2 days to build either option. Memory work runs on Fable per standing feedback. | Finding 3 |
| 3 | Fix | Correct the factual claims about Claude Code in `README.md`, `docs/LAUNCH_COPY.md`, `docs/LAUNCH_PLAN.md` (both local-only, gitignored) and clayrune.io (list below). This is accuracy, not positioning: what the launch hook should be stays Ron's call (MC-911 standing position, declined 2026-09-10; its reopen condition has not fired). | 1 to 2 hours of edits. Site copy goes through the steward-owned website project. | Finding 5, [Stale copy](#stale-copy) |
| 4 | Adopt, test first | Pass `--system-prompt-snapshot off` on Claude resume paths, behind a config key. Help text (installed 2.1.312): with the default `on`, "every later request and resume sends the record as-is, even when a later launch passes different text, until the conversation is compacted"; `off` renders the prompt fresh every request. That is the documented cause of Clayrune's "`claude -r` ignores `--append-system-prompt`" class and the persona-lost-on-revive bugs. | Half a day plus a regression test. Unknown: prompt-cache cost when the appended text differs between launches. Not tested here. | 2.1.267 (2026-09-09) |
| 5 | Adopt | Set the unattended retry caps on runs that already get `CLAUDE_CODE_RETRY_WATCHDOG` (`mc/launch_marker.py:38`): `..._MAX_WAIT_MS` (2.1.295), `..._MAX_USAGE_LIMIT_WAIT_MS` (2.1.301), `..._USAGE_LIMIT_RECHECK_MS` (2.1.305). Clayrune sets none of them today. | About 1 hour. | Changelog 2026-10-08 to 2026-10-20 |
| 6 | Deprioritise | Stop growing hivemind as a fan-out engine. Vendor workflows run 16 agents concurrently by default (1 to 256), up to 1000 per run, and Clayrune already reads their journals for live progress (`static/js/agent-console.js:541`). Keep what only hivemind does: per-worker worktrees, serial smoke-gated integration onto one branch, non-Claude workers. | Zero to decide. | Workflows docs; hivemind idle since 2026-09-30 |
| 7 | Double down, conditionally | Governed unattended work (steward charter, fence, email decisions, workflow approval nodes, revival after reboot) is the one area with no vendor equivalent. Before investing, find out why the steward runs on 1 of 23 projects here. | A usage review first: an hour. | Findings 2 and 6 |

## What shipped in the window, by theme

Method: `CHANGELOG.md` from `anthropics/claude-code`, fetched through `gh api`
on 2026-10-31: 3,578 entries in the window, 374 of them feature-level (347
Added, 14 Removed, 7 Reverted, 4 Bug, 1 Security, 1 Deprecated). npm lists 55
versions in the window; GitHub Releases lists 54 (2.1.315 is npm-only). Docs
pages at code.claude.com read on 2026-10-31. Installed CLI on this machine:
2.1.312 (`claude --version`); npm latest: 2.1.316 (2026-10-31).

### Parallel agents and orchestration

- **Agent view and background sessions** (`claude agents`, `--bg`; research
  preview; first shipped 2.1.50, 2026-02-20). In the window: attach and logs by
  name (2.1.290, 10-05), peek panel and `/effort`/`/fast` inside agent view
  (2.1.299, 10-14), Ctrl+F (2.1.288), `n:` filter (2.1.287). Docs: a supervisor
  keeps sessions running after the terminal closes; sleep is preserved; a
  session moves into its own worktree under `.claude/worktrees/` before editing
  (`worktree.bgIsolation`); a reboot stops them; `claude respawn` restarts
  them.
- **Dynamic workflows** (first shipped 2.1.152, 2026-05-26). Docs: Claude writes
  a JavaScript script that runs many subagents and cross-checks them; saved
  scripts become `/name` commands; bundled `/deep-research`; the `ultracode`
  keyword; 16 concurrent agents by default, 1 to 256 via
  `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (2.1.269, 09-11); 1000 agents
  per run; resumable within the same session; "No mid-run user input ... For
  sign-off between stages, run each stage as its own workflow"; the keyword is
  not honoured in `-p`, scheduled-task or webhook prompts; no visual editor. In
  the window: `/workflows` list (2.1.281, 09-23), workflow subagent model
  variable (2.1.296), workflow agents in the `agent.spawn` mod hook (2.1.292,
  2.1.300), `/usage` warns on workflows (2.1.299).
- **`/batch`** (5 to 30 worktree subagents), `/fork`, `/subtask`: listed on
  the agents overview page (code.claude.com/docs/en/agents, read 2026-10-31),
  which names five parallel approaches: subagents, agent view, agent teams,
  Projects, dynamic workflows.
- **Agent Teams**: unchanged status (finding 7). In the window, `agent.spawn`
  covers teammates (2.1.289, 10-03).
- **Cross-session messaging** (first shipped 2.1.77, 2026-03-16; native Windows
  from v2.1.234). Docs: list and message sessions on this machine (socket or
  named pipe), on other machines and in the cloud (through Remote Control); an
  idle receiver "starts a new turn with the message"; `notify_when_idle` is
  local only and expires after 12 hours; messages cannot approve prompts or
  change config; 50 queued. The `ListAgents` and `SendMessage` tools are present
  in this session.
- **Subagent controls**: `CLAUDE_CODE_SUBAGENT_EFFORT` (2.1.316), Agent tool
  `effort` parameter (2.1.292), subagent `autoCompactWindow` (2.1.296),
  `--append-subagent-system-prompt-file` (2.1.261), `omitClaudeMd` agent
  frontmatter (2.1.271), `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` (2.1.257).
- **Managed Agents**: `/claude-api` now builds subagent teams on
  `multiagent_20261001` with dynamic workflows on by default (2.1.309, 10-22).
  API-side, not a CLI surface.

### Projects, cloud and desktop

- **Projects**: findings 1 and 2. Also from the docs: available at
  claude.ai/code, the Desktop Code tab and the Claude mobile apps; 500 new
  threads per day; Anthropic models only; one user, no sharing; a thread that
  hits a usage limit waits and resumes on reset (scheduled-task threads do not);
  a sandbox pause can lose uncommitted work.
- **Desktop Code tab** (code.claude.com/docs/en/desktop): parallel sessions
  sidebar, opt-in worktree per session, split view, pop-out panes, a browser
  pane (screenshots, DOM, click, fill), terminal and file editor, diff line
  comments, side chats, suggested tasks with "Start all", OS notifications,
  Dispatch (Pro, Max and Ultra). `claude --desktop` (2.1.285, 09-29). Idle
  compaction in Desktop and VS Code (2.1.314, 10-30).
- **Remote Control** (first shipped 2.1.51, 2026-02-23). Docs: server mode
  `claude remote-control --spawn same-dir|worktree|session --capacity N`
  (default 32); outbound HTTPS only; Trusted Devices with biometric step-up;
  mobile push notifications; photos and files from the phone; Pro, Max, Ultra,
  Team and Enterprise; API keys not supported, nor Bedrock, Vertex or a custom
  `ANTHROPIC_BASE_URL`; the local process must keep running. In the window:
  fork a Remote Control session from the Claude app into a background session
  (2.1.273, 09-15), `--sandbox` flags removed (2.1.300), Enterprise roles gate
  cloud sessions and Remote Control (2.1.310), "Catching up…" state (2.1.316).
- **Cloud sessions**: fast mode in cloud and on self-hosted runners (2.1.271),
  built-in `gh api` (2.1.288), Personal and Org environments (2.1.277), MCP
  form card (2.1.316). Self-hosted runners and `--environment` matter to the
  hosted product, which lives in its own project; not assessed here.
- **A revert to know about**: 2.1.293 (10-07) reverted the 2.1.290 fix for
  cloud sessions that stay asleep after a container restart loses a pending
  `/loop` wakeup or scheduled task: "Claude is no longer told, and the session
  stays asleep".

### Scheduling and unattended work

- **Desktop scheduled tasks** (code.claude.com/docs/en/desktop-scheduled-tasks):
  local tasks in the Desktop app; fire only while the app is open and the
  machine awake; on wake, one catch-up run for the most recent miss within 7
  days; per-task model, permission mode, folder and optional worktree; a task
  can reschedule itself through `update_scheduled_task`; the docs' comparison
  table marks them persistent across restarts. *Dark:* first release date.
- **Cloud scheduled tasks** (formerly routines; code.claude.com/docs/en/routines;
  research preview): schedule, API and GitHub-event triggers; a fresh clone per
  run; 1-hour minimum interval; locally added MCP servers not available; 100
  scheduled runs per hour per account. In the window: effort level (2.1.312),
  "Include completed" (2.1.314), a banner when a run did not start because of
  the org's concurrent-session limit (2.1.302).
- **`/goal` and `/loop`**: an active `/goal` is no longer lost on resume after
  compaction (2.1.274, 09-16); `/loop` interval tasks come back on resume after
  compaction (2.1.290, 10-05); self-paced one-hour wakeups no longer miss the
  prompt cache (2.1.304).
- **Unattended plumbing**: `--permission-prompts none` for unattended headless
  hosts (2.1.259, 09-02); retry-watchdog caps (2.1.295, 2.1.301, 2.1.305) and
  fixes (2.1.281, 2.1.288, 2.1.311); hooks `onFailure: "block"` (2.1.295);
  `permissions.blockWritesOutsideWorkingDirectories` (2.1.314); idle compaction
  in the terminal (2.1.299, 10-14): "a 200K+ token conversation left idle for
  about 54 minutes is compacted before its one-hour prompt cache expires";
  `--system-prompt-snapshot off` (2.1.267, 09-09).

### Memory

- **Auto memory** (code.claude.com/docs/en/memory; first shipped 2.1.59,
  2026-02-25, before the window): on by default for local sessions; types
  user, feedback, project, reference; `MEMORY.md` index plus topic files; first
  200 lines or 25KB loaded; machine-local, not shared with cloud; worktrees
  share it; off by default in background sessions spawned by another session
  and on self-hosted runners; subagents get their own through a `memory` field.
- **In the window**: VS Code Memory dialog and Customize > Memory (2.1.274 and
  2.1.275, 09-16 and 09-17); claude.ai skills and plugins sync to terminal
  sessions (2.1.275); Projects project memory (09-17), which the docs say is
  "separate from the auto memory Claude Code keeps on your machine, even though
  both use a `MEMORY.md` index"; Claude Tag workspace and channel memory files
  editable (2.1.296); a resume warning (2.1.304, 10-19) that offers to "start a
  new conversation and Claude can look up the previous one if you refer to it".
  *Dark:* the mechanism behind that lookup.

### Extensibility

- **Claude Mods** (2.1.287, 10-01; code.claude.com/docs/en/plugins/mods/overview):
  plugins with JS or TS handlers that run inside Claude Code, draw panes, bands
  and buttons, redraw its UI, hold, answer or reroute tool calls, run commands
  without a Claude turn, call a model, set timers and message other sessions;
  on by default; org setting `allowManagedModsOnly`. Built-in "You should know"
  mod: a side agent that "watches your back and flags things you or Claude
  might miss". Also `$.agent.list()` idle and waiting states (2.1.289),
  `$.ui.view` (2.1.300), `$.ui.notify` and the OSC 7501 Program Status Protocol
  (2.1.295). 119 changelog entries in the window.
- **Claude Tag** (Slack): from 2.1.267 (09-09), 227 changelog entries in the
  window. *Dark:* not read beyond the changelog.
- **AGENTS.md** is read when a project has no `CLAUDE.md` (2.1.277). Clayrune
  writes `AGENTS.md` for Codex, but every Clayrune project with a `CLAUDE.md`
  is unaffected: zero findings.
- **`claude import`** imports config from another AI coding tool (seen in
  `claude --help`; not tested).

### Review

- **Code Review**: Quick, Standard and Deep levels plus review-all-repos
  (2.1.304), follow-up reviews (2.1.268), `@claude review stop` (2.1.307),
  severity analytics (2.1.316), `/code-review --max-findings` (2.1.288),
  `claude ultrareview`. No Clayrune surface overlaps: zero findings.

### Outside Claude Code, same direction

- **Claude Duet** (claude.com/resources/articles/claude-duet-safety-privacy-and-security,
  2026-10-23): a research-preview "proactive agent that works for you, even
  while you're away", on a cloud computer; keeps conversation history and
  memory between tasks; stops to ask before important actions. *Reading:* the
  concept of Clayrune's steward, aimed at errands (inbox, booking), not code.

## Mapping to Clayrune surfaces

Clayrune facts below were checked in code at `22412273` (2026-10-30) and in
the live config on 2026-10-31.

| Clayrune surface | Overlapping vendor features | Rating | What decides it |
|---|---|---|---|
| Hivemind (`mc/blueprints/hivemind_routes.py`, `mc/hivemind_integration.py`) | Dynamic workflows, `/batch`, Agent Teams, Projects coordinator, Managed Agents | Partly overlapping; redundant for plain fan-out | Vendor fan-out is bigger and built in. Hivemind's own parts: each worker in its own worktree, serial merge into `hivemind/<id>` with each workstream's smokes after every merge, stop at the first conflict, never touch `master`, non-Claude workers. |
| Dispatch and the Floor (`mc/blueprints/floor_routes.py`, delegation wake MC-946 and MC-1063) | Agent view, cross-session messaging, Projects Overview, Desktop sessions sidebar | Partly overlapping | Vendor views are per machine (agent view) or per cloud project (Projects), Claude only. The Floor is one read-only call over every project's sessions with the whole roster on the bench, across 8 engines. Vendor messaging starts a new turn on an idle receiver; Clayrune's delivery store retries with backoff and parks blocked or uncertain reports for a person. |
| Worktree agents (`mc/agent_worktree.py`) | Agent-view auto-isolation, Desktop per-session worktrees, Remote Control `--spawn worktree`, `/batch`, `--worktree` | Redundant as a selling point; still needed as plumbing | Isolation is now a vendor default. Clayrune's version links gitignored runtime dirs back into the worktree and rewrites `.mcp.json` paths. *Dark:* whether vendor worktrees do either. |
| Scheduler (`mc/blueprints/scheduler_routes.py`) | Desktop scheduled tasks, cloud scheduled tasks, `/loop` | Partly overlapping | Desktop tasks need the app open and the machine awake; cloud tasks clone fresh, 1-hour minimum, no local MCP servers. Clayrune: headless server that starts at boot, five schedule types, run conditions (for example, spend leftover weekly allowance), a slot claim against double-firing, workflow targets, 8 engines. |
| Steward (`steward/core.py`, fence) | `--permission-prompts none`, `onFailure: "block"`, `blockWritesOutsideWorkingDirectories`, retry watchdog; Claude Duet as a concept | Still differentiated | No vendor surface found gives an agent a standing charter, a fence on irreversible commands, an email decision channel and a cadence (default 180 minutes, 30 to 1440). Anthropic shipped the primitives; Clayrune uses one of them (the retry watchdog). |
| Scribe memory (`mc/memory.py`, `mc/memory_*.py`, `mc/distiller.py`) | Auto memory, Projects project memory, Claude Tag memory, past-conversation lookup, Projects automatic compaction | Partly overlapping, on a shared file layer | Distinct: Scribe curation, read floor, condense, delivery telemetry, the learning safety rails, delivery to non-Claude engines (`GEMINI.md`, `AGENTS.md`). Conflict: finding 3. |
| Personas and roster (`mc/characters.py`) | Custom subagents (`--agents`, agent frontmatter), Agent Teams roles | Still differentiated | Nothing new in the window beyond subagent knobs. No vendor roster with faces, voices, per-character engines or hiring onto projects found. |
| Backlog (`mc/github_sync.py` two-way Issues sync) | Desktop suggested tasks, Agent Teams shared task list | Still differentiated | Vendor task lists live inside a session or a team run; no persistent prioritised backlog found. |
| Browser pane (`mc/blueprints/browser_routes.py`) | Desktop browser pane, Claude in Chrome (`--chrome`) | Partly overlapping | Desktop pane: screenshots, DOM, click, fill. Clayrune: named persistent profiles, a read envelope that marks page text as untrusted, hidden-text stripping, reachable from a phone over the tunnel. *Dark:* whether the Desktop pane keeps sign-ins (this session's skill list says so; the docs pages read did not). |
| Remote access (`mc_remote_iface`, tunnel, phone app, web push) | Remote Control server mode, Trusted Devices, mobile push, forking from the app, Projects on mobile | Redundant for Claude sessions on claude.ai plans; differentiated for API-key, Bedrock and Vertex users and for non-Claude engines | Remote Control does not support API keys or custom base URLs. Conceded for Claude.ai plans on 2026-08-29; the window widened it. |
| The Desk (`mc/desk_*.py`) | None found | Still differentiated | No social publishing pipeline in the changelog or the docs read. Claude Duet is the nearest, and it does errands. |
| Clayrune workflows (`mc/workflows.py`, `NODE_TYPES = ('agent', 'approval', 'action', 'wait')`) | Dynamic workflows | Still differentiated | Vendor: "No mid-run user input", scripts only. Clayrune: approval node, canvas, schedule trigger. Usage: finding 6. |

Live config on this install (2026-10-31): `worktree_isolation_enabled` true
(code default false), `agent_remote_control` false, `coordination_enabled`
true, `continuity_enabled` true, `scribe_enabled` true, `desk_v1` true,
`scheduler_paused` false; engines configured: aider, claude, codex, gemini,
goose, kiro, opencode, qwen.

## Stale copy

These are factual claims about Claude Code that its own docs now contradict.
Whether to change the launch hook is a separate question and is not reopened
here (MC-911 standing position).

| Where | Claim | What contradicts it |
|---|---|---|
| `README.md:141-144` (table "Bare Claude Code / Clayrune"; file last changed 2026-10-06) | "One repo, one terminal"; "You're at the keyboard"; "Session ends, context dies"; "Desk-bound" | Agent view and the Desktop sessions sidebar; `--bg`, Desktop and cloud scheduled tasks; auto memory (default on since 2026-02-25) and Projects; Remote Control with mobile push. |
| `docs/LAUNCH_PLAN.md` §1.3, near line 85 (local file, gitignored since `4fd7dc4f` on 2026-07-28; last modified 2026-07-13) | Leads on unattended agents (scheduler, steward, hivemind) as something no competitor has. Paraphrased: the file was taken out of the public tree as internal. | Its competitor set leaves out Anthropic, which ships background sessions, two kinds of scheduled tasks and Projects threads. First flagged 2026-08-29; still unedited. |
| `docs/LAUNCH_COPY.md:41`, `:98`, `:123` (local file, gitignored by the same commit; last modified 2026-07-12) | Three lines of draft launch copy present phone access as the part people will not expect. Paraphrased, same reason. | Remote Control with mobile push is standard on Pro, Max, Ultra, Team and Enterprise. |
| clayrune.io (fetched 2026-10-31, v2.0) | "A MEMORY.md per project. Conventions and context persist across sessions." | True, but it describes Claude Code's default auto memory, in the same file. Not false; not a difference. |
| clayrune.io | "Cron and interval routines" | "Routines" is Anthropic's former name for its cloud scheduled tasks; the word now points at the vendor feature. |
| clayrune.io | "Reply from anywhere" (hero) | Accurate for Clayrune; for Claude sessions on claude.ai plans the vendor does the same (finding 5). |

Still accurate and not stale: `README.md:43-45` (fence for unattended Claude
Code and Codex runs; blocked commands and questions emailed, the reply resumes
the run; "Starts with the machine, so a 3am reboot doesn't take it offline"),
and the site's "Watch live Claude Code Workflow progress", which
`static/js/agent-console.js:541` implements from the on-disk journals.

## Dark: what I could not establish

- Adoption or usage numbers for any vendor feature (Projects, agent view,
  workflows, Mods).
- When Desktop scheduled tasks first shipped, and whether that predates the
  window.
- Whether vendor worktrees link gitignored dependencies or rewrite `.mcp.json`.
- Whether the Desktop browser pane keeps sign-ins across restarts.
- How "Claude can look up the previous one" (2.1.304) works.
- Whether Clayrune-launched sessions count as "spawned by another session" for
  auto memory's background default, which decides whether auto memory is even
  on in dispatched agents.
- Whether idle compaction (2.1.299) fires in Clayrune's long-lived stream-json
  sessions, and how it interacts with Clayrune's own 200K-token rollover.
- Claude Tag beyond its changelog entries.
- Dated Anthropic launch posts for agent view, workflows or Mods in the window:
  a search found none; the weekly what's-new page for 2026-W43 returned 404.
  Projects (2026-09-17) and Claude Duet (2026-10-23) are the only dated posts
  found.
- `--system-prompt-snapshot off` on `claude -r`: behaviour taken from the help
  text, not tested.

## Sources (all read 2026-10-31 unless dated otherwise)

- github.com/anthropics/claude-code `CHANGELOG.md` and Releases (via `gh api`);
  npm registry `@anthropic-ai/claude-code` publish times.
- code.claude.com/docs/en/: `claude-projects`, `agents`, `agent-view`,
  `agent-teams`, `workflows`, `memory`, `cross-session-messaging`, `routines`,
  `desktop-scheduled-tasks`, `remote-control`, `desktop`, `sessions`,
  `plugins/mods/overview`, `whats-new/index`.
- claude.com/blog/projects-redesigned (2026-09-17).
- claude.com/resources/articles/claude-duet-safety-privacy-and-security
  (2026-10-23); claude.com/blog index.
- clayrune.io home page.
- Installed CLI: `claude --version` (2.1.312) and `claude --help`.
- Clayrune code at `22412273`; live `/api/config`; counts from the main
  checkout's `data/` (one install, one user).
