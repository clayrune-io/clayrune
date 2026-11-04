"""Plan detection for the API-credits offload (docs/API_CREDITS_BUILD_SPEC.md).

Reads ONLY `subscriptionType` and `rateLimitTier` from
`~/.claude/.credentials.json` > `claudeAiOauth`. The same object holds the
OAuth access and refresh tokens; this module never copies, returns or logs any
other key of it.

Anthropic renamed the plans on 2026-10-30 (Max 5x -> Max $100, Max 20x ->
Ultra $200, prices unchanged) while the CLI still writes the old labels, so
both vocabularies map to the same plan. Detection only PREFILLS the credit the
user then confirms: the label can lag a rename and Clayrune cannot see whether
a Console org is linked.

Never raises: a missing, unreadable or odd file is `unknown`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

# (credit USD per month, display label). Team is pooled and the amount is
# user-entered (up to $500), so it carries no default.
_PLANS: Dict[str, Dict[str, Any]] = {
    'ultra': {'label': 'Ultra', 'credit_usd': 200.0},
    'max': {'label': 'Max', 'credit_usd': 100.0},
    'team': {'label': 'Team (pooled, up to $500)', 'credit_usd': None},
}


def credentials_path() -> Path:
    return Path.home() / '.claude' / '.credentials.json'


def classify(subscription_type: str, rate_limit_tier: str) -> Optional[str]:
    """Map the two raw labels to 'ultra' | 'max' | 'team', else None.

    Team is decided from `subscriptionType` first: a Team Premium seat can carry
    a max-5x style tier, and must not be read as a personal Max plan.
    """
    sub = (subscription_type or '').strip().lower()
    tier = (rate_limit_tier or '').strip().lower()
    if 'team' in sub:
        return 'team'
    both = f'{sub} {tier}'
    if 'ultra' in both or 'max_20x' in both:
        return 'ultra'
    if 'max_5x' in both or sub == 'max' or 'max' in tier:
        return 'max'
    if 'team' in tier:
        return 'team'
    return None


def detect(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return {status, plan, label, credit_usd, subscription_type, rate_limit_tier}.

    status: 'detected' (eligible plan), 'not_eligible' (a plan that has no API
    credits, e.g. Pro/Free), or 'unknown' (no readable credentials). The two raw
    labels are returned so the UI can show what the CLI reported; they are not
    secrets.
    """
    out: Dict[str, Any] = {'status': 'unknown', 'plan': None, 'label': '',
                           'credit_usd': None, 'subscription_type': '',
                           'rate_limit_tier': ''}
    try:
        data = json.loads((path or credentials_path()).read_text(encoding='utf-8'))
        oauth = data.get('claudeAiOauth') if isinstance(data, dict) else None
        if not isinstance(oauth, dict):
            return out
        sub = oauth.get('subscriptionType')
        tier = oauth.get('rateLimitTier')
    except Exception:
        return out
    sub = sub if isinstance(sub, str) else ''
    tier = tier if isinstance(tier, str) else ''
    out['subscription_type'] = sub
    out['rate_limit_tier'] = tier
    if not sub and not tier:
        return out
    plan = classify(sub, tier)
    if plan is None:
        out['status'] = 'not_eligible'
        return out
    out.update(status='detected', plan=plan, label=_PLANS[plan]['label'],
               credit_usd=_PLANS[plan]['credit_usd'])
    return out
