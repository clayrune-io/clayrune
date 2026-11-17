"""CLIs the daily updater must leave on their audited version (MC-1179 AP58).

The restricted Aider adapter runs only against the one `aider-chat` release in
`mc/aider_source_pin.py` and refuses any other as drift. The updater keeps
`aider` current with pip, pipx or uv, so with the adapter on, the first release
after the audit would move Aider off the pin and every unattended Aider run
would be refused until someone downgraded by hand, and the next pass would
upgrade it again. While the adapter flag is on, the updater does not touch it.
"""
from __future__ import annotations

from typing import Optional


def pinned_reason(name: str) -> Optional[str]:
    """Why the updater must not touch `name`, or None when it may."""
    if name == 'aider':
        from mc import aider_launch
        if aider_launch.enabled():
            return ('the restricted Aider adapter is on and runs only the audited '
                    'version, so Clayrune does not update Aider')
    return None
