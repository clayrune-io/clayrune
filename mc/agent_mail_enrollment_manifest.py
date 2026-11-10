"""Revalidate persisted signed evidence without writing the manifest CAS floor."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_service_manifest import (
    ManifestRefused, ManifestState, ServiceKeyCandidate, verify_service_manifest,
)


def manifest_digest(payload: list[object]) -> str:
    raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


class _EvidenceState:
    """Read-only verification sink; only the separately stored digest can pass."""
    def __init__(self, digest: str):
        self.digest = digest

    def load(self, origin: str) -> ManifestState | None:
        return None

    def compare_and_set(self, origin: str, expected: ManifestState | None, value: ManifestState) -> bool:
        return expected is None and value.digest == self.digest


def candidate_from_evidence(row: dict, *, root: str, origin: str) -> ServiceKeyCandidate:
    """The cache is a consistency check, never the source of pin/disclosure data.

    Verify signature/shape at the original acceptance time for status/review;
    authority mutations separately require the CURRENT floor and expiry.
    """
    try:
        evidence = json.loads(row['manifest'])
        if not isinstance(evidence, dict) or set(evidence) != {'payload', 'signature'}:
            raise ValueError()
        candidate = verify_service_manifest(
            evidence['payload'], evidence['signature'], root_public_key=root,
            service_origin=origin, store=_EvidenceState(row['manifest_digest']),
            now_ms=row['manifest_checked_ms'], enabled=True)
        cache = json.loads(row['candidate'])
        if cache != json.loads(json.dumps(asdict(candidate))):
            raise ValueError()
        if row['confirmed'] and row['confirmed_digest'] != row['manifest_digest']:
            raise ValueError()
        return candidate
    except (ValueError, TypeError, KeyError, ManifestRefused):
        raise EnrollmentRefused('manifest_evidence_invalid') from None
