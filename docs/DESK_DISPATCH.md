# Desk campaign agent dispatch

Branch implementation; integration and a real child-agent run are not yet verified.
Ron chose direct dispatch on 2026-11-03, accepting the prompt-injection risk.

## User behavior

In campaign chat, supported Claude planners can start agents hired on that
campaign project's Floor. They get the exact active `scope:name` roster, not
the global Bench. Removed hires and missing characters are refused with a reason.
No agent is hired implicitly.

**Settings > Providers > Advanced > Desk agent dispatch** is the default-on
`desk_dispatch_enabled` switch, beside the independent `desk_research_enabled`.
It uses the existing Settings allowlist and gates. Research can be off while
dispatch is on. Disabled/unsupported turns receive no dispatch tool and their
reply gets a deterministic explanation. No provider/model is substituted.

Each typed message allows **two launch attempts**, including failed/ambiguous
attempts: one builder plus one specialist, without unbounded fan-out. Invalid
inputs/unhired targets do not spend the budget. The transport never retries.

Each successful dispatch immediately saves a target/task card with an **Open
child session** deep link, visible while the planner is still thinking.
Completions and questions append result cards to that campaign without invoking
the planner. Questions show `waiting_for_answer`; open the child session to
answer. Later typed messages can discuss these results as untrusted data.
If the planner fails after dispatch, cards remain and the error advises checking
them before retrying. Child sessions have their normal tools and human gates.

## Boundary and existing seams

- `mc/desk_dispatch.py` owns admission, capability lifetime, current roster
  checks, the attempt budget, saved cards and the campaign notification sink.
  Its single `admission()` guard is called by `mc/desk_research.py`.
- Only the model call in `desk_campaign_chat.chat` enters the server-owned
  ContextVar. Admission also requires the actual POST campaign-chat endpoint,
  rejects OS-attributed managed-agent callers and uses the existing unattended
  signal. No body field can choose an origin. Internal calls, reply ingest,
  listening, scheduled work, storyboard generation/chat and article chat have no
  dispatch tool. This retains the localhost-trust limitations documented in
  `mc/caller_attribution.py`; it is not proof of human identity against deliberately
  detached local processes.
- Claude requires the existing supported fail-closed hooks (CLI 2.1.295+), outside
  safe mode. `desk_research.run` remains the only model runner. It adds exactly
  one strict MCP server, `desk`, only for eligible turns; other calls retain an
  empty MCP config. The read hook admits exactly `mcp__desk__dispatch_agent` only
  when enabled by the adapter. Shell, writes, built-in subagents and unrelated
  MCP tools remain excluded from the planner.
- `mc/desk_dispatch_mcp.py` is a stdio JSON-RPC transport with one tool accepting
  `agent` and `task`. It forwards to `/api/desk/dispatch-tool` with a random,
  short-lived capability inherited through the environment, never argv, model
  input or a repo file. Frozen builds use the existing internal hook registry.
- The tool route rechecks the switch, campaign project, active project roster
  and character before launch. It posts over loopback to the existing route
  `POST /api/project/<pid>/agent/dispatch`, including normal routing, strict
  character resolution, dispatch trigger and durable launch bookkeeping. It
  never launches a process itself. Tasks start with
  `Origin: Desk campaign <id>, agent <planner name>`.
- `notify_session=desk-campaign:<id>` uses the existing durable completion and
  question outbox/inbox. Three wiring lines in `agent_routes._process_inbox`,
  after claim-fence and shutdown checks, select the campaign sink before parent
  revival. There is no second delivery channel, synthetic parent session or
  automatic planner continuation. Normal session notifications are unchanged.

The campaign's `dispatch_sessions` map retains child identities beyond bounded
chat history; `dispatch_receipts` deduplicates events even after result trimming.
If launch succeeded before its card was saved, delivery reconstructs the card
from the notify-bearing live session or durable launch row, checking campaign
origin and callback target. A missing/moved campaign blocks delivery; an unavailable
launch row defers it. Neither case re-dispatches the child.

`static/js/desk-v1-dispatch.js` and its CSS own cards and thread refresh. They use
the existing project/session deep link. Refresh fetches saved chat while the
planner/child is pending, skips requests otherwise, and stops when the chat
element disconnects. All task/result text is escaped. Campaign-chat hooks retain
the draft lifecycle. Monolith edits are wiring only: key/default, asset references
and sink selector; feature bodies live in separate modules.

## Validation and handoff

The new dispatch tests cover every excluded context, typed/attributed callers,
provider/project/switch checks, unknown/removed targets, turn expiry, input/cap
failures, existing dispatch route/origin/notify, independent research switch,
model failure after launch, deterministic disabled notice, existing durable
inbox return, question callbacks, trimming/dedup and missing-card recovery,
read-hook isolation, MCP protocol and frozen entry. Settings tests round-trip
both switches. Existing Desk composition/research/product/routes and notify
tests run alongside them. Exact commands/results are in the integration journal.

Campaign-chat smoke uses worktree assets at 1440/390: pending card, escaped task,
deep link, later result with no model POST, column fit and reload, plus existing
composer/repaint/pop-out cases. Boot smoke covers the SPA wiring.

A disposable real Claude `claude-haiku-5-5` probe exercised the actual campaign
route, existing runner, MCP handshake/tool call and existing dispatch route;
only the final child launcher was stubbed. One launch recorded, HTTP 200, saved
card and honest planner reply saying `dispatched`, not `finished`. No real
campaign/live config changed. This does not certify real child execution or
production delivery across a server restart. Frozen entry is unit-tested;
packaged builds and POSIX CLI execution remain unverified.

Dave owns review/merge, post-merge campaign/boot smokes and restart/live
verification. This build does not merge, push, restart or alter live config.
Turning off the Settings switch removes the tool; existing children and queued
results continue normally.
