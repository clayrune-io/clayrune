#!/usr/bin/env python3
"""Run a command with vault secrets injected — without them touching stdout.

This is the agent-facing half of the secrets vault (``mc/secrets_store.py``).
An agent writes the *name* of a credential, never the credential:

    python tools/with-secret.py \
        --env REDDIT_USER=reddit.user \
        --env REDDIT_PASS=reddit.password \
        --project mission_control \
        -- python tools/post_reddit.py --subreddit selfhosted

The values land in the child process's environment. They are never printed,
never echoed into the command line the agent typed, and so never reach the
transcript, MEMORY.md, or a distilled skill.

Two more injection shapes for tools that don't read env vars:

    --stdin gh.token        pipe one secret to the child's stdin
                            (e.g. `gh auth login --with-token`)
    --arg   '{{secret:x}}'  any argument containing a {{secret:name}}
                            placeholder is resolved in place

Child output is scrubbed of any dispensed value before it is echoed, so a
chatty tool that prints its own password can't leak it into the transcript.
Pass --raw to stream through unmodified (needed for interactive commands).

Every read is written to ~/.clayrune/secrets_audit.jsonl.

Exit codes: the child's, or 2 if a secret could not be resolved (in which case
the child is never started — a missing secret must not become an anonymous
login attempt).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import secrets_store as vault  # noqa: E402


def _pair(text: str) -> tuple[str, str]:
    if '=' not in text:
        raise argparse.ArgumentTypeError(
            f"expected ENV_VAR=secret.name, got '{text}'")
    var, name = text.split('=', 1)
    var, name = var.strip(), name.strip()
    if not var or not name:
        raise argparse.ArgumentTypeError(
            f"expected ENV_VAR=secret.name, got '{text}'")
    return var, name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog='with-secret',
        description='Run a command with Clayrune vault secrets injected.')
    ap.add_argument('--env', action='append', type=_pair, default=[],
                    metavar='VAR=secret.name',
                    help='inject a secret as an environment variable (repeatable)')
    ap.add_argument('--stdin', metavar='secret.name',
                    help="pipe a secret to the child's stdin")
    ap.add_argument('--project', default=None,
                    help='project id, for project-scoped secrets')
    ap.add_argument('--unattended', action='store_true',
                    help='this is a steward/scheduled cycle — enforces the '
                         'per-secret attended-only flag')
    ap.add_argument('--raw', action='store_true',
                    help='stream child output unmodified (no redaction); use '
                         'for interactive commands')
    ap.add_argument('command', nargs=argparse.REMAINDER,
                    help='-- followed by the command to run')
    args = ap.parse_args(argv)

    cmd = args.command
    if cmd and cmd[0] == '--':
        cmd = cmd[1:]
    if not cmd:
        ap.error('no command given (use: --env VAR=name -- your command here)')

    project: str | None = args.project
    unattended = bool(args.unattended)

    try:
        env = dict(os.environ)
        env.update(vault.env_for(args.env, consumer='with-secret',
                                 project_id=project, unattended=unattended))
        # Resolve placeholders inside the command line itself.
        cmd = [vault.resolve_placeholders(a, consumer='with-secret',
                                          project_id=project,
                                          unattended=unattended)[0]
               for a in cmd]
        stdin_value = (vault.get_secret_value(args.stdin, consumer='with-secret',
                                              project_id=project,
                                              unattended=unattended)
                       if args.stdin else None)
    except vault.SecretsError as e:
        print(f"with-secret: {e}", file=sys.stderr)
        return 2

    if args.raw:
        proc = subprocess.run(
            cmd, env=env,
            input=(stdin_value.encode() if stdin_value is not None else None))
        return proc.returncode

    proc = subprocess.Popen(
        cmd, env=env,
        stdin=subprocess.PIPE if stdin_value is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1)

    if stdin_value is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_value)
            proc.stdin.close()
        except OSError:
            pass

    # Line-buffered passthrough so output still arrives progressively; each
    # line is scrubbed of any value the vault has dispensed this process.
    if proc.stdout is not None:
        for line in proc.stdout:
            sys.stdout.write(vault.redact(line))
            sys.stdout.flush()
    return proc.wait()


if __name__ == '__main__':
    raise SystemExit(main())
