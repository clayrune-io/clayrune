"""The Desk's INBOUND side — engagement feed, per-post metrics, read costing.
Spec: `docs/THE_DESK_V1_IA_REVISION_2.md` §6 (landing bundles), §8 ticket
R1-E, §10.7 (per-post outcome entries `source:'feed'`).

`mc/desk_publish.py` is the only module that POSTs. This is the only module
that READS a platform on the Desk's behalf, and like that one it keeps every
network call in one small file so "what can reach api.x.com" stays a grep.

## Honesty rules this module exists to keep

- **A gap is never silence.** A platform we cannot read is reported as
  `Not connected: replies on 𝕏 aren't read yet`, and a bundle with no covered
  platform carries `None` counts, never `0`. "No one is talking" and "we are
  not listening" must not look the same (§6).
- **A metric we did not read is never 0.** A platform that does not return a
  metric writes no outcome entry for it, so the retro's cell reads
  `No per-post numbers yet` (§10.7).
- **Every paid read is costed.** X reads are pay-per-use ($0.005 per resource
  returned, docs.x.com 2026-09-30). A read runs only if the project's budget
  (`presence.budget.amount` per `period`, which already covers publishing) has
  room for its worst case, and its actual cost lands in the read ledger
  (`mc.desk.record_read`). Budget 0 means no paid reads, not unlimited.
- **No substitute.** A platform with no read credential/approval reports the
  gap. There is no scraper fallback, and no silent switch between the two
  routes below: an account is read the way its user chose, or not at all.

## Two ways to read an account (user's choice per account, Ron 2026-09-30)

`presence.accounts[].read_via` is `pane` (DEFAULT; absent = pane) or `api`.

- `pane`: free (0 against the budget). Reads the signed-in page text of the
  account's own named browser profile (`accounts[].browser_profile`) through
  `browser_routes.ProfilePageReader`, the in-process twin of
  `POST /api/browser/read`. Read-only: navigate + read, never a click. Page text
  is untrusted third-party data and is only ever parsed, never acted on. A read
  failure is recorded as a gap, never retried through another HTTP client.
- `api`: paid per read ($0.005/resource), costed against the budget, needs the
  vault token below.

Broad listening (search, discussions) is not built here and stays pane-only.

## What the API route needs (verified 2026-09-30, metadata only)

- X: a read needs a user-context token in the vault under `X_READ_SECRET`
  (the same entry `desk_publish` posts with). The vault held only an `x.com`
  website *password* entry, no OAuth token, so X reads report `not_connected`
  until a human adds one. Endpoints: `GET /2/users/me`,
  `GET /2/users/{id}/mentions`, `GET /2/tweets?ids=` — all read-scope, none of
  them write.
- LinkedIn: comments and page analytics sit behind the Community Management
  API (`r_organization_social`), which needs app review the Clayrune page's
  posting already waits on. No token, no approval: `LinkedInReader` is a
  gap-only reader and makes no network call, ever.

## Transport

Readers take an injectable `transport(url, params, token) -> dict`. Tests pass
recorded payloads; production uses `_urllib_transport`. Nothing here is called
on import or on a schedule — `poll_project` runs only from its route.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mc import desk as _desk
from mc import desk_oauth as _oauth
from mc import secrets_store
from mc.core import _log

X_API_BASE = 'https://api.x.com/2'
# Same vault entry desk_publish posts with; created by a human, never by an agent.
X_READ_SECRET = 'x.oauth-token'

# docs.x.com/x-api/getting-started/pricing, fetched 2026-09-30:
# "Posts: Read | $0.005 per resource", "charged per resource returned".
X_READ_UNIT_COST = 0.005

MENTIONS_MAX_RESULTS = 20       # API floor is 5, ceiling 100; worst case costed = this x unit
METRICS_BATCH = 100             # ids per GET /2/tweets
METRICS_MAX_POSTS = 50          # posts read per poll, newest first
METRICS_WINDOW_DAYS = 30        # older posts stop moving; not worth a daily read
_HTTP_TIMEOUT_SECONDS = 20

PLATFORM_LABELS = {'x': '\U0001D54F', 'linkedin': 'LinkedIn'}

# x public_metrics / non_public_metrics key -> the outcome metric name
_X_METRIC_NAMES = {
    'impression_count': 'impressions', 'like_count': 'likes', 'reply_count': 'replies',
    'retweet_count': 'reposts', 'quote_count': 'quotes', 'bookmark_count': 'bookmarks',
    'url_link_clicks': 'clicks',
}

_STATUS_ID = re.compile(r'/status/(\d+)')


class ReadError(Exception):
    """A platform read failed. `str(e)` is the verbatim reason to show.
    `kind` tags a failure the UI words specially (`not_signed_in`)."""
    kind: str | None = None


class NotSignedIn(ReadError):
    """The pane's profile reached the site's login wall: a coverage gap with its
    own wording (`Not connected (sign in to X in the browser pane)`)."""
    kind = 'not_signed_in'


# -- readers --------------------------------------------------------------------

def _urllib_transport(url: str, params: dict, token: str) -> dict:
    """The only live network call in this module. GET only."""
    full = f'{url}?{urllib.parse.urlencode(params)}' if params else url
    req = urllib.request.Request(full, method='GET',
                                 headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:300]
        raise ReadError(f'X API HTTP {e.code}: {detail}') from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ReadError(f'X API request failed: {e}') from e


class Reader:
    """One platform's inbound read. `capability()` must never touch the
    network and never dispense a secret value. `via` is the route this reader
    takes (`api` paid, `pane` free); it is stamped on the coverage record.

    `capability()` -> `{connected, reason, short}`: `reason` is the specific
    why, `short` the parenthetical the UI shows (`Not connected (<short>)`)."""
    platform = ''
    via = 'api'
    unit_cost = 0.0
    sign_in_short = ''

    def capability(self) -> dict:
        raise NotImplementedError

    def fetch_mentions(self, *, since_id: str | None, known_posts: dict[str, str]) -> dict:
        """-> {items: [normalized], resources: int, cursor: str|None, account: str|None}.
        `known_posts` maps our platform post ids to ledger ids."""
        raise ReadError(f'{self.platform} replies are not readable')

    def fetch_metrics(self, external_ids: list[str]) -> dict:
        """-> {resources: int, metrics: {external_id: {metric: number}},
        unavailable: {external_id: reason}}. A post whose numbers could not be
        read goes in `unavailable`, never in `metrics` as 0."""
        raise ReadError(f'{self.platform} post metrics are not readable')

    def close(self) -> None:
        """Release anything a read opened (a pane Chromium). Never raises."""


class LinkedInReader(Reader):
    """Gap-only on both routes: the API route needs Community Management API
    approval we lack, and no LinkedIn page parser exists for the pane route."""
    platform = 'linkedin'

    def __init__(self, via: str = 'api'):
        self.via = via

    def capability(self) -> dict:
        if self.via == 'pane':
            return {'connected': False,
                    'short': "LinkedIn isn't read through the browser pane yet",
                    'reason': 'no LinkedIn page reader exists for the browser pane yet; '
                              'only the API route is planned, and it needs approval'}
        return {'connected': False,
                'short': 'LinkedIn read access needs API approval',
                'reason': 'LinkedIn comments and page analytics need Community '
                          'Management API approval (r_organization_social); none granted'}


class XReader(Reader):
    platform = 'x'
    via = 'api'
    unit_cost = X_READ_UNIT_COST

    def __init__(self, *, transport: Callable[[str, dict, str], dict] | None = None,
                 token: str | None = None, include_nonpublic: bool = False):
        self._transport = transport or _urllib_transport
        self._token = token            # tests inject; production resolves lazily
        # `non_public_metrics` (url_link_clicks) is author-token-only per X's
        # model and NOT exercised against the live API here; off until a human
        # confirms it on a real post. When on, a missing key writes nothing.
        self._nonpublic = include_nonpublic

    def capability(self) -> dict:
        if self._token:
            return {'connected': True, 'reason': None, 'short': None}
        try:
            names = {s.get('name') for s in secrets_store.list_secrets()}
        except Exception as e:
            return {'connected': False, 'short': 'no API token',
                    'reason': f'vault unreadable: {e}'}
        if _oauth.SERVICES['x']['vault'] in names:      # signed in from Connections
            st = _oauth.status('x')
            if st['state'] == 'connected':
                return {'connected': True, 'reason': None, 'short': None}
            return {'connected': False, 'short': 'sign in again', 'reason': st['reason']}
        if X_READ_SECRET not in names:
            return {'connected': False, 'short': 'no API token',
                    'reason': f'no X read credential (vault entry {X_READ_SECRET!r} is not set)'}
        return {'connected': True, 'reason': None, 'short': None}

    def _tok(self) -> str:
        if self._token:
            return self._token
        try:
            return _oauth.x_token(consumer='desk_engagement')
        except (secrets_store.SecretsError, _oauth.OAuthError) as e:
            raise ReadError(f'credential unavailable: {e}') from e

    def fetch_mentions(self, *, since_id, known_posts):
        token = self._tok()
        me = (self._transport(f'{X_API_BASE}/users/me', {}, token) or {}).get('data') or {}
        uid, handle = me.get('id'), me.get('username')
        if not uid:
            raise ReadError('X /users/me returned no user id')
        params = {'max_results': MENTIONS_MAX_RESULTS,
                  'tweet.fields': 'created_at,author_id,referenced_tweets,conversation_id',
                  'expansions': 'author_id', 'user.fields': 'username'}
        if since_id:
            params['since_id'] = since_id
        payload = self._transport(f'{X_API_BASE}/users/{uid}/mentions', params, token) or {}
        users = {u.get('id'): u.get('username')
                 for u in (payload.get('includes') or {}).get('users') or []}
        items = []
        for t in payload.get('data') or []:
            tid = t.get('id')
            if not tid:
                continue
            parent = next((r.get('id') for r in t.get('referenced_tweets') or []
                           if r.get('type') == 'replied_to'), None)
            who = users.get(t.get('author_id'))
            items.append({
                'external_id': tid,
                'author': f'@{who}' if who else None,
                'excerpt': (t.get('text') or '')[:280],
                'created_at': t.get('created_at'),
                'url': f'https://x.com/{who}/status/{tid}' if who else f'https://x.com/i/web/status/{tid}',
                # a reply under one of OUR posts is an our_posts conversation
                'post_id': known_posts.get(parent) if parent else None,
                'source': 'our_posts' if parent and parent in known_posts else 'mentions',
            })
        # +1: the /users/me lookup is itself a billed read as far as we can
        # tell; charge it rather than assume it free (unverified either way).
        return {'items': items, 'resources': len(items) + 1,
                'cursor': (payload.get('meta') or {}).get('newest_id'),
                'account': f'@{handle}' if handle else None}

    def fetch_metrics(self, external_ids):
        token = self._tok()
        fields = 'public_metrics' + (',non_public_metrics' if self._nonpublic else '')
        payload = self._transport(f'{X_API_BASE}/tweets',
                                  {'ids': ','.join(external_ids), 'tweet.fields': fields},
                                  token) or {}
        out: dict[str, dict] = {}
        rows = payload.get('data') or []
        for t in rows:
            got: dict[str, Any] = {}
            for group in ('public_metrics', 'non_public_metrics'):
                for k, v in (t.get(group) or {}).items():
                    name = _X_METRIC_NAMES.get(k)
                    if name and isinstance(v, (int, float)) and not isinstance(v, bool):
                        got[name] = v
            if t.get('id') and got:
                out[t['id']] = got
        return {'resources': len(rows), 'metrics': out}


# -- the pane route: parse page text as DATA ------------------------------------
#
# Everything below turns the visible text of an x.com page into rows. The text
# is untrusted third-party content (the pane's read envelope says so): it is
# matched against fixed patterns and copied into fields, never interpreted, and
# a tweet that says "ignore previous instructions" is just an excerpt string.
# Anything the patterns do not recognise is dropped or reported unavailable;
# nothing is guessed from it.

X_MENTIONS_URL = 'https://x.com/notifications/mentions'
X_STATUS_URL = 'https://x.com/i/status/{id}'
PANE_METRICS_MAX_POSTS = 10      # one page load per post: a smaller cap than the API's 50

_MID_DOT = '·'
_HANDLE = re.compile(r'^@[A-Za-z0-9_]{1,15}$')
# relative ("2h", "14m") or absolute ("Sep 3", "Sep 3, 2025") as X renders a tweet's age
_TWEET_AGE = re.compile(r'^(\d{1,3}[smhdw]|[A-Z][a-z]{2} \d{1,2}(, \d{4})?)$')
_COUNT_LINE = re.compile(r'^\d[\d,.]*[KM]?$')
_LOGIN_PATH = re.compile(r'^/(login|i/flow/(login|signup|single_sign_on))', re.I)
_LOGIN_LINES = {'sign in to x', 'log in to x', 'log in', 'sign in'}
# label on a status page -> the outcome metric name (same names the API route writes)
_PANE_METRIC_NAMES = {'view': 'impressions', 'views': 'impressions', 'reply': 'replies',
                      'replies': 'replies', 'repost': 'reposts', 'reposts': 'reposts',
                      'quote': 'quotes', 'quotes': 'quotes', 'like': 'likes', 'likes': 'likes',
                      'bookmark': 'bookmarks', 'bookmarks': 'bookmarks'}
_LABEL_ALT = '|'.join(sorted(_PANE_METRIC_NAMES, key=len, reverse=True))
_LABELLED_ONE = re.compile(rf'^(\d[\d,]*) ({_LABEL_ALT})$', re.I)
_PLAIN_INT = re.compile(r'^\d[\d,]*$')
_LABEL_ONLY = re.compile(rf'^({_LABEL_ALT})$', re.I)


def _text_lines(text: str | None) -> list[str]:
    return [ln.strip() for ln in (text or '').split('\n') if ln.strip()]


def _looks_signed_out(final_url: str, title: str, lines: list[str]) -> bool:
    try:
        path = urllib.parse.urlsplit(final_url or '').path
    except ValueError:
        path = ''
    if _LOGIN_PATH.match(path):
        return True
    if (title or '').strip().lower().startswith(('log in', 'sign in')):
        return True
    return any(ln.lower() in _LOGIN_LINES for ln in lines)


def parse_x_mentions_text(text: str | None) -> list[dict]:
    """Rows from the visible text of x.com/notifications/mentions.

    A tweet is anchored on its header: `@handle`, `·`, age, on three
    consecutive lines. The body is what follows up to the next tweet's
    display-name line, minus an optional `Replying to @…` prefix and the
    trailing run of bare counts (reply/repost/like/view numbers). A body that
    really ends in a bare number loses it: the flat text cannot tell them apart.
    `external_id` is a hash of handle + body, so a re-read dedupes; it is NOT an
    X status id. No link, timestamp or parent post is recoverable from text.
    Returns [] for text with no tweet in it."""
    lines = _text_lines(text)
    heads = [i for i in range(1, len(lines) - 2)
             if _HANDLE.match(lines[i]) and lines[i + 1] == _MID_DOT
             and _TWEET_AGE.match(lines[i + 2])]
    items = []
    for n, i in enumerate(heads):
        end = heads[n + 1] - 1 if n + 1 < len(heads) else len(lines)
        body = lines[i + 3:end]
        if body and body[0].lower().startswith('replying to'):
            body = body[1:]
            while body and (_HANDLE.match(body[0]) or body[0].lower() in ('and', ',')):
                body = body[1:]
        for _ in range(5):
            if body and _COUNT_LINE.match(body[-1]):
                body.pop()
        excerpt = ' '.join(body)[:280]
        handle = lines[i]
        items.append({
            'external_id': 'pane:' + hashlib.sha1(f'{handle}|{excerpt}'.encode('utf-8')).hexdigest()[:16],
            'author': handle, 'excerpt': excerpt, 'created_at': None, 'url': None,
            'post_id': None, 'source': 'mentions',
        })
    return items


def parse_x_status_metrics(text: str | None) -> dict[str, int]:
    """Metrics the status page shows as EXACT labelled counts: `1,234 Views`, or
    a bare number on one line with its label on the next. The first occurrence
    of each label wins (the focal post renders before any reply). An abbreviated
    count (`1.2K`) is rounded by X and matches neither form, so it is left out:
    a metric not read is absent, never approximated and never 0."""
    lines = _text_lines(text)
    got: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = _LABELLED_ONE.match(ln)
        if m:
            num, label = m.group(1), m.group(2)
        elif _PLAIN_INT.match(ln) and i + 1 < len(lines) and _LABEL_ONLY.match(lines[i + 1]):
            num, label = ln, lines[i + 1]
        else:
            continue
        got.setdefault(_PANE_METRIC_NAMES[label.lower()], int(num.replace(',', '')))
    return got


def _default_page_reader(project_id: str, profile: str):
    from mc.blueprints import browser_routes
    return browser_routes.ProfilePageReader(project_id, profile)


def _default_profile_exists(name: str) -> bool:
    from mc.blueprints import browser_routes
    return browser_routes.named_profile_exists(name)


class PaneXReader(Reader):
    """Reads an X account through the signed-in pane profile: free, same
    interface as `XReader`. Nothing is written to X; the only actions are
    navigate and read."""
    platform = 'x'
    via = 'pane'
    unit_cost = 0.0
    sign_in_short = 'sign in to X in the browser pane'

    def __init__(self, project_id: str, profile: str | None, *,
                 page_reader_factory: Callable[[str, str], Any] | None = None,
                 profile_exists: Callable[[str], bool] | None = None):
        self._project_id = project_id
        self._profile = (profile or '').strip().lower()
        self._factory = page_reader_factory or _default_page_reader
        self._profile_exists = profile_exists or _default_profile_exists
        self._pages: Any = None

    def capability(self) -> dict:
        if not self._profile:
            return {'connected': False, 'short': self.sign_in_short,
                    'reason': 'no browser profile is chosen for this account'}
        if not self._profile_exists(self._profile):
            return {'connected': False, 'short': self.sign_in_short,
                    'reason': f'no saved browser profile {self._profile!r}'}
        return {'connected': True, 'reason': None, 'short': None}

    def _read(self, url: str) -> dict:
        if self._pages is None:
            self._pages = self._factory(self._project_id, self._profile)
        body = self._pages.read(url)
        if not isinstance(body, dict) or not body.get('ok'):
            b = body if isinstance(body, dict) else {}
            raise _pane_error(b.get('error'), b.get('detail'))
        return body

    def close(self) -> None:
        pages, self._pages = self._pages, None
        if pages is not None:
            try:
                pages.close()
            except Exception as e:
                _log(f'[desk_engagement] pane close failed: {e}')

    def fetch_mentions(self, *, since_id, known_posts):
        body = self._read(X_MENTIONS_URL)
        text = (body.get('content') or {}).get('text') or ''
        lines = _text_lines(text)
        if _looks_signed_out(body.get('final_url') or body.get('url') or '',
                             body.get('title') or '', lines):
            raise NotSignedIn(self.sign_in_short)
        if 'Notifications' not in lines or 'Mentions' not in lines:
            raise ReadError('the mentions page did not look like X notifications '
                            '(layout changed or page not loaded); nothing was read')
        return {'items': parse_x_mentions_text(text), 'resources': 0,
                'cursor': None, 'account': None}

    def fetch_metrics(self, external_ids):
        metrics: dict[str, dict] = {}
        unavailable: dict[str, str] = {}
        for ext in external_ids[:PANE_METRICS_MAX_POSTS]:
            try:
                body = self._read(X_STATUS_URL.format(id=ext))
            except _FatalPaneError:
                raise
            except ReadError as e:
                unavailable[ext] = str(e)
                continue
            final = body.get('final_url') or ''
            lines = _text_lines((body.get('content') or {}).get('text'))
            if _looks_signed_out(final, body.get('title') or '', lines):
                raise NotSignedIn(self.sign_in_short)
            if ext not in final:
                unavailable[ext] = 'the pane did not land on that post'
                continue
            got = parse_x_status_metrics((body.get('content') or {}).get('text'))
            if got:
                metrics[ext] = got
            else:
                unavailable[ext] = 'no exact counts visible on the post page'
        for ext in external_ids[PANE_METRICS_MAX_POSTS:]:
            unavailable[ext] = 'over the per-poll pane limit'
        return {'resources': 0, 'metrics': metrics, 'unavailable': unavailable}


class _FatalPaneError(ReadError):
    """A pane failure that makes every further page read pointless (no profile,
    profile open elsewhere, Chromium would not start): abort the pass."""


# read-envelope `error` kinds (browser_routes._read_error) that end the whole pass
_FATAL_PANE_KINDS = {'no_profile', 'profile_in_use', 'launch_failed', 'own_origin_blocked',
                     'unknown_session'}


def _pane_error(kind: str | None, detail: str | None) -> ReadError:
    msg = f'browser pane read failed ({kind or "unknown"}): {detail or "no detail"}'
    return _FatalPaneError(msg) if kind in _FATAL_PANE_KINDS else ReadError(msg)


def default_readers() -> dict[str, Reader]:
    return {'x': XReader(), 'linkedin': LinkedInReader()}


def readers_for_project(project_id: str) -> dict[str, Reader]:
    """One reader per platform the project is present on, chosen by that
    account's `read_via` (absent = `pane`). The project's first account on a
    platform decides; a platform it only published to (no account record)
    reads the default way."""
    out: dict[str, Reader] = {}
    for platform in project_platforms(project_id):
        acc = platform_account(project_id, platform)
        via = _desk.account_read_via(acc)
        if platform == 'x':
            out['x'] = (XReader() if via == 'api'
                        else PaneXReader(project_id, (acc or {}).get('browser_profile')))
        elif platform == 'linkedin':
            out['linkedin'] = LinkedInReader(via=via)
    return out


# -- budget ---------------------------------------------------------------------

_PERIOD_DAYS = {'day': 1, 'week': 7, 'month': 30}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def project_spend(project_id: str, *, now: datetime | None = None) -> dict:
    """Spend in the project's current budget period: what it paid to publish
    (ledger `cost`) plus what it paid to read (read ledger). The presence
    budget covers both (§5.2), so reads must count against the same number."""
    pres = _desk.get_presence(project_id) or {}
    budget = pres.get('budget') or {}
    days = _PERIOD_DAYS.get(budget.get('period') or 'month', 30)
    since = _iso(_now(now) - timedelta(days=days))
    published = sum(float(r.get('cost') or 0)
                    for r in _desk.list_ledger(limit=100000, project_id=project_id)
                    if (r.get('published_at') or '') >= since)
    reads = sum(float(r.get('cost') or 0)
                for r in _desk.list_reads(project_id=project_id, since=since))
    amount = float(budget.get('amount') or 0)
    return {'amount': amount, 'period': budget.get('period') or 'month',
            'published': round(published, 4), 'reads': round(reads, 4),
            'room': round(max(0.0, amount - published - reads), 4)}


# -- project <-> platform -------------------------------------------------------

def project_platforms(project_id: str) -> list[str]:
    """Platforms this project is present on: its bound accounts (an explicit
    `platform`, else the named voice's) and any platform it has published to."""
    plats: list[str] = []
    for acc in ((_desk.get_presence(project_id) or {}).get('accounts') or []):
        p = _account_platform(acc)
        if p:
            plats.append(p)
    for r in _desk.list_ledger(limit=100000, project_id=project_id):
        if r.get('platform'):
            plats.append(r['platform'])
    return sorted(set(plats))


def _account_platform(acc) -> str | None:
    """An account record's platform: explicit, else its voice's. A bare-id
    entry (no record) names none."""
    if not isinstance(acc, dict):
        return None
    p = acc.get('platform')
    if not p:
        try:
            p = _desk.get_voice(acc.get('voice') or '').get('platform')
        except Exception:
            p = None
    return p or None


def platform_account(project_id: str, platform: str) -> dict | None:
    """The project's first presence account on `platform`, or None."""
    for acc in ((_desk.get_presence(project_id) or {}).get('accounts') or []):
        if _account_platform(acc) == platform:
            return acc
    return None


def _x_post_id(row: dict) -> str | None:
    m = _STATUS_ID.search(row.get('url') or '')
    return m.group(1) if m else None


# -- coverage -------------------------------------------------------------------

def platform_coverage(project_id: str, platform: str, reader: Reader | None = None) -> dict:
    """`state`: `ok` | `not_connected` | `not_read_yet`. `message` is the exact
    line the UI shows (`Not connected (<why>)`); `reason` is the specific why;
    `via` the route the account is read by.

    A record written by the OTHER route (the user switched the account) says
    nothing about this one: it counts as not read yet, and its last error is not
    shown against a route it did not happen on."""
    reader = reader or readers_for_project(project_id).get(platform)
    label = PLATFORM_LABELS.get(platform, platform)
    rec = _desk.get_read_coverage(project_id).get(platform) or {}
    cap = reader.capability() if reader else {'connected': False,
                                              'short': f'no reader for {platform}',
                                              'reason': f'no reader for {platform}'}
    via = reader.via if reader else None
    same_route = reader is not None and (rec.get('via') or 'api') == reader.via
    base = {'platform': platform, 'label': label, 'via': via}
    if not cap['connected']:
        return {**base, 'state': 'not_connected', 'reason': cap.get('reason'),
                'last_ok_at': rec.get('last_ok_at') if same_route else None,
                'message': f"Not connected ({cap.get('short') or cap.get('reason')})"}
    if same_route and rec.get('error_kind') == 'not_signed_in' and reader is not None:
        # The profile exists but its last read hit the login wall.
        return {**base, 'state': 'not_connected', 'reason': rec.get('last_error'),
                'last_ok_at': rec.get('last_ok_at'),
                'message': f'Not connected ({reader.sign_in_short})'}
    if not same_route or not rec.get('last_ok_at'):
        err = rec.get('last_error') if same_route else None
        return {**base, 'state': 'not_read_yet', 'reason': err, 'last_ok_at': None,
                'message': f'Connected, not read yet on {label}' + (f' ({err})' if err else '')}
    return {**base, 'state': 'ok', 'reason': rec.get('last_error'),
            'last_ok_at': rec['last_ok_at'], 'message': ''}


# -- poll -----------------------------------------------------------------------

def poll_project(project_id: str, *, readers: dict[str, Reader] | None = None,
                 now: datetime | None = None) -> dict:
    """One read pass for one project: new replies/mentions into the feed, then
    today's per-post metrics as `source:'feed'` outcomes. Every platform that
    cannot be read is reported, none is skipped silently. Never raises.

    Each account is read the way its `read_via` says (see
    `readers_for_project`); `readers` overrides that for tests."""
    readers = readers if readers is not None else readers_for_project(project_id)
    now_dt = _now(now)
    report: dict[str, Any] = {'project_id': project_id, 'platforms': {}}
    for platform in project_platforms(project_id):
        reader = readers.get(platform)
        cov = platform_coverage(project_id, platform, reader)
        entry: dict[str, Any] = {'state': cov['state'], 'reason': cov['reason'],
                                 'via': cov['via'], 'message': cov['message'],
                                 'new_items': 0, 'metrics_written': 0,
                                 'metrics_unavailable': 0, 'spent': 0.0}
        report['platforms'][platform] = entry
        # Decided on capability, not on the recorded state: a profile that hit
        # the login wall last time must be retried once the user signs in.
        if reader is None or not reader.capability()['connected']:
            continue          # no credential/approval/profile: no call, no spend
        try:
            _poll_platform(project_id, platform, reader, entry, now_dt)
        except Exception as e:
            _log(f'[desk_engagement] {platform} poll for {project_id} failed: {e}')
            entry['error'] = str(e)
            _desk.set_read_coverage(project_id, platform, ok=False, error=str(e), via=reader.via)
        finally:
            reader.close()
    return report


def _afford(project_id: str, reader: Reader, resources: int, now_dt: datetime) -> bool:
    need = resources * reader.unit_cost
    return need <= 0 or need <= project_spend(project_id, now=now_dt)['room'] + 1e-9


def _poll_platform(project_id: str, platform: str, reader: Reader,
                   entry: dict, now_dt: datetime) -> None:
    rows = [r for r in _desk.list_ledger(limit=100000, platform=platform, project_id=project_id)]
    known = {}
    for r in rows:
        ext = _x_post_id(r) if platform == 'x' else None
        if ext:
            known[ext] = r['id']

    # 1. replies + mentions
    cursor = (_desk.get_read_coverage(project_id).get(platform) or {}).get('cursor')
    if not _afford(project_id, reader, MENTIONS_MAX_RESULTS + 1, now_dt):
        msg = 'read budget spent: replies not read this period'
        entry['budget_blocked'] = True
        _desk.set_read_coverage(project_id, platform, ok=False, error=msg, via=reader.via)
        return
    try:
        got = reader.fetch_mentions(since_id=cursor, known_posts=known)
    except ReadError as e:
        _desk.record_read(platform=platform, project_id=project_id, kind='replies',
                          resources=0, cost=0.0, ok=False, error=str(e))
        entry['error'] = str(e)
        _desk.set_read_coverage(project_id, platform, ok=False, error=str(e),
                                via=reader.via, error_kind=e.kind)
        if e.kind == 'not_signed_in':
            entry['state'] = 'not_connected'
            entry['message'] = f'Not connected ({reader.sign_in_short})'
        return
    cost = got['resources'] * reader.unit_cost
    _desk.record_read(platform=platform, project_id=project_id, kind='replies',
                      resources=got['resources'], cost=cost, ok=True)
    entry['spent'] += cost
    ledger_by_id = {r['id']: r for r in rows}
    for it in got['items']:
        led = ledger_by_id.get(it.get('post_id') or '')
        _row, created = _desk.upsert_engagement_item({
            **it, 'platform': platform, 'account': got.get('account'),
            'project_id': (led or {}).get('project_id') or project_id,
            'campaign_id': (led or {}).get('campaign_id'),
        })
        entry['new_items'] += 1 if created else 0
    _desk.set_read_coverage(project_id, platform, ok=True, cursor=got.get('cursor'),
                            via=reader.via)

    # 2. per-post metrics: once a day per post, recent posts only, each batch
    # costed before it is sent.
    cutoff = _iso(now_dt - timedelta(days=METRICS_WINDOW_DAYS))
    today = _iso(now_dt)[:10]
    todo = []
    for r in rows:
        ext = _x_post_id(r) if platform == 'x' else None
        if not ext or (r.get('published_at') or '') < cutoff:
            continue
        if any(o.get('source') == 'feed' and (o.get('at') or '')[:10] == today
               for o in r.get('outcomes') or []):
            continue          # already measured today; do not pay twice
        todo.append((r['id'], ext))
    todo = todo[:METRICS_MAX_POSTS]
    for i in range(0, len(todo), METRICS_BATCH):
        batch = todo[i:i + METRICS_BATCH]
        if not _afford(project_id, reader, len(batch), now_dt):
            entry['budget_blocked'] = True
            break
        try:
            res = reader.fetch_metrics([ext for _lid, ext in batch])
        except ReadError as e:
            _desk.record_read(platform=platform, project_id=project_id, kind='metrics',
                              resources=0, cost=0.0, ok=False, error=str(e))
            entry['error'] = str(e)
            if e.kind == 'not_signed_in':
                _desk.set_read_coverage(project_id, platform, ok=False, error=str(e),
                                        via=reader.via, error_kind=e.kind)
            break
        cost = res['resources'] * reader.unit_cost
        _desk.record_read(platform=platform, project_id=project_id, kind='metrics',
                          resources=res['resources'], cost=cost, ok=True)
        entry['spent'] += cost
        entry['metrics_unavailable'] += len(res.get('unavailable') or {})
        for lid, ext in batch:
            for metric, value in (res['metrics'].get(ext) or {}).items():
                if _desk.record_feed_outcome(lid, metric, value, at=_iso(now_dt)):
                    entry['metrics_written'] += 1
    entry['spent'] = round(entry['spent'], 4)


# -- aggregates (the §6 landing) -------------------------------------------------

_NEEDS = ('needs_you', 'needs_reply')


def _is_unread(row: dict) -> bool:
    return not row.get('read_at') and row.get('state') in _NEEDS


def project_bundle(project_id: str, *, period: str = 'week',
                   readers: dict[str, Reader] | None = None,
                   now: datetime | None = None) -> dict:
    """One landing bundle. Counts come from stored feed rows, so they match the
    lane view by construction (`lanes` uses the same state -> lane mapping as
    static/js/desk-v1-engagement.js `LANES`)."""
    readers = readers if readers is not None else readers_for_project(project_id)
    cov = [platform_coverage(project_id, p, readers.get(p))
           for p in project_platforms(project_id)]
    covered = [c for c in cov if c['state'] == 'ok']
    gaps = [c for c in cov if c['state'] != 'ok']
    items = _desk.list_engagement_items(project_id=project_id, limit=100000)
    base = {'project_id': project_id, 'period': period, 'coverage': cov,
            'gaps': [{'platform': g['platform'], 'state': g['state'],
                      'message': g['message'], 'reason': g['reason']} for g in gaps]}
    if not covered:
        # No platform read: the honest answer is "not connected", NOT zero.
        msg = gaps[0]['message'] if gaps else "Not connected: replies aren't read yet"
        return {**base, 'status': 'not_connected', 'message': msg, 'unread': None,
                'awaiting': None, 'total': None, 'lanes': None, 'by_platform': {},
                'latest': None}
    covered_p = {c['platform'] for c in covered}
    items = [i for i in items if i.get('platform') in covered_p]
    days = 30 if period == 'month' else 7
    since = _iso(_now(now) - timedelta(days=days))
    lanes = {'incoming': 0, 'suggested': 0, 'sent': 0}
    for i in items:
        k = {'needs_you': 'incoming', 'needs_reply': 'suggested', 'sent': 'sent'}.get(i.get('state'))
        if k:
            lanes[k] += 1
    by_platform: dict[str, dict] = {}
    for i in items:
        if (i.get('created_at') or '') >= since:
            b = by_platform.setdefault(i['platform'], {'total': 0, 'unread': 0})
            b['total'] += 1
            b['unread'] += 1 if _is_unread(i) else 0
    latest = None
    if items:
        top = items[0]   # list_engagement_items sorts newest first
        latest = {'id': top['id'], 'excerpt': top.get('excerpt'), 'platform': top['platform'],
                  'author': top.get('author'), 'created_at': top.get('created_at')}
    return {**base, 'status': 'partial' if gaps else 'connected',
            'message': gaps[0]['message'] if gaps else '',
            'unread': sum(1 for i in items if _is_unread(i)),
            'awaiting': lanes['suggested'],
            'total': sum(b['total'] for b in by_platform.values()),
            'lanes': lanes, 'by_platform': by_platform, 'latest': latest}


def overview(*, period: str = 'week', readers: dict[str, Reader] | None = None,
             now: datetime | None = None) -> dict:
    pids: set[str] = set()
    with _desk._store_lock:
        store = _desk._read_store()
    pids.update(store['presences'])
    pids.update(i['project_id'] for i in store['engagement']['items'].values() if i.get('project_id'))
    pids.update(r['project_id'] for r in store['ledger'] if r.get('project_id'))
    bundles = [project_bundle(p, period=period, readers=readers, now=now) for p in sorted(pids)]
    live = [b for b in bundles if b['status'] != 'not_connected']
    return {'period': period, 'bundles': bundles,
            # header `💬 Engagement · n`: None when nothing is being read at all
            'unread_total': sum(b['unread'] for b in live) if live else None}
