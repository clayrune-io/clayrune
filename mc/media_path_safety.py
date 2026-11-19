"""Classify Windows network and device paths without querying the filesystem."""

import re


def has_network_or_device_prefix(path: str) -> bool:
    normalized = path.replace('\\', '/')
    if not normalized.startswith('//'):
        return False
    # Extended-length paths to an ordinary local drive are valid file paths.
    return re.match(r'^//\?/[a-zA-Z]:/', normalized) is None
