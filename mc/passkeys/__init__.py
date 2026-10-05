"""Passkeys for human-only operations — slice 1 (docs/PASSKEYS_SPEC.md).

Core only: a credential registry, host-only enrollment, list and revoke. NOTHING
here authorizes anything yet. No gate reads this package, `_require_human_passcode`
is untouched, and a passkey registered today changes no route's behaviour. The
assertion verifier and the gate migration are slice 2, which waits for Ron.

One unit per file:

    host_check   is this request the owner's own browser on this machine?
    store        the credential registry under ~/.clayrune/passkeys/
    challenges   bounded, expiring, single-use in-memory ceremony records
    ceremony     the py_webauthn calls (imported lazily; the library is optional
                 at import time so a box without it still boots)
    audit        append-only record of enrollment and revocation outcomes

Routes live in `mc/blueprints/passkey_routes.py`; the Settings surface in
`static/js/passkeys.js`.
"""
