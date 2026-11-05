"""Test doubles for the MC-1152 channel-impact tests: a fake conversion source
and ready-made specs/observations. No network, no clock, no secrets."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from mc.desk_impact_sources import base as B

FAKE_SOURCE = 'fake_source'

CAMPAIGNS = {
    'camp-a': {'id': 'camp-a', 'project_id': 'proj1', 'title': 'A',
               'goal': {'metric': 'Stars', 'target': 100, 'source': 'manual',
                        'entries': [{'at': '2026-11-01', 'value': 7}]},
               'plan': {'accounts': [{'id': 'acc-1', 'platform': 'x'}]}},
    'camp-b': {'id': 'camp-b', 'project_id': 'proj1', 'title': 'B', 'plan': {}},
    'camp-other': {'id': 'camp-other', 'project_id': 'proj2', 'title': 'O'},
    'camp-orphan': {'id': 'camp-orphan', 'project_id': None},
}

CONNECTIONS = {
    'conn-1': {'id': 'conn-1', 'project_id': 'proj1'},
    'conn-2': {'id': 'conn-2', 'project_id': 'proj1'},
    'conn-foreign': {'id': 'conn-foreign', 'project_id': 'proj2'},
}


class FakeSource(B.SourceAdapter):
    source_id = FAKE_SOURCE

    def __init__(self) -> None:
        self.validated: list[dict] = []

    def capabilities(self, connection: Mapping[str, Any]) -> B.Capabilities:
        return B.Capabilities(
            source_id=FAKE_SOURCE, label='Fake source',
            metrics=(
                B.MetricCapability('stars', 'stars', 'cumulative', 'repository',
                                   {'repo': 'str'}),
                B.MetricCapability('downloads', 'downloads', 'cumulative', 'asset',
                                   {'repo': 'str', 'asset_id': 'int'},
                                   target_optional=('asset_id',)),
            ),
            requires=('vault_reference',))

    def validate_definition(self, definition, connection):
        self.validated.append(dict(definition))
        if connection.get('project_id') is None:
            raise B.DefinitionRefused('connection: has no project')
        B.check_against_capabilities(definition, self.capabilities(connection))

    def collect(self, request, transport, clock):          # pragma: no cover
        raise AssertionError('P1 never collects')

    def normalize(self, raw, request):                     # pragma: no cover
        raise AssertionError('P1 never normalizes')


def spec(**over: Any) -> dict:
    base = {'label': 'GitHub stars', 'source_id': FAKE_SOURCE,
            'connection_id': 'conn-1', 'metric': 'stars', 'unit': 'stars',
            'target': {'repo': 'example/repo'}}
    base.update(over)
    return base


def observation(*, value: Any = 10, as_of: str = '2026-11-04T00:00:00Z',
                observed_at: str | None = None, metric: str = 'stars',
                unit: str = 'stars', connection_id: str = 'conn-1',
                target: Mapping[str, Any] | None = None,
                definition_id: str | None = None,
                definition_revision: int | None = None, **over: Any) -> dict:
    obs = {
        'source_id': FAKE_SOURCE, 'connection_id': connection_id,
        'target': dict(target or {'repo': 'example/repo'}),
        'definition_id': definition_id, 'definition_revision': definition_revision,
        'window': {'start': None, 'end': as_of, 'granularity': 'instant',
                   'timezone': 'UTC'},
        'observed_at': observed_at or as_of, 'effective_at': as_of,
        'metric': metric, 'unit': unit, 'value': value,
        'null_reason': None if value is not None else 'source did not report it',
        'counter_kind': 'cumulative', 'scope': 'repository',
        'attribution': {'supported': False, 'dimensions': {}},
        'coverage': {'complete': True, 'gaps': [], 'incomplete_pages': False},
        'provenance': {'adapter': FAKE_SOURCE, 'request_count': 1},
    }
    obs.update(over)
    return copy.deepcopy(obs)


def link_record(*, link_id: str = 'imlk-0123456789abcdef', campaign_id: str = 'camp-a',
                account_id: str = 'acc-1', post_id: str = 'post-1',
                version_id: str = 'ver-1', source: str = 'x',
                medium: str = 'social',
                destination: str = 'https://example.com/landing?ref=keep#top') -> dict:
    sep = '&' if '?' in destination.split('#')[0] else '?'
    base, _, frag = destination.partition('#')
    final = (f'{base}{sep}utm_source={source}&utm_medium={medium}'
             f'&utm_campaign={campaign_id}&utm_content={link_id}'
             + (f'#{frag}' if frag else ''))
    return {'link_id': link_id, 'campaign_id': campaign_id, 'account_id': account_id,
            'post_id': post_id, 'version_id': version_id,
            'original_destination': destination, 'final_url': final,
            'utm': {'utm_source': source, 'utm_medium': medium,
                    'utm_campaign': campaign_id, 'utm_content': link_id},
            'created_at': '2026-11-04T12:00:00Z'}
