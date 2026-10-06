"""The version list of an npm package, read for dependency resolution (docs/
DESK_SERVICE_PROFILES_SPEC.md, section 6.2, slice U2b).

One document per package from the registry's fixed host, through the same reader as U1 and U2a
(`parameter_sources.http_get`: https, port 443, the vetted address, no redirect, a size cap and a
deadline). The abbreviated metadata type is asked for because the full document of a busy package
is many megabytes. Only four things are kept from it: the name, the dist-tags, the list of exact
versions and, per version, the registry's stated sha512 and whether it is deprecated. Nothing else
in the document is trusted or used: the dependencies of a version come from the ARCHIVE'S own
`package.json` (`custom_npm_closure`), because the registry's copy can differ from the archive's.

No `.npmrc`, environment variable or npm configuration is read anywhere: the address is built from
`custom_npm_artifact.REGISTRY` and the package name, and nothing else can change it.
"""
from __future__ import annotations

from urllib.parse import quote

from mc.desk_connect import custom_npm_artifact as _artifact
from mc.desk_connect import custom_npm_semver as _semver
from mc.desk_connect import parameter_parsers as _pp
from mc.desk_connect import parameter_sources as _sources
from mc.desk_connect.mcp_errors import ActivationError

ACCEPT = 'application/vnd.npm.install-v1+json'
MAX_DOCUMENT = 16 * 1024 * 1024
MAX_VERSIONS = 20000
FETCH_S = 30.0


def document_url(name: str) -> str:
    return f'{_artifact.REGISTRY}{quote(name, safe="@").replace("/", "%2F")}'


def fetch(name: str, *, fetch_fn=None) -> dict:
    """`{name, tags, versions}` where `versions` maps an exact version to `{integrity, tarball, deprecated}`.
    Raises ActivationError (`dependency_unavailable`, `registry_mismatch`, ...)."""
    try:
        raw = (fetch_fn or _sources.http_get)(document_url(name), FETCH_S, MAX_DOCUMENT, accept=ACCEPT)
        doc = _sources._json(raw)
    except _sources.SourceError as e:
        status = 404 if e.code == 'source_not_found' else 502
        code = 'dependency_not_found' if e.code == 'source_not_found' else 'dependency_unavailable'
        raise ActivationError(f'a package this one depends on ({name[:80]}) could not be read from the registry: {e}',
                              code, status) from e
    versions = doc.get('versions')
    if doc.get('name') != name or not isinstance(versions, dict) or len(versions) > MAX_VERSIONS:
        raise ActivationError(f'the registry answered with a different package than the dependency {name[:80]}, so '
                              f'nothing was saved', 'registry_mismatch', 502)
    out: dict = {}
    for version, meta in versions.items():
        if _semver.parse_version(version) is None or not _pp._EXACT_NPM.match(version):
            continue
        meta = meta if isinstance(meta, dict) else {}
        dist = meta.get('dist') if isinstance(meta.get('dist'), dict) else {}
        stated = dist.get('integrity') if isinstance(dist.get('integrity'), str) else None
        tarball = dist.get('tarball') if isinstance(dist.get('tarball'), str) else None
        out[version] = {'integrity': stated, 'tarball': tarball, 'deprecated': bool(meta.get('deprecated'))}
    tags = doc.get('dist-tags') if isinstance(doc.get('dist-tags'), dict) else {}
    return {'name': name, 'versions': out,
            'tags': {t: v for t, v in tags.items() if isinstance(t, str) and isinstance(v, str) and v in out}}


def pick(doc: dict, range_text: str) -> str | None:
    """The exact version of `doc` a dependency range selects, or None: a dist-tag name picks that tag's
    version; a range picks the highest version inside it, a deprecated one only when nothing else fits.
    Raises `custom_npm_semver.RangeError` for text that is not a range or a tag."""
    text = range_text.strip()
    if _semver.is_tag(text):
        return doc['tags'].get(text)
    avoid = frozenset(v for v, m in doc['versions'].items() if m['deprecated'])
    return _semver.max_satisfying(list(doc['versions']), text, avoid=avoid)
