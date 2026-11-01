# BUILTINS-7b: selected MCP servers in four vendor launches

Branch candidate based on `1123dc56`. No merge, push, restart, authenticated
model turn, or live cross-session messaging was performed for this change.

## Contract

Codex, OpenCode, Goose and Kiro now consume `mcp_config_json` on dispatch.
`session_mcp_launch.resolve` supplies canonical server definitions; individual
vendor modules own translation. Runtime changes are call-site wiring only.

- An explicit `enabled_mcp_servers` list wins, including an empty list. A failed
  explicit resolution refuses the launch instead of inheriting the native fleet.
- Without a list, merge Clayrune's global and project catalog, with project
  entries winning, then add the first-party `clayrune` registration.
- `clayrune_mcp_enabled=false` removes that registration. Incognito sessions and
  frozen builds receive an empty set in these four bridges.
- Follow-ups retain the resolved selection and launch environment. Codex native
  resume and its one model-refusal retry both pass through the same bridge.
- Existing vendor fencing stays authoritative. OpenCode, Goose and Kiro remain
  refused unattended while their fence adapters are unverified. Dispatch keeps
  `check_launch` and the shared `prepare_env`; follow-ups also run `prepare_env`.

No user's existing vendor configuration is overwritten. Generated file profiles
and agents use exclusive random names, outside repositories; their values do not
appear on argv. Launch-owned artifacts are removed on clean server exit, and may
remain after a hard kill. No native credential store or authentication home moves.

## Vendor mechanisms

| Vendor / module | Per-launch input | Native verification before model spawn |
|---|---|---|
| Codex / `mc/mcp_bridge_codex.py` | Unique `$CODEX_HOME/<name>.config.toml`, selected by root `--profile <name>` before `exec` or `exec resume` | `mcp list --json` discovers inherited names; a second call with the profile checks the active set and selected definitions |
| OpenCode / `mc/mcp_bridge_opencode.py` | `OPENCODE_CONFIG_CONTENT`, retaining unrelated inline settings and explicitly disabling unselected native names | `debug config` before and after the override checks the effective set and translated fields |
| Goose / `mc/mcp_bridge_goose.py` | Private JSON `--recipe` with explicit `extensions`, system instructions and prompt | `recipe validate <path>` |
| Kiro / `mc/mcp_bridge_kiro.py` | Unique launch-owned agent under the existing Kiro home's `agents/`, selected by `--agent <name>`; `includeMcpJson=false` | `agent validate --path <path>` |

Codex's `mcp_servers={}` merges with its native table, rather than clearing it.
The profile therefore disables each inherited, unselected server. Plugin loading
is disabled for this exact selection; an enabled catalog entry such as `engram`
can still re-declare a selected plugin server explicitly. The profile retains
the native authentication home and the runtime's separate hook/sandbox flags.

Goose recipes must have an explicit `extensions` array, even when empty. Do not
combine this with `--no-profile`: upstream's session builder then discards the
recipe's own extensions. Recipe prompt/context and templated server string values
are escaped for Goose's Jinja preprocessing, including invalid variable syntax
and example include/endraw directives, so task text remains literal.

Kiro uses a newly created file, never an existing named agent. The existing
`KIRO_HOME` is preserved; invalid overrides and repository-owned configuration
homes refuse the launch. A generated agent has matching filename/name and keeps
the runtime's existing tool availability.

## Explicit refusals and remaining live checks

- Codex and current Goose support stdio and streamable HTTP here; selected SSE
  definitions refuse rather than being silently treated as HTTP.
- Kiro agent definitions have no verified per-server `cwd` equivalent; a selected
  stdio definition containing `cwd` refuses rather than discarding it.
- OpenCode deep merging can retain an old transport or environment. A conflicting
  native transport, managed override, or effective configuration mismatch refuses
  instead of launching an unintended set.
- Missing native profile/recipe/agent support, malformed selections, config
  discovery errors and validator failures are visible launch errors. The bridge
  never retries with the unfiltered native fleet. Validator output is captured,
  not included in errors, because configuration may contain values.
- OpenCode, Goose and Kiro were not installed on this host. Their real launches,
  native validators and model tool discovery still require integration testing.
  No model from any vendor called a Clayrune tool during this work.

## Validation

Final focused suite: **967 passed, 4 skipped**, rc **0**, 35.73 seconds,
across 37 matching files. The four skips are the opt-in live Codex session
and three opt-in installed vendor fence checks. The full suite was not run.
All six new production modules: pyright basic **0 errors**, rc **0**.
`git diff --check`: rc **0**. No UI or installer code changed, so browser and
VM install smokes are not applicable. USER_GUIDE and the API reference were
updated; no new standing rule or install/quickstart change requires AGENTS
or README edits.

Tests cover each translation, native-validation refusals, explicit empty sets,
canonical catalog precedence, kill switch, incognito/frozen exclusions,
dispatch/follow-up environment carry, Codex native resume/model retry, and
unattended fence refusal before bridge setup or spawning.

PowerShell focused suite (only matching filenames plus existing provider tests):

```powershell
$mcpTestFiles = rg --files tests | Where-Object { $_ -match 'test_.*(mcp|codex|opencode|goose|kiro|vendor_fence|agent_runtime|provider_runtimes).*\.py$' }
python -m pytest $mcpTestFiles
```

New-module type check:

```powershell
pyright mc/mcp_launch_config.py mc/mcp_bridge_codex.py mc/mcp_bridge_opencode.py mc/mcp_bridge_goose.py mc/mcp_bridge_kiro.py mc/session_mcp_launch.py
```

Read-only installed Codex probes confirmed zero enabled servers for the empty
selection, only the fixture for a nonempty selection, replacement of an existing
native name's command/args/environment, HTTP header preservation, and parsing of root `--profile` before
`exec resume --help`. These start neither MCP servers nor a model turn; they do
not establish live tool invocation or cross-provider messaging.

## Primary references

- [Codex configuration and MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), plus the installed CLI's `exec --help`, `exec resume --help` and `mcp list --help`.
- [OpenCode config precedence](https://opencode.ai/docs/config/) and [MCP definitions](https://opencode.ai/docs/mcp-servers/).
- [Goose recipe schema](https://goose-docs.ai/docs/guides/recipes/recipe-reference/), [session extension collection](https://github.com/aaif-goose/goose/blob/main/crates/goose-cli/src/session/builder.rs), and [template preprocessing](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/recipe/template_recipe.rs).
- [Kiro CLI options](https://kiro.dev/docs/reference/cli-commands/), [agent locations](https://kiro.dev/docs/custom-agents/creating/), [MCP inheritance](https://kiro.dev/docs/custom-agents/configuration-reference/), and [profile environment](https://kiro.dev/docs/reference/settings/).

Reversal: revert this change. No user configuration, schema, or message-store
migration needs to be reversed.
