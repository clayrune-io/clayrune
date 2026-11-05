# MC-1158: Windows resolved-path containment

`mc/core.py:path_is_within` now compares resolved path components in the same
Windows namespace. This branch is a candidate only: no merge, push or restart.

## Reproduction and failure direction

On Windows with Python 3.14.0, 300 probes raced a sibling thread creating
`store/<probe>/campaign` against checking its not-yet-written `checklist.json`.
The original function returned **false outside in 86/300** inside checks and
**false inside in 0/300** sibling-directory checks. Sample resolved pair:

```text
candidate: \\?\C:\<temporary-root>\store\1\campaign\checklist.json
base:          C:\<temporary-root>\store
```

The baseline was commit `4669e1ed72d5eefb9037c0d17af22834b784a08e`.
The probe wrapped `Path.resolve` only to record its actual return values; it
did not change them. It called the actual helper and awaited each mkdir before
the next probe. After the fix, the same probe returned **0/300 wrong answers
in either direction**. Probe frequencies depend on scheduling.

Inspection of this interpreter's `ntpath.realpath` explains the spelling:
Windows returns extended paths internally. The non-strict fallback resolves
existing ancestors, then probes the ordinary spelling again before deciding
whether to strip the prefix. Directory creation can change the error between
those probes, leaving the extended spelling on just one side.

This is a **false-negative containment bug**. It refuses valid operations for
an allow-inside consumer, but **can bypass a deny-inside guard**. The current
callers mostly deny inside paths; no false-positive containment is necessary
for a safety impact. Tests using the exact original function from the branch
base, loaded in memory without reverting files, demonstrated all four caller
failures. No live exploit or live data change was attempted.

## Fix and boundaries

Both paths still undergo filesystem `Path.resolve()` before comparison. On
Windows, ordinary resolved drive paths gain `\\?\`, and ordinary resolved UNC
paths gain `\\?\UNC\`. Already extended or device paths retain their spelling.
Adding the prefix to the resolved ordinary spelling avoids interpreting
extended trailing-dot/space names as ordinary names. There is no second
filesystem resolve and no lexical fallback on resolution failure.

`Path.is_relative_to` compares components, handles equal paths and volume/share
roots correctly, preserves Windows case-insensitivity, and rejects siblings
whose name merely starts with the base's name. POSIX comparison remains
case-sensitive. Symlink escapes are still rejected; a symlinked base still
denotes its resolved target. Unsupported/different namespaces are not aliased.
Resolution/comparison exceptions remain False and now produce a warning.

**Existing limitation:** False-on-resolution-error is the original predicate
contract, not universal fail-closed authorization. Deny-inside callers below
can still treat an unresolvable path as allowed. Their error-policy/API redesign
is outside this namespace-race repair. As before, this check is not an atomic
filesystem operation and cannot prevent malicious symlink replacement between
checking and opening a path. The tested race creates ordinary directories;
it does not replace symlink/reparse targets.

## Complete caller audit at this branch

`rg -n path_is_within mc server.py` finds **four caller files, five calls**;
`server.py` has no reference. The ticket's estimate of thirteen files does not
match this branch. Searches across all Python files found no additional callers
or aliases. Separate helpers such as `mc/backup.py:_is_within` and the human-owned
steward fence are different functions and are unchanged.

| Caller | Meaning of True | Effect of old false outside | Reliance on bug |
| --- | --- | --- | --- |
| `mc/claude_projects_scan.py:scan` | Exclude a discovered workspace inside the app install unless opted in | Install workspace could appear as an import candidate | None; exclusion is intended |
| `mc/blueprints/project_routes.py:_refuse_project_path_in_install_dir` | Refuse a new inside-install assignment unless grandfathered or opted in | Could bypass the install-directory refusal | None; existing grandfather/opt-in tests pass |
| `mc/blueprints/system_routes.py:_projects_pointing_at_install_dir` | Include a project in the Settings update warning | Could omit an inside-install warning | None; informational, not itself a traversal refusal |
| `mc/desk_agent_store.py:AgentChannelStore.__init__` (two symmetric calls) | Refuse overlap of store root and DATA_DIR in either direction | Could miss an overlap | None; separation protects `load_projects()` |

`ea329f28`'s lexical `AgentChannelStore._path` workaround is **redundant for the
reported mkdir/prefix race** after this fix: the shared helper can now accept
those valid paths. It also verifies exactly `(pid, cid, FILE_NAME)` components
and intentionally has lexical semantics, so it is not simply equivalent to the
shared resolved helper. It remains unchanged, as requested. No caller changed.

## Verification

New tests: `tests/test_core_paths.py` (40) and
`tests/test_core_path_callers.py` (4). They cover deterministic drive/UNC prefix
asymmetry on either side, equality, roots, case, sibling/drive/share boundaries,
extended special names, symlink escapes, missing paths, malformed paths,
resolution errors on either side, all four callers and 300 live mkdir races.
UNC cases use supplied resolve outputs; no live network share was accessed.

| Check | Result | rc |
| --- | --- | --- |
| Original function + new regressions | 18 failed, 26 passed; includes all four caller regressions | 1 (expected) |
| Fixed function + focused regressions | 44 passed, including actual Windows symlinks and mkdir race; no skips | 0 |
| Core and caller suite command below | 339 passed in 41.63s; no skips | 0 |
| `pyright mc/core.py --outputjson` before / after | 0 errors, 0 warnings in both | 0 / 0 |

Focused command:

```powershell
python -m pytest tests/test_core_paths.py tests/test_core_path_callers.py
```

Core/caller suite command (additional existing core tests selected by imports):

```powershell
python -m pytest tests/test_core_paths.py tests/test_core_path_callers.py tests/test_project_routes.py tests/test_claude_projects_scan.py tests/test_desk_agent_store.py tests/test_system_routes_process_sweep.py tests/test_system_update_stash.py tests/test_system_update_resync.py tests/test_system_update_frozen.py tests/test_timestamped_lines.py tests/test_runtime_completion_log.py tests/test_character_skills.py tests/test_habit_report.py tests/test_resume_sysprompt.py tests/test_browser_agent_read_requests.py --durations=10
```

The first broad run had 338 passes and one failure caused by a new scanner-test
fixture adding an extra `projects/` directory. Correcting the fixture made the
focused suite pass; the final broad rerun is recorded above. No product failure
was suppressed. No frontend or live-server tests are needed for this pure helper
change. README, user guide, agent instructions and rules require no update.
