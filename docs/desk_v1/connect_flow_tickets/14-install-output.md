# MC-1062/14: dependency and build output follow-up

The original launch manifest applied the staged-source limits (10,000 files /
100 MB) after install and before every launch. A dependency tree such as
googleapis exceeded those limits, and even smaller installs were unnecessarily
hashed at every start.

## Source pin and generated output

New repository approvals use `desk-github-connection/2`; the operation fingerprints
the source hashes, selected steps and file-check policy. `github_manifest.py`
prunes recorded generated paths before descending into them. Every reviewed
source file must keep its hash during setup and before every start. A missing or
changed source, including a file shipped in `dist/` or `node_modules/`, refuses
setup/start before credential resolution. New repository files outside the
recorded output paths and the existing `.git`/`.meta.json` exclusions also refuse start.

`github_install_output.py` captures outputs once after the selected steps. It
records hashes/sizes of generated files and internal link targets, with bounds
of 200,000 entries, 2 GB of file bytes, and 64 directory levels. External links,
junctions and unsupported file types refuse Save. Generated directories are only
pruned if they contain no reviewed source; mixed source/build folders retain
their source checks. Corrupt exclusions cannot hide source files or ancestors.

Generated files/directories are **not rechecked at launch**. They may be changed
after Save and those changes can run code with the server's access. The card's
risk list and install-step note state this explicitly. The commit pin applies to
reviewed source; it is not a dependency pin or a sandbox. Old approvals without
the versioned policy keep their previous full-tree check; they are not silently
upgraded. Reverting this implementation requires reviewing newly saved v2
connections again.

`github_install_steps.py` uses `npm ci --ignore-scripts` for a staged npm lockfile,
otherwise `npm install --ignore-scripts`. This preserves reviewed lockfile bytes.
Every dependency/script step stays off initially and needs the existing final
human/passcode Save. Only selected package script bodies run, so a TypeScript
repository can select its `build` without also running `prepublishOnly` or
dependency lifecycle hooks.

## npm environment defect found by the real probe

The fixed script environment previously set both npm userconfig and globalconfig
to the same null device. Actual npm refused with `double-loading config ... as
global, previously loaded as user` before installing. `custom_npm_scripts.py`
now points them at distinct absent files under the empty temporary home. It
continues to build the child environment from scratch, with no inherited token,
operator npm config, or vault resolution. An offline npm configuration command
regression exercises this startup path.

The next probe installed dependencies and built TypeScript successfully, but
`npm install` added five peer markers to the reviewed lockfile. The source check
correctly refused Save. That finding drove the displayed `ci` step for locked
repositories; the source guarantee was preserved.

## Verification

The affected backend run passed **153 tests in 36.57 seconds**:

```powershell
python -m pytest tests/test_desk_connect_github.py tests/test_desk_connect_github_output.py tests/test_desk_connect_npm_scripts.py tests/test_desk_connect_npm_launch_gate.py tests/test_desk_connect_npm_node_paths.py -o addopts='' -q
```

The local integration regression runs a real fixture install subprocess
creating 10,001 dependency files, a separate build subprocess, and a Node launch
through the approval gate. It records all outputs, proves launch checks prune
generated directories, and proves source edits refuse the connection. Additional
tests cover source/build overlap, unsafe links/junctions, corrupt exclusions,
source deletion, independent output bounds, old approvals and npm configuration.
Pyright basic passed with **zero errors across six affected Python modules**.

All **22 `desk-v1-connect*.mjs` browser smoke entrypoints passed in 321.26
seconds**, including Connections and connection status. The simplify smoke
asserts the generated-output risk text on the GitHub card at desktop/phone
widths. Main-checkout Playwright dependencies were linked into the worktree
for the run; no dependencies or browsers were installed.

The actual public repository at commit
`36142e30de15841139ae9613c568e0bd09aef372` installed **205 packages** and built
TypeScript successfully. Timings from the final probe:

| Check | Time/result |
|---|---|
| Shallow clone | 0.56 s |
| Displayed `npm ci --no-audit --no-fund --ignore-scripts` | 18.22 s, exit 0 |
| Selected `build` body (`tsc`) | 4.50 s, exit 0 |
| Provision including one-time output record | 72.12 s, registered in the in-memory fixture |
| Subsequent source check | 0.0172 s, unchanged |

The snapshot held **50 reviewed source files**, **7,436 generated files** and
**269,293,986 total bytes** across 8,707 entries. `node_modules/` and `dist/`
were pruned from subsequent source checks. The real repository exceeded the
original byte cap; the local fixture exceeded the original file cap.

The public-repository probe uses the actual Git staging and provisioner with a
scratch `INSTALLS_ROOT`, a credential-free fixed child environment, and an
in-memory MCP writer. It does not register a real MCP connection, read vault
credentials, restart Clayrune, or establish a vendor session. Install/build is
distinct from authenticated YouTube tool use.
