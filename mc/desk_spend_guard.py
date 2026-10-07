"""The Desk spend guard (backlog bb65f3bb).

A campaign's `how.budget` is one number for the whole campaign. The approval
bounds check whether that number was RAISED behind the human's back; nothing
checked whether a paid platform call would take the campaign past what is LEFT.
This module is that check, and it is the only one: three boundaries call it.

  publish        `desk_publish.publish`      an X post ($0.015, $0.20 with a link)
  verify_post    `desk_publish.verify_post`  one paid X read ($0.005)
  engagement     `desk_engagement_poll`      a paid API metrics batch ($0.005 a post)

WHAT IT CHECKS. `remaining = budget - spent - reads - reserved`:
  budget    `desk_engines.budget_state` (reused as is, never edited): the
            campaign's `how.budget` amount, and what the engine jobs plus the
            story ledger's post `cost` rows have already spent.
  reads     `engagement.reads[].cost` rows tagged with this campaign's id. The
            read ledger is the existing one; rows gained an optional
            `campaign_id` (`desk.record_read`). There is no second ledger.
  reserved  calls that passed the check and have not finished (below).
A call whose cost is over `remaining` is REFUSED with a plain reason naming the
campaign, never skipped silently. A campaign with `source: 'none'` has a $0
budget: every PAID call is refused and the reason says to set a budget in the
campaign's brief (Dave's call, 2026-10-06, matching `budget_state`).

FREE CALLS ARE NEVER BLOCKED. A cost of 0 (a LinkedIn post, a browser-pane read,
a call with no campaign to charge) returns before the campaign is even looked at.

CONCURRENCY. `reserve()` checks and adds its hold under `_lock`, one lock for
every caller in the process, so two calls racing for the last of a budget cannot
both pass. A reservation is released when the call fails (`release`) and is
swapped for the real ledger row when it succeeds: a read writes its row and
drops the hold under the same lock (`settle_read`), so no other caller can see
neither or both. A post's ledger row is written by `desk_tick._record_ledger`
right after `publish` returns, inside the tick's `_send_lock`, which is the same
lock every campaign publish already takes; a second send cannot start between
the two. Holds live in memory: a restart drops them, and the ledger rows are
what survive. If a settled read's ledger write fails the hold is KEPT (and
logged CRITICAL) so the spend is never undercounted.

ONE CALL IS NOT COUNTED. After an X post `desk_publish` asks `GET /2/users/me`
for the handle so the permalink is readable. X does not price that call on the
page this guard's rates come from, and it fails soft (a generic permalink), so
it is neither reserved nor recorded. It is now made once per account per
process, not after every post (`desk_publish._cached_username`).

NOT COVERED: mentions/replies reads have no campaign, so they stay on the
project cap in `desk_engagement._afford` (known leftover against the standing
"no limit outside the campaign" position); a reply sent from the Engagement
screen carries no campaign id and is a human click.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass

from mc import desk as _desk
from mc.core import _log

# What a post costs to send (docs/SOCIAL_WORKSPACE_FIELD_SCAN.md, Feb 2026
# pricing): X $0.015, $0.20 when the post carries a link. LinkedIn's API is free.
# `desk_tick` re-exports these under its old names; the ledger row's `cost` and
# this guard's price must be the same number.
X_POST_COST = 0.015
X_LINK_POST_COST = 0.20


class SpendRefused(Exception):
    """A paid call was refused before it was made. `str(e)` is the sentence the
    user reads: it names the campaign and says what to change."""


@dataclass
class Reservation:
    id: str
    campaign_id: str
    amount: float
    what: str


_lock = threading.RLock()
_reservations: dict[str, Reservation] = {}


# -- prices ---------------------------------------------------------------------

def post_cost(platform: str, body: str) -> float:
    if platform != 'x':
        return 0.0
    return X_LINK_POST_COST if re.search(r'https?://', body or '', re.I) else X_POST_COST


def read_cost(platform: str, resources: int = 1) -> float:
    if platform != 'x':
        return 0.0
    from mc.desk_engagement import X_READ_UNIT_COST
    return resources * X_READ_UNIT_COST


# -- the budget -----------------------------------------------------------------

def _campaign(campaign_id: str) -> dict | None:
    return next((c for c in _desk.list_campaigns() if c.get('id') == campaign_id), None)


def owning_campaign(campaign_id: str | None) -> str | None:
    """The id if that campaign still exists, else None (a post of a deleted
    campaign has no budget to charge, so its reads stay on the project cap)."""
    return campaign_id if campaign_id and _campaign(campaign_id) is not None else None


def _name(camp: dict | None, campaign_id: str) -> str:
    return f"\"{(camp or {}).get('title') or campaign_id}\""


def campaign_budget(campaign_id: str) -> dict:
    """`{amount, spent, reads, reserved, remaining, source}`. Raises
    `SpendRefused` when the campaign is gone or the spend accounting cannot be
    read: an unreadable ledger must not read as "nothing spent"."""
    from mc import desk_engines
    try:
        b = desk_engines.budget_state(campaign_id)
    except desk_engines.Refused as e:
        raise SpendRefused(f'campaign {campaign_id} no longer exists, so its budget cannot be checked') from e
    except RuntimeError as e:
        raise SpendRefused(f'the spend records could not be read ({e}): nothing paid was sent') from e
    reads = sum(float(r.get('cost') or 0) for r in _desk.list_reads()
                if r.get('campaign_id') == campaign_id)
    with _lock:
        reserved = sum(r.amount for r in _reservations.values() if r.campaign_id == campaign_id)
    remaining = max(b['amount'] - b['spent'] - reads - reserved, 0.0)
    return {'amount': b['amount'], 'spent': b['spent'], 'reads': round(reads, 6),
            'reserved': round(reserved, 6), 'remaining': round(remaining, 6),
            'source': b.get('source', 'none')}


def reserve(campaign_id: str | None, amount: float, *, what: str) -> Reservation | None:
    """Hold `amount` of the campaign's remaining budget for one paid call.
    Returns None for a free call or one with no campaign to charge. Raises
    `SpendRefused` (and holds nothing) when it does not fit. `what` finishes the
    sentence "<what> costs $x": e.g. 'posting to X'."""
    if not campaign_id or amount <= 0:
        return None
    with _lock:
        b = campaign_budget(campaign_id)
        camp = _campaign(campaign_id)
        if b['source'] == 'none':
            raise SpendRefused(
                f"the campaign {_name(camp, campaign_id)} has no budget, so it cannot make paid calls "
                f"({what} costs ${amount:.3f}): set a budget in its brief")
        if amount > b['remaining'] + 1e-9:
            raise SpendRefused(
                f"{what} costs ${amount:.3f} but the campaign {_name(camp, campaign_id)} has "
                f"${b['remaining']:.3f} left of its ${b['amount']:.2f} budget: raise it in the campaign's brief")
        res = Reservation(uuid.uuid4().hex, campaign_id, float(amount), what)
        _reservations[res.id] = res
        return res


def release(res: Reservation | None) -> None:
    """Drop a hold (the call failed or cost nothing). Safe to call twice."""
    if res is not None:
        with _lock:
            _reservations.pop(res.id, None)


def settle_read(res: Reservation | None, *, platform: str, project_id: str | None, kind: str,
                resources: int, cost: float) -> None:
    """The paid read happened: write its row to the read ledger, tagged with the
    campaign, and drop the hold, as one step under `_lock`."""
    if res is None:
        _desk.record_read(platform=platform, project_id=project_id, kind=kind,
                          resources=resources, cost=cost, ok=True)
        return
    with _lock:
        try:
            _desk.record_read(platform=platform, project_id=project_id, kind=kind,
                              resources=resources, cost=cost, ok=True, campaign_id=res.campaign_id)
        except Exception as e:
            _log(f'[desk_spend_guard] CRITICAL: a paid {kind} read for campaign {res.campaign_id} '
                 f'(${cost:.3f}) happened but its ledger row failed; the hold stays so the spend is '
                 f'not undercounted: {e}', level='error')
            return
        _reservations.pop(res.id, None)
