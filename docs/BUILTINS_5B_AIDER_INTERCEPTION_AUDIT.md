# BUILTINS-5b — Aider interception boundary decision

2026-11-02. Backlog `10796682`. Sol_Tobin. Base `7e5c2246`.

Historical part-1 audit. Part 2 implements Dave's selected restricted adapter
behind a default-OFF flag; see [the restricted adapter contract and proof](BUILTINS_5B_RESTRICTED_AIDER.md).
The negative tests below remain unchanged as controls.

**Part-1 result: stopped at a design decision; no production bridge implemented.** The
unattended-Aider refusal remains unchanged. There is **no enablement flag** to
flip. No merge, push, server restart, human-owned fence edit, or live config
change is part of this work.

## Finding

The named edit and shell methods can be intercepted, but they are not a complete
boundary. An audit of an isolated, installed **aider-chat 0.86.2** found direct
filesystem mutations, other process-launch paths, and native filesystem IO.
Neither a central text-writer/common-shell wrapper nor Python's file-open audit
event covers them all. A version check or startup self-test does not repair
these coverage gaps.

This establishes that the two examined boundaries are insufficient. It does
**not** establish that every possible Aider adapter is impossible. A complete
adapter needs an explicit supported feature surface or a stronger isolation
boundary. That choice changes the product and implementation scope, so it goes
back to the spawner under this ticket's decision rule.

## Installed source inventory

Paths below are relative to the installed `aider` package, not the Clayrune
source tree. Line numbers refer to the measured 0.86.2 wheel.

| Surface | Actual mutation/execution boundary | Why the named wrappers miss it |
|---|---|---|
| Normal edits | `coders/base_coder.py:2296` → format-specific `apply_edits` → `io.py:478` | Text writes are interceptable, but this is only one family of writes. |
| Initial file selection | `coders/base_coder.py:457` → `utils.touch_file` (`utils.py:285`) | Constructor creates files before `apply_updates`. |
| Edit preparation | `coders/base_coder.py:2191` → `utils.touch_file` | `allowed_to_edit` can create a missing file without `io.write_text`. |
| Edit helpers | `coders/editblock_coder.py:371`, `coders/udiff_coder.py:128` | Helpers call `Path.touch` directly. |
| Patch format | `coders/patch_coder.py:549` | Direct mkdir, unlink, destination write, and source deletion during moves. Guarding only the original target misses `move_path`. |
| Saved sessions | `commands.py:1479` (`cmd_save`) | Direct `open(..., 'w')`; reached by command preprocessing before an LLM turn. |
| Histories | `io.py:760`, `io.py:1131`; prompt-toolkit history | Direct append and dependency-owned writes outside the edit writer. |
| Model/analytics caches | `models.py:200`, `openrouter.py:120`, `analytics.py:181` | Direct `Path.write_text`, plus append paths. |
| Repository map | `repomap.py:175–225` | diskcache/SQLite writes and recursive cache deletion. SQLite performs native filesystem IO. |
| Clipboard image | `commands.py:1259` → Pillow `image.save` (`:1280`) | Separate writer; basename may be an absolute argument. |
| Voice | `voice.py:140` → `soundfile.SoundFile` → native libsndfile | The measured native WAV creation emits no Python `open` write event. |
| Suggested shell | `base_coder.py:2450` → imported `run_cmd` | Common helper is interceptable, subject to its separate POSIX branch. |
| `/run`, `/test` | `commands.py:1003`, `:983` → imported `run_cmd` | Same helper; test callbacks can also be Python callables. |
| `/git` | `commands.py:958` → `subprocess.run` | Does not call the common `run_cmd` alias. |
| Common subprocess | `run_cmd.py:42` → `subprocess.Popen` | Central subprocess interception covers this path, not native writes or POSIX pexpect by itself. |
| POSIX TTY shell | `run_cmd.py:89` → `pexpect.spawn` → PTY/fork/exec | Separate branch from Popen; source-audited only on this Windows host. |
| Linting | `linter.py:47`, `:136` | Common shell helper **and** direct subprocess for flake8. |
| Editor | `editor.py:134` → `subprocess.call(shell=True)` | Separate launcher and temporary-file writer. |
| Dependency installation | `utils.py:210` → `subprocess.run` / `Popen` | Installer children write files; parent Python IO hooks do not propagate as filesystem hooks to them. |
| Reporting | `report.py:31` → `subprocess.check_output` | Separate process entry, plus cache deletion. |
| Notifications | `io.py:1093` → `subprocess.run` | Separate configurable command. |
| Repository actions | `repo.py` → GitPython; `main.py:144` config writer | GitPython process and direct config-write paths, not the common Aider shell helper. |
| Optional browser/document conversion | `scrape.py` → Playwright / pypandoc | Dependency-owned processes and installer paths require separate review or refusal. |
| GUI/onboarding | `main.py:217`, `onboarding.py:366` | Configuration/credential writes outside coding IO. |

These are an inventory of inspected Aider call sites, **not** a certification
of every transitive dependency, every platform, or every optional installation.

## Reproducible negative-boundary proof

`tests/aider_interception_probe.py` runs in a separate Python process containing
the installed Aider. It constructs real `Coder.create` objects and native
`InputOutput`/`Commands`/format implementations. The model is a metadata stub;
no provider call is made. Targets live under a caller-owned temporary `.claude`
directory so the existing fence actually classifies them as refused writes.
The fence is imported unchanged and `classify_action` supplies the decisions.

The candidate instruments `InputOutput.write_text` plus the two imported
common-shell aliases. The tests deliberately **reject** that candidate:

1. Real `WholeFileCoder.apply_updates` parses a synthetic whole-file response.
   The fence rejects the writer; original bytes remain. Aider catches the
   exception and reflects the error, so an exception reaching the caller is
   not required for a verified block.
2. Real `Coder.handle_shell_commands` and `Commands.cmd_run` each reach the
   candidate shell guard and are refused on `git push` without starting it.
   Confirmation UI is stubbed to accept, ensuring UI refusal is not mistaken
   for fence enforcement.
3. Real edit preparation creates a protected empty file despite that writer.
4. Real `PatchCoder.apply_edits` deletes the protected file despite that writer.
5. Real preprocessing of `/save` overwrites the protected file despite it.
6. Real preprocessing of `/git push` reaches the separate subprocess boundary;
   a recorder replaces that boundary so **no actual push is executed**.
7. Real `Commands.cmd_voice` → `Voice.raw_record_and_transcribe` creates a WAV
   through installed libsndfile despite a Python `open` audit guard. A positive
   control first proves that guard rejects Python's own write-open to the same
   target. Audio device/prompt/transcription and temporary filename selection
   are synthetic; no microphone or API is used. This demonstrates an uncovered
   native write primitive, **not an authenticated exploit or a claim that an
   ordinary voice call selects that protected path by default**.

The tests do not install or certify a production wrapper, do not certify future
internal shapes, and do not alter the runtime launch policy. Their green result
means **the coverage gaps were reproduced**, not that Aider is safely confined.

## Decision for the spawner

| Option | Scope / cost | Reversal |
|---|---|---|
| **A — restricted, pinned adapter (recommended)** | Explicitly support reviewed coding formats and commands; refuse unsupported commands/features before execution. Pin audited source and critical dependency shapes, intercept all accepted filesystem/process paths, and reject drift. Native audio, GUI, browser/installer paths and unreviewed extensions remain unavailable in guarded runs. Requires a feature allowlist decision and further implementation/native tests. | Keep the default-OFF gate off; expand supported features only after separate proof. |
| B — OS confinement plus a broker | Isolate the entire Aider process tree and mediate filesystem/process capabilities outside Python. Preserves a broader feature goal but is a larger Windows/POSIX security architecture project with packaging and integration tests. | Disable the new launcher and retain today's refusal. |

Keep today's unattended refusal until a complete selected design is built and
reviewed. A restricted adapter must not silently claim full Aider support. Any
future gate that lifts refusal stays default OFF, with Ron owning enablement.

## Validation

Installation is isolated under gitignored `_scratch/aider-proof-env`; it neither
installs a globally resolvable CLI nor changes the project's Python environment.
The project Python runs pytest; only the probe children use Python 3.12/Aider.

```powershell
uv venv --python 3.12 _scratch/aider-proof-env
uv pip install --python _scratch/aider-proof-env/Scripts/python.exe aider-chat==0.86.2
$env:CLAYRUNE_TEST_AIDER_AUDIT='1'
$env:CLAYRUNE_AIDER_AUDIT_PYTHON=(Resolve-Path '_scratch/aider-proof-env/Scripts/python.exe').Path
python -m pytest -q -o addopts='' --tb=short tests/test_aider_interception_audit.py
```

Measured Python **3.12.10**, soundfile **0.13.1**, libsndfile **1.2.2**.
The installed Aider package contains 81 Python files. SHA-256 over sorted
relative POSIX paths, each followed by a NUL and its raw file bytes:
`c20db8e0c2fd5bd0bc97ca30ac4fd638de61272c1a7b19fd24f15cbfe8c69eda`.
This fingerprint is evidence only; no production loader enforces it.

Audit alone: **8 passed, rc=0**. Final combined run, with the same opt-in
environment:

```text
python -m pytest -q -o addopts='' --tb=short tests/test_aider_interception_audit.py tests/test_vendor_fence.py tests/test_fence_fail_closed_unattended.py
```

**153 passed, 1 xfailed, rc=0**, 11.48 seconds. The existing expected failure
is `test_project_fence_entry_timeout_is_seconds_not_ms`, awaiting a human-owned
fence timeout patch. No new safety certification is implied by these results.

No `mc/` module
is added or changed, so the new-module pyright requirement is inapplicable.
README, user guide, changelog and agent instructions need no behavior update:
no user-visible behavior, install requirement, authority, or gate has changed.

Primary context: [BUILTINS-5 engine audit](BUILTINS_5_FAIL_CLOSED_ENGINES.md),
[Aider's open hook-system request](https://github.com/Aider-AI/aider/issues/5712),
[Python audit-hook limitations](https://docs.python.org/3.12/library/sys.html#sys.addaudithook).
