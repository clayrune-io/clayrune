"""Optional adapter preparation at human render time, before cost caps or generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mc.desk_engines import Estimate


@dataclass
class Prepared:
    arguments: dict
    estimate: Estimate
    snapshot: dict


def prepare(adapter, model, req, creds) -> Prepared | None:
    method = getattr(adapter, 'prepare', None)
    return method(model, req, creds) if method else None


def prepare_plan(plan: dict, *, unattended: bool) -> None:
    from mc import desk_engines as eng
    model = eng.get_model(plan['engine_id'], plan['model_id'])
    if model is None:
        raise eng.EngineError('engine', 'The selected generation model is no longer available')
    adapter = eng._adapter(model)
    if not getattr(adapter, 'prepare', None):
        return
    creds = eng._creds(plan['engine_id'], plan['project_id'], unattended)
    # Price/prepare ALL exact scene arguments before any paid submission.
    for row in plan['rows']:
        row['prepared'] = prepare(adapter, model, row['req'], creds)
        if row['prepared']:
            row['est'] = row['prepared'].estimate
    plan['total_usd'] = round(sum(r['est'].usd for r in plan['rows']), 6)
    plan['total_amount'] = round(sum(eng._amount(r['est'], plan['engine_id']) for r in plan['rows']), 6)
    plan['approximate'] = any(r['est'].approximate for r in plan['rows'])


def notes(plan: dict) -> str | None:
    return '; '.join(dict.fromkeys(r['est'].note for r in plan['rows'] if r['est'].note)) or None
