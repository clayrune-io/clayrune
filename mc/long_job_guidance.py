"""Context guidance for long background commands (MC-1149 phase 1).

A mid-turn context rollover (mc/midturn_rollover.py) goes through the
interrupt path, which tree-kills the agent's CLI process. Every background
job the agent started under that process dies with it -- measured 2026-11-04
(Dave 92a97b85be6a: a full pytest run at 41%, ~25 min of work, gone). The
server-owned job route (mc/agent_jobs.py, `POST /agent/<sid>/job`) is not a
child of the CLI, so it survives the roll and delivers completion as a
follow-up turn. This module is the one sentence that tells EVERY engine,
Claude included, to use it for anything expected to outlive ~5 minutes.

Before MC-1149 only engines without a `run_in_background` facility
(Codex/Gemini/Qwen, `ProviderCapabilities.background_jobs=False`) heard
about the route, as the reason their `&` jobs were orphaned. That text lives
here too now, so the two notes share one route description instead of
costing context twice.

Leaf module: no Flask, no session state. `_build_agent_context` decides
WHETHER to inject (needs a session id to address; incognito sessions cannot
take a job endpoint response) and passes the facts in.
"""
from __future__ import annotations

import json
from pathlib import Path

# Rough threshold above which a job is likelier than not to meet a rollover
# or an interrupt before it ends. Worded as "~5 minutes" in the note; kept
# here so tests and docs cite one number.
LONG_JOB_MINUTES = 5


def job_route_url(port: int, project_id: str, session_id: str) -> str:
    """The server-owned job endpoint for THIS session, fully addressed."""
    return f"http://localhost:{port}/api/project/{project_id}/agent/{session_id}/job"


def long_job_note(port: int, project_id: str, session_id: str,
                  *, background_jobs: bool, test_run_progress_enabled: bool = False,
                  app_dir: Path | None = None) -> str:
    """The guidance paragraph for one session.

    `background_jobs` is the runtime's declared capability
    (`ProviderCapabilities.background_jobs`): True for the Claude CLI
    (`run_in_background`), False for the one-process-per-turn engines. Both
    get the long-job rule; only the latter get the "you have no background
    facility at all" preface, verbatim from before MC-1149 (pinned by
    tests/test_context_profile_by_model_tier.py).
    """
    url = job_route_url(port, project_id, session_id)
    route = (f"POST {url} "
             '{"command":"...","timeout_minutes":optional,"shell":optional} — '
             "runs on bash by default (any OS; \"powershell\"/\"cmd\"/\"sh\" also "
             "accepted) — Clayrune runs it and delivers a follow-up turn into "
             "this session with the exit code, duration and output tail when "
             "it finishes.")
    if background_jobs:
        note = (
            f"Long background commands (anything likely to run more than "
            f"~{LONG_JOB_MINUTES} minutes: a full pytest run, a smoke suite, a "
            "build): do NOT start them with run_in_background. A context "
            "rollover or an interrupt ends your CLI process tree and kills "
            "every background job under it, and the work is lost. Hand them "
            f"to Clayrune instead: {route} The job is not a child of your "
            "process, so it survives a rollover. Short background commands "
            "may still use run_in_background.")
    else:
        note = (
            "You have no background-job facility of your own -- your process "
            "exits when this turn ends, so backgrounding a command with `&` "
            "orphans it and nothing wakes you when it finishes. For a long "
            "command, either run it in the foreground within your tool's own "
            f"timeout, or hand it to Clayrune: {route} Anything likely to run "
            f"more than ~{LONG_JOB_MINUTES} minutes (a full pytest run, a smoke "
            "suite, a build) goes to that route, never a shell `&`.")
    if test_run_progress_enabled is True and app_dir is not None:
        launcher = app_dir / 'tools' / 'test-run.py'
        if launcher.is_file():
            prefix = json.dumps(['python', str(launcher), '--project', project_id,
                                 '--port', str(port), '--'])
            note += (
                '\nReported tests: use the project Python interpreter with launcher argv '
                + prefix + ' followed by the exact original argv. Preserve cwd/env/test '
                'selection. Use --manifest for explicit ordered suites, or --shell plus '
                '--command for exact shell syntax; never guess npm script expansion. '
                'Long tests still go through the job route with this launcher as its '
                'command. Current units are scripts, not assertions; framework reporters '
                'are unavailable. Capture gaps are printed; child exit codes are preserved. '
                'Incognito/sandbox reporting is unsupported. No automatic rerun or '
                'permission change. See the installation docs/TEST_RUN_LAUNCHER.md.')
        else:
            note += '\nReported test launcher unavailable in this installation; progress capture is incomplete.'
    return note
