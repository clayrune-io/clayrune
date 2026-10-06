"""Standalone Studio articles (Ron 2026-10-06: an article is possible for ANY
project and ANY topic, even one that is not a campaign yet).

A Studio article is a draft the writer works on with no campaign behind it, the
way a Studio video is a storyboard with no campaign behind it. It lives in
`store['studio_articles']` inside `data/desk.json` under `_store_lock`: the same
file as every other Desk store, outside DATA_DIR (the LOAD-BEARING rule in
CLAUDE.md), so it is not a project record.

SHAPE. `{id, topic, project_id, campaign_id, piece_id, tabs:[{id,label,body}], rev,
created_at, updated_at}`. `topic` is the free text the user typed and the title
the article carries. `project_id` is any Clayrune project (optional);
`campaign_id` an existing campaign it is meant for (optional). The id is minted
by the page, as a Studio video's is. `piece_id` is set only by `attach`: the
campaign piece this draft became.

WRITES ARE A WHOLE-RECORD PUT GUARDED BY `rev`, like the storyboards beside it
(mc/desk_storyboard.py): a stale rev is a 409 carrying the current one, a write
is `rev + 1`. A new article is a PUT at rev 0.

ATTACH makes a campaign piece (kind article) from the draft and stamps its id on
the draft; it never moves or edits the draft's text. The piece is an ordinary
campaign piece from then on and is deleted in What like any other.

DELETE MOVES, NEVER ERASES: a deleted article goes to `store['studio_articles_trash']`
under a token and `restore` puts it back at its revision. Entries older than
TRASH_KEEP_DAYS are purged on the next delete. An article that is attached to a
campaign piece that still exists cannot be deleted (409): detach it there first.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from mc import desk as _desk
from mc import desk_pieces as _pieces
from mc.core import _log, now_iso

PieceError = _pieces.PieceError

MAX_ARTICLES = 500
MAX_TABS = 10
MAX_LABEL = 80
TRASH_KEEP_DAYS = 30
DEFAULT_TAB = {'id': 'tab-draft', 'label': 'Draft', 'body': ''}


def _articles(store: dict) -> dict:
    return store.setdefault('studio_articles', {})


def _trash(store: dict) -> dict:
    return store.setdefault('studio_articles_trash', {})


def _check_id(value, what: str = 'an id') -> str:
    if not (isinstance(value, str) and _pieces._CLIENT_ID.match(value)):
        raise PieceError(f'{what} must be 1-80 letters, digits, - or _')
    return value


def _attached_piece(store: dict, art: dict) -> dict | None:
    pid = art.get('piece_id')
    return _pieces._pieces(store).get(pid) if isinstance(pid, str) else None


def _out(store: dict, art: dict) -> dict:
    piece = _attached_piece(store, art)
    camp = (store.get('campaigns') or {}).get(art.get('campaign_id') or '') or {}
    return {
        'id': art['id'], 'topic': art.get('topic') or '', 'project_id': art.get('project_id'),
        'campaign_id': art.get('campaign_id'),
        # An attached article whose piece was since deleted in What is not attached any more.
        'piece_id': piece['id'] if piece else None, 'attached': bool(piece),
        'campaign_title': ((camp.get('plan') or {}).get('title') or camp.get('title') or '') if piece else '',
        'tabs': [dict(t) for t in art.get('tabs') or []], 'rev': int(art.get('rev') or 0),
        'created_at': art.get('created_at'), 'updated_at': art.get('updated_at'),
    }


def _clean_tabs(raw, problems: list[str]) -> list[dict]:
    if raw is None:
        return [dict(DEFAULT_TAB)]
    if not isinstance(raw, list) or not raw or len(raw) > MAX_TABS:
        problems.append(f'tabs must be a list of 1 to {MAX_TABS} tabs')
        return []
    out, seen = [], set()
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            problems.append(f'tab {i + 1}: must be an object')
            continue
        tid, label, body = t.get('id'), t.get('label'), t.get('body', '')
        if not (isinstance(tid, str) and _pieces._CLIENT_ID.match(tid)) or tid in seen:
            problems.append(f'tab {i + 1}: id must be 1-80 letters, digits, - or _, and unique')
            continue
        if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
            problems.append(f'tab {i + 1}: label must be text of 1 to {MAX_LABEL} characters')
            continue
        if not isinstance(body, str) or len(body) > _pieces.MAX_BODY:
            problems.append(f'tab {i + 1}: body must be text of at most {_pieces.MAX_BODY} characters')
            continue
        seen.add(tid)
        out.append({'id': tid, 'label': label.strip(), 'body': body})
    return out


def _check_project(project_id, load_project) -> str | None:
    if project_id in (None, ''):
        return None
    if not isinstance(project_id, str) or not project_id.strip():
        raise PieceError('project_id must be a project id or empty')
    if load_project is not None and not load_project(project_id):
        raise PieceError('that project does not exist', 404)
    return project_id


def _check_campaign(store: dict, campaign_id) -> str | None:
    if campaign_id in (None, ''):
        return None
    _pieces._campaign_of(store, campaign_id)
    return campaign_id


# -- reads --------------------------------------------------------------------------

def list_articles() -> list[dict]:
    """Every standalone article, newest first. Recent reads this: an article's id is
    minted in the browser, so without it a reload would lose the way back."""
    with _desk._store_lock:
        store = _desk._read_store()
        out = [_out(store, a) for a in _articles(store).values() if isinstance(a, dict)]
    out.sort(key=lambda a: a['updated_at'] or '', reverse=True)
    return out


def get_article(article_id: str) -> dict:
    _check_id(article_id)
    with _desk._store_lock:
        store = _desk._read_store()
        art = _articles(store).get(article_id)
        if not isinstance(art, dict):
            raise PieceError('that article is not saved on the server', 404)
        return _out(store, art)


# -- writes -------------------------------------------------------------------------

def put_article(article_id: str, body, load_project=None) -> dict:
    """Create (rev 0) or replace. `body` = `{rev, topic, project_id?, campaign_id?, tabs?}`.
    An omitted `tabs` keeps the stored ones (one empty `Draft` tab for a new article);
    an omitted `project_id` / `campaign_id` is kept, an empty one clears it."""
    _check_id(article_id)
    if not isinstance(body, dict):
        raise PieceError('body must be a JSON object')
    rev = body.get('rev')
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise PieceError('rev is required: the revision you last read (0 for a new article)')
    topic = body.get('topic')
    if not isinstance(topic, str) or not topic.strip():
        raise PieceError('an article needs a topic')
    if len(topic.strip()) > _pieces.MAX_TITLE:
        raise PieceError(f'the topic must be at most {_pieces.MAX_TITLE} characters')
    problems: list[str] = []
    tabs = _clean_tabs(body['tabs'], problems) if 'tabs' in body else None
    if problems:
        raise PieceError('the article is not valid', 400, problems)
    project_id = _check_project(body['project_id'], load_project) if 'project_id' in body else ...
    with _desk._store_lock:
        store = _desk._read_store()
        campaign_id = _check_campaign(store, body['campaign_id']) if 'campaign_id' in body else ...
        arts = _articles(store)
        art = arts.get(article_id)
        current = int((art or {}).get('rev') or 0)
        if rev != current:
            raise PieceError(f'this article changed since you loaded it (it is at revision {current}, '
                             f'you had {rev}): reload it, then make the change again', 409, [f'current_rev={current}'])
        if art is None and len(arts) >= MAX_ARTICLES:
            raise PieceError(f'Studio holds at most {MAX_ARTICLES} articles: delete one first', 409)
        now = now_iso()
        new: dict = dict(art or {'id': article_id, 'created_at': now, 'piece_id': None,
                           'project_id': None, 'campaign_id': None})
        new['topic'] = topic.strip()
        new['tabs'] = tabs if tabs is not None else (new.get('tabs') or [dict(DEFAULT_TAB)])
        if project_id is not ...:
            new['project_id'] = project_id
        if campaign_id is not ...:
            new['campaign_id'] = campaign_id
        new['rev'] = current + 1
        new['updated_at'] = now
        arts[article_id] = new
        _desk._write_store(store)
        return _out(store, new)


def attach(article_id: str, campaign_id, piece_id: str | None = None) -> dict:
    """Make a campaign piece from the draft. -> `{article, piece}`. `piece_id` is the id
    the page already minted for the piece (so its local copy and the stored one agree).
    409 when the draft is already attached to a piece that still exists."""
    _check_id(article_id)
    with _desk._store_lock:
        store = _desk._read_store()
        art = _articles(store).get(article_id)
        if not isinstance(art, dict):
            raise PieceError('that article is not saved on the server', 404)
        _pieces._campaign_of(store, campaign_id)
        if _attached_piece(store, art):
            raise PieceError('this article is already in a campaign: detach it there first', 409)
        text = next((t['body'] for t in art.get('tabs') or [] if t.get('body', '').strip()), '')
        topic = art.get('topic') or ''
    # create_piece takes the store lock itself, so it runs outside the block above.
    piece = _pieces.create_piece(campaign_id, 'article', topic, piece_id=piece_id or None, body=text,
                                 word_count=len(text.split()) or None)
    try:
        with _desk._store_lock:
            store = _desk._read_store()
            art = _articles(store).get(article_id)
            if not isinstance(art, dict) or _attached_piece(store, art):
                raise PieceError('this article changed while it was being added: try again', 409)
            art['campaign_id'] = campaign_id
            art['piece_id'] = piece['id']
            art['updated_at'] = now_iso()
            _desk._write_store(store)
            out = _out(store, art)
    except BaseException:
        _pieces.delete_piece(piece['id'])
        raise
    return {'article': out, 'piece': piece}


def delete_article(article_id: str) -> dict:
    """Trash an article. -> `{token}`. 404 for one the server holds nothing for (the
    page never saved it); 409 while it is attached to a campaign piece."""
    _check_id(article_id)
    with _desk._store_lock:
        store = _desk._read_store()
        arts = _articles(store)
        art = arts.get(article_id)
        if not isinstance(art, dict):
            raise PieceError('that article is not saved on the server', 404)
        if _attached_piece(store, art):
            out = _out(store, art)
            raise PieceError(f"Attached to {out['campaign_title'] or 'a campaign'}, detach it there first", 409)
        _purge_old(store)
        token = uuid.uuid4().hex
        _trash(store)[token] = {'article': art, 'deleted_at': now_iso()}
        del arts[article_id]
        _desk._write_store(store)
    return {'token': token}


def restore(token: str) -> dict:
    """Undo a delete. -> `{id}`. 404 once the entry is gone."""
    if not (isinstance(token, str) and token and set(token) <= set('0123456789abcdef') and len(token) <= 64):
        raise PieceError('that is not a restore token')
    with _desk._store_lock:
        store = _desk._read_store()
        entry = _trash(store).get(token)
        if not isinstance(entry, dict):
            raise PieceError('there is nothing to restore (it was already restored or has expired)', 404)
        art = entry['article']
        arts = _articles(store)
        if art['id'] in arts:
            raise PieceError('an article with that id exists again, so the deleted one was not restored', 409)
        arts[art['id']] = art
        del _trash(store)[token]
        _desk._write_store(store)
    return {'id': art['id']}


def _purge_old(store: dict) -> None:
    cutoff = time.time() - TRASH_KEEP_DAYS * 86400
    trash = _trash(store)
    for token in list(trash):
        try:
            when = datetime.fromisoformat(str((trash[token] or {}).get('deleted_at')).replace('Z', '+00:00'))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            old = when.timestamp() < cutoff
        except (ValueError, TypeError, AttributeError) as e:
            _log(f'[desk] studio article trash entry {token} has no readable date, kept: {e}', flush=True)
            continue
        if old:
            del trash[token]
