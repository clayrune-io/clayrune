"""API-credits offload routes (docs/API_CREDITS_BUILD_SPEC.md).

    GET  /api/api-credits/status     plan detection, settings, cycle spend, state
    PUT  /api/api-credits/settings   mode / key name / budget / stop point / reset day
    POST /api/api-credits/test       one human-clicked haiku call, max_tokens 1

No route returns or accepts a key. The settings hold the vault secret's NAME;
the key itself is created through the existing Settings > Secrets save path
(`POST /api/secrets`, human + passcode) and read server-side at call time.

Human-only: an unattended agent session may read status but may not change
settings or spend through the test route. Turning offload on (or widening what it
may spend) additionally needs the re-typed dashboard passcode, the same gate the
vault uses, because it lets background work spend the user's credits.
"""
from flask import Blueprint, jsonify, request

from mc import api_credits_budget as budget
from mc import api_credits_offload as offload
from mc import api_credits_plan as plan
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.unattended import is_unattended_caller

bp = Blueprint('api_credits_routes', __name__)

# Fields whose change can increase what background work spends.
_SPEND_FIELDS = ('mode', 'secret_name', 'budget_usd', 'stop_pct')


def wire(data_dir) -> None:
    budget.wire(data_dir)


def _unattended_refusal():
    return jsonify({'error': 'this action needs a human; an unattended agent session '
                             'cannot change API-credit settings or run the key test'}), 403


def _widens_spend(old: dict, new: dict) -> bool:
    if new['mode'] != 'on':
        return False
    return any(old[f] != new[f] for f in _SPEND_FIELDS)


@bp.get('/api/api-credits/status')
def api_credits_status():
    out = budget.status()
    out['plan'] = plan.detect()
    return jsonify(out)


@bp.put('/api/api-credits/settings')
def api_credits_put_settings():
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    old = budget.load_settings()
    merged = {**old, **{k: v for k, v in data.items() if k != 'passcode'}}
    new, problem = budget.validate_settings(merged)
    if new is None:
        return jsonify({'error': problem}), 400
    if _widens_spend(old, new):
        refusal = _require_human_passcode(data)
        if refusal is not None:
            return refusal
    saved = budget.save_settings(new)
    # A saved change is the user's answer to a sticky stop; the next call tries again.
    budget.clear_halt()
    offload.forget_key()
    out = budget.status()
    out['plan'] = plan.detect()
    out['settings'] = saved
    return jsonify(out)


@bp.post('/api/api-credits/test')
def api_credits_test():
    if is_unattended_caller():
        return _unattended_refusal()
    return jsonify(offload.run_key_test())
