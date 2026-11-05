"""Offline fixtures shared by the P3 evidence tests."""
import copy
from datetime import datetime, timedelta, timezone
import json
import socket

import pytest

from mc.desk_impact_sources import base as B
from mc.desk_impact_sources.github import GitHubSource
from mc.desk_impact_store import ImpactStore

TARGET = {'owner': 'example', 'repo': 'repo'}
NOW = '2026-11-04T00:00:00+00:00'
WINDOW = B.Window('2026-11-03T00:00:00+00:00', NOW, 'day')


def response(body, status=200, headers=None):
    return B.TransportResponse(status, headers or {}, json.dumps(body).encode())


def star(ident=1, at='2026-11-03T12:00:00Z'):
    return {'starred_at': at, 'user': {'id': ident, 'login': 'private-person-not-to-persist'}}


class FakeTransport:
    def __init__(self, *, stars=None, traffic=None, referrers=None, asset=None):
        self.calls = []
        self.stars = stars if stars is not None else [response([star()])]
        self.traffic = traffic or response({'count': 3, 'uniques': 2, 'views': [
            {'timestamp': '2026-11-03T00:00:00Z', 'count': 3, 'uniques': 2}]})
        self.referrers = referrers or response([{'referrer': 'example.org', 'count': 3, 'uniques': 2}])
        self.asset = asset or response({'id': 42, 'download_count': 100})

    def get(self, url, *, connection_ref, params=None, headers=None):
        self.calls.append({'url': url, 'connection': connection_ref, 'params': params, 'headers': headers})
        if url.endswith('/stargazers'):
            result = self.stars[int(params['page']) - 1]
        elif url.endswith('/traffic/views'):
            result = self.traffic
        elif url.endswith('/traffic/popular/referrers'):
            result = self.referrers
        else:
            result = self.asset
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('P3 tests may not use the network')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)


@pytest.fixture
def world(tmp_path):
    now = [NOW]
    clock = lambda: now[0]
    campaigns = {
        c: {'id': c, 'project_id': 'proj1', 'plan': {'accounts': ['acc-1', 'acc-2']},
            'goal': {'metric': 'Stars', 'source': 'manual', 'target': 100,
                     'entries': [{'value': 7, 'at': '2026-11-01'}]}}
        for c in ('camp-a', 'camp-b')}
    connections = {'conn-1': {'id': 'conn-1', 'project_id': 'proj1', 'target': TARGET,
                               'vault_ref': 'chosen-by-human', 'read_enabled': True}}
    resolver = lambda ident: copy.deepcopy(connections.get(ident))
    store = ImpactStore(tmp_path / 'impact', campaign_resolver=lambda cid: copy.deepcopy(campaigns.get(cid)),
                         connection_resolver=resolver, clock=clock)
    def define(cid='camp-a', metric='stars', target=None):
        unit = next(m.unit for m in GitHubSource().capabilities({}).metrics if m.metric == metric)
        return store.set_definition('proj1', cid, {'label': metric, 'source_id': 'github',
            'connection_id': 'conn-1', 'metric': metric, 'unit': unit, 'target': target or TARGET},
            expected_revision=len(store.get_definitions('proj1', cid)['revisions']))
    return {'store': store, 'now': now, 'clock': clock, 'campaigns': campaigns,
            'connections': connections, 'resolver': resolver, 'define': define}


def request(target=None, window=WINDOW):
    return B.CollectRequest('proj1', 'github', 'conn-1', target or TARGET, window)


def normalize(kind, body, target=None, now=NOW, window=WINDOW):
    return GitHubSource().normalize({'kind': kind, 'body': body, 'observed_at': now,
                                     'request_count': 1}, request(target, window))
