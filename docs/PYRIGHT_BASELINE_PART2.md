# Pyright baseline part 2

Scope: behavior-preserving typing cleanup in `mc/agent_runtime.py` and
`mc/distiller.py` only. Base: `1dfcd0f5`; branch: `clayrune/agent/97cafe01683b`.
Continues the measurement in `docs/PYRIGHT_BASELINE.md` on branch
`clayrune/agent/204922582242`, commit `858dcf90`.

## Type checking

Native Windows, Pyright 1.1.410, unchanged `pyproject.toml`, command
`pyright --outputjson`. Both runs analyzed 435 files, with zero warnings.

| Scope | Before | After |
| --- | ---: | ---: |
| `mc/agent_runtime.py` | 22 | 0 |
| `mc/distiller.py` | 20 | 0 |
| Repository total | 219 | 177 |

All remaining diagnostics (including messages and source ranges) are identical
between runs. The earlier 221-error baseline predates the two-error watermark
fix in `1dfcd0f5`. No ignores, exclusions, CI changes, or other production files
were added or changed.

## Changes and behavior check

- Runtime: express the existing piped-text stdout contract; retain the original
  guarded dictionary lookups in OpenCode parsing; permit the existing
  `dict.get(None)` lookup miss while following transcript parents.
- Distiller: annotate optional injected hooks and cast required hooks/paths at
  uses that require startup registration; retain every `None` sentinel and
  existing guard. Cast evidence strings only inside their existing truthiness
  checks. No learning safety rail changed.
- Keep all expressions, lookup counts, branches, exception handlers, and
  defaults intact. No assertion, validation, coercion, new fallback, or logging
  change was introduced. Exception handling remains unchanged under the
  dispatch's explicit type-only restriction.
- An AST comparison against `1dfcd0f5` passes for both complete modules after
  erasing typing imports, annotations, and `cast(type, value)` wrappers. This
  checks executable structure independently of the test results.

## Regression verification

Use the project virtual environment. This checkout resolves to Python 3.14.0;
the previous session used Python 3.12. Before and after in this session use
the same executable and frozen selection.

The selection contains 113 test files: all `test_distiller*.py`, all
`test_agent_runtime*.py`, and test modules referencing/importing either module.
A recursive AST scan confirms no direct-import test module is omitted.

Before: **2,859 passed, 2 failed, 8 skipped**, 2,869 collected, 175.02 seconds,
pytest exit 1. Pre-existing failures:

- `tests/test_agent_routes.py::test_no_unexpected_agent_routes`: the registered
  delegation `mark-handled` route is absent from the test's expected routes.
- `tests/test_provider_architecture_guard.py::test_feature_boundary_has_no_undocumented_provider_coupling`:
  the existing `mc/delegation_transcript_reconcile.py` import is outside the
  architecture test's allowlist.

After: **2,859 passed, 2 failed, 8 skipped**, 2,869 collected, 156.66 seconds,
pytest exit 1. JUnit comparison confirms identical counts, failing test names
and messages, and skipped test names. No new failure. The before-only warning
is an existing invalid escape sequence in a test docstring; the cached after
run does not repeat it.

The dispatched known failure is outside that selection, so it was also run
separately, unchanged, before and after:

```text
python -m pytest tests/test_scribe_log_fallback.py::test_codex_transcript_path_lookup_failure_falls_back_cleanly
```

Both runs: **1 failed**, pytest exit 1. `RuntimeError('unknown provider')` from
the test's `get_runtime` stub escapes through background model selection in
`mc/memory.py`, after the transcript lookup fallback logs its warning.
It is pre-existing and outside this task's file/behavior scope.

Eight broad-selection skips cover live auth/CLI opt-ins, missing Gemini/Qwen
bundles, and POSIX process groups on Windows. No live sign-in was requested.

Scratch captures (gitignored): `_scratch/pyright-part2/{before,after}.json`,
`tests.txt`, `tests-{before,after}.{log,xml}`,
`known-{before,after}.{log,xml}`, `verify_ast.py`, `check_manifest.py`, `run.py`.
The runner registers each child with Clayrune and waits for completion.
Its first known-before output-tail read hit a Windows encoding mismatch after
pytest had completed; the JUnit XML confirms exit 1 and the one known failure.
The reader was corrected; this did not change or rerun the test. Both modified
modules also parse with Python 3.10's syntax grammar. `git diff --check` passes.

Reconstruct the broad selection:

```python
from pathlib import Path
import pytest

paths = []
for path in Path('tests').glob('test_*.py'):
    text = path.read_text(encoding='utf-8')
    if (path.name.startswith(('test_distiller', 'test_agent_runtime'))
            or any(s in text for s in ('mc.agent_runtime', 'mc.distiller',
                                      'from mc import agent_runtime',
                                      'from mc import distiller'))):
        paths.append(path.as_posix())
raise SystemExit(pytest.main(sorted(paths)))
```

## Handoff boundary

Local commit only; no merge, push, deployment, or restart. The workflow remains
non-blocking, with 177 existing errors outside the two cleared files.
No new behavior decision is needed for these typing fixes.
The Scribe failure needs a separately authorized behavior fix if pursued.
Engram tools were unavailable; this committed report is the durable handoff.

The report is the documentation artifact for this internal typing change;
user guides, changelog, agent instructions, and rules need no changes.
