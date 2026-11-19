"""A campaign's own engine pick: the provider, model and effort its agent runs on.

Stored beside the campaign's agent as `how.engine = {provider?, model?, effort?}`.
Every field is optional and an absent one means "the agent's default", so a
campaign with no `how.engine` resolves exactly as it did before this module
existed. This file only decides what a pick means; the surfaces that use it
(campaign chat, story chat, the Desk dispatches) call `apply` / `dispatch_overrides`.

No substitution. A pick is refused on save when it names a provider, model or
effort the live catalog does not offer, and at use time a pick that no longer
holds (catalog moved on, provider signed out, allowance exhausted) fails the call
with that reason. It never falls back to the agent's default, and an explicit
provider/model/effort also stops the pace and auto-model routers (both already
step aside for an explicit override at `/agent/dispatch`).
"""
from __future__ import annotations

import sys
from typing import Callable, NamedTuple, Optional

from mc.core import _log
from mc.desk_pieces import PieceError

KEYS = ('provider', 'model', 'effort')
# Auth states that mean the provider cannot run anything right now. 'unknown' and
# 'unverified' are not here: a probe that could not tell is not a refusal.
_BLOCKING_AUTH = ('not_logged_in', 'invalid_api_key', 'not_installed', 'oauth_rejected', 'quota_exceeded')


class EngineError(PieceError):
    """A refusal with the HTTP status the route should answer (400 a bad pick on
    save, 409 a pick that cannot run now)."""


class Whose(NamedTuple):
    """What owns a pick, for the words of a refusal. The validate / apply logic is the
    same for a campaign and for a standalone Studio item (`mc/desk_studio_engine.py`);
    only who is named, and where the user goes to change it, differ."""
    noun: str
    where: str


CAMPAIGN = Whose('campaign', "on the campaign's Brief")
ITEM = Whose('item', 'in the agent box beside it')


# -- the live catalog ------------------------------------------------------------------

def catalog() -> dict[str, dict]:
    """provider -> {display_name, model_selection, models: [id], efforts: [level]}.

    The same source `GET /api/engine/preferences` serves the picker from, so what
    the UI offers and what a save accepts cannot drift apart."""
    from mc import agent_runtime, provider_preferences
    out: dict[str, dict] = {}
    for rt in agent_runtime.available_runtimes():
        meta = provider_preferences.runtime_metadata(rt)
        out[rt.name] = {'display_name': getattr(rt, 'display_name', rt.name),
                        'model_selection': bool(meta['model_selection']),
                        'models': [str(v) for v, _ in rt.model_choices()],
                        'efforts': list(meta['efforts'])}
    return out


# -- reading a stored pick -------------------------------------------------------------

def _clean(raw) -> Optional[dict]:
    """The non-empty text fields of `raw`, or None when it is not an object."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip().lower() if k == 'provider' else v.strip()
    return out


def stored_pick(raw) -> Optional[dict]:
    """The pick held in `raw` (`{provider?, model?, effort?}`), or None for "agent
    default". Reads only; whether the pick still holds is `apply`'s check."""
    return _clean(raw) or None


def pick_of(camp: Optional[dict]) -> Optional[dict]:
    """The campaign's stored pick, or None for "agent default"."""
    how = (camp or {}).get('how')
    return stored_pick(how.get('engine')) if isinstance(how, dict) else None


# -- save-time validation --------------------------------------------------------------

def validate(raw, *, agent_provider: str = '', cat: Optional[dict] = None) -> Optional[dict]:
    """A pick the catalog offers, normalised; None for "agent default" (None, {}, or
    every field empty). `agent_provider` is the provider an effort-only pick is
    checked against (the agent's own). Raises EngineError(400) naming what is wrong."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise EngineError('engine must be an object like {"provider": ..., "model": ..., "effort": ...}, or null', 400)
    extra = sorted(set(raw) - set(KEYS))
    if extra:
        raise EngineError(f'engine has no field {extra[0]!r}; it holds provider, model and effort', 400)
    for k in KEYS:
        if raw.get(k) is not None and not isinstance(raw.get(k), str):
            raise EngineError(f'engine {k} must be text', 400)
    pick = _clean(raw) or {}
    if not pick:
        return None
    cat = catalog() if cat is None else cat
    provider = pick.get('provider', '')
    if provider and provider not in cat:
        raise EngineError(f'unknown provider {provider!r}; choose one of: {", ".join(sorted(cat)) or "none installed"}', 400)
    if provider and not pick.get('model'):
        raise EngineError(f'a provider pick needs its model: choose which {provider} model to run', 400)
    if pick.get('model'):
        if not provider:
            raise EngineError('a model needs its provider: say which provider it belongs to', 400)
        entry = cat[provider]
        if not entry['model_selection']:
            raise EngineError(f'{entry["display_name"]} chooses its own model, so none can be picked for it', 400)
        if pick['model'] not in entry['models']:
            raise EngineError(f'{entry["display_name"]} does not offer a model called {pick["model"]!r}', 400)
    if pick.get('effort'):
        owner = provider or agent_provider
        if not owner:
            raise EngineError('pick an agent first: an effort level is checked against the agent\'s provider', 400)
        if owner not in cat:
            raise EngineError(f'unknown provider {owner!r}', 400)
        efforts = cat[owner]['efforts']
        if not efforts:
            raise EngineError(f'{cat[owner]["display_name"]} has no effort setting', 400)
        if pick['effort'] not in efforts:
            raise EngineError(f'{cat[owner]["display_name"]} does not offer effort {pick["effort"]!r}; '
                              f'it offers: {", ".join(efforts)}', 400)
    return pick


def _agent_provider(project: Optional[dict], character_engine: Optional[dict]) -> str:
    from mc import engine_selection, state
    return engine_selection.resolve_provider(
        state.CONFIG, project, character=character_engine or None, legacy_default='claude')[0]


def check_save(body, campaign_id: Optional[str], load_project: Optional[Callable]) -> None:
    """Validate (and normalise in place) the `how.engine` of a campaign create or
    PATCH body; call before the store write. A body without `how.engine` is left
    alone. `None` / an all-empty pick removes the key (clears the pick).

    A pick equal to the one already stored is not re-checked: the Brief re-sends the
    whole `how` on every save, and a catalog that moved on since must not make an
    unrelated edit (the Strategy box) unsaveable. It is then refused when used."""
    how = body.get('how') if isinstance(body, dict) else None
    if not isinstance(how, dict) or 'engine' not in how:
        return
    from mc import desk as _desk
    stored = next((c for c in _desk.list_campaigns() if c.get('id') == campaign_id), None) if campaign_id else None
    raw = how['engine']
    if raw is not None and _clean(raw) is not None and _clean(raw) == pick_of(stored):
        how['engine'] = pick_of(stored)
        return
    agent_provider = ''
    if isinstance(raw, dict) and raw.get('effort') and not raw.get('provider'):
        agent_provider = _agent_provider_for_save(body, how, stored, load_project)
    pick = validate(raw, agent_provider=agent_provider)
    if pick:
        how['engine'] = pick
    else:
        how.pop('engine', None)


def _agent_provider_for_save(body: dict, how: dict, stored: Optional[dict], load_project: Optional[Callable]) -> str:
    """The provider the campaign's agent runs on (what an effort-only pick is checked
    against), or '' when no agent or project can be resolved."""
    from mc import desk as _desk, desk_story
    pid = body.get('project_id') or (stored or {}).get('project_id')
    project = load_project(pid) if (load_project and pid) else None
    ref = how.get('agent') or ((stored or {}).get('how') or {}).get('agent') \
        or ((_desk.get_presence(pid or '') or {}).get('desk_agent') if pid else None)
    if not ref or project is None:
        return ''
    try:
        rec = desk_story._agent(ref, project)
        return _agent_provider(project, (rec or {}).get('engine'))
    except Exception as e:
        _log(f'[desk] engine pick: could not read the agent\'s provider for {ref}: {e}', flush=True)
        return ''


# -- can the pick run right now --------------------------------------------------------

def availability_error(provider: str, model: str = '') -> str:
    """Why `provider` (and `model`) cannot run right now, or ''. Read-only: it spends
    no trial run and never picks another provider."""
    from mc import agent_runtime, allowance_state
    entry = allowance_state.get(provider)
    if entry:
        from mc import allowance_unverified
        if not allowance_unverified.is_unverified(entry):   # an unverified record lets one trial run through
            return allowance_state.refusal_message(provider)
    try:
        health = agent_runtime.get_runtime(provider).health_check()
    except Exception as e:   # a probe that crashed is not a signed-out provider; the call itself will say
        _log(f'[desk] engine pick: availability probe for {provider} failed: {e}', flush=True)
        health = None
    if health is not None:
        if not health.installed:
            return f'{provider} is not installed on this machine'
        status = health.auth_state.status if health.auth_state else 'unknown'
        if status in _BLOCKING_AUTH:
            return f'{provider} is not signed in ({status})'
    routes = sys.modules.get('mc.blueprints.agent_routes')   # the dispatch gate's own recorded-quota check
    quota = getattr(routes, '_model_quota_blocked', None)
    if model and callable(quota):
        try:
            return str(quota(provider, model) or '')
        except Exception as e:
            _log(f'[desk] engine pick: quota check for {provider}/{model} failed: {e}', flush=True)
    return ''


def _refuse(pick: dict, provider: str, why: str, whose: Whose = CAMPAIGN) -> EngineError:
    what = ' / '.join(pick[k] for k in KEYS if pick.get(k)) or provider
    return EngineError(f'this {whose.noun} is set to run on {what}, which cannot run now: {why}. '
                       f"Nothing was run on another engine. Change the {whose.noun}'s model, or try again when it is back.", 409)


# -- use-time resolution ---------------------------------------------------------------

def for_piece(store: dict, piece_id: str) -> Optional[dict]:
    """The campaign a piece belongs to (so a piece-level call runs on its campaign's
    pick), or None for a piece with no campaign or one that is gone."""
    from mc import desk_pieces
    try:
        cid = desk_pieces._find(store, piece_id).get('campaign_id')
    except PieceError:
        return None
    camps = store.get('campaigns')
    return camps.get(cid) if isinstance(camps, dict) and cid else None


def apply(camp: Optional[dict], project: Optional[dict], rec: Optional[dict], default_engine, *, available: bool = True):
    """(engine, effort) for one Desk call on a campaign. See `apply_pick`."""
    return apply_pick(pick_of(camp), project, rec, default_engine, available=available)


def apply_pick(pick: Optional[dict], project: Optional[dict], rec: Optional[dict], default_engine, *,
               available: bool = True, whose: Whose = CAMPAIGN):
    """(engine, effort) for one Desk call. With no pick this returns `default_engine`
    and the agent's own effort, i.e. exactly what the caller did before. With a pick
    it resolves through `engine_selection.resolve_engine` with the pick as the
    explicit override, and raises EngineError if the pick no longer holds. `whose`
    names the owner of the pick (a campaign, or a Studio item) in the refusals."""
    own_effort = ((rec or {}).get('engine') or {}).get('effort') or ''
    if not pick:
        return default_engine, own_effort
    from mc import engine_selection, state
    char = dict((rec or {}).get('engine') or {})
    try:
        agent_provider = _agent_provider(project, char)
        provider = pick.get('provider') or agent_provider
        # The agent's own model/effort belong to ITS provider; another vendor's CLI must
        # never be handed them, so a different provider starts from its own defaults.
        if provider != agent_provider:
            char.pop('model', None)
            char.pop('effort', None)
        validate(pick, agent_provider=agent_provider)
        effort = pick.get('effort') or (char.get('effort') or '')
        engine = engine_selection.resolve_engine(
            state.CONFIG, project, provider_override=pick.get('provider', ''),
            model_override=pick.get('model') or None, effort_override=effort,
            character=char, legacy_default='claude')
    except EngineError as e:
        raise EngineError(f"this {whose.noun}'s engine pick no longer holds: {e}. Nothing was run; "
                          f'choose the model again {whose.where}.', 409)
    except (engine_selection.EngineSelectionError, ValueError) as e:
        raise EngineError(f"this {whose.noun}'s engine pick cannot be used: {e}. Nothing was run on another engine.", 409)
    why = availability_error(engine.provider, engine.model) if available else ''
    if why:
        raise _refuse(pick, engine.provider, why, whose)
    return engine, engine.effort


def dispatch_overrides(camp: Optional[dict], project: Optional[dict] = None, agent_ref: Optional[str] = None) -> dict:
    """Keyword arguments for `dispatch_agent(...)` that carry the campaign's pick; {}
    when it has none (so the dispatch is byte-for-byte what it was). An explicit
    provider/model/effort bypasses the pace and auto-model routers at the dispatch
    itself. Raises EngineError(409) when the pick no longer holds or its provider
    cannot run now, so the allowance fallback never swaps it for another vendor
    behind the pick's back."""
    pick = pick_of(camp)
    if not pick:
        return {}
    agent_provider = ''
    if agent_ref and project is not None:
        try:
            from mc import desk_story
            agent_provider = _agent_provider(project, (desk_story._agent(agent_ref, project) or {}).get('engine'))
        except Exception as e:   # the dispatch resolves the agent itself and refuses a bad ref
            _log(f"[desk] engine pick: could not read the agent's provider for {agent_ref}: {e}", flush=True)
    try:
        validate(pick, agent_provider=agent_provider)
    except EngineError as e:
        raise EngineError(f"this campaign's engine pick no longer holds: {e}. Nothing was run; "
                          "choose the model again on the campaign's Brief.", 409)
    provider = pick.get('provider') or agent_provider
    why = availability_error(provider, pick.get('model', '')) if provider else ''
    if why:
        raise _refuse(pick, provider, why)
    return {f'{k}_override': pick[k] for k in KEYS if pick.get(k)}
