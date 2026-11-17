"""Serve an untrusted file without letting it run on Clayrune's origin.

`/api/attachments/<name>` and `/api/serve-image` hand back files whose bytes
came from outside (an upload, or something an agent wrote after reading
hostile content). Served inline, an `.html` or an `.svg` carrying a
`<script>` is a first-party document: it can `fetch()` every other Clayrune
API in the signed-in session (review finding P56, 2026-11-16).

Policy, decided by the file's extension (never a guessed mimetype, which on
Windows comes from the registry and which an upload controls the name of):

* raster images, PDF, plain text, audio and video -> inline, `nosniff`.
* SVG                             -> inline as a picture, but under a
  sandbox Content-Security-Policy with no `allow-scripts`, so opening it as a
  page cannot run script. In an `<img>` it never could.
* everything else (HTML, XHTML, XML, unknown, no extension) -> a download.

Every response carries `X-Content-Type-Options: nosniff`, so a `.png` that is
really HTML is not promoted to a page.

`/api/serve-file` is deliberately not routed through here: its `inline=1`
form shows generated HTML viewers on purpose (see `_INLINE_UNSAFE_TYPES`).
"""
from __future__ import annotations

import os

from flask import send_file

_RASTER_EXTS = frozenset({
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico',
    '.tif', '.tiff', '.avif',
})
# Audio and video cannot carry script, and "Open" on an attached clip should
# play it in the tab, as it did before this module existed.
_MEDIA_EXTS = frozenset({'.mp4', '.webm', '.mov', '.m4v', '.mp3', '.wav', '.m4a', '.ogg'})
_INLINE_OTHER_EXTS = frozenset({'.pdf', '.txt', '.md', '.log', '.csv', '.json'}) | _MEDIA_EXTS
_SVG_EXT = '.svg'

# No `allow-scripts` / `allow-same-origin`: the document gets an opaque origin
# and cannot execute script. `default-src 'none'` blocks any fetch it tries.
SANDBOX_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"


def inline_kind(path: str) -> str:
    """'inline' | 'sandboxed' | 'download' for a file path, by extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _RASTER_EXTS or ext in _INLINE_OTHER_EXTS:
        return 'inline'
    if ext == _SVG_EXT:
        return 'sandboxed'
    return 'download'


def send_untrusted_file(path: str, **send_file_kwargs):
    """`send_file` for a file whose content we do not trust (see module doc)."""
    kind = inline_kind(path)
    if kind == 'download':
        send_file_kwargs.setdefault('download_name', os.path.basename(path))
    resp = send_file(path, as_attachment=(kind == 'download'), **send_file_kwargs)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    if kind != 'inline':
        resp.headers['Content-Security-Policy'] = SANDBOX_CSP
    return resp
