# Clayrune API Reference

Curated surface for agents running **inside** Clayrune. Use these endpoints
instead of guessing or curl-probing. Host: `http://localhost:{PORT}` (port is
injected separately into your system prompt).

All endpoints return JSON unless noted. Paths use `<project_id>` for the
project you are running in (also injected into your prompt).

---

## Projects

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/projects` | List all projects + state (live_agent, last_updated, …). |
| POST | `/api/project/<project_id>` | Create or update a project. Body is the project dict. |
| DELETE | `/api/project/<project_id>` | Delete a project (records only; not the workspace). |
| POST | `/api/project/<project_id>/generate_summary` | Re-generate the project's one-line summary from recent activity. |
| GET | `/api/project/<project_id>/scribe-stats` | Memory-Scribe telemetry: extracted/checkpoint/skipped counters. |
| POST | `/api/project/<project_id>/import` | Import legacy session/data into a project. |

## Project memory (MEMORY.md + archive)

`MEMORY.md` lives at the user-memory path (`~/.claude/projects/<encoded>/memory/MEMORY.md`).
It is curated + machine-managed; do NOT hand-edit the managed region.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/project/<project_id>/memory` | Read the full MEMORY.md as text. |
| PUT | `/api/project/<project_id>/memory` | Replace MEMORY.md (rare; UI uses this). |
| POST | `/api/project/<project_id>/memory/append` | Append a session-log entry programmatically. |
| GET | `/api/project/<project_id>/memory/search?q=…&k=N` | Ranked memory search (topic files + archive + log). Prefer the `mc-memory-search` skill. |

## Rules

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/project/<project_id>/rules` | Read AGENT_RULES.md for this project. |
| PUT | `/api/project/<project_id>/rules` | Write AGENT_RULES.md (`{"text": "..."}`). |
| GET | `/api/rules/shared` | Read SHARED_RULES.md (applies to all projects). |
| PUT | `/api/rules/shared` | Write SHARED_RULES.md. |

## Backlog (per-project)

When the user says "backlog", "the list", "todo", they mean THIS — never grep
the filesystem.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/project/<project_id>/backlog` | List items (with statuses, notes, attachments). |
| POST | `/api/project/<project_id>/backlog` | Add an item (`{"text":"...","priority":"..."}`). |
| PATCH | `/api/project/<project_id>/backlog/<item_id>` | Update fields (status, text, priority). Status values: `open`, `in_progress`, `blocked`, `done`. |
| POST | `/api/project/<project_id>/backlog/<item_id>/note` | Append a note (`{"text":"..."}`). |
| DELETE | `/api/project/<project_id>/backlog/<item_id>` | Remove an item. |
| POST | `/api/project/<project_id>/backlog/<item_id>/attachments` | Upload a file to an item (multipart). |
| DELETE | `/api/project/<project_id>/backlog/<item_id>/attachments/<att_id>` | Remove an attachment. |

## Agent control (this project)

You usually do NOT need these from inside a session — they exist so other tools
can drive a project's agent. Listed for completeness.

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/project/<project_id>/agent/dispatch` | Start a fresh agent run. |
| POST | `/api/project/<project_id>/agent/send` | Send a message to the running agent. |
| GET | `/api/project/<project_id>/agent/stream` | SSE stream of agent events. |
| POST | `/api/project/<project_id>/agent/followup` | Resume after an asked question. |
| POST | `/api/project/<project_id>/agent/stop` | Stop the running agent (clean). |
| POST | `/api/project/<project_id>/agent/interrupt` | Hard interrupt (force). |
| DELETE / POST | `/api/project/<project_id>/agent/session` | Reset the live session. |
| GET | `/api/project/<project_id>/agent/status` | Current run state (status, claude_session_id, queued msgs). |
| POST | `/api/project/<project_id>/agent/guardian-reset` | Clear stuck-guardian flag if the agent is wedged. |
| GET | `/api/project/<project_id>/agent/plan-file` | Read the live plan file for the running agent. |

## History / transcripts / runs

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/project/<project_id>/agent/log` | Past run log (completion records + summaries). |
| GET | `/api/project/<project_id>/transcript/<claude_session_id>` | Full Claude Code transcript (jsonl, parsed). |
| GET | `/api/project/<project_id>/session/<session_id>/reconstruct` | Reconstruct a prior session view from MC's records. |
| GET | `/api/project/<project_id>/conversations` | Indexed list of prior conversations. |
| GET | `/api/project/<project_id>/plans` | List plan files for this project. |
| GET | `/api/plan-file?path=…` | Read a specific plan file. |
| GET | `/api/recent-runs?limit=N` | Cross-project recent runs feed (schedule/hivemind/manual). |

## Terminal pop-out

Use for ANY long-running visual command the user should see (servers,
dashboards, watchers). Surfaces in MC's pop-out terminal with full ANSI.

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/terminal/launch` | `{"project_id":"...","command":"..."}` — launch a pop-out terminal. |
| GET | `/api/terminal/stream?session_id=…` | SSE stream of terminal output. |
| POST | `/api/terminal/stdin` | Send keystrokes to a terminal session. |
| POST | `/api/terminal/stop` | Stop a session. |
| GET | `/api/project/<project_id>/terminal/status` | Status of this project's terminals. |
| POST | `/api/terminal/delete` | Remove a session record. |

## Browser pane

Clayrune's built-in Chromium, rendered live in the user's dashboard (and on
their phone over the tunnel). **Use this instead of opening the host's browser**
(`start` / `open` / `xdg-open` / `webbrowser.open` / a local Playwright window)
whenever a task means visiting a web page.

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/browser/launch` | `{"project_id":"...","url":"https://…","profile":"name"?,"ephemeral":true?}` → `{session_id, url, profile, reused}`. |
| POST | `/api/browser/input` | Drive it: `{"session_id":…,"type":"navigate\|mouse\|wheel\|text\|key\|back\|forward\|reload", …}`. |
| POST | `/api/browser/selection` | `{"session_id":…}` → the page's currently selected text. |
| GET | `/api/project/<project_id>/browser/status` | Live sessions for this project. |
| POST | `/api/browser/stop` | End a session. |
| GET | `/api/browser/profiles` | Saved signed-in profiles (names/sizes only, never cookies). |
| DELETE | `/api/browser/profiles/<name>` | Forget a profile — **this is the sign-out. Destructive; ask first.** |

Surfacing it to the user (chat markers, each on its OWN line):

- `[browser:https://example.com]` — open the pane on that URL. Simplest path
  when you just need the user to see a page.
- `[browser-attach:<session_id>]` — attach the pane to a session you already
  launched via the API, so the user watches what you're driving.

Notes:

- **Unnamed launches are throwaway** — logged out every time. Pass `profile`
  for any site you sign into; the profile persists across sessions/restarts.
  Naming an already-open profile *adopts* that session (`reused: true`).
- It is a viewing/interaction surface, **not a scraper** — no read-whole-page
  endpoint (only the selection). Use WebFetch/WebSearch to read content for
  your own reasoning.

## Processes (manager)

**MANDATORY**: every long-running process you spawn MUST be registered here
or the user cannot see / stop it.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/processes` | List registered processes. |
| POST | `/api/processes/register` | `{"pid":NUM,"name":"...","project_id":"...","command":"..."}` |
| POST | `/api/processes/<pid>/kill` | Kill by PID. |
| POST | `/api/processes/cleanup` | Sweep dead PIDs from the registry. |

## Hivemind (multi-agent)

Launch coordinated multi-agent analysis. Always ask the user clarifying
questions about scope/priorities/constraints BEFORE creating.

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/hivemind/create` | `{"project_id":"...","goal":"...","max_concurrent_workers":3,"orchestrator_model":"sonnet","worker_model":"sonnet"}` |
| GET | `/api/hivemind/list` | List hiveminds. |
| GET | `/api/hivemind/<hivemind_id>` | Get a hivemind's state. |
| PUT | `/api/hivemind/<hivemind_id>` | Update fields. |
| POST | `/api/hivemind/<hivemind_id>/start` | Start the hivemind. |
| POST | `/api/hivemind/<hivemind_id>/pause` | Pause workers. |
| POST | `/api/hivemind/<hivemind_id>/stop` | Stop and finalize. |
| DELETE | `/api/hivemind/<hivemind_id>` | Delete. |
| GET | `/api/hivemind/<hivemind_id>/workstreams` | List workstreams. |
| POST | `/api/hivemind/<hivemind_id>/workstreams/create` | Create a workstream. |
| PUT | `/api/hivemind/<hivemind_id>/workstreams/<ws_id>` | Update a workstream. |
| POST | `/api/hivemind/<hivemind_id>/workstreams/<ws_id>/status` | Set status (open / running / completed / blocked). |
| POST | `/api/hivemind/<hivemind_id>/workstreams/<ws_id>/spawn` | Spawn a worker agent for this workstream. |
| POST | `/api/hivemind/<hivemind_id>/workstreams/<ws_id>/handoff` | Submit handoff document (required before completion). |
| GET/POST | `/api/hivemind/<hivemind_id>/bus/[post\|poll/<ws>\|history\|stream]` | Inter-worker message bus. |
| GET/PUT | `/api/hivemind/<hivemind_id>/knowledge/[synthesis\|decisions\|findings]` | Shared knowledge layer. |
| POST | `/api/hivemind/<hivemind_id>/knowledge/questions/<qid>/resolve` | Resolve a worker question. |
| POST | `/api/hivemind/<hivemind_id>/escalate` | Escalate to user. |
| POST | `/api/hivemind/<hivemind_id>/intervene` | Inject a directive mid-run. |
| POST | `/api/hivemind/<hivemind_id>/findings/<fid>/review` | Review a finding. |
| POST | `/api/hivemind/<hivemind_id>/decisions/<did>/approve` | Approve a proposed decision. |
| GET | `/api/hivemind/<hivemind_id>/runs` | Run history for this hivemind. |

## Schedules (local — long-term, repeatable)

For LONG-TERM jobs that must outlive any single session. For short-interval
polling inside the current session, use the cloud `/schedule` skill instead.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/schedules` | List schedules. |
| POST | `/api/schedules` | Create. Body: `{"project_id":"...","task":"...","schedule_type":"daily\|weekly\|interval\|once\|cron","time":"09:00","days":[],"interval_minutes":60,"run_at":"ISO8601","cron_expr":"..."}` |
| PUT | `/api/schedules/<schedule_id>` | Update. |
| DELETE | `/api/schedules/<schedule_id>` | Delete. |
| POST | `/api/schedule/<schedule_id>/run-now` | Fire immediately (one-shot). |
| GET | `/api/schedule/<schedule_id>/runs` | Run history. |

## Skills (Anthropic-format)

Clayrune owns skills at `~/.claude/skills/` (global) and
`<project>/.claude/skills/` (per-project). Use the UI for the user-facing add
flow; use these endpoints to inspect / search.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/skills` | List skills (global + per-project for the active project). |
| GET | `/api/skills/<scope>/<name>` | Read one (`scope` = `global` or `project`). |
| POST | `/api/skills` | Create. |
| PUT | `/api/skills/<scope>/<name>` | Update. |
| DELETE | `/api/skills/<scope>/<name>` | Archive (recoverable). |
| POST | `/api/skills/archive/<name>/restore` | Restore from archive. |
| GET | `/api/skills/search?q=…` | Keyword search across descriptions. |
| GET | `/api/skills/usage` | Skill-use telemetry (if enabled). |
| POST | `/api/skills/import/[paste\|folder\|git\|plugin\|git/install\|git/cancel]` | UI import flows. |

## MCP servers

Clayrune owns MCP configs at `~/.claude.json` (`mcpServers` key, global) and
`<project>/.mcp.json` (per-project). Project-scope shadows global at session
start. Use the UI for add/edit; these endpoints are for inspection / scripted
mutation.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/mcp` | List all MCP servers (global + project; surfaces `shadowed_by_project`). |
| GET | `/api/mcp/<scope>/<name>` | Read one (`scope` = `global` or `project`). |
| POST | `/api/mcp` | Create. |
| PUT | `/api/mcp/<scope>/<name>` | Update. |
| DELETE | `/api/mcp/<scope>/<name>` | Delete. |
| POST | `/api/mcp/url/preview` | Preview a remote MCP install URL (well-known metadata). |
| POST | `/api/mcp/url/install` | Install from URL. |
| DELETE | `/api/mcp/url/staged` | Drop a staged install. |

## GitHub sync (per-project)

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/project/<project_id>/github/setup` | Connect a GitHub repo. |
| POST | `/api/project/<project_id>/github/disconnect` | Disconnect. |
| POST | `/api/project/<project_id>/github/sync` | Trigger a sync now. |
| GET | `/api/project/<project_id>/github/status` | Read sync state. |

## Attachments / images

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/attachments/<stored_name>` | Serve a backlog attachment by stored filename. |
| GET | `/api/serve-image?path=ABSOLUTE_PATH` | Serve an image from an allowed scope (project / uploads / data). Used for inline image rendering in chat. |
| POST | `/api/agent/upload-image` | Upload an image generated by the agent for inline display. |

## Settings / Config

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/config` | Read live MC config (flags, ports, scribe state). |
| PUT | `/api/config` | Patch config (partial JSON merge). |
| GET | `/api/usage` | API usage / token-cost telemetry. |

## System (health, restart, update)

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/system/heartbeat` | Liveness ping. |
| GET | `/api/system/status` | Status snapshot (uptime, restart info). |
| GET | `/api/system/usage` | Resource usage (mem, CPU). |
| POST | `/api/system/status/refresh` | Force a status refresh. |
| GET | `/api/system/update/status` | Installed vs latest commit + dates. |
| GET | `/api/system/update/cached` | Cached update status (no fetch). |
| POST | `/api/system/update` | Pull + apply an update. |
| POST | `/api/system/restart` | Restart the server. **Requires user approval — never call without explicit go-ahead.** |
| GET | `/api/system/restart/status` | Heartbeat-based restart detection. |

## Distill (skill proposals)

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/distill` | Write a proposed SKILL.md to `data/skills/_proposed/<sid>/`. The `mc-distill` skill mediates this. |

---

## Conventions

- All endpoints accept/return JSON unless they explicitly serve a file.
- Errors return `{"error": "..."}` with the appropriate 4xx/5xx code.
- Long-running work should be backed by the Terminal pop-out (visible to the
  user) and registered with the Process Manager (mandatory).
- For LONG-TERM repeatable jobs use the local Scheduler; for SHORT-INTERVAL
  in-session polling use the cloud `/schedule` skill.
- Never call `/api/system/restart` without explicit user approval.
- When the user asks how to manage MCP / Skills / Schedules / Settings /
  Memory: point them at the Clayrune UI surface, not at the `claude` CLI.

This reference is auto-loaded into your system prompt. For features missing
here, grep `server.py` for `@app.route` to discover endpoints — or run
`python tools/regen-api-reference.py` to refresh this file from source.
