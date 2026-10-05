"""Parameter detection for a user-chosen MCP server or API (spec §5, slice U1): the one
function the route calls.

    detect(kind, input) -> {ok, schema, kind, status, evidence, alternatives, problems, ...}

What it does, in order, for the kind the person named:

  1. validates the input (`parameter_parsers.parse_input`, `parse_package_spec`, `url_problem`);
  2. reads ONE bounded source (npm or PyPI metadata, an explicitly given OpenAPI document,
     optionally a public documentation page through the guarded pane) with the readers in
     `parameter_sources`; nothing is downloaded beyond that document and nothing is run;
  3. runs the deterministic parsers on it (`parameter_parsers`);
  4. sends only README / documentation / pasted PROSE to the isolated classifier
     (`parameter_classifier`, certified toolless `run_text_transform`), and only when the
     parsers did not already settle a pasted configuration;
  5. merges, dedupes and labels the alternatives (`parameter_schema.finish_alternative`).

It never activates, registers, writes, probes the target, opens a vault or starts a
process. When the isolated reader is unavailable the deterministic findings are kept and
the draft says what is missing; no other reader takes its place. One detection at a time
(`Busy`), 60 seconds in total.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

from mc.core import _log
from mc.desk_connect import parameter_classifier as pc
from mc.desk_connect import parameter_parsers as pp
from mc.desk_connect import parameter_schema as ps
from mc.desk_connect import parameter_sources as src
from mc.desk_connect.parameter_parsers import InputError, NeedsKind

__all__ = ['detect', 'Busy', 'DetectError', 'InputError', 'NeedsKind']

TOTAL_S = 60.0
CLASSIFY_S = 30.0
_lock = threading.Lock()


class Busy(Exception):
    pass


class DetectError(Exception):
    """A refusal with a machine-readable code. `status` is the HTTP status the route uses."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _evidence(eid: str, kind: str, label: str, text: str | None = None, source: str | None = None) -> dict:
    return {'id': eid, 'kind': kind, 'label': label, 'text': text, 'source': source}


def _names_package(arg: str, name: str) -> bool:
    """`pkg`, `pkg@1.2.3`, `@scope/pkg@1.2.3`, `pkg==1.2.3` as the package `name`."""
    a = arg.lower().split('==')[0]
    base = '@' + a[1:].split('@')[0] if a.startswith('@') else a.split('@')[0]
    return base == name.lower()


def _attach_package(alts: list[dict], package: dict) -> None:
    """A README alternative that launches the package the registry described gets that
    package's facts (install scripts, source-build flag, claimed digest). Only a name match
    on an argument: nothing else is inferred."""
    for alt in alts:
        if alt.get('package') or alt['transport'] != 'stdio':
            continue
        args = (alt['fields'].get('args') or {}).get('value') or []
        if any(isinstance(a, str) and _names_package(a, package['name']) for a in args):
            alt['package'] = dict(package)


def _problem(code: str, message: str) -> dict:
    return {'code': code, 'message': message}


def _remaining(start: float, now) -> float:
    return TOTAL_S - (now() - start)


def detect(kind: str | None, raw, *, text: str | None = None, docs_url: str | None = None, own_hosts=(),
           transform=None, fetch=None, read_docs=None, now=time.monotonic) -> dict:
    """Run one detection. Raises InputError / NeedsKind (bad input), DetectError (a
    refusal the person must fix) or Busy. A source that is merely unavailable does not
    raise: its absence is a `problems` entry and the draft is `incomplete`.
    `transform` / `fetch` / `read_docs` are test seams for the model, the registry/spec
    reader and the guarded pane."""
    if not _lock.acquire(blocking=False):
        raise Busy()
    try:
        return _detect(kind, raw, text, docs_url, own_hosts, transform, fetch, read_docs, now)
    finally:
        _lock.release()


def _detect(kind, raw, text, docs_url, own_hosts, transform, fetch, read_docs, now) -> dict:
    start = now()
    parsed = pp.parse_input(kind, raw, text)
    kind, value = parsed['kind'], parsed['input']
    if docs_url and kind not in ('remote_mcp', 'api_base', 'api_spec'):
        raise DetectError('docs_url_not_used', 'A documentation address applies to a remote MCP address or an API only.')
    evidence: list[dict] = []
    alts: list[dict] = []
    problems: list[dict] = []
    notes: dict = {}
    package: dict | None = None
    prose_from_parser = False       # a pasted configuration the deterministic parser settled

    def new_ev(ekind, label, body=None, source=None) -> str:
        eid = f'e{len(evidence) + 1}'
        evidence.append(_evidence(eid, ekind, label, body, source))
        return eid

    def readme_alts(body: str, eid: str, provenance: str) -> None:
        alts.extend(pp.parse_config_text(body, eid, provenance))

    if kind in ('npm', 'pypi'):
        spec = pp.parse_package_spec(kind, value)
        fetcher = src.fetch_npm if kind == 'npm' else src.fetch_pypi
        eid = new_ev('npm_registry' if kind == 'npm' else 'pypi_registry', f"{kind} registry: {spec['name']}",
                     source=src.NPM_HOST if kind == 'npm' else src.PYPI_HOST)
        try:
            doc = fetcher(spec, fetch=fetch, timeout=min(src.FETCH_S, max(1.0, _remaining(start, now))))
        except src.SourceError as e:
            if e.code == 'source_not_found':
                raise DetectError('source_not_found', f"The registry has no {kind} package named {spec['name']}"
                                  + (f" at version {spec['version']}." if spec['version'] else '.'), 404) from e
            problems.append(_problem(e.code, str(e)))
            alt = ps.new_alternative('mcp', 'stdio')
            alt['package'] = {'ecosystem': kind, 'name': spec['name'], 'resolved_version': None,
                              'requested_version': spec['version'] or spec['tag'],
                              'pin': 'resolved_unverified' if spec['version'] else 'unpinned',
                              'provenance': 'user_input', 'evidence_id': eid, 'bytes_fetched': False}
            alt['fields']['transport'] = ps.field('stdio', 'user_input', eid, 'inferred')
            alt['fields']['auth_type'] = ps.field('unknown', 'user_input', eid, 'uncertain')
            alts.append(alt)
        else:
            parse = pp.parse_npm if kind == 'npm' else pp.parse_pypi
            alt, readme = parse(doc, eid)
            if alt is None:
                problems.append(_problem('registry_document_unreadable',
                                         'The registry answered, but not with a package description Clayrune can read.'))
            else:
                package = alt['package']
                alts.append(alt)
            if readme.strip():
                rid = new_ev('readme', f"{kind} README: {spec['name']}", readme, source=eid)
                readme_alts(readme, rid, 'readme_example')
                if package is not None:
                    _attach_package(alts, package)
            elif alt is not None:
                problems.append(_problem('no_readme', 'The registry has no README for this package, so nothing was read for '
                                         'environment variables or arguments.'))
    elif kind == 'remote_mcp':
        eid = new_ev('user_input', 'Address you entered')
        alts.append(pp.remote_alternative(value, eid))
    elif kind == 'api_base':
        eid = new_ev('user_input', 'Address you entered')
        alts.append(pp.api_base_alternative(value, eid))
    elif kind == 'api_spec':
        eid = new_ev('openapi', 'OpenAPI document', source=None)
        doc = None
        if text and text.strip():
            doc = pp.load_json(text, allow_dupes=True)
            evidence[-1]['source'] = 'pasted'
            if doc is None:
                raise DetectError('openapi_not_json', 'Clayrune reads JSON OpenAPI documents. Paste the JSON form of the document.')
        else:
            try:
                doc = src.fetch_spec(value, own_hosts=own_hosts, fetch=fetch,
                                     timeout=min(src.FETCH_S, max(1.0, _remaining(start, now))))
                evidence[-1]['source'] = urlsplit(value).hostname
            except src.SourceError as e:
                raise DetectError(e.code, str(e), 502 if e.code in ('source_timeout', 'source_unreachable') else 400) from e
        if not pp.is_openapi(doc):
            raise DetectError('source_not_openapi', src.MESSAGES['source_not_openapi'])
        found, notes = pp.parse_openapi(doc, eid, value if not text else None)
        alts.extend(found)
        if notes.get('external_refs_ignored'):
            problems.append(_problem('external_refs_ignored', f"{notes['external_refs_ignored']} reference(s) to other documents "
                                     "were not followed."))
        if notes.get('operations_total', 0) > notes.get('operations_shown', 0):
            problems.append(_problem('operations_truncated', f"Showing {notes['operations_shown']} of "
                                     f"{notes['operations_total']} operations."))
    else:   # pasted
        body = value
        eid = new_ev('pasted', 'Text you pasted', body, source='pasted')
        doc = pp.load_json(body, allow_dupes=True)
        if pp.is_openapi(doc):
            evidence[-1]['kind'] = 'openapi'
            evidence[-1]['text'] = None
            found, notes = pp.parse_openapi(doc, eid)
            alts.extend(found)
            prose_from_parser = True
        else:
            found = pp.parse_config_text(body, eid, 'pasted_config')
            alts.extend(found)
            prose_from_parser = bool(found)
            if prose_from_parser:
                evidence[-1]['text'] = None

    if docs_url:
        if _remaining(start, now) < 5:
            problems.append(_problem('deadline', 'Detection ran out of time before the documentation page was read.'))
        else:
            page = (read_docs or src.read_docs_page)(docs_url, own_hosts)
            if page.get('ok') and str(page.get('text') or '').strip():
                did = new_ev('docs_page', 'Documentation page', page['text'], source=urlsplit(docs_url).hostname)
                evidence[-1]['truncated_by_reader'] = bool(page.get('truncated'))
                readme_alts(page['text'], did, 'readme_example')
                if page.get('hidden_flagged'):
                    problems.append(_problem('hidden_text_removed', 'The page had hidden text, which was not used.'))
            else:
                problems.append(_problem(page.get('code') or 'page_unreachable', page.get('message') or 'The page could not be read.'))

    classifier_info: dict = {'ran': False}
    prose = [e for e in evidence if e['kind'] in ps.PROSE_KINDS and e.get('text')]
    if prose and not prose_from_parser:
        if _remaining(start, now) < 3:
            problems.append(_problem('deadline', 'Detection ran out of time before the isolated reader could run.'))
        else:
            answer = pc.classify(prose, timeout=min(CLASSIFY_S, _remaining(start, now)), label=kind, transform=transform)
            classifier_info = {'ran': True, 'ok': bool(answer.get('ok'))}
            if answer.get('ok'):
                classifier_info.update(outcome=answer['outcome'], dropped=answer['dropped'])
                alts.extend(pc.to_alternatives(answer))
                if answer['dropped']:
                    problems.append(_problem('classifier_dropped', f"{answer['dropped']} suggested connection(s) were dropped "
                                             "because their text was not found in the source."))
                if answer['outcome'] == 'incomplete':
                    problems.append(_problem('classifier_incomplete', 'The isolated reader could not finish reading the source.'))
                if package is not None:
                    _attach_package(alts, package)
            else:
                classifier_info['code'] = answer.get('code')
                problems.append(_problem(answer.get('code') or 'model_unavailable', answer.get('message') or
                                         pc.MESSAGES['model_unavailable']))

    by_key: dict = {}
    for a in alts:
        k = ps.dedupe_key(a)
        if k in by_key:
            ps.merge_duplicate(by_key[k], a)
        else:
            by_key[k] = a
    unique = list(by_key.values())
    if len(unique) > ps.MAX_ALTERNATIVES:
        problems.append(_problem('alternatives_truncated', f'Showing {ps.MAX_ALTERNATIVES} of {len(unique)} alternatives.'))
        unique = unique[:ps.MAX_ALTERNATIVES]
    finished = [ps.finish_alternative(a, i + 1) for i, a in enumerate(unique)]
    if not finished:
        problems.append(_problem('nothing_detected', 'No way to connect was found in the evidence. You can fill the details in yourself.'))
    status = 'none' if not finished else 'incomplete' if (problems or any(a['incomplete'] for a in finished)) else 'complete'
    _log(f'[desk_connect] parameter detection kind={kind} alternatives={len(finished)} status={status}', flush=True)
    return {
        'ok': True, 'schema': ps.SCHEMA, 'kind': kind, 'status': status, 'warning': ps.WARNING,
        'evidence': [_public_evidence(e, classifier_info) for e in evidence],
        'alternatives': finished, 'problems': problems, 'notes': notes, 'classifier': classifier_info,
        'approved': False,
    }


def _public_evidence(e: dict, classifier_info: dict) -> dict:
    """What the person is told about an evidence item: never its text."""
    out = {'id': e['id'], 'kind': e['kind'], 'label': e['label'], 'source': e.get('source'),
           'chars': len(e['text']) if e.get('text') else 0}
    if 'truncated_by_reader' in e:
        out['truncated_by_reader'] = e['truncated_by_reader']
    if e.get('text'):
        out['read_by_isolated_reader'] = bool(classifier_info.get('ran'))
        out['truncated'] = len(e['text']) > pc.MAX_INPUT
    return out
