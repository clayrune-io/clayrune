# Native recipes and changed-line coverage (DH-M4)

MC-960 / `d813ea78`. Backend continuation of DH-M2/M3; no UI, installation,
activation, managed merge hold or release gate. M5 owns integration and M6 owns
real toolchain/dogfood acceptance. A native command result proves that command,
not a complete practice, semantic security review or licence compatibility.
The practice descriptors retain Unsupported target coverage; native recipes do
not turn unimplemented parts of P02/P06/P07/P08/P09/P10/P16 into supported checks.

## Owners and internal entry

- Each recipe is its own `data/dev_harness_catalogue/checks/<id>.json` file.
- `mc/dev_harness_checks/recipes.py` loads data and compiles approved argv;
  `reports.py` validates report envelopes and projects only counts/status.
- `output.py` owns bounded memory-only capture; `execution.py` composes version
  and scan calls through M3. `coverage.py` owns report/source binding and the
  installed diff-cover invocation. No scanner or coverage engine is copied.
- The M3 runner adds composition/capture seams only. `agent_jobs.py` and
  `agent_worktree.py` are unchanged by M4.

The internal coordinator supplies an approved launcher, explicit targets,
native config/rule/lock files and installed distribution/runtime/plugin roots:

```python
from mc.dev_harness_checks.recipes import make_step
from mc.dev_harness_runner import evaluate

step = make_step('ruff', launcher=['<installed-ruff-executable>'],
    targets=['src'], configs=['pyproject.toml'],
    tool_roots=['<absolute-installed-distribution-directory>'])
result = evaluate(project=project, steps=[step], logs=logs,
                  target=target, incoming=incoming,
                  register=register, unregister=unregister)
```

Placeholders are documentation, not executable defaults. `make_step` creates
M3's existing step fields plus an internal `native` object containing recipe,
launcher and tool roots. There is no new API accepting commands or report bodies.
The report-only CLI may inspect such steps but cannot mint trusted receipts.
`run_managed` remains the only internal receipt owner. Receipts still have
`authorizes_merge=false` and do not enter measured-preview activation evidence.

Tools must already be installed. There is no npx resolution, package-manager
installation, implicit rule download, scanner fix or lockfile replacement.
No native executable is invoked outside M3's registered, cancellable executor.
Version probes, coverage export and diff-cover use that same executor.

## Released compatibility baselines

These are narrow explicit compatibility pins, not recommendations to downgrade
an existing project. A different version returns Error until that recipe's
contract and fixtures are reviewed. Registry metadata confirmed each Python/npm
version below exists; live native binary compatibility remains unverified.
Recipe data links the individual upstream release sources.

| Recipe | Tool release | Result contract |
| --- | --- | --- |
| pytest | 8.4.2 | Existing approved suite argv; exit 0/1 only |
| vitest | 3.2.4 | Existing approved run argv; exit 0/1 only |
| Ruff | 0.13.3 | JSON diagnostics, no cache |
| ESLint | 9.36.0 | JSON counts; fatal/parser diagnostics are Error |
| Pyright | 1.1.405 | Existing approved command, exit 0/1 |
| TypeScript | 5.9.3 | Existing approved command, exit 0/1/2; 1/2 are findings |
| npm build | 10.9.3 | Existing approved project build command |
| Gitleaks | 8.28.0 | Full candidate target scan, JSON stdout, 100% redaction |
| Bandit | 1.8.6 | JSON; nonempty measured scope and no scan errors |
| Semgrep CE (optional) | 1.136.0 | JSON, strict errors, metrics off, approved local YAML rules |
| pip-audit | 2.9.0 | JSON against existing resolved requirements; no dependency resolution/install |
| npm audit | 10.9.3 | JSON audit schema 2, scripts disabled |
| OSV-Scanner | 2.2.1 | JSON with all packages, including clean packages |
| Existing licence-policy command | Protocol 1.0.0 | Owner-supplied existing command, envelope below |
| Python coverage | Coverage.py 7.10.6 | Fresh coverage run plus XML export |
| JS/TS coverage | Vitest 3.2.4 | Fresh existing run producing LCOV |
| Changed-line calculation | diff-cover 10.2.0 | Native JSON statistics, explicit base and floor |

The licence pin is the existing command's declared wrapper protocol, not a new
licence tool or a claimed upstream scanner release. No wrapper is installed by
Clayrune. Its `--version` must identify 1.0.0, and its approved command must emit
`{"schema_version":1,"packages":[{"decision":"allowed|rejected|unknown"}]}`.
Any rejection is Fail. Unknown metadata is Review required, including exit 0;
empty/malformed results are Error. This does not replace SPDX/compatibility
assessment, advisory freshness or dependency graph semantics in DH-A10.

Configured unit/type/build/licence/coverage argv is supplied explicitly by the
project owner. A zero exit proves only that approved command. The harness cannot
infer assertion quality, detect every no-op custom command or distinguish a
tool's internal crash if that tool reuses its ordinary findings exit code.
Scanner/lint/audit argv and output flags come from the data recipes; unknown
exit codes, version mismatches, invalid/partial reports, missing executables,
timeouts, cancellation and exit/report disagreements are Error.

## Output and input identities

Native stdout is piped into a bounded 8 MiB in-memory buffer; stderr stays
DEVNULL. The existing managed-job logs remain empty and job callbacks contain
no raw stdout. Overflow is drained/discarded and returns Error. Duplicate JSON
keys, nonfinite JSON and unknown report shapes cannot pass. Validators retain
only fixed fields, counts, statuses, versions and digests; no filenames, code
snippets, matches, advisories or arbitrary tool messages are copied from scanner
reports into chat/activity/receipts. There is no normalized findings browser.

Gitleaks is forced to `--redact=100 --report-format=json --report-path=-`.
Findings must have `Secret=REDACTED` and a redacted Match. Unredacted or malformed
results return Error and are discarded in memory. No security scanner report
file is created, even temporarily. Its full-target scan includes new files and
can also report existing findings; new-versus-legacy ratchets are not implemented.
The upstream [redaction implementation](https://github.com/gitleaks/gitleaks/blob/v8.28.0/report/finding.go)
and [stdout report contract](https://github.com/gitleaks/gitleaks/blob/v8.28.0/report/report.go)
are the adapter basis.

M3 freezes source/config/executable identities and revalidates them. M4 adds
recipe/code hashes, declared installed tool-root inventories (file bytes plus
additions/deletions), native config discovery and the inherited environment
digest. Only the environment hash is retained, never its values. External
Gitleaks/Semgrep/Coverage configuration overrides are refused. Known native
configs are included by per-recipe globs; supplied transitive rule/config/plugin
files remain mandatory. A newly appearing ignored native config invalidates
the run. Recipes, environment and complete declared tool roots are rechecked
after all steps; coverage also binds the diff-cover executable.

Closure is a reviewed caller declaration, not automatic dependency discovery or
an OS sandbox. The owner must include the interpreter's loaded distributions,
plugins, rules, transitive configuration and lock inputs. Arbitrary native
commands can write their own files or use external services. Audit network or
database failures are Error, not zero vulnerabilities. M5 must bind the approved
plan/closure to active policy and revalidate under its integration lock.

## Fresh coverage contract

For `coverage-python`, the launcher is the installed Coverage.py command (e.g.
`[python, '-m', 'coverage']`), and configured args are the existing approved
`run ...` command. M4 subsequently invokes that same launcher with
`xml -o <report>`. For `coverage-javascript`, use the installed Vitest launcher
and existing approved run/export args. Its output must actually be LCOV; an
arbitrary JS runner or incompatible export is not silently substituted.

The step's additional `coverage` object has exactly these fields:

```json
{
  "report": ".clayrune/coverage/report.xml",
  "format": "coverage.py-xml-7.10.6",
  "source_roots": ["src"],
  "floor_percent": 75,
  "diff_launcher": ["<already-installed-diff-cover>"]
}
```

The floor is illustrative, never a default. JS uses format `lcov-v1`. The
report path must already be Git-ignored and absent in the disposable frozen
checkout. Existing Coverage.py data is refused rather than reused/overwritten.
The fresh producer and export must succeed before parsing. Classes/LCOV records
resolve to existing frozen source under declared roots; absolute paths must
remain inside that checkout, ambiguous source-root mappings are Error. XML
entities/DTDs, empty/duplicate/out-of-range line maps and foreign source roots
are refused. Every changed/new production source path in those roots must be
represented with executable measurements; a newly added file cannot disappear
from the denominator. Source/config/report digests bind the execution receipt.

Installed diff-cover computes changed executable lines using B..T for an
integration or explicit frozen HEAD for a dirty preview, including untracked
files. It receives `--diff-range-notation=..`, `--include-untracked`, explicit
`--fail-under` and JSON output. The adapter checks nonzero denominator, source
mapping, line/count consistency and exit/result agreement; it does not build
another percentage calculator. No changed executable measurement yields Error
pending explicit verification classification, never 100%. No branch coverage
or aggregate/base-to-candidate delta is inferred (DH-A3 owns delta).

## Validation and remaining acceptance

Per-tool positive/finding/crash fixtures live in separate files under
`tests/fixtures/dev_harness/`. Tests exercise actual registered M3 subprocess
execution using synthetic tool executables, including both coverage pipelines,
version/missing-tool/overflow/config mutation boundaries and persistent Review
required licence receipts. These are adapter/lifecycle tests, not real-tool
compatibility probes.

```powershell
python -m pytest -o addopts= -q tests/test_dev_harness_native.py tests/test_dev_harness_coverage.py
python -m pytest -o addopts= -q tests -k dev_harness
pyright mc/dev_harness_checks mc/dev_harness_runner.py
git diff --check
```

No pinned native suite/scanner/diff-cover was installed or run. Real Python and
JS coverage exports, scanner/advisory-service behavior, complete installed
closure declarations, POSIX execution and packaged builds remain unverified.
The catalogue/checks data packaging gap remains with M1's existing packaging
gap. M5/M6 must retain these prerequisites; fixtures cannot authorize activation.
No static assets changed; USER_GUIDE/boot-smoke changes are not applicable.

Final branch validation on Windows:

- Focused command above: **207 passed, rc=0** (83.71s).
- Full command above with `--tb=short`: **590 passed, 1 skipped,
  16001 deselected, rc=0** (163.47s).
- Scoped pyright: **0 errors, 0 warnings, rc=0**; diff check: rc=0.
- The existing skip is the POSIX newline-filename test; Windows forbids that name.

## DH-M5 A2a compatibility binding

[Native approval](DEV_HARNESS_NATIVE_APPROVAL.md) pins each recipe and adapter
code identity through its separate shipped `<recipe>.fixtures.json` manifest.
Tests recompute the fixture-file digests and exercise positive/finding/crash
cases; runtime validates shipped metadata without requiring test files. These
manifests do not prove native project execution. Required coverage recipes
are refused with `coverage_required_unmeasurable` under the approved M5
section 9 amendment; explicitly selected warning coverage still needs a
human-selected P02 changed-line floor. M4 execution and report behavior remain
unchanged. A2b's branch candidate executes approved steps through M3 and retains
complete native identities in trusted receipts. Early version failures remain
incomplete errors; they are never fabricated into complete activation evidence.
Candidate config/closure changes are checked against the approved payload in
the disposable checkout before a native process starts. Warning failures remain
visible and do not skip later required steps. See the
[A2b handoff](DEV_HARNESS_M5_PLAN_CONTRACT.md#9-a2b-implementation-candidate).
M6 real installed-tool, packaging and dogfood acceptance remains pending.
