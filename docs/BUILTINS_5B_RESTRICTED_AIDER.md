# BUILTINS-5b: restricted source-pinned Aider adapter

Backlog `10796682`, part 2. Base `779a39e2`. Sol_Tobin.
Branch candidate only; no merge, push, restart or live enablement.

The default remains unattended refusal. A human may opt into the restricted
adapter with boolean `aider_restricted_adapter_enabled: true` through the
existing passcode-protected config API. There is no new UI toggle. Missing,
false, string or numeric values do not opt in. Attended launches and launches
with fencing disabled retain their existing behavior. Rollback: switch the
flag off; the next dispatch/follow-up refuses. No running process is changed.

## Supported surface

Dave's binding allowlist is implemented without changing `steward/fence.py`:

- `aider-chat==0.86.2`, CPython 3.12, and the measured dependency versions in
  `mc/aider_source_pin.py`. Both preflight and the actual child verify all 81
  Aider Python sources and the three model resource files against SHA-256.
  Shadowing native modules and sourceless bytecode are refused; the child uses
  a fresh random bytecode prefix so existing cached bytecode is not loaded.
  Import provenance must resolve to that verified package, before executing
  any Aider code; a project/source-path module cannot substitute for it.
  Version, source, resource or required API shape drift refuses startup.
- Whole-file and `diff` SEARCH/REPLACE formats only. Default is `whole`.
  `--edit-format diff` is accepted by the restricted runner. Unsupported
  factories, direct format constructors and direct edit methods refuse.
- Exact `/run` and suggested shell commands use the same `classify_action`
  shell guard. GitPython, `subprocess.run` and other Popen callers pass through
  that guard too, with their actual cwd. `/git` itself is unavailable.
- Every accepted Python filename write, preparation mkdir/touch, rename and
  delete is classified before mutation. Resolved aliases, both sides of a
  rename, directory-fd ambiguity and guard-source/interpreter writes are
  handled explicitly. Fence classifier errors fail closed with a reason.
- Every other slash command (including abbreviations and `!`) refuses before
  execution. `/voice`, `/web`, `/save`, `/git`, patch/udiff, editor, browser/GUI,
  help/browser installers and direct native voice entrypoints have named
  refusals. The POSIX pexpect branch is unavailable; accepted shell requests
  use the guarded subprocess path on every platform.

## Modules and launch contract

`mc/aider_source_pin.py` owns the source/version manifest.
`mc/aider_guard.py` owns the accepted operations and pre-execution decisions.
`mc/aider_runner.py` owns the one-turn coding lifecycle.
`mc/aider_launch.py` owns server-side opt-in, interpreter resolution and
per-spawn preflight. Runtime and settings monolith changes are only boundary
calls, a default and the config allowlist entry; the new concern lives outside
them. No new project dependency or installer behavior is introduced.

The server selects the interpreter next to the resolved Aider installation,
then runs it with `-I -B` and the dedicated runner. It does **not** run
`aider.main`, load `.aider.conf.yml`/dotenv/user model extensions, construct
analytics/history/notifications/RepoMap/watchers, or offer optional installers.
The runner ignores installed bytecode and disables new bytecode writes.
Model metadata cache/web fallbacks are disabled. Model provider requests still
use the existing provider environment; no credentials are copied into argv.
Weak/editor references use the selected model; no architect mode is enabled.

Preflight verifies source, installs the real guard, constructs both accepted
coder formats, and checks real fence deny/allow decisions without mutating
tools or making a model turn (GitPython imports perform guarded version reads).
Unexpected initialization refusals, invalid proof, failed load, timeout or unknown
interpreter layout refuses through `VendorFenceError`. Dispatch and each
follow-up use the same preparation path. The actual child checks everything
again; source changes between preflight and startup do not reuse stale proof.
Caught upstream denials remain printed and cause child exit 2.

## Verification

`tests/aider_guard_probe.py` repeats the negative audit scenarios with the
adapter installed. It calls real Aider factories, edit-preparation, command
preprocessing, native command methods and edit parsers. Model responses are
synthetic; no provider request or microphone is used. The five prior bypasses
now prove refusal and absence of the corresponding side effect: preparation
creates no protected file, patch deletes nothing, save preserves original
bytes, git starts no subprocess, voice creates no native WAV. The original
negative audit remains as a control, documenting what happens without this
adapter rather than being weakened into mocks.

Other installed controls cover both allowed formats editing an unprotected
file, both protected formats, allowed preparation and shell/git reads, denied
shell/GitPython push, direct unsupported/native features, exact command
matching, source drift, and the real runner ignoring project CLI configuration.
Portable tests cover version/dependency drift, flag-off refusal, strict opt-in,
preflight failure, dispatch/follow-up wrapping and the human config gate.

Reproducible PowerShell commands (use an isolated environment, never a global
CLI install):

```powershell
uv venv --python 3.12 _scratch/aider-proof-env
uv pip install --python _scratch/aider-proof-env/Scripts/python.exe aider-chat==0.86.2 GitPython==3.1.46 litellm==1.81.10 diskcache==5.6.3 soundfile==0.13.1 pexpect==4.9.0 pillow==12.1.1
$env:CLAYRUNE_TEST_AIDER_ADAPTER='1'
$env:CLAYRUNE_TEST_AIDER_AUDIT='1'
$env:CLAYRUNE_AIDER_AUDIT_PYTHON=(Resolve-Path '_scratch/aider-proof-env/Scripts/python.exe').Path
python -m pytest -q -o addopts='' --tb=short tests/test_aider_guard_installed.py tests/test_aider_interception_audit.py
```

```text
python -m pytest -q -o addopts='' --tb=short tests/test_aider_launch.py tests/test_vendor_fence.py tests/test_vendor_fence_routes.py tests/test_provider_runtimes.py tests/test_fence_fail_closed_unattended.py tests/test_launch_marker.py tests/test_human_proof_guard.py tests/test_settings_routes.py tests/test_provider_env_scope.py tests/test_engine_fallback.py
pyright mc/aider_source_pin.py mc/aider_guard.py mc/aider_runner.py mc/aider_launch.py mc/vendor_fence.py mc/agent_runtime.py
git diff --check
git diff --exit-code -- steward/fence.py
```

Measured results, all **rc=0**:

| Check | Result |
|---|---|
| Installed adapter + original negative audit command above | **50 passed**, 64.30 seconds |
| Final import-provenance addition, command below | **5 passed, 38 deselected**, 7.60 seconds |
| Portable runtime/settings command above | **667 passed, 1 skipped, 1 xfailed**, 36.74 seconds |
| Final persisted human-config assertions, command below | **6 passed, 77 deselected**, 2.21 seconds |
| Pyright command above | **0 errors**, including all four new modules |
| Both git diff commands above | Clean; human-owned fence unchanged |

The import-provenance test was added after the full installed run; the final
focused run covers it, source/resource/native drift, and real selected-install
preflight plus execution. All **51 distinct installed cases** in the final
tree were exercised. Long runs used a foreground wrapper that registered its
own PID with Process Manager and called `pytest.main` with the exact arguments
shown in the replay commands. The existing skip is the opt-in live Codex probe;
the xfail is the unapplied human-owned fence timeout fix, unrelated to Aider.

```text
python -m pytest -q -o addopts='' --tb=short tests/test_aider_guard_installed.py -k 'drift or shadow or selected_install'
python -m pytest -q -o addopts='' --tb=short tests/test_human_proof_guard.py -k config_put
```

The changelog and user guide are updated. README and agent instructions do not
need new conventions or install steps; the experimental install contract is
above. No static assets changed, so the frontend boot smoke is inapplicable.

## Limits

This implements the reviewed Aider surface, not an OS sandbox. Shell children
have the existing fence's classification boundary, not inherited Python IO
hooks. Python audit hooks alone do not cover arbitrary native libraries; the
unreviewed native features are unavailable instead. No full-CLI compatibility,
provider round trip, POSIX execution or packaged-app certification is claimed.
Frozen builds explicitly refuse this source-runner path. No human-owned fence
change was needed, so no fence patch is included. Ron still owns enablement.
