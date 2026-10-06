"""In-memory ceremony records (docs/PASSKEYS_SPEC.md, "One ceremony, one operation").

A record is the server-side half of one WebAuthn ceremony: the 32-byte
challenge, the RP and origin it was issued for, the policy epoch it began under,
and a digest of the browser-session nonce that asked for it. It is bounded in
count and lifetime, single-use, and lost on restart by design.

`consume` removes the record BEFORE checking expiry or session, so a failed
attempt still spends it: a retry needs a new ceremony, and a replayed finish
call finds nothing. The challenge itself is never derived from anything
predictable; it is `secrets.token_bytes(32)`.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

CHALLENGE_TTL_S = 120          # spec: "Proposed challenge lifetime: two minutes"
# Bounds memory. Registration and assertion ceremonies have separate buckets: the
# assertion endpoint is reachable by any loopback caller before it proves
# anything, and sharing one pool let that caller fill it and 429 the owner's own
# add / revoke / enroll. `PER_CLIENT` caps one client address inside a bucket.
MAX_PENDING_BY_KIND = {'registration': 8, 'assertion': 12}
MAX_PENDING_PER_CLIENT = {'registration': 6, 'assertion': 6}
_DEFAULT_CAP = 4

_clock = time.monotonic        # replaced in tests


class CeremonyError(Exception):
    code = 'ceremony_error'


class UnknownCeremony(CeremonyError):
    code = 'unknown_or_used_ceremony'


class ExpiredCeremony(CeremonyError):
    code = 'expired_ceremony'


class SessionMismatch(CeremonyError):
    code = 'session_mismatch'


class OperationMismatch(CeremonyError):
    """An assertion ceremony issued for one operation presented for another."""
    code = 'operation_mismatch'


class TooManyCeremonies(CeremonyError):
    code = 'too_many_ceremonies'


@dataclass(frozen=True)
class Ceremony:
    id: str
    kind: str
    challenge: bytes
    rp_id: str
    origin: str
    owner_handle: str
    epoch: int
    session_digest: str
    label: str
    expires_at: float
    purpose: str = ''           # assertion ceremonies: the one operation they approve
    target: str = ''            # ... and what it acts on (a credential id), or ''
    client: str = ''            # address that asked, for the per-client cap


def session_digest(nonce: str) -> str:
    return hashlib.sha256((nonce or '').encode('utf-8')).hexdigest()


def new_session_nonce() -> str:
    return secrets.token_urlsafe(32)


class ChallengeStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: Dict[str, Ceremony] = {}

    def _prune(self, now: float) -> None:
        for cid in [k for k, v in self._items.items() if v.expires_at <= now]:
            del self._items[cid]

    def issue(self, *, kind: str, rp_id: str, origin: str, owner_handle: str,
              epoch: int, session_nonce: str, label: str,
              purpose: str = '', target: str = '', client: str = '') -> Ceremony:
        now = _clock()
        with self._lock:
            self._prune(now)
            same_kind = [v for v in self._items.values() if v.kind == kind]
            if len(same_kind) >= MAX_PENDING_BY_KIND.get(kind, _DEFAULT_CAP):
                raise TooManyCeremonies('too many ceremonies pending')
            if sum(1 for v in same_kind if v.client == client) >= MAX_PENDING_PER_CLIENT.get(kind, _DEFAULT_CAP):
                raise TooManyCeremonies('too many ceremonies pending for this client')
            c = Ceremony(
                id=secrets.token_urlsafe(18), kind=kind,
                challenge=secrets.token_bytes(32), rp_id=rp_id, origin=origin,
                owner_handle=owner_handle, epoch=epoch,
                session_digest=session_digest(session_nonce), label=label,
                expires_at=now + CHALLENGE_TTL_S, purpose=purpose, target=target, client=client)
            self._items[c.id] = c
            return c

    def consume(self, ceremony_id: str, *, kind: str, session_nonce: str,
                purpose: Optional[str] = None, target: Optional[str] = None) -> Ceremony:
        with self._lock:
            c: Optional[Ceremony] = self._items.pop(ceremony_id, None) if isinstance(ceremony_id, str) else None
        if c is None:
            raise UnknownCeremony(ceremony_id)
        if c.expires_at <= _clock():
            raise ExpiredCeremony(c.id)
        if c.kind != kind:
            raise UnknownCeremony(c.id)
        if (purpose is not None and c.purpose != purpose) or (target is not None and c.target != target):
            raise OperationMismatch(c.id)
        if not hmac.compare_digest(c.session_digest, session_digest(session_nonce)):
            raise SessionMismatch(c.id)
        return c

    def clear(self) -> int:
        """Drop every pending ceremony (revocation, recovery). Returns how many."""
        with self._lock:
            n = len(self._items)
            self._items.clear()
            return n

    def pending(self) -> int:
        with self._lock:
            self._prune(_clock())
            return len(self._items)


# The one process-wide store, shared by registration and assertion ceremonies.
STORE = ChallengeStore()
