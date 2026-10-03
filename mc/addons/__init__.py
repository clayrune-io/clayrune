"""Add-ons: software an agent may ask for and only a human may install (MC-1022).

Spec: docs/ADDON_INSTALLS_SPEC.md. The consumer-facing surface is small:

    from mc import addons
    path = addons.resolve('ffmpeg')        # absolute path, re-hashed first
    ...                                    # raises AddonMissing if not approved

A consumer that hits `AddonMissing` files a request with
`addons.file_request('ffmpeg', reason, requested_by)` and parks its job; approval
happens on the card in the dashboard behind the dashboard passcode.
"""
from mc.addons.manifest import (  # noqa: F401
    AddonError, AddonInUse, AddonMissing, addons_root, hold, holders, is_usable, resolve,
)
from mc.addons.service import register_resume_hook, file_request  # noqa: F401
