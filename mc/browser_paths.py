"""Browser storage roots, isolated only for non-packaged sandbox instances."""
from __future__ import annotations

import os
import sys


def browser_home() -> str:
    """Keep saved sign-ins in their legacy home for normal and frozen apps.

    app.py always sets MC_DATA_DIR for packaged installs. Treating that as
    sandbox isolation would silently switch every packaged user's profiles
    to an empty directory on update. Only a non-frozen server with an explicit
    data root uses instance storage; guardrail_hooks.clayrune_home has a
    different contract and must continue honouring MC_DATA_DIR when frozen.
    """
    data_dir = os.environ.get('MC_DATA_DIR')
    if data_dir and not getattr(sys, 'frozen', False):
        return os.path.join(data_dir, '.clayrune')
    return os.path.join(os.path.expanduser('~'), '.clayrune')
