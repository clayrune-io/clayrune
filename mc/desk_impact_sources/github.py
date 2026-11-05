"""GitHub repository context, DARK (MC-1152 P3).

No attribution, credentials, HTTP client or import-time reads. Stargazer identities
exist only during pagination; only aggregates persist. Referrers are top-ten windows.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from mc import desk_impact_schema as S
from mc.desk_impact_sources import base as B
from mc.desk_impact_time import canonical_utc, canonical_window

API = 'https://api.github.com'
MAX_PAGES = 100
MAX_BODY_BYTES = 2_000_000
REPO_FIELDS = {'owner': 'str', 'repo': 'str'}
METRICS = (
    B.MetricCapability('stars', 'stars', 'interval', 'repository', REPO_FIELDS),
    B.MetricCapability('star_total', 'stars', 'cumulative', 'repository', REPO_FIELDS),
    B.MetricCapability('traffic_views', 'views', 'interval', 'repository', REPO_FIELDS),
    B.MetricCapability('traffic_unique_visitors', 'visitors', 'interval', 'repository', REPO_FIELDS),
    B.MetricCapability('referrers_listed', 'referrers', 'window', 'repository', REPO_FIELDS),
    B.MetricCapability('referrer_views', 'views', 'window', 'repository',
                       {**REPO_FIELDS, 'referrer': 'str'}),
    B.MetricCapability('referrer_unique_visitors', 'visitors', 'window', 'repository',
                       {**REPO_FIELDS, 'referrer': 'str'}),
    B.MetricCapability('downloads', 'downloads', 'cumulative', 'asset',
                       {**REPO_FIELDS, 'asset_id': 'int'}),
)
_PART = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
_HOST = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.-]{0,199}$')


def repository_target(target: Mapping[str, Any]) -> dict:
    """Reject path/host injection before URL construction; canonical case."""
    if set(target) - {'owner', 'repo', 'asset_id', 'referrer'}:
        raise B.DefinitionRefused('target: unsupported GitHub field')
    for field in ('owner', 'repo'):
        if not isinstance(target.get(field), str) or not _PART.fullmatch(target[field]):
            raise B.DefinitionRefused('target: invalid GitHub owner or repository')
    if 'asset_id' in target and (type(target['asset_id']) is not int or target['asset_id'] <= 0):
        raise B.DefinitionRefused('target: asset_id must be a positive integer')
    if 'referrer' in target and (not isinstance(target['referrer'], str)
                               or not _HOST.fullmatch(target['referrer'])):
        raise B.DefinitionRefused('target: referrer must be a hostname')
    if 'asset_id' in target and 'referrer' in target:
        raise B.DefinitionRefused('target: asset and referrer cannot be combined')
    return {**target, 'owner': target['owner'].lower(), 'repo': target['repo'].lower()}


def _integer(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise S.SchemaError('GitHub count: expected nonnegative integer')
    return value


def _counts(row: Mapping[str, Any]) -> tuple[int, int]:
    count, uniques = _integer(row.get('count')), _integer(row.get('uniques'))
    if uniques > count:
        raise S.SchemaError('GitHub uniques cannot exceed the count')
    return count, uniques


def _retry(headers: Mapping[str, str], now: str) -> str | None:
    headers = {k.lower(): v for k, v in headers.items()}
    value = headers.get('retry-after')
    try:
        if value is not None:
            stamp = (S.parse_utc('now', now) + timedelta(seconds=max(0, int(value)))
                     if value.isdigit() else parsedate_to_datetime(value).astimezone(timezone.utc))
            return stamp.isoformat()
        if headers.get('x-ratelimit-remaining') == '0':
            return datetime.fromtimestamp(int(headers['x-ratelimit-reset']), timezone.utc).isoformat()
    except (ValueError, KeyError, OverflowError, TypeError):
        return None
    return None


class _ReadFailure(Exception):
    def __init__(self, error: B.SourceError):
        self.error = error


class GitHubSource(B.SourceAdapter):
    source_id = 'github'

    def capabilities(self, connection: Mapping[str, Any]) -> B.Capabilities:
        return B.Capabilities(
            'github', 'GitHub repository context', METRICS,
            time_granularity=('instant', 'day', 'rolling_window'), history_limit_days=14,
            requires=('chosen_vault_reference', 'repository_read_permission',
                      'traffic_administration_read', 'stargazers_permission_live_check'))

    def validate_definition(self, definition: Mapping[str, Any],
                            connection: Mapping[str, Any]) -> None:
        B.check_against_capabilities(definition, self.capabilities(connection))
        target = repository_target(definition['target'])
        if dict(definition['target']) != target:
            raise B.DefinitionRefused('target: GitHub owner/repo must use lowercase canonical spelling')
        if connection.get('id') != definition.get('connection_id'):
            raise B.DefinitionRefused('connection: reference mismatch')
        # ImpactStore checks campaign/connection ownership before invoking us.
        if definition.get('project_id', connection.get('project_id')) != connection.get('project_id'):
            raise B.DefinitionRefused('connection: wrong project')
        bound = connection.get('target')
        if not isinstance(bound, Mapping) or any(target[k] != bound.get(k) for k in REPO_FIELDS):
            raise B.DefinitionRefused('connection: repository mismatch')

    def normalize(self, raw: Any, request: B.CollectRequest) -> tuple[dict, ...]:
        """Internal envelope {kind, body, observed_at, request_count}; never persisted."""
        if not isinstance(raw, dict) or set(raw) != {'kind', 'body', 'observed_at', 'request_count'}:
            raise S.SchemaError('GitHub envelope: kind/body/observed_at/request_count required')
        request = replace(request, window=canonical_window(request.window))
        if request.source_id != 'github':
            raise S.SchemaError('GitHub envelope: wrong source')
        now = canonical_utc('observed_at', raw['observed_at'])
        body, kind = raw['body'], raw['kind']
        n = _integer(raw['request_count'])
        target = repository_target(request.target)
        rows: list[dict] = []

        def emit(metric: str, value: int | None, *, window: B.Window | None = None,
                 tgt: dict | None = None, reason: str | None = None) -> None:
            cap = next(c for c in METRICS if c.metric == metric)
            req = replace(request, target=tgt or target, window=window or request.window)
            cov = B.CoverageReport(reason is None, (reason,) if reason else ())
            rows.append(B.snapshot_input(req, cap, adapter='github', value=value,
                        null_reason=reason, observed_at=now, effective_at=req.window.end,
                        coverage=cov, request_count=n))

        instant = B.Window(None, now, 'instant')
        if kind == 'asset':
            if not isinstance(body, dict) or type(body.get('id')) is not int \
                    or body['id'] != target.get('asset_id'):
                raise S.SchemaError('GitHub asset: identity mismatch')
            emit('downloads', _integer(body.get('download_count')), window=instant)
        elif kind == 'stars':
            if not isinstance(body, list):
                raise S.SchemaError('GitHub stargazers: list required')
            seen: set[int] = set()
            in_period = 0
            start = S.parse_utc('window.start', request.window.start)
            end = S.parse_utc('window.end', request.window.end)
            for row in body:
                if not isinstance(row, dict) or not isinstance(row.get('user'), dict):
                    raise S.SchemaError('GitHub stargazers: malformed timestamp representation')
                ident = _integer(row['user'].get('id'))
                if ident == 0 or ident in seen:
                    raise S.SchemaError('GitHub stargazers: duplicate or invalid identity')
                seen.add(ident)
                stamp = S.parse_utc('starred_at', row.get('starred_at'))
                if stamp > S.parse_utc('now', now):
                    raise S.SchemaError('GitHub stargazers: future timestamp')
                in_period += int(start <= stamp < end)
            emit('stars', in_period)
            emit('star_total', len(seen), window=instant)
        elif kind == 'traffic':
            if not isinstance(body, dict) or not isinstance(body.get('views'), list):
                raise S.SchemaError('GitHub traffic: malformed response')
            total, _ = _counts(body)
            today = S.parse_utc('now', now).replace(hour=0, minute=0, second=0, microsecond=0)
            floor = today - timedelta(days=13)
            seen_days: set[str] = set()
            daily_total = 0
            for row in body['views']:
                if not isinstance(row, dict):
                    raise S.SchemaError('GitHub traffic: object row required')
                daily_count, _ = _counts(row)
                daily_total += daily_count
                stamp = S.parse_utc('traffic.timestamp', row.get('timestamp'))
                day = stamp.isoformat()
                if stamp != stamp.replace(hour=0, minute=0, second=0, microsecond=0) \
                        or not floor <= stamp <= today or day in seen_days:
                    raise S.SchemaError('GitHub traffic: duplicate or out-of-window UTC day')
                seen_days.add(day)
                window = B.Window(day, (stamp + timedelta(days=1)).isoformat(), 'day')
                for metric, field in (('traffic_views', 'count'), ('traffic_unique_visitors', 'uniques')):
                    value = _integer(row.get(field))
                    reason = 'Current UTC day is incomplete' if stamp == today else None
                    emit(metric, None if reason else value, window=window, reason=reason)
            if daily_total != total:
                raise S.SchemaError('GitHub traffic: total and daily counts disagree')
        elif kind == 'referrers':
            if not isinstance(body, list) or len(body) > 10:
                raise S.SchemaError('GitHub referrers: expected top-ten list')
            window = B.Window((S.parse_utc('now', now) - timedelta(days=14)).isoformat(), now,
                              'rolling_window')
            seen_hosts: set[str] = set()
            emit('referrers_listed', len(body), window=window,
                 tgt={k: target[k] for k in REPO_FIELDS})
            for row in body:
                if not isinstance(row, dict):
                    raise S.SchemaError('GitHub referrers: malformed row')
                host = row.get('referrer')
                if not isinstance(host, str):
                    raise S.SchemaError('GitHub referrers: hostname required')
                tgt = repository_target({**target, 'referrer': host})
                if host in seen_hosts:
                    raise S.SchemaError('GitHub referrers: duplicate hostname')
                seen_hosts.add(host)
                _counts(row)
                for metric, field in (('referrer_views', 'count'), ('referrer_unique_visitors', 'uniques')):
                    emit(metric, _integer(row.get(field)), window=window, tgt=tgt)
        else:
            raise S.SchemaError('GitHub envelope: unsupported kind')
        return tuple(rows)

    def collect(self, request: B.CollectRequest, transport: B.Transport,
                clock: B.Clock) -> B.CollectResult:
        target = repository_target(request.target)
        S.require_id('project_id', request.project_id)
        S.require_id('connection_ref', request.connection_ref)
        request = replace(request, window=canonical_window(request.window))
        if request.source_id != 'github' or request.window.timezone != 'UTC':
            raise B.DefinitionRefused('request: GitHub source and UTC window required')
        if request.definition_id is not None or request.campaign_id is not None:
            raise B.DefinitionRefused('request: collect shared repository data without campaign binding')
        if 'referrer' in target:
            raise B.DefinitionRefused('request: collect the native repository referrer list')
        now = canonical_utc('clock', clock())
        if request.window.start is None or S.parse_utc('end', request.window.end) > S.parse_utc('now', now):
            raise B.DefinitionRefused('request: bounded historical interval required')
        request = replace(request, target=target)
        root = f'{API}/repos/{target["owner"]}/{target["repo"]}'
        count = 0
        errors: list[B.SourceError] = []
        observations: list[dict] = []
        gaps: list[str] = []
        incomplete_pages = False

        def get(path: str, params: dict | None = None, *, stars: bool = False):
            nonlocal count
            count += 1
            headers = {'Accept': 'application/vnd.github.star+json' if stars else 'application/vnd.github+json',
                       'X-GitHub-Api-Version': '2026-03-10'}
            try:
                response = transport.get(root + path, connection_ref=request.connection_ref,
                                         params=params, headers=headers)
            except TimeoutError:
                raise _ReadFailure(B.SourceError('timeout', 'GitHub request timed out')) from None
            except PermissionError:
                raise _ReadFailure(B.SourceError('permission', 'GitHub connection or vault policy refused the read')) from None
            except OSError:
                raise _ReadFailure(B.SourceError('unavailable', 'GitHub transport unavailable')) from None
            retry = _retry(response.headers, now)
            if response.status != 200:
                code = {401: 'auth', 403: 'permission', 404: 'not_found', 429: 'rate_limited'}.get(
                    response.status, 'unavailable')
                if response.status == 403 and retry:
                    code = 'rate_limited'
                raise _ReadFailure(B.SourceError(code, f'GitHub HTTP {response.status}', retry))
            if len(response.body) > MAX_BODY_BYTES:
                raise S.SchemaError('GitHub response exceeds bounded size')
            try:
                return json.loads(response.body), {k.lower(): v for k, v in response.headers.items()}
            except (ValueError, UnicodeDecodeError):
                raise S.SchemaError('GitHub response is not valid JSON') from None

        kinds = ('asset',) if 'asset_id' in target else ('stars', 'traffic', 'referrers')
        for kind in kinds:
            if any(e.code == 'rate_limited' for e in errors):
                break
            before = count
            try:
                if kind == 'stars':
                    body: Any = []
                    for page in range(1, MAX_PAGES + 1):
                        got, hdr = get('/stargazers', {'per_page': '100', 'page': str(page)}, stars=True)
                        if not isinstance(got, list) or len(got) > 100:
                            raise S.SchemaError('GitHub stargazers: malformed page')
                        body.extend(got)
                        next_links = [p for p in hdr.get('link', '').split(',') if re.search(r'rel="next"', p)]
                        if not next_links:
                            break
                        if len(next_links) != 1:
                            raise S.SchemaError('GitHub pagination: ambiguous next page')
                        match = re.search(r'<([^>]+)>', next_links[0])
                        url = urlsplit(match[1] if match else '')
                        if (url.scheme, url.netloc, url.path) != ('https', 'api.github.com',
                                f'/repos/{target["owner"]}/{target["repo"]}/stargazers') \
                                or url.fragment or parse_qs(url.query) != {'page': [str(page + 1)], 'per_page': ['100']}:
                            raise S.SchemaError('GitHub pagination: unsafe or nonsequential next page')
                    else:
                        raise _ReadFailure(B.SourceError('partial', 'GitHub stargazer pagination limit reached'))
                else:
                    path = (f'/releases/assets/{target["asset_id"]}' if kind == 'asset' else
                            '/traffic/views' if kind == 'traffic' else '/traffic/popular/referrers')
                    body, _ = get(path, {'per': 'day'} if kind == 'traffic' else None)
                normalized = self.normalize({'kind': kind, 'body': body, 'observed_at': now,
                                             'request_count': count - before}, request)
                observations.extend(normalized)
                if kind == 'traffic':
                    start = S.parse_utc('start', request.window.start)
                    end = S.parse_utc('end', request.window.end)
                    covered = {r['window']['start'] for r in normalized if r['metric'] == 'traffic_views'
                               and r['value'] is not None}
                    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
                    if start != day or end != end.replace(hour=0, minute=0, second=0, microsecond=0):
                        gaps.append('Traffic supports complete UTC days only')
                    if (end - start).days > 14:
                        gaps.append('Traffic older than the native 14-day window is unavailable')
                    for _ in range(15):
                        if day >= end:
                            break
                        if day.isoformat() not in covered:
                            gaps.append('Missing traffic UTC day ' + day.date().isoformat())
                        day += timedelta(days=1)
            except _ReadFailure as exc:
                errors.append(exc.error)
                incomplete_pages |= kind == 'stars' and (count - before > 1 or exc.error.code == 'partial')
            except (S.SchemaError, B.DefinitionRefused, KeyError, TypeError, AttributeError):
                errors.append(B.SourceError('malformed', 'GitHub response failed deterministic validation'))
                incomplete_pages |= kind == 'stars'
        if gaps and not errors:
            errors.append(B.SourceError('partial', 'GitHub native coverage does not cover the requested interval'))
        retry_dates = [e.retry_after for e in errors if e.retry_after]
        return B.CollectResult(tuple(observations),
            B.CoverageReport(not errors, tuple(dict.fromkeys(gaps)), incomplete_pages),
            tuple(errors), count, max(retry_dates) if retry_dates else None)
