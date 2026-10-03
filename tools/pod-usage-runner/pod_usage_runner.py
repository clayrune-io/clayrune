#!/usr/bin/env python3
"""Pod-side Claude usage runner — publishes a NUMBERS-ONLY usage document.

Runs INSIDE the customer's pod, next to the unmodified `claude` CLI. It reads
the pod's own ~/.claude/.credentials.json, calls Anthropic's usage endpoint
(the same call the CLI `/usage` command makes), and writes ONLY the usage
numbers to a JSON file. The Clayrune service reads that file (config
`claude_usage_source=runner`, `claude_usage_runner_doc=<path or URL>`) and
never opens the credential file itself. Backlog 1d940d0f.

Written to the document: five_hour / seven_day / seven_day_opus /
seven_day_sonnet / extra_usage / limits (as the endpoint returned them) plus
`sampled_at`. Never written, printed or logged: the token, any header, any raw
response body. Errors print the exception TYPE and, for HTTP errors, the status
code — never the exception message, which can echo request detail.

Standard library only. Usage:
    python pod_usage_runner.py --out /run/clayrune/claude-usage.json          # loop
    python pod_usage_runner.py --out ./claude-usage.json --once               # one shot
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
# Only these top-level keys are ever published.
PUBLISHED_KEYS = ('five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet',
                  'extra_usage', 'limits')


def _claude_version():
    """Best-effort `claude --version`. The usage endpoint throttles any
    User-Agent that doesn't start with `claude-code/`."""
    exe = shutil.which('claude')
    if exe:
        try:
            out = subprocess.run([exe, '--version'], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            tok = out.split()[0] if out else ''
            if tok and tok[0].isdigit():
                return tok
        except Exception:
            pass
    return '2.0.0'


def _read_token(cred_path: Path):
    creds = json.loads(cred_path.read_text(encoding='utf-8'))
    return ((creds.get('claudeAiOauth') or {}).get('accessToken')) or None


def fetch_usage(token: str, version: str) -> dict:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            'Authorization': f'Bearer {token}',
            'anthropic-beta': 'oauth-2025-04-20',
            'User-Agent': f'claude-code/{version}',
            'Content-Type': 'application/json',
        },
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('usage response was not a JSON object')
    return data


def build_document(data: dict) -> dict:
    doc = {k: data[k] for k in PUBLISHED_KEYS if k in data}
    doc['sampled_at'] = datetime.now(timezone.utc).isoformat()
    return doc


def write_atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.usage-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_once(cred_path: Path, out_path: Path, version: str) -> bool:
    """One sample. Returns True if a document was written. Prints a one-line
    status that can never contain the token."""
    try:
        token = _read_token(cred_path)
        if not token:
            print('usage-runner: no access token in the credentials file', file=sys.stderr)
            return False
        doc = build_document(fetch_usage(token, version))
        write_atomic(out_path, doc)
        print('usage-runner: wrote usage document')
        return True
    except urllib.error.HTTPError as e:
        print(f'usage-runner: usage endpoint returned HTTP {e.code}', file=sys.stderr)
    except Exception as e:
        print(f'usage-runner: failed ({type(e).__name__})', file=sys.stderr)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    ap.add_argument('--out', required=True, help='path of the numbers-only usage document to write')
    ap.add_argument('--credentials', default=str(Path.home() / '.claude' / '.credentials.json'),
                    help="the pod's own Claude credentials file (read here, never copied)")
    ap.add_argument('--interval', type=int, default=60, help='seconds between samples (loop mode)')
    ap.add_argument('--once', action='store_true', help='take one sample and exit')
    ap.add_argument('--claude-version', default='', help='override the detected CLI version')
    args = ap.parse_args(argv)

    version = args.claude_version or _claude_version()
    cred_path, out_path = Path(args.credentials), Path(args.out)
    if args.once:
        return 0 if run_once(cred_path, out_path, version) else 1
    while True:
        run_once(cred_path, out_path, version)
        time.sleep(max(15, args.interval))


if __name__ == '__main__':
    sys.exit(main())
