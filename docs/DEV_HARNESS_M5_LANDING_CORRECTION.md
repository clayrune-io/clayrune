# DH-M5 A2b: Hivemind native-candidate location correction

Base: `05af493a`, which already contains the merge-review fixture correction.
This unit changes native-candidate allocation and its regression coverage; it
does not repeat that fixture fix or change the integration-boundary contract.

## Root cause

Both reported Hivemind cases reproduced from the main checkout. Tracing the
existing worktree owner revealed Git's actual refusal:

```text
fatal: '$GIT_DIR' too big
```

The coordinator passed the Hivemind integration workspace to the native runner.
The runner correctly used it for source revalidation, but also used it as the
parent for another disposable candidate. Candidate creation therefore nested
`.clayrune/agents/dh_<id>` beneath `.clayrune/hivemind-integration/<id>`.
Git rejected that location in the main checkout's Windows pytest paths.
The earlier worktree's shorter temporary path did not trigger the same refusal.
No live configuration or harness-store leak was found in these failures.

The runner recorded `status=error`, `code=worktree_create_failed`, `results=[]`.
The required-failure test's receipt injector then indexed `results[0]`, causing
the reported secondary `list index out of range`. Without that injector, the
runtime test correctly stopped at the standards hold instead of completing.

## Correction and retained boundaries

`mc/dev_harness_runner.py` passes the registered project as the private candidate
lifecycle owner. Only allocation moves: source freezing, original-workspace
revalidation, exact B/H/T, disposable-tree validation, approved plan/config/tool
checks, registered-main tracked-dirt checks and process registration remain.
Candidates still use the existing isolated create/remove lifecycle with no
shared runtime links. Dirty Preview continues to use its original full overlay.
The runner already participates in the approved checker digest, so this source
change also invalidates earlier approved checker identities as designed.

`tests/test_dev_harness_candidate_location.py` asserts the allocation parent
independently of platform and temporary-path length. Real native execution must
complete, and an injected creation refusal must retain an Error receipt,
standards hold, worker and original checkout without reaching smokes.
The existing Hivemind landing cases cover required failure, runtime-file writes
and registered-main tracked dirt.

## Earlier test report evidence

The earlier native transcript confirms a Windows PowerShell invocation of:

```text
python _scratch/run_checks.py boundary-required-final python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review"
```

It ran in the earlier agent worktree. The wrapper used `subprocess.Popen` with
inherited cwd, waited for the actual child, recorded `rc=0`, and exited with that
same code. Both the native tool output and saved log contain this literal line:

```text
1038 passed, 15 skipped, 16796 deselected in 453.62s (0:07:33)
```

Thirteen skips explicitly say `needs a POSIX sh`; the other two are the Windows
newline filename case and optional missing `psutil`. Thus the earlier report
was a real passing run with skipped hook coverage, not evidence that the hook
fixture worked when those cases executed. Its Windows-worktree scope also did
not prove main-checkout behavior. The wrapper did not fingerprint source files
or record the resolved interpreter, so those stronger claims cannot be inferred
from the result JSON alone. Original command/output evidence is retained in the
item journal and scratch artifacts, outside committed operator data.

## Validation

Five targeted Hivemind cases passed from the main checkout:

```text
5 passed, 18 deselected in 32.21s
```

Runner Pyright: `0 errors, 0 warnings, 0 informations`, exit code 0.

The main-checkout suite uses a temporary overlay of the changed runner and new
test file, after checking that the existing runner matches the base. The wrapper
records cwd, argv, exit code, log hash and source hashes, then restores original
bytes and removes its new test file. It verifies main HEAD remains unchanged.
Git's `sh` is explicitly available on PATH so the prior shell skips execute.
The required invocation is:

```text
python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review" -q -p no:cacheprovider
```

The repository already supplies `-q` in its default pytest options; the exact
invocation therefore suppresses the totals line. A second run of the same
selection changes reporting only, to capture a literal summary:

```text
python -m pytest tests -k "dev_harness or agent_worktree or hivemind or agent_jobs or merge_review" -o addopts=-ra -q -p no:cacheprovider
```

The exact requested command completed with **exit code 0** (548.96s including
the wrapper). Only the optional missing `psutil` and Windows newline-filename
cases skipped; all thirteen hook cases executed. It reported one SyntaxWarning
from an unchanged `tests/test_agent_routes.py:2361` docstring (`\T` escape).
The overlay restored both owned paths and main HEAD remained `05af493a`.
The reporting-only rerun completed with **exit code 0** (539.76s including the
wrapper), with this literal final line:

```text
1053 passed, 2 skipped, 16800 deselected in 536.62s (0:08:56)
```

It used the same source/test hashes and main HEAD as the exact-command run.
Its two skips are the same cases listed above; no warning was emitted on this
second collection. Both main-checkout overlays were restored, and the new test
file was removed from main afterward. The changed runner passed Pyright with
zero errors and warnings. The earlier 1,038-pass count must not be reused as
this correction's validation result.

No UI, configuration, credential, dependency, installer or approval-gate change;
README, USER_GUIDE and frontend smokes do not apply. No merge, push, restart,
live activation, M6 installed-tool acceptance or deployment is claimed.
