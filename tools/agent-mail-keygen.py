#!/usr/bin/env python3
"""Human-run OFFLINE Ed25519 root/service key generation. Never prints private bytes.

Default: ~/.clayrune/agent-mail-keys/. Use --directory for a different location
outside every checkout. Existing output files (including symlinks) are refused.
Printed next commands use Windows Git Bash, as in AGENT_MAIL_OPERATOR_SETUP.md.
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import shlex
import sys
from typing import Sequence

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from agent_mail_key_files import key_path_in_checkout, write_new_private_file  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

FILENAMES = ('root.key', 'root.pub', 'service.key', 'service.pub')


def _public(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    parser.add_argument('--directory', default=str(Path.home() / '.clayrune' / 'agent-mail-keys'),
                        metavar='PATH', help='output directory outside every git checkout')
    return parser


def run(argv: Sequence[str] | None = None, *, stdout=None, stderr=None) -> int:
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        folder = Path(args.directory).expanduser().resolve()
        if key_path_in_checkout(folder):
            stderr.write('error: key directory is inside a git checkout; keep keys outside every repo\n')
            return 2
        # Preflight every output before creating any key; lexists catches broken symlinks too.
        if any(os.path.lexists(folder / name) for name in FILENAMES):
            stderr.write('error: output exists; no files overwritten; choose a new directory\n')
            return 2
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Recheck resolved location after mkdir, before generating or writing keys.
        if key_path_in_checkout(folder):
            stderr.write('error: key directory is inside a git checkout\n')
            return 2
        root, service = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        root_public, service_public = _public(root), _public(service)
        for name, key in (('root', root), ('service', service)):
            raw = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            private = base64.urlsafe_b64encode(raw).rstrip(b'=') + b'\n'
            write_new_private_file(folder / (name + '.key'), private)
            write_new_private_file(folder / (name + '.pub'), (_public(key) + '\n').encode('ascii'))
    except OSError:
        # Partial outputs remain for human inspection; never delete/retry/overwrite keys.
        stderr.write('error: could not create private key files; partial files may remain; '
                     'inspect the directory before retrying\n')
        return 2
    quote = lambda path: shlex.quote(path.as_posix())
    stdout.write(f'root PUBLIC key: {root_public}\n')
    stdout.write('next (offline, after setting the runbook variables): '
                 f'{quote(Path(sys.executable))} {quote(TOOLS / "agent-mail-manifest-sign.py")} '
                 f'--root-key-file {quote(folder / "root.key")} '
                 '--origin "$MAIL_ORIGIN" --sequence "$MANIFEST_SEQUENCE" '
                 '--issued "$ISSUED" --activate "$ISSUED" --expires "$EXPIRES" --retire "$RETIRE" '
                 f'--key-id "$MAIL_KEY_ID" --service-public-key={service_public} '
                 f'--out {quote(folder)}/manifest-"$MANIFEST_SEQUENCE".json\n')
    stdout.write(f'service PUBLIC key: {service_public}\n')
    stdout.write('next (online, only after deploy approval): '
                 'wrangler secret put MAIL_SERVICE_PRIVATE_KEY '
                 f'--config {quote(TOOLS.parent / "workers/agent-mail/wrangler.toml")} '
                 f'< {quote(folder / "service.key")}\n')
    return 0


if __name__ == '__main__':
    sys.exit(run())
