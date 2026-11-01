# BUILTINS-7: cross-session messaging tool availability

Investigation only, 2026-10-31. Source baseline: `f857e759b98610ffa75081470bcaaef291dbef13`.
No product changes, model dispatches, messages, jobs, merges, pushes, restarts or emails were performed.

## Verdict

**Clayrune has provider-aware HTTP coordination and delivery, but it does not expose those facilities as dedicated MCP tools to its engines.** Claude's native `ListAgents` / `SendMessage` are a separate harness facility, not MCP wrappers over Clayrune's outbox/inbox. Non-Claude sessions with permitted shell/network tools can call the HTTP API. That is not equivalent to discovering a typed messaging tool.

There are two independent gaps: missing tools and missing MCP launch wiring. Only Claude, Gemini and Qwen consume Clayrune's resolved MCP selection. Codex, OpenCode, Goose, Aider and Kiro accept the dispatch keyword through `**_extra` and discard it. Aider additionally declares no native MCP support. Consequently, adding tools to one existing server alone cannot deliver eight-engine parity.

## Per-engine result

“Passed” below means explicit Clayrune launch integration, not whatever an operator independently configured in a vendor CLI. No row asserts a model successfully called a messaging tool.

| Engine | MCP servers Clayrune passes at launch | Dedicated Clayrune messaging tools | How a session can reach another session today |
|---|---|---|---|
| Claude | Selected global/project catalog entries via `--strict-mcp-config --mcp-config`; without a selection, native global/project/plugin discovery. Catalog can re-declare `engram`. | None found. Native `ListAgents` / `SendMessage` are separate. | Native peer tools where supplied by the harness, or shell HTTP to Clayrune; dispatch/send must supply `notify_session` for completion callbacks. |
| Gemini | Explicit selected catalog, or merged global/project set when untrimmed; server definitions in generated system settings plus exact `--allowed-mcp-server-names`. | None found. | Permitted `run_shell_command` / HTTP to Clayrune. |
| Qwen | Same resolved set and transport conversion as Gemini; `--mcp-config` plus exact name allowlist. | None found. | Permitted shell / HTTP to Clayrune. |
| Codex | **None explicitly.** Dispatch discards the selection; native vendor config/plugins can still provide MCP tools. | No Clayrune tools found in this session's discovered tool registry. | Permitted execution tool / HTTP. Its own collaboration tools are not a demonstrated bridge to unrelated Clayrune sessions. Sandboxed jobs are explicitly refused by Clayrune. |
| OpenCode | **None explicitly.** Dispatch discards the selection; runtime describes vendor settings discovery. | No Clayrune bridge in the launch path. | Permitted native shell / HTTP; actual installed model/tool invocation untested. |
| Goose | **None explicitly.** Dispatch discards the selection; vendor extensions remain independent. | No Clayrune bridge in the launch path. | Permitted native execution extension / HTTP; actual installed model/tool invocation untested. |
| Aider | **None.** Runtime declares `supports_mcp=False`. | No. | An external caller can use HTTP; this adapter does not establish an autonomous model-callable shell or MCP messaging path. Do not count prompt curl instructions as tool parity. |
| Kiro | **None explicitly.** Dispatch discards the selection; native MCP capability is advertised, but no selected-server configuration is passed. | No Clayrune bridge in the launch path. | Permitted native execution / HTTP; actual installed model/tool invocation untested. |

Launch evidence at the baseline:

- `mc/blueprints/agent_routes.py:681`, `:715`, `:749`: catalog, trimming and runtime resolver; `:844` passes Claude's selection; `:10618` passes `mcp_config_json` to non-Claude dispatch.
- `mc/agent_runtime.py:2193`: Claude flags. `:3709`, `:3751`, `:3777`, `:4050`, `:4767`: Gemini settings and allowlist. `:5227` reuses the set on follow-up.
- `mc/agent_runtime.py:7063`, `:7072`, `:7627`, `:7659`: Qwen selection, storage and follow-up.
- `mc/agent_runtime.py:8339`, `:9428`: Codex command/dispatch; `:9908`, `:10153`: OpenCode; `:10326`, `:10504`: Goose; `:10698`, `:10783`, `:10799`: Aider; `:11007`, `:11142`: Kiro. Each latter dispatch has a catchall and no consumption of `mcp_config_json`.
- `mc/agent_runtime.py:11265`: eight registered runtimes. `mc/mcp.py:185`, `:209`, `:675`, `:704`: global/project storage and Gemini/Qwen transport conversion.

Some comments are stale: Gemini's class introduction says MCP is not injected, Qwen's historical introduction says `supports_mcp=False`, and the dispatch-site comment names only Qwen. Executable code and the command-builder probe below take precedence. The older Gemini finding in `docs/VENDOR_HARNESS_MATRIX.md:103` has already been addressed in this baseline; Codex's corresponding wiring gap remains.

## Existing servers and ownership

`mc/mcp.py` is a configuration manager and installer, **not an MCP protocol server**. It cannot gain `ListAgents` merely by adding a function to that file. Its builtin templates are read from `data/mcp/builtin` (`mc/mcp.py:389`, `:447`, `:544`, `:595`). That directory is absent from this worktree's tracked files; the live main checkout contains templates for external npm servers `filesystem` and `sequential-thinking`.

The selected-server catalog also knows the external `engram mcp --tools=agent` binary (`mc/blueprints/agent_routes.py:678`). A project can select it explicitly; the untrimmed Gemini/Qwen merge does not add that plugin-only fallback by itself (`mc/mcp.py:704`). Other catalog entries depend on installation and project configuration; there is no universal fixed fleet.

Read-only protocol discovery of the existing `filesystem` and `engram` executables returned 14 and 15 tools respectively, listed below. Neither exposes Clayrune sessions, coordination events, dispatch callbacks, job follow-ups or delivery status. The repository-owned mail server exposes only `list_recent`, `search_email`, `read_email` (`tools/mail-mcp/server.py:204`, `:294`). Reusing it would tie a core feature to an optional account-specific email integration and is not a suitable default. Remote MCP bridges forward approved external servers; they are not currently Clayrune control servers.

Installed configuration names and the live project's selection were inspected without printing credentials. Those operator-specific details are retained in the ignored journal `docs/_journal/8fd0b1ec-builtins-7-cross-session-messaging.md` in the main checkout, rather than making them repository defaults. Configured does not mean connected: only the two protocol handshakes below were performed. No arbitrary remote MCP catalogs or mail tools were called.

## What the HTTP facilities actually mean

1. **Coordination is awareness, not addressed mail.** `GET /api/project/<pid>/coord/roster`, `GET .../coord/events`, and `POST .../coord/publish` list live agents and publish intentions/completions. The publish body accepts `type`, `agent`, `session_id`, `summary`, `targets`, and optionally `commit_sha`. Source: `mc/blueprints/coordination_routes.py:611`, `:641`, `:657`. The context builder injects sibling activity at `mc/blueprints/agent_routes.py:5463`. Publishing does not guarantee a target wakes; the interrupt rung is separately gated and is not part of this proposal.
2. **Dispatch/send are the existing addressed APIs.** `POST /api/project/<pid>/agent/dispatch` with `task` and `notify_session=<caller sid>` creates a child whose result wakes the caller. `POST .../agent/send` with `session_id=<target sid>`, `message`, and `notify_session=<caller sid>` sends further work and re-arms the callback. Omitting `notify_session` on a follow-up makes it human-owned work without that callback. Sources: `data/agent_reference/CLAYRUNE_API.md:165`, `:166`; `mc/blueprints/agent_routes.py:4933`, `:12445`, `:12803`. Direct send's acceptance/queueing is not a durable exactly-once messaging guarantee.
3. **The outbox/inbox is a trusted completion transport.** `_notify_agent_spawner` records a completion source and enqueues it (`mc/blueprints/agent_routes.py:8968`). `DeliveryStore.accept` requires an exact matching outbox row and rejects invented or modified events (`mc/delegation_delivery.py:334`). Do not expose “write inbox” or “write SQLite” as messaging tools. `GET .../agent/delegation/status` and `.../status-list` are the appropriate observation APIs. A submitted receipt proves handoff, not successful task execution.
4. **Delivery is provider-aware already.** `_process_inbox` retains the parent's provider/model, defers busy parents and uses `agent_followup` (`mc/blueprints/agent_routes.py:8998`). Missing parents use `_revive_parent_for_delegation` (`:7986`), which distinguishes Claude and other providers. This is the machinery the proposed tools should reuse, not replace.
5. **Jobs are server-owned continuations.** `POST .../agent/<sid>/job` runs a command and records its result through the same source/outbox path (`mc/blueprints/agent_routes.py:1733`, `:1775`, `:1784`). Incognito is refused, and a session with `_codex_unattended_sandbox` is refused with HTTP 403 (`:1811`). Preserve those restrictions. A network-capable MCP process must not become a way around the owning session's sandbox or unattended fence.

## Probes and results

These are launch-construction, protocol-discovery and read-only HTTP probes, **not end-to-end model messaging tests**. No production tests were changed.

### Runtime argument probe

Executed from the investigation checkout with `python -` (no CLI/model subprocesses):

```python
import inspect, json
from unittest.mock import patch
from mc import agent_runtime as ar
fixture = json.dumps({'mcpServers': {
    'filesystem': {'command': 'fixture'}, 'engram': {'command': 'fixture'}}})
classes = [ar.ClaudeRuntime, ar.GeminiRuntime, ar.QwenRuntime,
           ar.CodexRuntime, ar.OpenCodeRuntime, ar.GooseRuntime,
           ar.AiderRuntime, ar.KiroRuntime]
with patch.object(ar, '_guardrail_launch_file', return_value=None), \
     patch.object(ar, '_guardrail_codex_hook_args', return_value=[]):
    for cls in classes:
        rt = cls()
        with patch.object(rt, 'resolve_binary', return_value=None):
            accepted = 'mcp_config_json' in inspect.signature(rt.build_command).parameters
            cmd = rt.build_command(**({'mcp_config_json': fixture} if accepted else {}))
            print(cls.__name__, rt.capabilities().supports_mcp, accepted,
                  [v for v in cmd if 'mcp' in v or v in ('engram', 'filesystem')])
```

Observed output, with the repeated fixture JSON abbreviated:

```text
engine          supports_mcp  accepts MCP argument  MCP argv
Claude          True          True                  --strict-mcp-config --mcp-config <fixture>
Gemini          True          True                  --allowed-mcp-server-names engram filesystem
Qwen            True          True                  --mcp-config <fixture> --allowed-mcp-server-names engram filesystem
Codex           True          False                 []
OpenCode        True          False                 []
Goose           True          False                 []
Aider           False         False                 []
Kiro            True          False                 []
exit_code=0
```

A separate AST inspection confirmed that all five nonconsuming dispatch methods accept `**_extra` and have no explicit `mcp_config_json` parameter; their bodies do not read it. Claude's generic `dispatch(**kwargs)` signature is not its actual launch evidence: `_build_claude_flags` is.

### MCP protocol discovery

Sent newline-delimited JSON-RPC `initialize` (protocol `2025-03-26`, empty capabilities), `notifications/initialized`, and `tools/list` to:

```text
engram mcp --tools=agent
node <already-cached @modelcontextprotocol/server-filesystem/dist/index.js> <checkout>
```

Each ran in the foreground with stdin closed after those requests and a ten-second subprocess timeout. Both exited 0; no `tools/call`, install, authentication, model request or background service was involved. The cache path was resolved locally, not downloaded. Output:

```text
engram rc=0 tools=[mem_capture_passive, mem_compare, mem_context,
mem_current_project, mem_doctor, mem_get_observation, mem_judge, mem_save,
mem_save_prompt, mem_search, mem_session_end, mem_session_start,
mem_session_summary, mem_suggest_topic_key, mem_update]

filesystem rc=0 tools=[read_file, read_text_file, read_media_file,
read_multiple_files, write_file, edit_file, create_directory,
list_directory, list_directory_with_sizes, directory_tree, move_file,
search_files, get_file_info, list_allowed_directories]
```

The current Codex-hosted session's discovered tool-name search for Clayrune/coordination/delegation tool names also returned `[]`. Native collaboration tools and externally supplied app plugins are not evidence that Clayrune injected an MCP bridge.

### Live HTTP read probes

Executed Python `urllib.request.urlopen(..., timeout=10)`, parsing JSON and printing only response shape:

```text
GET /api/project/mission_control/coord/roster
  HTTP 200; keys [enabled, live_agents]
GET /api/project/mission_control/coord/events?limit=1
  HTTP 200; list
GET /api/project/mission_control/agent/delegation/status-list?limit=1&offset=0
  HTTP 200; keys [items, limit, offset, total, usage]
```

These establish reachability from this session, not permission or reachability for every sandboxed engine. Live server code may differ from the pinned worktree baseline. No dispatch, send, publish, inbox write, retry or job was used as a probe. Delivery across engine pairs, process exit/revival and crash recovery therefore remain untested in this investigation.

## Smallest fix under the existing-server constraint

**Conditional proposal: extend the existing `filesystem` MCP registration with a thin Clayrune-owned stdio wrapper, preserving its existing file tools and adding a separate coordination tool module.** This uses a server name Clayrune already installs and launches; it is not a claim that the external npm filesystem package already has extension hooks. The wrapper would forward ordinary MCP requests to that unchanged package, merge the additional `tools/list` schemas, and handle only explicitly named Clayrune tools. It introduces a proxy process and must preserve initialization, pagination, IDs, errors, notifications and shutdown behavior.

This is the smallest identifiable in-repository route that honors the requested existing-server slot. There is **no existing first-party general-purpose MCP server** to which eight functions can simply be appended. Engram is an external memory binary, and adding volatile coordination to persistent memory would cross subsystem boundaries. A standalone `clayrune` MCP server would be cleaner and avoid proxy complexity, but is an explicit alternative requiring the spawner to relax the “already launches” constraint; it is not silently substituted here.

Suggested eight tools, all bound to the launching project/session rather than taking a freely supplied caller identity:

| Proposed tool | Existing operation | Required semantics |
|---|---|---|
| `clayrune_list_agents` | `GET .../coord/roster` | Current project's live roster; provider metadata only where actually supplied. |
| `clayrune_send_message` | `POST .../agent/send` | Target sid + message; bind `notify_session` to caller for requested reply. Report queued/accepted status honestly. |
| `clayrune_dispatch_agent` | `POST .../agent/dispatch` | Task + existing character/provider options; bind callback and preserve existing routing, lineage and authorization checks. |
| `clayrune_coord_events` | `GET .../coord/events` | Bounded read, no reconcile side effect by default. |
| `clayrune_coord_publish` | `POST .../coord/publish` | Intention/completion only; no new automatic interrupt behavior. |
| `clayrune_delivery_status` | `GET .../agent/delegation/status` or `status-list` | Read-only delivery evidence; never automatic retry or invented inbox events. |
| `clayrune_start_job` | `POST .../agent/<caller sid>/job` | Existing timeout, registration, incognito and sandbox restrictions remain authoritative. |
| `clayrune_job_status` | `GET .../agent/<caller sid>/job/<job id>` | Owner-scoped status; completion wake remains server-owned. |

A two-tool `list_agents`/`send_message` slice is smaller but does not satisfy the requested dispatch, coordination, delivery and job coverage. Raw inbox/retry tools are deliberately unnecessary for that coverage.

### Files and estimated size (proposal, not implementation)

| File(s) | Change | Estimated new/changed lines |
|---|---|---:|
| New `mc/cross_session_tools.py` | Schemas, validation and bounded HTTP forwarding for the eight tools. | 220–320 |
| New `tools/clayrune-filesystem-mcp.py` | Stdio proxy around the existing filesystem server; owns lifecycle and protocol merging. | 180–280 |
| New `mc/session_mcp_config.py`; small wiring edits in `mc/blueprints/agent_routes.py` and `mc/mcp.py` | Build per-launch wrapped registration and project/session binding; preserve user configs, explicit empty selections and existing transport conversion. No large new body in either monolith. | 120–200 |
| `mc/agent_runtime.py`, with provider-specific helpers under a new `mc/session_mcp_adapters/` package | Consume and retain config on fresh/resume/retry paths for Codex/OpenCode/Goose/Kiro. Existing Gemini/Qwen carry paths remain covered. | 200–400 |
| New `tests/test_cross_session_tools.py`, `tests/test_session_mcp_config.py`; extend `tests/test_provider_runtimes.py` | Protocol pass-through, caller binding, policy rejection, selection parity and continuation tests. | 350–550 |
| `data/agent_reference/CLAYRUNE_API.md`, this report | Document tools and add the currently absent coordination routes to the API reference. | 40–70 |

Planning estimate: **1–2 days for wrapper/tools plus Claude/Gemini/Qwen wiring; another 1–2 days for the four missing MCP adapters and live validation**, approximately 1,100–1,800 lines including tests/docs. These are estimates, not measured implementation sizes. Runtime-specific configuration mechanisms for OpenCode/Goose/Kiro still require local CLI verification. No Aider MCP implementation is included in those numbers.

Do not edit the operator's `.mcp.json`, vendor home configs, external npm cache or Engram binary as the product fix. Prefer a generated per-launch registration over changing ignored builtin templates, and preserve customized filesystem commands. If `filesystem` is excluded or explicitly disabled, this proposal cannot provide tools without a separate approved selection policy. That is another reason the existing-slot constraint is not a universal-platform solution.

### Decisions for the spawner before building

- **Existing slot versus dedicated server:** recommend a dedicated first-party `clayrune` server for the eventual platform feature; choose the `filesystem` wrapper only if reuse of an existing registration is mandatory. Reversal of the wrapper means removing generated wrapping and restoring direct package launch; no message-store migration is needed. Dedicated server work avoids file-proxy maintenance but changes the requested packaging constraint.
- **Aider:** native MCP parity is not established and cannot be claimed. An explicit host-mediated tool protocol/execution loop is a separate adapter feature with authorization and continuation tests; alternatively mark Aider unsupported. Recommend treating the adapter as a separately scoped follow-up, while clearly labeling the initial delivery as seven-engine rather than every-engine parity. Full eight-engine completion remains open until that decision and implementation.

No architecture option was implemented or silently selected. No human-only gate, fence, sandbox, retry review requirement or coordination interrupt setting would be loosened by the proposed tools. Launch identity must be enforced server-side using existing attribution where it is sufficient; environment labels alone are not authentication. The implementation must test this boundary before exposing mutating tools.

Acceptance for a future build: verify `tools/list` from each supported engine and one authorized read call; then controlled cross-provider dispatch/send with the same callback identity, a job completion after owner-process exit, duplicate-event rejection and busy-parent handling. Include negative tests for sandboxed jobs, incognito, mismatched project/session, disabled selection and uncertain delivery. Those tests are future work, not results of this investigation.
