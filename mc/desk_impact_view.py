"""Service layer behind the Goal screen's Channel impact module (MC-1152 P4, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` sections 10 and 12 (P4); D1 = A.
The routes in `mc/blueprints/desk_impact_compare_routes.py` call this module;
it composes the P1 store, the P3 cached projection and the P3 collector, and
adds nothing to what they measure.

DARK. `enabled()` reads the config key `desk_impact_compare_enabled`, default
False (the same shape as `desk_impact_link_insert.enabled`). While it is off
the routes answer 404 and the page module draws nothing.

CONNECTIONS. The P3 collector needs a trusted projection of an existing GitHub
read connection (`{id, project_id, target, vault_ref, read_enabled}`). On
2026-11-05 no such connection exists in Clayrune: the GitHub profile's
`github-api` route is `info_only` ("Clayrune has no flow for it yet") and the
Desk has no GitHub account. This module therefore does NOT invent a connection
or guess a vault name; the default resolver is P1's `no_connection_resolver`,
under which no definition can be saved and a refresh reads nothing. A future
connection flow plugs in through `wire(connection_resolver=...)`.

THE READ NEVER COLLECTS. `comparison_view` is local: it reads the Desk store and
the impact store, never a transport, a platform reader or the vault. Only
`refresh` can reach the network, and only through an injected transport.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from mc import desk as _desk
from mc import desk_impact_schema as S
from mc import state
from mc.desk_impact_collect import collect_campaigns
from mc.desk_impact_compare import NOT_ATTRIBUTABLE, comparison
from mc.desk_impact_binding import goal_binding
from mc.desk_impact_sources import base as B
from mc.desk_impact_store import (
    ImpactError, ImpactStore, NotFound, StoreIOError, desk_campaign_resolver,
    no_connection_resolver,
)
from mc.desk_impact_time import canonical_utc

CONFIG_KEY = 'desk_impact_compare_enabled'

ConnectionResolver = Callable[[str], Mapping[str, Any] | None]

_store: ImpactStore | None = None
_resolver: ConnectionResolver = no_connection_resolver
_transport_factory: Callable[[str, ConnectionResolver], B.Transport] | None = None
_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class NoConnection(ImpactError):
    """The connection a definition names is not available to this project. Raised
    before the store or the collector can answer 'not found' for it, so the page
    can say what is missing instead of a bare 404."""


def enabled() -> bool:
    """True only when the operator has switched the feature on. Default off."""
    return (state.CONFIG or {}).get(CONFIG_KEY, False) is True


def wire(*, store: ImpactStore | None = None,
         connection_resolver: ConnectionResolver | None = None,
         transport_factory: Callable[[str, ConnectionResolver], B.Transport] | None = None,
         clock: Callable[[], datetime] | None = None) -> None:
    """Replace the collaborators (tests, and the future connection flow).
    Calling with no arguments restores the production defaults."""
    global _store, _resolver, _transport_factory, _clock
    _store = store
    _resolver = connection_resolver or no_connection_resolver
    _transport_factory = transport_factory
    _clock = clock or (lambda: datetime.now(timezone.utc))


def get_store() -> ImpactStore:
    global _store
    if _store is None:
        if _desk.STORE_PATH is None:
            raise StoreIOError('the Desk store is not wired')
        data = Path(_desk.STORE_PATH).parent
        # Beside data/desk.json, outside data/projects (spec section 5).
        _store = ImpactStore(data / 'desk_impact', data_dir=data / 'projects',
                             connection_resolver=_resolver)
    return _store


# -- window -----------------------------------------------------------------------------

def _midnight(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def default_window(campaign: Mapping[str, Any], now: datetime) -> tuple[B.Window, str]:
    """`[campaign start, start of today UTC)` in whole UTC days, so GitHub's native
    UTC-day traffic can be complete and today's partial day is not mixed in.
    A campaign that began today has no complete day yet: its window is today
    (`[today, tomorrow)`), which is still shown but cannot be refreshed.
    Returns the window and a one-line note saying which case applied."""
    today = _midnight(now)
    raw = campaign.get('started_at') or campaign.get('created_at')
    try:
        start = _midnight(S.parse_utc('campaign start', raw))
    except (S.SchemaError, TypeError, ValueError):
        raise S.SchemaError('campaign has no usable start date') from None
    if start < today:
        return (B.Window(start.isoformat(), today.isoformat(), 'day'),
                'Whole UTC days from the campaign start up to, not including, today.')
    return (B.Window(today.isoformat(), (today + timedelta(days=1)).isoformat(), 'day'),
            'The campaign began today: showing today (UTC), which is still open.')


def resolve_window(campaign: Mapping[str, Any], start: Any, end: Any) -> tuple[B.Window, str]:
    now = _clock()
    if start is None and end is None:
        return default_window(campaign, now)
    if start is None or end is None:
        raise S.SchemaError('start and end must be given together')
    window = B.Window(canonical_utc('start', start), canonical_utc('end', end), 'day')
    S.validate_window(window.as_dict())
    return window, 'Window chosen by the caller.'


# -- campaign ownership ------------------------------------------------------------------

def _campaign(campaign_id: str) -> dict:
    S.require_id('campaign_id', campaign_id)
    camp = desk_campaign_resolver(campaign_id)
    if not camp or not S.valid_id(camp.get('project_id') or ''):
        raise NotFound('campaign not found')
    return camp


# -- reads ------------------------------------------------------------------------------

def definition_view(campaign_id: str) -> dict:
    camp = _campaign(campaign_id)
    return goal_binding(get_store(), camp['project_id'], campaign_id)


def comparison_view(campaign_id: str, start: Any = None, end: Any = None) -> dict:
    """The cached comparison for one campaign. Local only; starts no collection."""
    camp = _campaign(campaign_id)
    window, note = resolve_window(camp, start, end)
    out = comparison(get_store(), camp['project_id'], campaign_id, window)
    definition = out.get('definition')
    if not definition:
        conn = {'state': 'no_definition',
                'message': 'No conversion definition is set for this campaign.'}
    elif not _resolver(definition['connection_id']):
        conn = {'state': 'unavailable',
                'message': 'The connection this campaign reads from is not available to this project.'}
    else:
        conn = {'state': 'available', 'message': ''}
    out['campaign_id'] = campaign_id
    out['window_note'] = note
    out['historical'] = S.parse_utc('end', window.end) <= _clock()
    out['connection'] = conn
    out['not_attributable'] = NOT_ATTRIBUTABLE
    return out


# -- writes (the routes gate both on a human) ----------------------------------------------

def save_definition(campaign_id: str, spec: Any, expected_revision: Any) -> dict:
    camp = _campaign(campaign_id)
    store = get_store()
    ref = spec.get('connection_id') if isinstance(spec, dict) else None
    if not isinstance(ref, str) or not _resolver(ref):
        raise NoConnection('no connection with that reference is available to this project')
    store.set_definition(camp['project_id'], campaign_id, spec, expected_revision=expected_revision)
    return definition_view(campaign_id)


def refresh(campaign_id: str, start: Any = None, end: Any = None) -> dict:
    """Explicit read of the campaign's source into the cache, then the new cached
    comparison. Needs a completed window and an available connection."""
    camp = _campaign(campaign_id)
    window = resolve_window(camp, start, end)[0]
    if S.parse_utc('end', window.end) > _clock():
        raise B.DefinitionRefused('refresh: the window is still open; a completed UTC day range is required')
    pid = camp['project_id']
    active = get_store().get_definitions(pid, campaign_id)['active']
    if active and not _resolver(active['connection_id']):
        raise NoConnection('the connection this campaign reads from is not available to this project')
    if _transport_factory is not None:
        transport = _transport_factory(pid, _resolver)
    else:
        from mc.desk_impact_github_transport import GitHubTransport
        # The route is human-gated (passcode, not an unattended caller), so the
        # vault is asked for this read as an attended one.
        transport = GitHubTransport(pid, _resolver, unattended=False)
    collected = collect_campaigns(
        get_store(), pid, [campaign_id], window, transport=transport,
        clock=lambda: _clock().isoformat(), connection_resolver=_resolver)
    return {'collection': collected, 'comparison': comparison_view(campaign_id, window.start, window.end)}
