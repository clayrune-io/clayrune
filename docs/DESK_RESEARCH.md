# Desk read-only research

Ron reversed the earlier toolless-only choice on 2026-11-02. This change is a
branch candidate, not a live deployment. Only the four composition calls use it:
campaign chat, storyboard generation, storyboard chat and article chat. Their
existing persona, provider, model, effort, excerpts, proposal validation and
human acceptance flows are retained.

`mc/desk_research.py` owns execution and capability disclosure;
`mc/desk_read_guard.py` owns file admission. `mc/agent_runtime.py` is unchanged.
Typed campaign chat can additionally receive one bounded dispatch tool through
this same runner; see `docs/DESK_DISPATCH.md`. Every other composition path
retains the research-only boundary below.

## Current provider matrix

This describes the enforcement available through the current runtime adapters.
It does not claim that other vendors could never implement a suitable profile.
Only Claude's new research profile was exercised with a real model in this build.

| Provider | Existing enforceable transform | Desk research in this build | Reason / source |
| --- | --- | --- | --- |
| Claude CLI 2.1.295+ | Toolless | WebSearch, WebFetch, Read, Grep, Glob | Exact `--tools` list, strict empty MCP, isolated settings, `dontAsk`, per-tool hook with `onFailure: block`; real CLI 2.1.316 verified. |
| Older/unavailable Claude, or safe mode | Toolless if its existing adapter is ready | Toolless with notice | Required fail-closed hook support unavailable. |
| Gemini | Certified toolless policy | Toolless with notice | Existing `_transform_argv` uses deny-all policy, `--skip-trust`, `-e none`, isolated CLI home. No certified Desk whitelist plus dynamic path guard is exposed by this adapter. |
| Qwen | Certified toolless isolation | Toolless with notice | Existing `TRANSFORM_ISOLATION` removes tools/MCP. No certified Desk whitelist plus dynamic path guard is exposed by this adapter. |
| Codex | Uncertified; refused | Refused visibly | `tool_free_transform_enforced = False`; its current adapter does not certify complete tool removal. A filesystem sandbox alone does not meet the no-shell requirement. |
| OpenCode | Uncertified; refused | Refused visibly | Current `oneshot` uses `run --format json` without a certified removal of tools/extensions. |
| Goose | Uncertified; refused | Refused visibly | Current `oneshot` uses `run --no-session`; that does not certify removal of its tools/extensions. |
| Aider | Uncertified; refused | Refused visibly | Current `oneshot` uses its coding command; no certified Desk no-write/no-shell transform. |
| Kiro | Uncertified; refused | Refused visibly | Current `oneshot` uses `build_command`; no certified tool-free transform. |

Evidence: the registered runtime classes and `_authorize_text_transform` in
`mc/agent_runtime.py`. The new adapter never substitutes a different provider.
Successful toolless chat replies receive a deterministic capability notice;
uncertified adapters retain their explicit existing refusal, rather than being
run with their regular coding tools.

## Admission and isolation

- Research offers only the five named built-ins. No Bash/PowerShell, Write/Edit,
  notebook edits, built-in subagents or installed skills are offered. Eligible
  typed campaign chat can additionally receive the one strict `desk` MCP dispatch
  tool; all other calls retain an empty MCP config.
- The process runs in the existing neutral transform directory, with the
  transform environment disabling automatic project instructions and memory.
  Project contents and web pages remain untrusted data behind the task's output
  contract. Current competitor claims must cite URLs.
- The selected project record, not task text, supplies `project_path` to every
  call. Without a project folder, only WebSearch and WebFetch are offered.
- Read and Grep validate each current path and its resolved target. The guard
  rejects escape paths, environment/key/PEM/id_rsa names, credential/secret/token
  path components, Git metadata and Git ignores. Relative tool paths are rewritten
  to the checked absolute path, avoiding resolution against the neutral cwd.
- Grep accepts one admitted regular file. Directory Grep is refused with guidance
  to discover names with Glob and then read/search individual files. Glob lists
  filenames, not contents; discovering a name does not authorize reading it.
- Git ignore checks include tracked files now matching an ignore. Non-Git project
  folders use a disposable external Git directory to apply their `.gitignore`
  files without changing the project. Missing/broken Git policy refuses reads.
- Starting excerpts retain their six-file, 8 KiB/file and 24 KiB/total limits and
  now also check Git ignores. An explicit empty materials list disables excerpts,
  not research access; use the research setting to disable tools.
- `onFailure: block` is load-bearing. An explicit `permissions.ask` rule was
  tested and removed because it also rejects admitted reads in `dontAsk` mode.
  Live tests confirmed that a missing guard command blocks the tool entirely.
- Source hooks use Python. Frozen hooks use the existing
  `<app> --clayrune-hook desk-read <root>` dispatch before server/UI startup.
  Static imports let the existing bundler discover the hook module. Registry
  behavior is unit-tested; a new packaged artifact was not built or VM-tested.

The deadline is 480 seconds, with at most 16 model turns. The existing chat
pending states and storyboard status explain that research may take eight
minutes; this does not claim per-tool streaming progress. Processes register
immediately with the running installation's Process Manager and are terminated
by their owned PID/tree on registration failure or timeout, including Windows
command-shim children. No transcript is persisted by the research CLI.

## Configuration and API

`desk_research_enabled` defaults to true (also when absent), is on the existing
Settings allowlist, and appears under **Providers → Advanced → Desk research**.
Existing human-only Settings gates remain. It is read for each call. Off means
the existing toolless transform plus excerpts, never a fallback to coding tools.

`GET /api/desk/research-capabilities?project_id=<id>&agent=<scope:name>` resolves
the same selected agent/engine as the chat and returns `web`, `project_read`,
`toolless`, `label`, `reason` and `provider`. Omitting `agent` uses the project's
default. The campaign Reads line requests the actual campaign agent, so an
override cannot inherit the default planner's capability label. Failure to get
capabilities is displayed explicitly.

## Verification

The required four browser smokes ran against this worktree's static files and
fixture APIs, not the live server's source. Campaign smoke additionally checks
research and toolless labels, retained excerpts, add/remove/reset/reload and
1440/390-pixel layouts. No browser failure was seen in these runs.

Backend coverage includes all four project arguments and starting excerpts,
tool isolation, forbidden names, symlink targets, ignored files, non-Git folders,
relative-path rewriting, malformed input, failed policy, provider notices, empty
answers, timeout/registration cleanup, selected-agent capabilities, frozen hook
dispatch and Settings persistence. Exact commands and counts are recorded in
the session journal after the final run.

### Real campaign call

A Flask test client in this worktree used a disposable Desk store and a pinned
Claude `claude-haiku-5-5` persona. The actual campaign route called the real CLI,
searched the web, fetched the official Playwright page, discovered the source
file with Glob and read it. No model seam was mocked. No live campaign was
created/modified and no running server was restarted.

The final stream's init event reported exactly `Glob, Grep, Read, WebFetch,
WebSearch`, `mcp_servers=[]`, `skills=[]`, and `permissionMode=dontAsk`.
It also listed three vendor built-in plugins; the evidence is not a claim that
the CLI reported zero plugins. The four successful tool calls were WebSearch,
Glob, WebFetch (HTTP 200) and Read. Reply excerpt:

> Per the official Playwright actionability page (https://playwright.dev/docs/actionability), locator.click checks Visible, Stable, Receives Events and Enabled. Editable is not applicable to click.

> Desk research code (mc/desk_research.py, found by Glob at mc\desk_research.py under the project folder): TIMEOUT_SEC = 480. TOOLS = ('WebSearch', 'WebFetch', 'Read', 'Grep', 'Glob'). With no project folder, only WebSearch and WebFetch are passed. Bash, PowerShell, Write, Edit, NotebookEdit, Agent and Task are disallowed.

The complete reply, including its caveat about a contradictory search snippet,
is retained in the gitignored session journal. This is verification of the
research path, not a separately verified Playwright release/version claim.

Separate real-CLI probes used synthetic files only: one public read succeeded;
three reads (.env, gitignored, outside root) were denied and their marker never
entered the stream. A deliberately nonexistent guard command blocked an
otherwise permitted public read. Shell and write tools were absent.

References: [Claude hook decision and failure contract](https://code.claude.com/docs/en/hooks),
and the existing measured `docs/FENCE_FAIL_CLOSED_MEASUREMENT.md`. Rollback: turn
off the Settings key; revert this branch's commit to remove the feature entirely.
