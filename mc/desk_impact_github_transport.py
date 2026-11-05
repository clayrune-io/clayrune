"""Opt-in GitHub GET transport; unmounted (MC-1152 P3).

The server supplies its trusted existing connection resolver. No default token
name, new connection store or secret management path. Requests and redirects can
never move a credential to another host/repository. Tests inject the opener/vault.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import re

from mc import desk_impact_schema as S
from mc.desk_impact_sources.base import TransportResponse
from mc.desk_impact_sources.github import MAX_BODY_BYTES, repository_target


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubTransport:
    def __init__(self, project_id: str, connection_resolver: Callable[[str], Mapping[str, Any] | None],
                 *, unattended: bool, opener=None, secret_reader=None):
        self.project_id = S.require_id('project_id', project_id)
        self.resolve = connection_resolver
        self.unattended = unattended
        self.opener = opener if opener is not None else build_opener(_NoRedirect())
        self.secret_reader = secret_reader

    def get(self, url: str, *, connection_ref: str,
            params: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None) -> TransportResponse:
        S.require_id('connection_ref', connection_ref)
        connection = self.resolve(connection_ref)
        if not connection or connection.get('id') != connection_ref \
                or connection.get('project_id') != self.project_id \
                or connection.get('read_enabled') is not True:
            raise PermissionError('GitHub connection is unavailable or not enabled for reading')
        target = repository_target(connection.get('target') or {})
        parts = urlsplit(url)
        prefix = f'/repos/{target["owner"]}/{target["repo"]}'
        suffix = parts.path[len(prefix):] if parts.path.startswith(prefix) else ''
        if parts.scheme != 'https' or parts.netloc != 'api.github.com' or parts.query or parts.fragment \
                or not (suffix in ('/stargazers', '/traffic/views', '/traffic/popular/referrers')
                        or re.fullmatch(r'/releases/assets/[1-9][0-9]*', suffix)):
            raise PermissionError('GitHub URL is outside the selected read connection')
        params = dict(params or {})
        if set(params) - {'page', 'per_page', 'per'} or any(
                (k == 'per' and v != 'day') or (k != 'per' and (not v.isdigit() or int(v) < 1))
                for k, v in params.items()):
            raise PermissionError('GitHub query is not an allowed read')
        headers = dict(headers or {})
        if set(headers) - {'Accept', 'X-GitHub-Api-Version'} or headers.get('Accept') not in (
                'application/vnd.github+json', 'application/vnd.github.star+json'):
            raise PermissionError('GitHub headers are outside the adapter contract')
        vault_ref = connection.get('vault_ref')
        if not isinstance(vault_ref, str) or not vault_ref.strip():
            raise PermissionError('GitHub connection has no selected vault reference')
        from mc import secrets_store
        reader = self.secret_reader or secrets_store.get_secret_value
        try:
            # The server caller provides the true trigger context, as for other adapters.
            token = reader(vault_ref, consumer='desk-impact-github', project_id=self.project_id,
                           unattended=self.unattended)
        except secrets_store.SecretsError:
            raise PermissionError('GitHub vault access refused') from None
        if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
            raise PermissionError('GitHub vault entry is not a usable token')
        request = Request(url + ('?' + urlencode(params) if params else ''),
                          headers={**headers, 'Authorization': 'Bearer ' + token,
                                   'User-Agent': 'Clayrune-channel-impact'}, method='GET')
        try:
            with self.opener.open(request, timeout=10) as response:
                body = response.read(MAX_BODY_BYTES + 1)
                if len(body) > MAX_BODY_BYTES:
                    raise OSError('GitHub response exceeds size limit')
                return TransportResponse(response.status, dict(response.headers), body)
        except HTTPError as exc:
            # Drop error bodies/URLs; neither can enter logs or persisted status.
            safe_headers = {k: v for k, v in exc.headers.items()
                            if k.lower() in ('retry-after', 'x-ratelimit-reset', 'x-ratelimit-remaining')}
            exc.close()
            return TransportResponse(exc.code, safe_headers)
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError('GitHub request timed out') from None
            raise OSError('GitHub request unavailable') from None
