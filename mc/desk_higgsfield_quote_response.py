"""Translate quote refusals without forwarding vendor sales copy or instructions.

Diagnostics contain bounded shapes, known field names and enumerated routing
values only. Neither a recovery tool nor a purchase link is ever followed.
"""
from __future__ import annotations

import json
import math
import re

from mc.core import _log

_FIELDS = frozenset('''input_check prepared_params results error request_id upgrade_url
monetization_intent recovery_tool recovery_tool_args recovery_reason media_recovery_context
billing_recovery_context assistant_response promotional_text sales_headline recommendation_reason
primary_cta_text primary_purchase_link checkout_url checkout_label plan_purchase_links
credit_purchase_links auto_refill_purchase_link purchase_links warning notice adjustments
avatar_auto_picked next_step unlim_choice brand_kit_status cost required_media_present
image_count video_count missing_roles generation_validated submitted requested missing media_type
tool params message model remaining expires_at type data credits credits_exact intent
media_index role original_url url value medias duration prompt count aspect_ratio use_unlim
get_cost mode'''.split())
_ENUMS = {
    'recovery_tool': {'show_plans_and_credits', 'media_import_url'},
    'monetization_intent': {'upgrade', 'topup', 'auto_refill', 'trial', 'general'},
    'intent': {'upgrade', 'topup', 'auto_refill', 'trial', 'general'},
    'missing_roles': {'image', 'video'}, 'media_type': {'image', 'video', 'audio'},
    'tool': {'show_marketing_studio', 'show_plans_and_credits', 'media_import_url', 'models_explore'},
}
_STRUCTURES = ('input_check', 'prepared_params', 'next_step', 'unlim_choice', 'error',
               'recovery_tool_args', 'billing_recovery_context', 'media_recovery_context',
               'notice', 'cost')


def _shape(value: object, field: str = '', depth: int = 0, *, budget: list[int]) -> object:
    budget[0] -= 1
    if depth > 4 or budget[0] < 0:
        return 'structure_limit'
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if abs(value) <= 1e9 and math.isfinite(value) else 'number'
    if isinstance(value, str):
        return value if value in _ENUMS.get(field, set()) else {'text_length': len(value)}
    if isinstance(value, dict):
        known = sorted(k for k in value if k in _FIELDS)
        return {**{k: _shape(value[k], k, depth + 1, budget=budget) for k in known[:40]},
                'other_fields': len(value) - min(len(known), 40)}
    if isinstance(value, list):
        return {'length': len(value), 'items': [_shape(v, field, depth + 1, budget=budget) for v in value[:8]]}
    return 'unexpected_type'


def refusal(out: dict) -> str | None:
    """Use structured evidence, never interpret a suggested call as permission."""
    check = out.get('input_check')
    if isinstance(check, dict) and check.get('required_media_present') is False:
        roles = check.get('missing_roles')
        roles = roles if isinstance(roles, list) else []
        needs = [label for role, label in (('image', 'a picture'), ('video', 'a video')) if role in roles]
        return 'Higgsfield needs ' + (' and '.join(needs) or 'reference media') + ' before it can price this request'
    if isinstance(out.get('unlim_choice'), dict):
        return 'Higgsfield needs you to choose between your free generations and credits before it can price this request'
    intent = out.get('monetization_intent')
    args = out.get('recovery_tool_args')
    if intent is None and isinstance(args, dict):
        intent = args.get('intent')
    if out.get('recovery_tool') == 'show_plans_and_credits' or intent in ('upgrade', 'topup', 'auto_refill', 'trial'):
        if intent in ('topup', 'auto_refill'):
            return 'Higgsfield says this request needs more credits in your account'
        if intent == 'upgrade':
            return 'Higgsfield says this request needs a different account plan'
        if intent == 'trial':
            return 'Higgsfield says this request needs an account plan or trial'
        return 'Higgsfield needs you to check your account plan and credits before it can price this request'
    if out.get('recovery_tool') == 'media_import_url':
        return 'Higgsfield needs the reference media added to its library before it can price this request'
    # Only recognize specific error facts; arbitrary vendor sentences never reach the UI.
    error = out.get('error') or out.get('recovery_reason')
    if isinstance(error, str):
        error = error[:2000].casefold()
        if re.search(r'(prompt.{0,40}(too long|maximum length)|prompt_too_long)', error):
            return 'Higgsfield needs a shorter scene description before it can price this request'
        if re.search(r'(insufficient|not enough|out of).{0,20}credits', error):
            return 'Higgsfield says this request needs more credits in your account'
        if out.get('error'):
            return 'Higgsfield refused this price request without a recognized explanation'
    if out.get('brand_kit_status') in ('pending', 'not_ready', 'incomplete', 'failed'):
        return 'Higgsfield needs your brand kit ready before it can price this request'
    if isinstance(out.get('next_step'), dict):
        return 'Higgsfield needs another setup step before it can price this request; check its dashboard'
    return None


def report(out: dict, *, reason: str | None = None) -> str:
    """Log no prompts, credentials, URLs, arbitrary keys or vendor prose."""
    reason = reason or refusal(out) or 'Higgsfield did not explain why it could not price this request'
    budget = [200]
    diagnostic = {'keys': sorted(k for k in out if k in _FIELDS),
                  'other_fields': sum(k not in _FIELDS for k in out),
                  'reason': reason,
                  'structure': {k: _shape(out[k], k, budget=budget) for k in _STRUCTURES if k in out}}
    for field in ('recovery_tool', 'monetization_intent'):
        if field in out:
            diagnostic[field] = _shape(out[field], field, budget=budget)
    _log('[higgsfield_quote] ' + json.dumps(diagnostic, sort_keys=True), flush=True)
    return reason + '; nothing was sent'
