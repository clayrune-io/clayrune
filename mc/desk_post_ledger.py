"""Durable accounting for campaign API posts that already went out."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mc import desk
from mc.core import _log

if TYPE_CHECKING:
    from mc.desk_spend_guard import Reservation


def fields(piece: dict, version: dict, campaign: dict, account: dict) -> dict:
    term = campaign.get('term') or {}
    return {
        'voice': account.get('voice') or '',
        'campaign_id': campaign['id'],
        'project_id': campaign.get('project_id'),
        'piece_id': piece['id'],
        'format': version.get('format') or piece.get('kind'),
        'account': account['id'],
        'term': str(term.get('index') or 1) if isinstance(term, dict) else '1',
    }


def _entry(receipt: dict, context: dict) -> dict:
    from mc.desk_spend_guard import post_cost

    return {**context, 'publish_id': receipt['item_id'], 'platform': receipt['platform'],
            'body': receipt['body'], 'url': receipt['permalink'],
            'published_at': receipt['posted_at'],
            'cost': post_cost(receipt['platform'], receipt['body'])}


# A failed receipt-file write keeps the successful send accounted for in this
# process. The next repair also retries persisting the publisher receipt.
_unwritten: dict[str, dict] = {}


def persist_receipt(receipt: dict, context: dict | None, held: Reservation | None) -> None:
    """Called with the publisher lock held, after the platform confirms a send."""
    from mc import desk_publish, desk_spend_guard

    if context is not None:
        receipt['ledger_pending'] = _entry(receipt, context)
    with desk_spend_guard._lock:
        try:
            store = desk_publish._read_store(strict=True)
            store['receipts'][receipt['item_id']] = receipt
            desk_publish._write_store(store)
        except Exception as e:
            if context is not None:
                _unwritten[receipt['item_id']] = receipt
            _log(f"[desk_publish] CRITICAL: post {receipt['post_id']} for item {receipt['item_id']} "
                 f'succeeded but the receipt failed to persist. A retry after restart risks a duplicate '
                 f'post. Receipt: {json.dumps(receipt)}. Write error: {e}', level='error')
        # Transfer the reservation to the pending receipt. Both the check and
        # this transfer take the spend lock, so it is counted once throughout.
        desk_spend_guard.release(held)


def _pending() -> list[dict]:
    from mc import desk_publish

    # Read the fallback first so a concurrent repair moving it to disk cannot
    # make the post disappear between reads. The receipt file is atomic.
    receipts = _unwritten.copy()
    receipts.update(desk_publish._read_store()['receipts'])
    return [r for r in receipts.values() if r.get('ledger_pending')]


def accounted_posts(ledger: list[dict] | None = None) -> list[dict]:
    """The story ledger plus pending receipts, counted once during handoff.

    A caller supplying a ledger snapshot (the cadence check) holds the send
    lock. Budget callers read pending receipts first because repair writes
    the ledger before it clears the pending marker.
    """
    pending = _pending()
    rows = desk.list_ledger(limit=10 ** 9) if ledger is None else ledger
    if not pending:
        return rows
    ids = {r.get('publish_id') for r in rows}
    urls = {r['url'] for r in rows if r.get('url')}
    entries = [r['ledger_pending'] for r in pending]
    return rows + [r for r in entries if r['publish_id'] not in ids and r['url'] not in urls]


def matching_post(ledger: list[dict], publish_id: str, url: str | None) -> dict | None:
    return next((row for row in ledger if row.get('publish_id') == publish_id
                 or (url and row.get('url') == url)), None)


def stage_legacy(receipt: dict, context: dict) -> None:
    """An interrupted send created before receipts carried the pending row."""
    from mc import desk_publish

    with desk_publish._lock:
        persist_receipt(receipt, context, None)


def repair(receipt: dict) -> None:
    """Settle one receipt. The ledger and marker may be on separate writes."""
    from mc import desk_publish

    try:
        desk.record_published(**receipt['ledger_pending'])
        item_id = receipt['item_id']
        with desk_publish._lock:
            store = desk_publish._read_store(strict=True)
            store['receipts'][item_id] = {**receipt, 'ledger_pending': None}
            desk_publish._write_store(store)
            _unwritten.pop(item_id, None)
    except Exception as e:
        _log(f"[desk_tick] post {receipt['post_id']} is live and its ledger repair is pending: {e}",
             level='error')


def repair_pending() -> None:
    for receipt in _pending():
        repair(receipt)
