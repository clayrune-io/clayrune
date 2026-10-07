"""Refuse a storyboard's prepared price above the total shown to the human."""
from __future__ import annotations

import math


def shown_total(data: dict) -> float:
    from mc.desk_engines import Refused
    value = data.get('shown_total')
    message = 'shown_total must be the finite, non-negative price shown before Render'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Refused('invalid_input', message, 400)
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise Refused('invalid_input', message, 400)
    return float(value)


def check(total: float, plan: dict, *, estimate: dict, public_plan: dict) -> None:
    from mc.desk_engines import Refused
    # Plans sum and round amounts to six decimals. Compare at that same precision.
    if plan['total_amount'] <= round(total, 6):
        return
    amount = plan['total_amount']
    credits = plan['currency'] == 'credits'
    new = f'{amount:g} credits' if credits else f'${amount:g}'
    old = f'{total:g}' if credits else f'${total:g}'
    picture = any(row['scene'].get('picture') for row in plan['rows'])
    message = (f'Price with your pictures is {new} (was {old}).' if picture else
               f'The price is now {new} (was {old}).')
    raise Refused('render_price_changed', message + ' Press Render again to accept.', 409,
                  shown_total=total, prepared_total=amount, currency=plan['currency'],
                  estimate=estimate, plan=public_plan)
