# Masked test verdicts (MC-1171)

`mc/test_run_masking.py` reports a test run whose pass or fail Clayrune cannot
see. It only reports: no command is rewritten, blocked or rerun, and no exit
code changes. Setting: `masked_test_verdict_enabled`, default on, read live
(Floor, Preferences, "Flag hidden test results").

## What is reported

One chat line, and for a background job the same sentence in the callback text
the agent receives:

```
[test verdict] UNVERIFIED: the test command is piped into tail, so the exit code is tail's, not the test run's. For the real pass or fail, run the tests through tools/test-run.py.
```

The line names the other command or the script file, never the arguments.
`[test verdict]` is a registered status label (`mc/reply_line_kind.py`), so the
line is never collected as an agent's reply.

## When

`classify(command, shell)` returns `not_test`, `clean`, `masked` or `unknown`.
Only `masked` produces a line. `unknown` is never treated as clean and never
produces a line. The rules, each with its example, are in the module
docstring; in short, a recognised runner is masked when:

- its output is piped into another command and no pipefail is in effect;
- another command follows it after `;` or a newline;
- it is followed by `|| true` or another fallback that does not exit nonzero;
- it starts inside a script file that cannot be read and whose name says it
  runs tests.

A runner is whatever `mc.test_run_detection.candidate()` recognises.

## Where

| Path | Call |
| --- | --- |
| Claude, both stream readers (`agent_routes._read_agent_stream`, `_read_agent_stream_b`) | `note_shell_call` after the `[tool: ...]` line |
| Codex, Qwen and the other Mode A engines (`agent_runtime._mode_a_reader`) | same |
| Gemini (`GeminiRuntime._read_stream`) | same |
| Job route (`agent_routes._job_on_complete`) | `job_line`, appended to the chat and to the callback text |

The job route runs `bash` jobs under `-o pipefail`, so a pipe at the top level
of a bash job is not masked; a pipe inside a script the job runs still is.

## Not covered

- Test-run cards. A card exists only for a command `candidate()` accepts, and
  it refuses every pipe, `;`, `&&` and script, so no masked run has a card.
- A missing summary line (a doubled `-q`): the exit code is still the test
  run's, so this module does not report it.
- Vendor shell tools on Windows other than Claude's `Bash` and `PowerShell`:
  the shell is not known, so a command is judged as bash, PowerShell and cmd
  and reported only when all three agree.
