"""The Desk's outbound publisher — the ONLY module in `mc/` that posts to a
social platform. Step 2 of `docs/THE_DESK_SIMPLIFICATION_PLAN.md` §5.

Callers: the engagement reply route (a human click, passcode) and
`mc/desk_tick.py` (R1-W S7: approved/scheduled versions, behind the human
approve route, M19). Nothing else may reach it: wiring a caller in without an
approval gate in front would let something other than Ron's own click cause a
real, billed post (`docs/THE_DESK_SIMPLIFICATION_PLAN.md` §4, "why the Desk
cannot grant itself release").

Two platforms: X (`POST /2/tweets`) and a LinkedIn ORGANIZATION post
(`POST /rest/posts`, author `urn:li:organization:<id>`; R1-W S7, plan §4). The
LinkedIn path exists but no account can reach it until
`mc.desk_accounts.LINKEDIN_ORG_POSTING_APPROVED` is flipped, which happens when
LinkedIn approves the `w_organization_social` scope (Community Management API
review); `desk_accounts.publish_state` reports that account not ready until then.

WHY THIS IS ITS OWN MODULE, not a function on `mc/desk.py`: `mc/desk.py`'s own
docstring says its approval gate is permanent BECAUSE nothing in it publishes.
Keeping every outbound call in one small file makes "grep for the only place
that can reach api.x.com" a real audit, not a hope.

## Idempotency (requirement 1 of step 2)

A receipt is persisted keyed by `item['id']` the moment a post succeeds.
`publish()` checks that store BEFORE making any HTTP call — a second call for
an id that already has a receipt returns it unchanged and touches the network
not at all. This is what makes a retried route handler, a double-dispatched
tick, or a impatient double-click safe: at most one real post per item id,
ever.

That guarantee is only as good as the write actually landing, so unlike the
other Desk stores (`mc.desk.STORE_PATH`, best-effort — see its own module
docstring on `record_edit`), an unwired or unwritable `RECEIPTS_PATH` is
FAIL-CLOSED: `publish()` refuses to even attempt the post rather than risk
posting for real with no durable record to stop a retry from posting again.
The one place this can't fail closed is if the disk write itself fails
*after* the POST to X already succeeded — X does not offer a way to un-post
speculatively, so that path logs at error level with the full receipt (a
human can record it by hand) and still returns the receipt, because the post
genuinely happened and telling the caller 'failed' would be the lie, not the
truth.

## Where receipts live (requirement 1 continued)

`data/desk_receipts.json`, wired by `server.py` as `DESK_RECEIPTS_PATH` — a
SIBLING of `DATA_DIR` (`data/projects/`), never a member of it, same shelf as
`data/desk.json` / `data/desk_signals.jsonl` (see `mc/desk.py`'s own docstring
for why: `load_projects()` treats every `*.json` under `DATA_DIR` as a project
record, and a stray file there 500s both restart endpoints — the LOAD-BEARING
DATA_DIR rule in CLAUDE.md).

## Failure (requirement 2)

Every failure — missing/denied credential, non-2xx, timeout, a malformed
response — raises `PublishError` with the error text kept verbatim in the
exception. Nothing here retries. This module never touches a queue item's
`status`; the caller (not yet built) is the one place that decides what
'failed' means for its own store, exactly like `mc.desk.record_published`
records a fact instead of deciding one.

## The credential (vault rule 3, CLAUDE.md)

The X token comes from `mc.desk_oauth.x_token`: the sign-in a human made from
Connections (an access token that lapses within hours and is refreshed there
before it is handed over), else `X_OAUTH_TOKEN_SECRET`, a vault secret a HUMAN
pastes. This module only ever resolves it by name and never logs or returns
the value. It is a different secret from the
`x.com` *website* login the 2026-09-22 Desk audit found already in the vault
(`kind: password`, for signing into x.com by hand) — an OAuth user access
token is not a password and does not belong in the same entry.

## LinkedIn (verified 2026-10-01 against learn.microsoft.com Posts API, version moniker 2026-09)

`POST https://api.linkedin.com/rest/posts`, headers `Authorization: Bearer`,
`Linkedin-Version: YYYYMM` and `X-Restli-Protocol-Version: 2.0.0` (both
required on every call), JSON body `{author, commentary, visibility: PUBLIC,
distribution: {feedDistribution: MAIN_FEED, targetEntities: [],
thirdPartyDistributionChannels: []}, lifecycleState: PUBLISHED,
isReshareDisabledByAuthor: false}`. Success is `201` with the new post's URN in
the `x-restli-id` response header (no body). The permalink LinkedIn documents
is `https://www.linkedin.com/feed/update/<urn>/`. The docs list `Linkedin-Version`
202510 as sunsetting 2026-10-15, so `LINKEDIN_API_VERSION` is a current one;
re-check it when LinkedIn retires its own. The organization id is not a
secret and lives on the account record; only the token is in the vault.

## The permalink X's own API will not hand you

`POST /2/tweets` returns only `{data: {id, text, edit_history_post_ids}}` —
no permalink, no username (verified against `docs.x.com` 2026-09-23). Building
`https://x.com/{username}/status/{id}` needs a second authenticated call,
`GET /2/users/me` (same doc set, same date), so a receipt costs two requests,
not one. If that second call fails, the first one already posted for real —
raising `PublishError` there would report a successful post as a failure, so
it falls back to X's own generic permalink form, `https://x.com/i/web/status/
{id}`, and only logs the lookup failure. **Unverified**: that generic form is
long-standing X/Twitter front-end routing behavior (used in oEmbed/share
flows), not something `docs.x.com`'s API reference documents or guarantees —
flagged here rather than asserted as API contract.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mc.core import _atomic_write_text, _log, now_iso
from mc import desk as _desk
from mc import desk_account_refs as _refs
from mc import desk_oauth as _oauth
from mc import secrets_store

# -- wired by server.py -------------------------------------------------------
# Same placeholder shape as mc.desk.STORE_PATH / mc.automation_suggestions.STORE_PATH.
RECEIPTS_PATH: Path | None = None

STORE_VERSION = 1

X_API_BASE = 'https://api.x.com/2'

# Created by a human, never by an agent (vault rule 3) — step 3 wires the
# Accounts panel that lets Ron paste this in.
X_OAUTH_TOKEN_SECRET = 'x.oauth-token'
LINKEDIN_TOKEN_SECRET = 'linkedin.oauth-token'

LINKEDIN_API_BASE = 'https://api.linkedin.com/rest'
LINKEDIN_API_VERSION = '202609'
_ORG_ID = re.compile(r'^\d{1,20}$')

_HTTP_TIMEOUT_SECONDS = 20

# Guards the whole idempotency-check -> POST -> persist sequence so two
# concurrent callers for the same item id cannot both pass the "no receipt
# yet" check and both post.
_lock = threading.Lock()


class PublishError(Exception):
    """`publish()` could not produce a receipt. `str(e)` is the exact error
    text to keep — the caller decides what it means for its own store; this
    module never mutates a queue item.

    `maybe_posted` is True when the request got no response, so the platform may
    have accepted it: the caller must not offer a plain retry (it would double
    post) and records an unknown outcome instead of a failure."""

    def __init__(self, message: str = '', *, maybe_posted: bool = False):
        super().__init__(message)
        self.maybe_posted = maybe_posted


# -- receipt store --------------------------------------------------------------

def _empty_store() -> dict[str, Any]:
    return {'version': STORE_VERSION, 'receipts': {}}


def _read_store(*, strict: bool = False) -> dict[str, Any]:
    """The receipts store. `strict=True` (the publish path) RAISES on an
    unreadable or malformed file instead of returning an empty store: an
    empty store there means "no receipt yet", which would let a corrupt file
    re-post every item it used to guard. Read-only callers keep the lenient
    default."""
    if RECEIPTS_PATH is None or not RECEIPTS_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(RECEIPTS_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        if strict:
            raise PublishError(
                f'receipts store {RECEIPTS_PATH} is unreadable ({e}) -- refusing '
                f'to post, because an unreadable store cannot prove this item '
                f'was not already posted') from e
        _log(f'[desk_publish] receipts store unreadable, treating as empty: {e}')
        return _empty_store()
    if not isinstance(data, dict) or not isinstance(data.get('receipts', {}), dict):
        if strict:
            raise PublishError(
                f'receipts store {RECEIPTS_PATH} is malformed -- refusing to post')
        _log('[desk_publish] receipts store is malformed, treating as empty')
        return _empty_store()
    data.setdefault('version', STORE_VERSION)
    data.setdefault('receipts', {})
    return data


def _write_store(store: dict[str, Any]) -> None:
    RECEIPTS_PATH.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
    _atomic_write_text(RECEIPTS_PATH, json.dumps(store, indent=2, ensure_ascii=False))


def get_receipt(item_id: str) -> dict[str, Any] | None:
    """The persisted receipt for an item id, if `publish()` already produced
    one. Read-only, no lock needed -- lets a caller check before deciding
    whether to call `publish()` at all."""
    return _read_store()['receipts'].get(item_id)


# -- X API calls ----------------------------------------------------------------

def _post_tweet(token: str, body: str, in_reply_to: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {'text': body}
    if in_reply_to:
        payload['reply'] = {'in_reply_to_tweet_id': in_reply_to}
    req = urllib.request.Request(
        f'{X_API_BASE}/tweets',
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={'Authorization': f'Bearer {token}',
                 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as r:
        return json.loads(r.read().decode('utf-8'))


def _get_username(token: str) -> str:
    req = urllib.request.Request(
        f'{X_API_BASE}/users/me',
        method='GET',
        headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as r:
        payload = json.loads(r.read().decode('utf-8'))
    return ((payload or {}).get('data') or {}).get('username') or ''


def _post_linkedin(token: str, organization_id: str, body: str) -> dict[str, Any]:
    """`{'id': <post urn>}`. The API answers 201 with the URN in the
    `x-restli-id` header and no body; a response without it is an error to the
    caller, never a synthetic receipt."""
    payload = {
        'author': f'urn:li:organization:{organization_id}',
        'commentary': body,
        'visibility': 'PUBLIC',
        'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [],
                         'thirdPartyDistributionChannels': []},
        'lifecycleState': 'PUBLISHED',
        'isReshareDisabledByAuthor': False,
    }
    req = urllib.request.Request(
        f'{LINKEDIN_API_BASE}/posts',
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={'Authorization': f'Bearer {token}',
                 'Content-Type': 'application/json',
                 'Linkedin-Version': LINKEDIN_API_VERSION,
                 'X-Restli-Protocol-Version': '2.0.0'})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as r:
        return {'id': r.headers.get('x-restli-id') or ''}


def _send_x(token: str, body: str, in_reply_to) -> tuple[str, str]:
    """(post id, permalink) for one X post; PublishError on any failure."""
    try:
        payload = (_post_tweet(token, body, str(in_reply_to)) if in_reply_to
                   else _post_tweet(token, body))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:500]
        raise PublishError(f'X API HTTP {e.code}: {detail}') from e
    except (urllib.error.URLError, TimeoutError) as e:
        # No response is NOT proof nothing posted: X may have accepted the
        # request before the connection dropped. Say so, so whoever
        # retries checks the account first instead of double-posting.
        raise PublishError(
            f'X API request failed with no response ({e}) -- the post MAY '
            f'have gone out; check the account before retrying', maybe_posted=True) from e
    except Exception as e:
        raise PublishError(f'X API request failed unexpectedly: {e}') from e

    post_id = ((payload or {}).get('data') or {}).get('id')
    if not post_id:
        raise PublishError(f'X API returned no post id: {json.dumps(payload)[:500]}')

    try:
        username = _get_username(token)
    except Exception as e:
        # The post is already live on X -- a lookup failure here must
        # never be reported as a failed publish (see module docstring).
        _log(f'[desk_publish] could not resolve username for the permalink, '
             f'falling back to the generic form: {e}')
        username = ''

    permalink = (f'https://x.com/{username}/status/{post_id}' if username
                 else f'https://x.com/i/web/status/{post_id}')
    return post_id, permalink


def _send_linkedin(token: str, organization_id: str, body: str) -> str:
    """The new post's URN; PublishError on any failure."""
    try:
        payload = _post_linkedin(token, organization_id, body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:500]
        raise PublishError(f'LinkedIn API HTTP {e.code}: {detail}') from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise PublishError(
            f'LinkedIn API request failed with no response ({e}) -- the post MAY '
            f'have gone out; check the Company Page before retrying', maybe_posted=True) from e
    except Exception as e:
        raise PublishError(f'LinkedIn API request failed unexpectedly: {e}') from e
    post_id = (payload or {}).get('id')
    if not post_id:
        # A 2xx with no x-restli-id: the post probably exists but cannot be
        # linked or recorded, so this is an unknown outcome, never a failure.
        raise PublishError('LinkedIn API accepted the request but returned no post id '
                           '(x-restli-id header) -- the post MAY have gone out; check '
                           'the Company Page before retrying', maybe_posted=True)
    return post_id


# -- verify ---------------------------------------------------------------------

def _get_tweet(token: str, post_id: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f'{X_API_BASE}/tweets/{post_id}',
        method='GET',
        headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as r:
        return json.loads(r.read().decode('utf-8'))


def verify_post(platform: str, post_id: str, *, consumer: str = 'desk_publish',
                project_id: str | None = None, unattended: bool = False,
                account_id: str | None = None) -> bool | None:
    """Does the platform itself say this post exists? True = it does. False =
    the platform answered and the post is not there. None = this module has no
    way to ask (LinkedIn: reading a post back needs `r_organization_social`,
    which the posting scope does not grant), so the caller leaves the version
    `submitted` and says so rather than claiming a verification nobody did.
    Raises PublishError when the question could not be put (no credential, no
    response, an error status): "could not check" is never "not there".

    X: `GET /2/tweets/:id` with the same token that posted it. That is an X read
    and costs what a read costs (`mc.desk_engagement.X_READ_UNIT_COST`); fetching
    the permalink instead would prove nothing, since x.com answers 200 for any
    status URL."""
    if platform != 'x':
        return None
    try:
        token = _oauth.x_token(consumer=consumer, project_id=project_id, unattended=unattended,
                               account_id=_refs.oauth_arg_for(account_id))
    except (secrets_store.SecretsError, _oauth.OAuthError) as e:
        raise PublishError(f'credential unavailable: {e}') from e
    try:
        payload = _get_tweet(token, str(post_id))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        detail = e.read().decode('utf-8', errors='replace')[:300]
        raise PublishError(f'X API HTTP {e.code} while verifying: {detail}') from e
    except Exception as e:
        raise PublishError(f'could not reach X to verify: {e}') from e
    data = (payload or {}).get('data') or {}
    if str(data.get('id') or '') == str(post_id):
        return True
    # A 200 with errors[] and no data is X's "not found" for a deleted/never-seen id.
    return False


# -- publish --------------------------------------------------------------------

def publish(item: dict[str, Any], *, consumer: str = 'desk_publish',
            project_id: str | None = None, unattended: bool = False) -> dict[str, Any]:
    """POST one queue item to X and return its receipt.

    `item` is a `social_queue` row (`mc.blueprints.project_routes`): only
    `id`, `body`, `platform` and, for a reply, `in_reply_to` (the id of the post
    being answered) are read. Returns
    `{item_id, post_id, permalink, posted_at, body}`. Raises `PublishError` —
    never a bare exception, never a partial or synthetic receipt — on any
    failure, including a repeat call for an id whose earlier attempt failed
    (idempotency only caches SUCCESS; nothing here retries on its own, so a
    caller that wants to try a previously-failed item again calls `publish()`
    again on purpose).

    `consumer` / `project_id` / `unattended` pass straight through to
    `secrets_store.get_secret_value` for its scope check and audit trail.
    """
    item_id = item.get('id')
    if not item_id:
        raise PublishError("item has no 'id' -- cannot key idempotency")

    platform = item.get('platform')
    if platform not in ('x', 'linkedin'):
        raise PublishError(
            f"desk_publish only posts to X ('x') or LinkedIn ('linkedin'), "
            f"got platform={platform!r}")

    body = (item.get('body') or '').strip()
    if not body:
        raise PublishError(f"item {item_id} has no body to publish")

    organization_id = ''
    if platform == 'linkedin':
        if item.get('in_reply_to'):
            raise PublishError('LinkedIn replies are not supported by this publisher')
        organization_id = str(item.get('organization_id') or '').strip()
        if not _ORG_ID.match(organization_id):
            raise PublishError(
                'the LinkedIn account has no organization id (digits only, from the '
                'Company Page admin URL): set it on the account before publishing')

    with _lock:
        existing = _read_store(strict=True)['receipts'].get(item_id)
        if existing is not None:
            return existing

        # A Desk item names its campaign. The campaign must be running and its
        # CURRENT bounds must still sit inside what a human approved (Start /
        # Approve / Renew wrote that snapshot; a PATCH cannot). Checked after the
        # receipt lookup, which is a fact about a post that already went out, and
        # before any credential or network use. An item with no campaign_id (the
        # legacy queue) is not a campaign post and is not gated here.
        campaign_id = item.get('campaign_id')
        if campaign_id:
            blockers = _desk.publish_blockers(campaign_id)
            if blockers:
                raise PublishError(
                    f"campaign {campaign_id} is not approved to publish: {'; '.join(blockers)}")

        if RECEIPTS_PATH is None:
            # Fail BEFORE any network call -- see module docstring on why this
            # is the one thing that must not be best-effort.
            raise PublishError(
                'desk_publish.RECEIPTS_PATH is not wired -- refusing to post '
                'without durable idempotency in place')

        # Check the original workspace id before OAuth's legacy-name mapping.
        from mc.desk_connect import permission_check as _permissions
        try:
            _permissions.require_publish(item)
        except _permissions.PermissionDenied as e:
            raise PublishError(str(e)) from e
        x_account = _refs.oauth_arg_for(item.get('account_id')) if platform == 'x' else None

        try:
            if platform == 'x':
                # A sign-in from Connections (refreshed here, never a ~2 hour static
                # token), else the hand-pasted `x.oauth-token`.
                token = _oauth.x_token(consumer=consumer, project_id=project_id, unattended=unattended,
                                       account_id=x_account)
            else:
                token = secrets_store.get_secret_value(
                    LINKEDIN_TOKEN_SECRET, consumer=consumer, project_id=project_id, unattended=unattended)
        except (secrets_store.SecretsError, _oauth.OAuthError) as e:
            raise PublishError(f'credential unavailable: {e}') from e

        if platform == 'linkedin':
            post_id = _send_linkedin(token, organization_id, body)
            permalink = f'https://www.linkedin.com/feed/update/{post_id}/'
        else:
            post_id, permalink = _send_x(token, body, item.get('in_reply_to'))

        receipt = {
            'item_id': item_id,
            'platform': platform,
            'post_id': post_id,
            'permalink': permalink,
            'posted_at': now_iso(),
            'body': body,
        }

        try:
            store = _read_store(strict=True)
            store['receipts'][item_id] = receipt
            _write_store(store)
        except Exception as e:
            # Never overwrite a store we could not read: that would erase every
            # other item's receipt and re-arm them all for a duplicate post.
            # See module docstring: the post already happened, so this is
            # reported loudly, never as a PublishError -- doing so would tell
            # the caller a real post failed.
            _log(f'[desk_publish] CRITICAL: post {post_id} for item {item_id} '
                 f'succeeded but the receipt failed to persist -- a retry of '
                 f'this item risks a duplicate post. Receipt: '
                 f'{json.dumps(receipt)}. Write error: {e}', level='error')
        return receipt
