"""One free literal quote retry; vendor suggestions never supply request arguments."""
from __future__ import annotations

import re
from copy import deepcopy

from mc.core import _log
from mc.desk_mcp_schema_validation import finite_number, tool, validate

NOTE = 'Higgsfield suggested a preset; your text is used as written'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}')


def preset_notice(out: dict) -> bool:
    notice = out.get('notice')
    return isinstance(notice, dict) and notice.get('type') == 'preset_recommendation'


def credits(out: dict) -> float | None:
    cost = out.get('cost')
    value = cost.get('credits_exact', cost.get('credits')) if isinstance(cost, dict) else None
    return float(value) if isinstance(value, (int, float)) and finite_number(value) and value >= 0 else None


def _declined_id(out: dict) -> str | None:
    notice = out.get('notice')
    data = notice.get('data') if isinstance(notice, dict) else None
    if not isinstance(data, dict):
        return None
    ids = []
    if 'preset' in data:
        preset = data['preset']
        if not isinstance(preset, dict):
            return None
        ids.extend(preset[k] for k in ('id', 'preset_id') if k in preset)
        if not any(k in preset for k in ('id', 'preset_id')):
            return None
    if 'retry_literal_with' in data:
        retry = data['retry_literal_with']
        if not isinstance(retry, dict) or 'declined_preset_id' not in retry:
            return None
        ids.append(retry['declined_preset_id'])
    if not ids or any(not isinstance(v, str) or not _ID.fullmatch(v) for v in ids):
        return None
    return ids[0] if all(v == ids[0] for v in ids) else None


def _declared(doc: dict, name: str) -> bool:
    try:
        params = tool(doc, name)['inputSchema']['properties']['params']
        branches = params.get('anyOf', [params])
        return any(isinstance(b, dict) and b.get('type') == 'object'
                   and b.get('properties', {}).get('declined_preset_id', {}).get('type') == 'string'
                   for b in branches)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def quote(doc: dict, call, name: str, args: dict) -> tuple[dict, dict, str | None]:
    """Return the exact priced arguments, for reuse by the human-approved submit."""
    from mc import desk_engines as eng
    from mc.desk_higgsfield_quote_response import report
    if args.get('params', {}).get('get_cost') is not True:
        raise ValueError('preset decline is only allowed during a free quote')
    out = call(name, args)
    if not preset_notice(out):
        return out, args, None
    ident = _declined_id(out)
    if out.get('error') or ident is None:
        raise eng.EngineError('engine', report(out, reason=
                              'Higgsfield suggested a preset without one valid, agreeing decline identifier'))
    if not _declared(doc, name):
        raise eng.EngineError('engine', report(out, reason=
                              'Higgsfield has not declared literal preset decline for this tool'))
    retry = deepcopy(args)
    retry['params']['declined_preset_id'] = ident
    try:
        validate(doc, name, 'inputSchema', retry)
    except (KeyError, TypeError, ValueError):
        raise eng.EngineError('engine', report(out, reason=
                              'Higgsfield has not supplied a usable literal retry contract')) from None
    _log(f'[higgsfield_preset_decline] tool={name} declined_preset_id={ident}', flush=True)
    out = call(name, retry)
    if 'notice' in out or credits(out) is None or out.get('error'):
        raise eng.EngineError('engine', report(out, reason=
                              'Higgsfield did not return a usable literal price after the preset was declined'))
    return out, retry, NOTE


def check_submit(out: dict) -> None:
    """The direct-tool notice contract does not establish no-submit/no-charge semantics."""
    from mc import desk_engines as eng
    if 'notice' in out:
        _log('[higgsfield_preset_decline] submit_notice outcome=unknown retry=false', flush=True)
        raise eng.EngineError('engine', 'Higgsfield returned a notice during Render; submission and charging '
                              'could not be confirmed. Check its dashboard before retrying', definitive=False)
