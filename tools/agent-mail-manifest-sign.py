#!/usr/bin/env python3
"""Offline signer for the agent-mail service manifest (MC-1166 unit 7e-2p). Human-run only.

Builds the 11-item canonical array that ``mc/agent_mail_service_manifest.py`` verifies, signs it with
the OFFLINE root Ed25519 private key, and writes the ``{"manifest": [...], "signature": "..."}``
document the mail Worker serves (``MAIL_SERVICE_MANIFEST``) and the dashboard fetches.

    python tools/agent-mail-manifest-sign.py --root-key-file /path/outside/repo/root.key \\
        --origin https://mail.example.net --sequence 3 \\
        --issued 2026-11-11T00:00:00Z --activate 2026-11-11T00:00:00Z \\
        --expires 2026-12-11T00:00:00Z --retire 2027-01-11T00:00:00Z \\
        --key-id mail-service-v1 --service-public-key=<base64url 32 bytes> --out manifest.json

Rules this tool enforces (they are the point of it):
* The root private key comes from a FILE PATH, never from argv/env, and is never printed, logged,
  echoed in an error or written anywhere. Accepted: PEM (PKCS#8), or base64url text of the 32-byte seed
  or of PKCS#8 DER.
* A key file inside any git checkout (the repo this tool lives in, or any directory with a ``.git``
  ancestor) is refused before it is opened.
* Canonical bytes come from ``canonical_manifest_bytes`` in the verifier module; the signed result is run
  back through ``verify_service_manifest`` (derived root public key, in-memory floor) before anything is
  written, so a document this tool emits is one the dashboard's verifier accepts, or nothing is emitted.
* Every value is explicit. Nothing is defaulted or invented; in particular the service key id and the
  service PUBLIC key come from the deployed service key (the public half of MAIL_SERVICE_PRIVATE_KEY).
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from mc.agent_mail_service_manifest import (  # noqa: E402
    ManifestRefused, ManifestState, canonical_manifest_bytes, verify_service_manifest)

PURPOSE = "clayrune-mail-service-manifest"
VERSION = "1"
KEY_FILE_LIMIT = 4096
INPUT_KEYS = ("origin", "sequence", "issued", "activate", "expires", "retire", "key_id",
              "service_public_key", "retired_key_ids")
TIME_FIELDS = ("issued", "activate", "expires", "retire")


class SignerError(Exception):
    """Message is a fixed local sentence; it never carries key material or file contents."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _key_path_in_checkout(path: Path) -> bool:
    resolved = path.resolve()
    roots = [_REPO_ROOT.resolve()]
    if any(root == resolved or root in resolved.parents for root in roots):
        return True
    # A worktree or the main checkout: any ancestor holding a `.git` entry (dir or file) is a checkout.
    return any((parent / ".git").exists() for parent in resolved.parents)


def load_root_key(path_text: str) -> Ed25519PrivateKey:
    path = Path(path_text)
    if _key_path_in_checkout(path):
        raise SignerError("refused: the root key file is inside a git checkout; keep it outside every repo")
    try:
        if not path.is_file():
            raise SignerError("root key file not found or not a regular file")
        if path.stat().st_size > KEY_FILE_LIMIT:
            raise SignerError("root key file is too large to be an Ed25519 key")
        data = path.read_bytes()
    except OSError:
        raise SignerError("root key file could not be read") from None
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise SignerError("root key file is not a PEM or base64url text key") from None
    try:
        if text.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
        else:
            if not re.fullmatch(r"[A-Za-z0-9_\-+/=]+", text):
                raise ValueError
            raw = base64.urlsafe_b64decode(text.replace("+", "-").replace("/", "_")
                                           + "=" * (-len(text) % 4))
            key = (Ed25519PrivateKey.from_private_bytes(raw) if len(raw) == 32
                   else serialization.load_der_private_key(raw, password=None))
    except Exception:  # message deliberately fixed: parser errors must not echo key bytes
        raise SignerError("root key file is not a valid Ed25519 private key (PEM PKCS#8, or base64url "
                          "of the 32-byte seed or PKCS#8 DER)") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise SignerError("root key file is not an Ed25519 private key")
    return key


def parse_time_ms(value: Any, name: str) -> str:
    """13-digit epoch milliseconds, or an ISO-8601 UTC timestamp ending in Z. Returns the decimal string."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]{13}", value):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z", value):
        try:
            moment = _dt.datetime.fromisoformat(value[:-1]).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            raise SignerError(f"--{name}: not a valid UTC timestamp") from None
        return str(int(moment.timestamp() * 1000))
    raise SignerError(f"--{name}: use 13-digit epoch milliseconds or an ISO-8601 UTC time like 2026-11-11T00:00:00Z")


def build_payload(fields: dict[str, Any]) -> list[object]:
    missing = [k for k in INPUT_KEYS if k != "retired_key_ids" and fields.get(k) in (None, "")]
    if missing:
        raise SignerError("missing required value(s): " + ", ".join(sorted(missing)))
    retired = fields.get("retired_key_ids") or []
    if not isinstance(retired, (list, tuple)) or any(not isinstance(k, str) for k in retired):
        raise SignerError("retired_key_ids must be a list of key id strings")
    times = {name: parse_time_ms(fields[name], name) for name in TIME_FIELDS}
    return [PURPOSE, VERSION, str(fields["origin"]), str(fields["sequence"]), times["issued"], times["expires"],
            str(fields["key_id"]), str(fields["service_public_key"]), times["activate"], times["retire"],
            sorted(set(retired))]


class _MemoryFloor:
    """In-memory rollback sink for the self-check only; nothing persists."""

    def __init__(self) -> None:
        self.value: ManifestState | None = None

    def load(self, origin: str) -> ManifestState | None:
        return self.value

    def compare_and_set(self, origin: str, expected: ManifestState | None, value: ManifestState) -> bool:
        self.value = value
        return True


def sign_manifest(payload: list[object], key: Ed25519PrivateKey) -> dict[str, object]:
    """Sign, then prove the result with the dashboard's own verifier. Raises SignerError on any refusal."""
    signature = _b64url(key.sign(canonical_manifest_bytes(payload)))
    public = _b64url(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    try:
        activate_ms = int(str(payload[8]))  # inside [issued, expires): the earliest moment the verifier accepts
        verify_service_manifest(payload, signature, root_public_key=public, service_origin=str(payload[2]),
                                store=_MemoryFloor(), now_ms=activate_ms, enabled=True)
    except (ManifestRefused, ValueError) as exc:
        raise SignerError(f"refused to sign: the manifest fails the dashboard verifier ({exc})") from None
    return {"manifest": payload, "signature": signature}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(allow_abbrev=False, description="Sign the agent-mail service manifest with the offline root key.")
    p.add_argument("--root-key-file", required=True, metavar="PATH",
                   help="path to the root Ed25519 private key (outside every git checkout)")
    p.add_argument("--input", metavar="FILE", help="JSON object with any of: " + ", ".join(INPUT_KEYS))
    p.add_argument("--origin", help="https origin of the mail service (agent_mail_service_origin)")
    p.add_argument("--sequence", help="manifest sequence, integer >= 1, must exceed the last one published")
    p.add_argument("--issued", help="issued time")
    p.add_argument("--activate", help="when the service key becomes active")
    p.add_argument("--expires", help="when this manifest expires")
    p.add_argument("--retire", help="when the service key retires (>= expires)")
    p.add_argument("--key-id", dest="key_id", help="service key id (MAIL_SERVICE_KEY_ID)")
    p.add_argument("--service-public-key", dest="service_public_key",
                   help="base64url Ed25519 PUBLIC key of the service signing key (32 bytes); write it as "
                        "--service-public-key=VALUE, since a base64url value can start with '-'")
    p.add_argument("--retired-key-id", dest="retired_key_ids", action="append", default=None,
                   help="a previously retired service key id (repeatable)")
    p.add_argument("--out", metavar="PATH", help="write the document here instead of stdout")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    return p


def _load_input(path_text: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SignerError("--input could not be read as JSON") from None
    if not isinstance(value, dict) or set(value) - set(INPUT_KEYS):
        raise SignerError("--input must be a JSON object using only: " + ", ".join(INPUT_KEYS))
    return value


def run(argv: Sequence[str] | None = None, *, stdout=None, stderr=None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        key = load_root_key(args.root_key_file)  # path policy first; nothing else is read before it passes
        fields: dict[str, Any] = _load_input(args.input) if args.input else {}
        for name in INPUT_KEYS:
            cli = getattr(args, name)
            if cli not in (None, []):
                fields[name] = cli
        document = sign_manifest(build_payload(fields), key)
        text = json.dumps(document, separators=(",", ":"), ensure_ascii=True) + "\n"
        if args.out:
            out = Path(args.out)
            try:
                with open(out, "w" if args.force else "x", encoding="ascii", newline="\n") as handle:
                    handle.write(text)
            except FileExistsError:
                raise SignerError("--out exists; pass --force to overwrite") from None
            except OSError:
                raise SignerError("--out could not be written") from None
        else:
            stdout.write(text)
    except SignerError as exc:
        stderr.write(f"error: {exc}\n")
        return 2
    public = _b64url(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    payload = document["manifest"]
    assert isinstance(payload, list)
    stderr.write(f"signed manifest sequence {payload[3]} for {payload[2]}, expires {payload[5]} ms, "
                 f"{len(text) - 1} bytes\n"
                 f"root PUBLIC key (must equal the dashboard's agent_mail_root_public_key): {public}\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
