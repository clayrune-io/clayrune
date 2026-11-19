"""Passkey enrollment, assertions, and registry management (docs/PASSKEYS_SPEC.md).

The assertion verifier issues proofs for passkey management. The dashboard's
existing passcode gates still use the passcode.

One unit per file:

    host_check   is this request the owner's own browser on this machine?
    store        the credential registry under ~/.clayrune/passkeys/
    challenges   bounded, expiring, single-use in-memory ceremony records
    ceremony     the py_webauthn calls (imported lazily; the library is optional
                 at import time so a box without it still boots)
    assertion    assertion verification and single-use proofs
    integrity    registry authentication
    recovery     explicit reset of an unusable registry
    audit        append-only record of enrollment and revocation outcomes

Routes live in `mc/blueprints/passkey_routes.py`; the Settings surface in
`static/js/passkeys.js`.
"""
