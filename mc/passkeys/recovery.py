"""Host reset for an unusable passkey registry (docs/PASSKEYS_SPEC.md, "Slice 2
prerequisites" item 3).

`store._write_state` writes the registry and then the enrollment marker, so a
crash between the two leaves a registry holding credentials with no marker.
`store.load()` refuses that state rather than read it as empty, and so does it
refuse an unsigned, tampered, rolled-back or unparseable registry. Passkeys are
then unavailable and the dashboard passcode applies; nothing can enroll either,
because every write starts with a load. This is the way out.

The route (`passkey_routes.reset`) is host-only and passcode-gated. The reset
itself only ever acts on a registry that is ALREADY refused: it re-checks that
under the write lock and raises `NotResettable` for a readable registry, so it
can never be used to delete a healthy one (that is `revoke` / `recover`, behind
a passkey assertion or the documented lost-all recovery). A locked vault is not
"unusable": `StoreLocked` propagates, because without the key a tampered
registry and a good one look the same.

The files are MOVED to `quarantine-<utc>/` beside the registry, never deleted,
so the operator can inspect or restore them. Revocation tombstones go with them:
a credential revoked before the reset could be registered again afterwards.
Pending ceremonies are cleared by the caller.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from mc.passkeys import store


class NotResettable(store.StoreError):
    code = 'reset_not_needed'


def reset_unusable() -> dict:
    """Quarantine a registry that `store` refuses. Returns
    `{'quarantine': <dir name>, 'moved': [file names]}`."""
    with store._write_lock():
        try:
            store._load_unlocked()
        except store.StoreCorrupt:
            pass
        else:
            raise NotResettable('the passkey registry is readable; nothing to reset')
        d = store.passkeys_dir()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        qdir, n = d / f'quarantine-{stamp}', 0
        while qdir.exists():
            n += 1
            qdir = d / f'quarantine-{stamp}-{n}'
        qdir.mkdir()
        moved = []
        for p in (store.registry_path(), store.marker_path()):
            if p.exists():
                os.replace(str(p), str(qdir / p.name))
                moved.append(p.name)
        return {'quarantine': qdir.name, 'moved': moved}
