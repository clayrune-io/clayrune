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

## Original implementation validation

Final focused suite: **967 passed, 4 skipped**, rc **0**, 35.73 seconds,
across 37 matching files. The four skips are the opt-in live Codex session
and three opt-in installed vendor fence checks. The full suite was not run
for that original candidate; post-merge repair results are recorded below.
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

## Post-merge regression diagnosis (13bcee5b)

Regression repair base: master `4c71ee1b`, on fresh branch
`clayrune/builtins7b-regression-fix`. Classification: **(b), tests not isolated
from the new MCP resolution**, rather than a demonstrated product regression.
Unchanged-master replay reproduced exactly the nine failures listed in
`BUILTINS_3_SR2_CYCLE_TASK.md`, rc=1:

- Five Codex incognito/context checks used a Popen recorder for model stdin
  without implementing the context-manager protocol needed by the bridge's
  new `subprocess.run` configuration probes.
- Three workflow-wake checks replaced the CLI with a script emitting model
  JSONL even for `mcp list --json`. The bridge correctly refused that non-list
  response before model dispatch, so there was no step session ID.
- The Qwen-era provider check still asserted Codex received an empty config.
  BUILTINS-7b deliberately supplies canonical definitions to Codex; the test
  now checks the exact stdio/HTTP definitions and unchanged Claude behavior.

`tests/codex_mcp_fixture.py` is an opt-in fixture for those model-only harnesses.
It is registered once in `tests/conftest.py`, so modules importing the shared
spawn recorder also see its fixture dependency; it is not autouse.
It supplies a deterministic Clayrune catalog and temporary `CODEX_HOME`, stubs
only the two offline `mcp list` shapes, reads the real generated TOML profile,
and asserts the inherited native entry is disabled and plugin loading is off.
The actual resolver, bridge, translation, equality check and model spawn path
remain in use. Other `subprocess.run` commands delegate normally; the workflow
tests still use a real subprocess/reader/completion hook. Context fingerprints,
memory exclusions, reconciliation and error-wake assertions remain intact.
Incognito dispatch/native resume additionally assert zero enabled MCP servers.
Existing explicit-empty, malformed-config and native-profile refusal checks
remain unchanged in the bridge/runtime suites. No test was removed or given a new skip,
and no production module or fence changed.

Repaired exact-nine replay: **9 passed, rc=0**. All `tests/test_mcp*.py` and
`tests/test_builtins*.py` files plus the affected route/Qwen/workflow files and
`tests/test_session_mcp_launch.py` and vendor-fence route checks:
**234 passed, rc=0**. Final main-app full suite: **18,529 passed, 1 failed,
54 skipped and 1 expected xfail, rc=1**, in **1,928.644 seconds** (18,585 JUnit
cases, zero setup errors). This is not an entirely green full suite. All nine
original failing cases pass within the full run; none of the original failures
remains. Artifacts under
`_scratch/builtins7b_{baseline,nine,focused,full}`
contain exact command JSON, pytest log, JUnit XML and pytest rc. The first full
attempt was stopped by its captured pytest PID before the final catalog
isolation edit and retained as `builtins7b_full_aborted`; it is not a completed
suite result. No `mc/` module changed, so the requested touched-module pyright
gate is not applicable; the six existing bridge modules nevertheless passed
pyright with 0 errors, rc=0. Read-only installed native probes also accepted
nonempty/empty selections and incognito/explicit-empty resume profile setup in
a temporary `CODEX_HOME` without altering its native config. No MCP server or
model turn was started. No UI or install behavior changed, so USER_GUIDE,
README, AGENTS, browser smokes and VM checks need no changes. No new dependency,
merge, push, restart or live model/tool invocation is included. Dave owns review
and integration; revert the test/documentation commit to undo this repair.

The first completed full attempt had 18,518 passes, zero assertion failures,
12 setup errors, 54 skips and one expected xfail, rc=1 (2,088.338 seconds).
This iteration had not registered the helper where imported shared-fixture
consumers could see it. All twelve errors were
`test_vendor_fence_routes.py::test_followup_and_interrupt_return_visible_refusal`,
for Gemini/Qwen/OpenCode/Goose/Aider/Kiro crossed with followup/interrupt,
each reporting `fixture 'codex_mcp_cli' not found`. These were repair-introduced
setup errors, not baseline failures. Registration was corrected centrally and
all twelve cases now pass in the 234-check focused replay. Evidence is retained
as `_scratch/builtins7b_full_fixture_errors.*`; a fresh full run covers the final
registration. No assertion or provider gate was weakened.

The final registration run reported a separate live-browser failure at
`test_browser_cdp_pipe_live.py::test_named_profile_is_saved_by_the_graceful_pipe_close`.
Unchanged master `4c71ee1b` reproduced this same node on the third full-file
browser replay, failing at its first-frame wait with
`AssertionError: first frame never became true`; the preceding two file replays
and five single-node replays passed. Evidence is retained under
`_scratch/builtins7b_browser_master_file_{1,2,3}.{log,xml,rc}`. These baseline
replays use a detached worktree of that exact master and the same interpreter.
Browser source/tests are unchanged by the repair. The final full run has exactly
the same first-frame assertion failure as the unchanged-master replay. This is
the sole remaining failure; it has not been repaired or described as green.
Completion date: 2026-11-01. No new product code or gate change is needed for the
nine reported regressions; the intermittent browser failure remains separate
follow-up work for the reviewer.

| Run | Result | rc |
|---|---|---:|
| Unchanged master `4c71ee1b`, exact nine | All nine reported cases failed | 1 |
| Final repair, exact nine | 9 passed | 0 |
| Final repair, all MCP/builtins files and affected consumers | 234 passed | 0 |
| Final repair, full main-app suite | 18,529 passed; one master-reproduced browser failure; no errors; 54 skipped; one xfail | 1 |
| Unchanged master, browser file replay 3 | Same named-profile first-frame timeout; 4 passed, 1 failed | 1 |
| Six existing production bridge modules, pyright | 0 errors | 0 |

Reproduction from this checkout (the exact nine node IDs remain listed in
`BUILTINS_3_SR2_CYCLE_TASK.md`):

```powershell
$mcpRegressionFiles = rg --files tests | Where-Object { $_ -match '(^|[\\/])test_(mcp|builtins).*\.py$' }
python -m pytest -q -p no:cacheprovider $mcpRegressionFiles tests/test_native_followup_context_routes.py tests/test_qwen_live_pass_2026_09_19.py tests/test_workflow_step_reconcile.py tests/test_session_mcp_launch.py tests/test_vendor_fence_routes.py
python -m pytest -q -p no:cacheprovider tests --junitxml=_scratch/builtins7b_full.xml
```
