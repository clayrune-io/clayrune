"""Scene media references: library containment, projection and bounded uploads."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
import mimetypes

from mc import desk_pieces as pieces
from mc.core import _log


def resolve_asset(rel, kind='image') -> Path:
    if kind not in ('image', 'video'):
        raise pieces.PieceError('a scene asset must be an image or video')
    if not isinstance(rel, str) or not rel.strip():
        raise pieces.PieceError('a scene asset needs a path under the material library')
    # Reject lexical escapes even if normalization would land back inside.
    parts = rel.replace('\\', '/').split('/')
    if '..' in parts or Path(rel).is_absolute() or PureWindowsPath(rel).drive or rel.startswith(('\\', '/')):
        raise pieces.PieceError('a scene asset needs a relative material library path without ..')
    root = pieces._uploads_root().joinpath(*pieces.LIBRARY_ROOT, kind)
    try:
        real = (pieces._uploads_root() / rel).resolve()
        real.relative_to(root)
    except (OSError, ValueError):
        raise pieces.PieceError(f'a scene asset must live in the material library ({kind})')
    if not real.is_file():
        raise pieces.PieceError('that scene asset does not exist in the material library', 404)
    mime = mimetypes.guess_type(real.name)[0]
    classified = pieces.asset_kind_for(real.name)
    if kind == 'image' and ((classified and classified != kind) or (
            not classified and mime and not mime.startswith('image/') and mime != 'application/octet-stream')):
        raise pieces.PieceError('a scene asset must be an image')
    return real


def asset_out(pic: dict) -> dict:
    kind = pic.get('kind', 'image')
    real = resolve_asset(pic.get('path'), kind)
    rel = pieces._rel_of(real)
    src = pieces._play_for(rel, kind) if kind == 'video' else pieces._src_for(rel, kind)
    return {'path': rel, 'kind': kind, 'title': pic.get('title') or real.name, 'src': src}


def save_asset(filename: str, stream, content_type: str = '') -> dict:
    # Browser metadata/decoding is the compatibility check; MIME is a storage
    # hint, never a claim that this machine can decode a particular container.
    mime_kind = content_type.split('/', 1)[0].lower()
    kind = mime_kind if mime_kind in ('image', 'video') else pieces.asset_kind_for(filename)
    if kind not in ('image', 'video'):
        raise pieces.PieceError('choose a picture or a video this browser can play')
    dest, _ = pieces._write_library_file(filename, stream, pieces.STORYBOARD_FOLDER, media_kind=kind)
    try:
        return asset_out({'path': pieces._rel_of(dest.resolve()), 'kind': kind,
                          'title': Path(filename).name[:pieces.MAX_TITLE]})
    except pieces.PieceError:
        try:
            dest.unlink()
        except OSError as e:
            _log(f'[desk] refused scene media cleanup failed: {e}', flush=True)
        raise
