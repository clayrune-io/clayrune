"""The Desk's outbound publisher — the ONLY module in `mc/` that posts to a
social platform. Step 2 of `docs/THE_DESK_SIMPLIFICATION_PLAN.md` §5.

Nothing calls `publish()` yet. No route, button or schedule reaches it — that
is steps 3 (Accounts/credential), 4 (human-only approval gate) and 6 (the Desk
tick). Wiring this in before step 4's approval gate exists would let something
other than Ron's own click cause a real, billed X post
(`docs/THE_DESK_SIMPLIFICATION_PLAN.md` §4, "why the Desk cannot grant itself
release").

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

`X_OAUTH_TOKEN_SECRET` names a vault secret a HUMAN creates in step 3 — this
module only ever resolves it by name via `secrets_store.get_secret_value`
and never logs or returns the value. It is a different secret from the
`x.com` *website* login the 2026-09-22 Desk audit found already in the vault
(`kind: password`, for signing into x.com by hand) — an OAuth user access
token is not a password and does not belong in the same entry.

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
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mc.core import _atomic_write_text, _log, now_iso
from mc import secrets_store

# -- wired by server.py -------------------------------------------------------
# Same placeholder shape as mc.desk.STORE_PATH / mc.automation_suggestions.STORE_PATH.
RECEIPTS_PATH: Path | None = None

STORE_VERSION = 1

X_API_BASE = 'https://api.x.com/2'

# Created by a human, never by an agent (vault rule 3) — step 3 wires the
# Accounts panel that lets Ron paste this in.
X_OAUTH_TOKEN_SECRET = 'x.oauth-token'

_HTTP_TIMEOUT_SECONDS = 20

# Guards the whole idempotency-check -> POST -> persist sequence so two
# concurrent callers for the same item id cannot both pass the "no receipt
# yet" check and both post.
_lock = threading.Lock()


class PublishError(Exception):
    """`publish()` could not produce a receipt. `str(e)` is the exact error
    text to keep — the caller decides what it means for its own store; this
    module never mutates a queue item."""


# -- receipt store --------------------------------------------------------------

def _empty_store() -> dict[str, Any]:
    return {'version': STORE_VERSION, 'receipts': {}}


def _read_store() -> dict[str, Any]:
    if RECEIPTS_PATH is None or not RECEIPTS_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(RECEIPTS_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        # A corrupt file must never look like "no receipt yet" to the
        # idempotency check on a WRITE path -- but this is a READ, and the
        # write path below re-reads-and-merges rather than trusting this
        # blindly, so treating a bad file as empty here costs at most a
        # duplicate-post *risk* the write path is about to re-check for real
        # via `RECEIPTS_PATH.exists()` failing loudly if truly unwritable.
        _log(f'[desk_publish] receipts store unreadable, treating as empty: {e}')
        return _empty_store()
    if not isinstance(data, dict):
        _log('[desk_publish] receipts store is not an object, treating as empty')
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

def _post_tweet(token: str, body: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f'{X_API_BASE}/tweets',
        data=json.dumps({'text': body}).encode('utf-8'),
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


# -- publish --------------------------------------------------------------------

def publish(item: dict[str, Any], *, consumer: str = 'desk_publish',
            project_id: str | None = None, unattended: bool = False) -> dict[str, Any]:
    """POST one queue item to X and return its receipt.

    `item` is a `social_queue` row (`mc.blueprints.project_routes`): only
    `id`, `body` and `platform` are read. Returns
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
    if platform != 'x':
        raise PublishError(
            f"desk_publish only posts to X ('x'), got platform={platform!r} "
            f"-- LinkedIn is step 8, not built")

    body = (item.get('body') or '').strip()
    if not body:
        raise PublishError(f"item {item_id} has no body to publish")

    with _lock:
        existing = _read_store()['receipts'].get(item_id)
        if existing is not None:
            return existing

        if RECEIPTS_PATH is None:
            # Fail BEFORE any network call -- see module docstring on why this
            # is the one thing that must not be best-effort.
            raise PublishError(
                'desk_publish.RECEIPTS_PATH is not wired -- refusing to post '
                'without durable idempotency in place')

        try:
            token = secrets_store.get_secret_value(
                X_OAUTH_TOKEN_SECRET, consumer=consumer, project_id=project_id,
                unattended=unattended)
        except secrets_store.SecretsError as e:
            raise PublishError(f'credential unavailable: {e}') from e

        try:
            payload = _post_tweet(token, body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', errors='replace')[:500]
            raise PublishError(f'X API HTTP {e.code}: {detail}') from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise PublishError(f'X API request failed: {e}') from e
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

        receipt = {
            'item_id': item_id,
            'post_id': post_id,
            'permalink': permalink,
            'posted_at': now_iso(),
            'body': body,
        }

        store = _read_store()
        store['receipts'][item_id] = receipt
        try:
            _write_store(store)
        except Exception as e:
            # See module docstring: the post already happened, so this is
            # reported loudly, never as a PublishError -- doing so would tell
            # the caller a real post failed.
            _log(f'[desk_publish] CRITICAL: post {post_id} for item {item_id} '
                 f'succeeded but the receipt failed to persist -- a retry of '
                 f'this item risks a duplicate post. Receipt: '
                 f'{json.dumps(receipt)}. Write error: {e}', level='error')
        return receipt
