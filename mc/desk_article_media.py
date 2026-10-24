"""Library document/audio discovery and Markdown references shared by articles."""
from __future__ import annotations
import re
from urllib.parse import unquote, quote
from mc import desk_pieces as pieces

LINE = re.compile(r'^!?\[[^\]]*\]\(([^)]+)\)$', re.MULTILINE)
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'}


def paths(text) -> list[str]:
    out = []
    for raw in LINE.findall(str(text or '')):
        path = unquote(raw).replace('\\', '/')
        if path.startswith('desk/library/') and not any(p in ('.', '..') for p in path.split('/')):
            out.append(path)
    return out


def decorate(library: dict) -> dict:
    if pieces.UPLOADS_ROOT is None:
        return library
    base = pieces._uploads_root().joinpath(*pieces.LIBRARY_ROOT)
    from mc.blueprints.project_routes import _is_secret_file
    folders: dict[tuple[str, str], list] = {}
    if base.is_dir():
        for file in base.rglob('*'):
            if pieces.asset_kind_for(file.name) or file.is_symlink() or not file.is_file():
                continue
            if any(part.startswith('.') for part in file.relative_to(base).parts) or _is_secret_file(str(file)):
                continue
            try:
                real = pieces._resolve_under_uploads(pieces._rel_of(file))
                real.relative_to(base.resolve())
            except (pieces.PieceError, ValueError):
                continue
            rel = pieces._rel_of(real)
            folder = str(file.parent.relative_to(base)).replace('\\', '/')
            url = '/api/serve-file?path=' + quote(str(real))
            kind = 'audio' if file.suffix.lower() in AUDIO_EXTENSIONS else 'file'
            folders.setdefault((kind, folder), []).append({'id': rel, 'path': rel, 'kind': kind,
                                                   'title': file.name, 'src': None, 'play': url, 'download': url})
    for (kind, name), items in folders.items():
        library.setdefault(kind, []).append({'id': kind + ':' + name, 'title': name, 'files': len(items), 'items': items})
    return library


def references(store: dict) -> tuple[dict, dict]:
    attached, drafts = {}, {}
    for piece in pieces._pieces(store).values():
        text = [piece.get('body')] + [v.get('body') for v in piece.get('versions') or []]
        for path in paths('\n'.join(str(t or '') for t in text)):
            campaign = (store.get('campaigns') or {}).get(piece.get('campaign_id')) or {}
            attached[path] = {'piece_id': piece['id'], 'campaign_id': piece.get('campaign_id'),
                              'campaign_title': campaign.get('title') or 'a campaign'}
    for key, article in (store.get('studio_articles') or {}).items():
        article_id = article.get('id') or key
        for tab in article.get('tabs') or []:
            for path in paths(tab.get('body')):
                drafts.setdefault(path, []).append({'key': 'article:' + article_id, 'kind': 'article',
                                                   'id': article_id, 'title': article.get('topic') or 'Article'})
    return attached, drafts
