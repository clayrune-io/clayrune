# Desk chat agent dispatch (campaign, storyboard and article chats)

Branch implementation; integration and a real child-agent run are not yet verified.
Ron chose direct dispatch on 2026-11-03, accepting the prompt-injection risk, and
on 2026-11-19 extended it from campaign chat to the two Studio chats (storyboard
chat and article chat), rather than giving the Studio chats tools of their own.
The Studio section at the end says what differs; everything else below applies to
all three owners ("campaign" read as "the chat's owner").

## User behavior

In campaign chat, supported Claude planners can start agents hired on that
campaign project's Floor. They get the exact active `scope:name` roster, not
the global Bench. Removed hires and missing characters are refused with a reason.
No agent is hired implicitly.

**Settings > Providers > Advanced > Desk agent dispatch** is the default-on
`desk_dispatch_enabled` switch, beside the independent `desk_research_enabled`.
It uses the existing Settings allowlist and gates. Research can be off while
dispatch is on. Disabled/unsupported turns receive no dispatch tool and their
reply gets a deterministic explanation.

The planner sees each hire’s provider and character pins, with model IDs and
effort levels grouped by provider. It can pass `model`, `effort`, or both to
`dispatch_agent`. Each omitted field follows the normal launch settings. An
explicit pick applies to that launch alone and uses the hire’s provider. If
allowance or model quota blocks the pick, the launch is refused. The two-field
`agent` + `task` request follows the existing launch path with the same loopback
body.

Each typed message allows **two launch attempts**, including failed/ambiguous
attempts: one builder plus one specialist, without unbounded fan-out. Invalid
inputs, including unavailable model IDs or effort levels, return 400 without
spending the budget. The transport never retries.

Each successful dispatch immediately saves a target/task card with the child’s
launch model and effort and an **Open child session** deep link. The card is
visible while the planner is still thinking. A blank launch setting displays as
a provider default. Older cards can have no settings line.
Completions and questions append result cards to that campaign without invoking
the planner. Questions show `waiting_for_answer`; open the child session to
answer. Later typed messages can discuss these results as untrusted data.
If the planner fails after dispatch, cards remain and the error advises checking
them before retrying. Child sessions have their normal tools and human gates.

## Boundary and existing seams

- `mc/desk_dispatch.py` owns admission, capability lifetime, current roster
  checks, the attempt budget, saved cards and the campaign notification sink.
  Its single `admission()` guard is called by `mc/desk_research.py`.
- Only the model call in `desk_campaign_chat.chat`, `desk_story_chat.chat` and
  `desk_article_chat.chat` enters the server-owned ContextVar (`typed_turn`).
  Admission also requires the actual POST endpoint of that owner's chat
  (`desk_dispatch_owner.ENDPOINTS`: campaign chat, `/api/desk/storyboard/chat`,
  article chat) and that the request itself names the owner the turn was opened for,
  rejects OS-attributed managed-agent callers and uses the existing unattended
  signal. No body field can choose an origin. Internal calls, reply ingest,
  listening, scheduled work, storyboard generation, scene revise, ingest, article
  save and every other path have no dispatch tool. This retains the localhost-trust limitations documented in
  `mc/caller_attribution.py`; it is not proof of human identity against deliberately
  detached local processes.
- Claude requires the existing supported fail-closed hooks (CLI 2.1.295+), outside
  safe mode. `desk_research.run` remains the only model runner. It adds exactly
  one strict MCP server, `desk`, only for eligible turns; other calls retain an
  empty MCP config. The read hook admits exactly `mcp__desk__dispatch_agent` only
  when enabled by the adapter. Shell, writes, built-in subagents and unrelated
  MCP tools remain excluded from the planner.
- `mc/desk_dispatch_mcp.py` is a stdio JSON-RPC transport with one tool accepting
  required `agent` and `task` and optional `model` and `effort`. It forwards to
  `/api/desk/dispatch-tool` with a random,
  short-lived capability inherited through the environment, never argv, model
  input or a repo file. Frozen builds use the existing internal hook registry.
- The tool route rechecks the switch, campaign project, active project roster
  and character before launch. `mc/desk_dispatch_model.py` uses the same runtime
  model catalog and effort levels as the Desk picker. It validates any explicit
  picks before the route reserves an attempt. The tool posts over loopback to
  the existing route
  `POST /api/project/<pid>/agent/dispatch`, including normal routing, strict
  character resolution, dispatch trigger and durable launch bookkeeping. It
  never launches a process itself. Tasks start with
  `Origin: Desk campaign <id>, agent <planner name>` (storyboard:
  `Origin: Desk storyboard studio:<id>` / `piece:<id>`; article:
  `Origin: Desk article <id>`).
- `notify_session=desk-campaign:<id>` (storyboard `desk-piece:storyboard:<studio|piece>:<id>`,
  article `desk-piece:article:<id>`; see Studio chats below) uses the existing durable completion and
  question outbox/inbox. Three wiring lines in `agent_routes._process_inbox`,
  after claim-fence and shutdown checks, select the campaign sink before parent
  revival. There is no second delivery channel, synthetic parent session or
  automatic planner continuation. Normal session notifications are unchanged.

The campaign's `dispatch_sessions` map retains child identities beyond bounded
chat history; `dispatch_receipts` deduplicates events even after result trimming.
If launch succeeded before its card was saved, delivery reconstructs the card
from the notify-bearing live session or durable launch row, including the launch
settings when available. It checks the owner origin and callback target.
A missing/moved campaign blocks delivery; an unavailable
launch row defers it. Neither case re-dispatches the child.

`static/js/desk-v1-dispatch.js` and its CSS own cards and thread refresh. They use
the existing project/session deep link. Refresh fetches saved chat while the
planner/child is pending, skips requests otherwise, and stops when the chat
element disconnects. All task/result text is escaped. Campaign-chat hooks retain
the draft lifecycle. Monolith edits are wiring only: key/default, asset references
and sink selector; feature bodies live in separate modules.

## Studio chats (storyboard and article)

`mc/desk_dispatch_owner.py` is the one place that knows how each owner is named,
where its record and thread live and which endpoint may open a turn. `desk_dispatch.py`
keeps admission, the attempt budget and delivery and asks the owner module.

| Owner | Sink | Record | Thread |
|---|---|---|---|
| campaign | `desk-campaign:<id>` (unchanged) | `campaigns[id]` | `chat` |
| storyboard | `desk-piece:storyboard:<studio\|piece>:<id>` | `storyboards[<studio\|piece>:<id>]` | `thread` |
| article | `desk-piece:article:<id>` | `studio_articles[id]` | `chat` |

Every record also carries its own `dispatch_sessions` and `dispatch_receipts`. A
storyboard rewrite (`put_storyboard`) carries them across, as it does the thread.

- **Project and roster.** Dispatch needs a project: the article's own project, or the
  `project_id` the storyboard page sends. With none (the no-project Studio fallback
  agent) the chat has no dispatch tool and answers as before. The roster is that
  project's active hires. Explicit model and effort picks use the hire's provider.
- **Delivery.** Same receipt dedupe and blocked/deferred semantics as campaign. A
  missing piece (deleted/trashed article, unsaved or piece-less storyboard) or a
  moved project blocks delivery and never re-dispatches. A storyboard record names
  no project, so its saved card is the project it launched under.
- **Prompt.** Studio chats are told the tool is the one thing they have beyond
  reading web pages, and to use it for work they cannot do themselves (writing
  files, building an HTML page, code). Their reply (`reply`/`change`/`proposal`) JSON
  contracts are unchanged. A child's result reaches a later turn labelled as
  untrusted child data, never as the user or the agent.
- **UI.** `desk-v1-story-chat.js` and `desk-v1-article-chat.js` render cards through
  `DeskV1Dispatch.card` and poll the saved thread with `DeskV1Dispatch.watch` while a
  child is pending. `watch`/`refresh` take either a campaign id (original form) or
  `{ url, status }`.
- **Failure after dispatch.** The chat returns 502 saying to check the conversation for
  dispatched sessions before retrying; the saved user turn and cards stay.

## Validation and handoff

Model and effort tests cover the original two-field payload, separate and combined
picks, hire-specific catalogs, refusals that leave the attempt budget intact,
the allowance and quota boundaries, saved card settings and crash recovery.
The campaign, storyboard and article browser smokes check the settings line.

Studio owners: `tests/test_desk_dispatch_studio.py` (admission per endpoint and
owner, non-typed refusal, sinks, launch origin/sink, whole typed turns for both
chats, delivery into the right thread, dedupe, blocked piece, inbox routing);
smokes `desk-v1-story-chat.mjs` and `desk-v1-article-chat.mjs` cover the cards and
polled result. A real child run from a Studio chat needs a human-typed message
(agent-attributed POSTs are refused by design) and is not verified.

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
