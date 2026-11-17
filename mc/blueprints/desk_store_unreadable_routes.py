"""One answer for an unreadable Desk store (MC-1187 follow-up F2).

`mc/desk_store_guard.StoreUnreadable` is raised from `desk._write_store` and so can
come out of any Desk route in any blueprint. `app_errorhandler` makes this one
handler cover them all; it is the single chokepoint, so a route added later is
covered without being touched. The body is closed: three keys, and `path` is the file
name only (never a directory). There is no recovery here and no route to start empty:
that stays a person's action outside the product.

The Desk connect package has its own `StoreUnreadable` (`custom_connection_store`),
a different class; this handler does not match it.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from mc.desk_store_guard import StoreUnreadable

bp = Blueprint('desk_store_unreadable', __name__)

ERROR = 'desk_store_unreadable'
MESSAGE = ("The Desk's saved data could not be read, so nothing can be saved right now. "
           'Nothing was deleted: the unreadable file has been kept. '
           'Ask whoever manages this install to restore it.')


@bp.app_errorhandler(StoreUnreadable)
def _store_unreadable(e: StoreUnreadable):
    return jsonify({'error': ERROR, 'message': MESSAGE, 'path': e.file_name}), 503
