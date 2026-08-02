#!/usr/bin/env python3
"""Run a command with vault secrets injected — without them touching stdout.

This is the agent-facing half of the secrets vault (``mc/secrets_store.py``).
An agent writes the *name* of a credential, never the credential:

    python tools/with-secret.py \
        --user REDDIT_USER=reddit.password \
        --env  REDDIT_PASS=reddit.password \
        --project mission_control \
        -- python tools/post_reddit.py --subreddit selfhosted

A login is one entry, not two: `--env` injects its password, `--user` injects
the username stored beside it (`{{secret:name}}` / `{{user:name}}` as
placeholders). The username is metadata rather than ciphertext — it is what
tells two accounts on the same site apart in the vault list.

The values land in the child process's environment. They are never printed,
never echoed into the command line the agent typed, and so never reach the
transcript, MEMORY.md, or a distilled skill.

Second factors work the same way — `--totp CODE=github.totp` injects a freshly
generated 6-digit code, waiting for the next window if the current one is about
to expire:

    python tools/with-secret.py \
        --env GH_PASS=github.password --totp GH_OTP=github.totp \
        -- python tools/login.py

More injection shapes for tools that don't read env vars:

    --stdin gh.token        pipe one secret to the child's stdin
                            (e.g. `gh auth login --with-token`)
    {{secret:x}} in an arg  any argument of the command after `--` that
                            contains a {{secret:name}}, {{totp:name}} or
                            {{user:name}} placeholder is resolved in place
                            (no flag needed)

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
import time
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
    ap.add_argument('--totp', action='append', type=_pair, default=[],
                    metavar='VAR=secret.name',
                    help='inject a freshly generated 2FA code as an environment '
                         'variable (repeatable)')
    ap.add_argument('--user', action='append', type=_pair, default=[],
                    metavar='VAR=secret.name',
                    help="inject the username stored with a secret (repeatable)")
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
        for var, sec in args.user:
            env[var] = vault.get_username(sec, project_id=project)
        for var, sec in args.totp:
            code, remaining = vault.generate_totp_code(
                sec, consumer='with-secret', project_id=project,
                unattended=unattended)
            # A code with a couple of seconds left will expire while the child
            # is still filling in the form. Wait for the next window rather
            # than hand out one that is about to fail.
            if remaining < 5:
                time.sleep(remaining + 1)
                code, remaining = vault.generate_totp_code(
                    sec, consumer='with-secret', project_id=project,
                    unattended=unattended)
            env[var] = code
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

    # We decode the child as utf-8, so tell it to encode as utf-8. Without
    # this a Python child writing to a pipe on Windows picks cp1252, we decode
    # its bytes as utf-8, and anything non-ASCII arrives as U+FFFD mojibake.
    env.setdefault('PYTHONIOENCODING', 'utf-8')

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

    # The child is decoded utf-8/errors='replace', so its output can contain
    # U+FFFD (and any non-ASCII it legitimately printed). On Windows our own
    # stdout is cp1252 by default and raises UnicodeEncodeError on those —
    # which killed the passthrough mid-stream and threw away the child's exit
    # code. Never let re-encoding the child's output be the thing that fails.
    try:
        sys.stdout.reconfigure(errors='replace')  # type: ignore[union-attr]
    except Exception:
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
