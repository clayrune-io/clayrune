"""Tier 2 of connect-by-URL: what can Clayrune say about a service it does not know
(docs/DESK_CONNECT_BY_URL_SPEC.md "Detection and trust"; Dave 2026-10-03: unknown
services get INFORMATION plus the "Saved for agents" fallback, no generated adapters).

    1. pre-flight   the address resolves to public addresses only (`net_guard`); a
                    private one is refused before any browser starts
    2. gather       CONCURRENTLY: the page, read in a temporary signed-out pane behind
                    `guard_proxy` (15 s, `pane_reader`), and the official MCP registry
                    (10 s, `registry_lookup`)
    3. classify     the evidence goes ONLY to the isolated, toolless transform
                    (30 s, `classifier`); its answer is validated against the exact schema
                    and its evidence ids are resolved HERE, against what was fetched
    4. answer       rows built from reviewed words (`usage_keys`), every one "Information
                    only", plus the ordinary "Save for agents" option

Whole run: 60 s. No retry. No write of any kind: nothing here touches the vault, the
Desk records, a profile or a setting; the pane's throwaway profile and the proxy are
removed in `finally`. One run at a time (a second answers "busy"); `cancel` stops the
waits, closes the pane and the proxy, and abandons a model call already running (it ends
at its own 30 s limit).

Each failure keeps its own code and sentence (`problems`): `page_unreachable`,
`page_not_html`, `page_timeout`, `page_blocked`, `page_empty`, `pane_unavailable`,
`signin_wall`, `registry_timeout`, `registry_unavailable`, `model_unavailable`,
`model_timeout`, `model_invalid_output`, `discovery_timeout`. Options that validated on
their own survive another source's failure; `incomplete` says so.

Everything returned is untrusted evidence, not authority: `warning` says it, and no URL
in the answer is ever fetched or executed by Clayrune afterwards.
"""
from __future__ import annotations

import concurrent.futures as cf
import threading
import time
from typing import Any

from mc.core import _log
from mc.desk_connect import classifier, display_text, guard_proxy, net_guard, pane_reader, registry_lookup, usage_keys

TOTAL_S = 60.0
PAGE_S = pane_reader.PAGE_S
REGISTRY_S = registry_lookup.REGISTRY_S
MODEL_S = 30.0
MIN_MODEL_S = 3.0
PANE_PROJECT = 'desk_connect'

WARNING = ('These findings come from the service\'s own page and a public registry, both written by third '
           'parties. They are information, not advice and not an endorsement; Clayrune cannot connect to any of them yet.')

_MESSAGES = {
    'signin_wall': 'The page is a sign-in screen, so Clayrune could not read what is behind it.',
    'page_empty': 'The page had no readable text.',
    'discovery_timeout': 'Looking this service up took too long, so it was stopped.',
    'page_unreachable': 'The page could not be reached.',
}


class Cancelled(Exception):
    """The person pressed Cancel."""


# ── one run at a time, cancellable by id ─────────────────────────────────────
_slot = threading.Lock()
_runs: dict[str, threading.Event] = {}
_runs_lock = threading.Lock()


def begin(request_id: str) -> threading.Event | None:
    """The cancel event for a new run, or None when another run is in progress."""
    if not _slot.acquire(blocking=False):
        return None
    ev = threading.Event()
    with _runs_lock:
        _runs[request_id] = ev
    return ev


def finish(request_id: str) -> None:
    with _runs_lock:
        _runs.pop(request_id, None)
    try:
        _slot.release()
    except RuntimeError:
        pass


def cancel(request_id: str) -> bool:
    with _runs_lock:
        ev = _runs.get(request_id)
    if ev is None:
        return False
    ev.set()
    return True


# ── helpers ──────────────────────────────────────────────────────────────────
class _Timeout:
    """What `_await` returns when its deadline passed first."""


TIMED_OUT = _Timeout()


def _await(fut: cf.Future, until: float, clock, cancel_ev: threading.Event) -> Any:
    while True:
        if cancel_ev.is_set():
            raise Cancelled()
        if fut.done():
            return fut.result()
        left = until - clock()
        if left <= 0:
            return TIMED_OUT
        done, _ = cf.wait([fut], timeout=min(0.2, left))
        if done:
            return fut.result()


def _problem(source: str, code: str, message: str | None = None) -> dict:
    return {'source': source, 'code': code, 'message': message or _MESSAGES[code]}


def _row(opt: dict, ev: dict) -> dict:
    method = opt['method']
    _m, _kind, sentence = usage_keys.USAGE[opt['usage_key']]
    if ev['kind'] == 'page':
        shown = f"Read from the service's page ({ev['host']})"
    else:
        shown = f"MCP registry listing “{display_text.clean(ev['name'], 200)}”: {usage_keys.RELATION_WORDS[ev['relation']]}"
    return {'method': method, 'support': 'info_only', 'title': usage_keys.METHOD_TITLES[method],
            'evidence': shown, 'guidance': usage_keys.METHOD_GUIDANCE[method], 'usage': sentence,
            'evidence_id': ev['id'], 'evidence_url': display_text.clean_url(ev['url']), 'selectable': False, 'discovered': True,
            'relation': ev.get('relation')}


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or '').lower()
    except ValueError:
        return ''


def discover(info: dict, own_hosts=(), cancel_ev: threading.Event | None = None, *,
             clock=time.monotonic, read_page=None, lookup=None, classify=None, make_proxy=None,
             resolve=None) -> dict:
    """`info` is `resolve.resolve`'s answer (`url`, `host`). Returns the answer dict
    (see module docstring) or `{ok: False, refused: True, code, error}` for an address
    that may not be opened. Never raises except `Cancelled`."""
    cancel_ev = cancel_ev or threading.Event()
    read_page = read_page or pane_reader.read_page
    lookup = lookup or registry_lookup.lookup
    classify = classify or classifier.classify
    make_proxy = make_proxy or (lambda hosts: guard_proxy.GuardProxy(own_hosts=hosts))
    resolve = resolve or net_guard.resolve_public
    t0 = clock()
    url, host = info['url'], info['host']
    hosts = tuple(own_hosts) + _remote_hosts()
    problems: list[dict] = []
    notes: list[str] = []
    try:
        resolve(host, hosts)
        skip_page = False
    except net_guard.Blocked as e:
        if e.code != 'dns_failed':
            return {'ok': False, 'refused': True, 'code': e.code, 'error': str(e),
                    'hint': 'Paste the public address of the service, not a local or internal one.'}
        problems.append(_problem('page', 'page_unreachable', str(e)))
        skip_page = True

    proxy = make_proxy(hosts)
    holder: list = []
    ex = cf.ThreadPoolExecutor(max_workers=3, thread_name_prefix='desk-discover')
    try:
        page_fut = None
        if not skip_page:
            proxy.start()
            page_fut = ex.submit(read_page, url, proxy, project_id=PANE_PROJECT, holder=holder)
        reg_fut = ex.submit(lookup, host, budget_s=REGISTRY_S)

        evidence: list[dict] = []
        if page_fut is not None:
            page = _await(page_fut, t0 + PAGE_S, clock, cancel_ev)
            if page is TIMED_OUT:
                for pane in list(holder):
                    pane.abort()
                problems.append(_problem('page', 'page_timeout', pane_reader._MESSAGES['page_timeout']))
            elif not page.get('ok'):
                problems.append(_problem('page', page['code'], page['message']))
            elif not page['text'].strip():
                problems.append(_problem('page', 'page_empty'))
            else:
                final = page['url']
                evidence.append({'id': 'p1', 'kind': 'page', 'url': final, 'host': _host_of(final),
                                 'title': page['title'], 'text': page['text']})
                local = [b for b in page.get('blocked') or () if b.get('code') in ('private_address', 'bad_host')]
                if local:
                    notes.append('The page tried to reach local or disallowed addresses; Clayrune blocked those requests.')
                if page.get('hidden_flagged'):
                    notes.append('Some hidden text on the page was ignored.')
                if page.get('truncated'):
                    notes.append('Only the first part of a long page was read.')
        capped = False
        reg = _await(reg_fut, t0 + REGISTRY_S + 1, clock, cancel_ev)
        if reg is TIMED_OUT:
            problems.append(_problem('registry', 'registry_timeout', 'The MCP registry did not answer in time.'))
        elif not reg.get('ok'):
            problems.append(_problem('registry', reg['code'], reg['message']))
        else:
            capped = bool(reg['capped'])
            if capped:
                notes.append('The MCP registry had more matches than Clayrune reads (three pages, 100 results); the list is partial.')
            for i, e in enumerate(reg['entries'][:20], 1):
                evidence.append({'id': f'r{i}', 'kind': 'registry', 'name': e['name'], 'description': e['description'],
                                 'relation': e['relation'], 'url': e['url']})

        options: list[dict] = []
        outcome = 'none'
        dropped = 0
        if evidence:
            left = t0 + TOTAL_S - clock()
            budget = min(MODEL_S, left - 1.0)
            if budget < MIN_MODEL_S:
                problems.append(_problem('model', 'discovery_timeout'))
            else:
                fut = ex.submit(classify, evidence, host, timeout=budget)
                got = _await(fut, t0 + TOTAL_S, clock, cancel_ev)
                if got is TIMED_OUT:
                    problems.append(_problem('model', 'discovery_timeout'))
                elif not got.get('ok'):
                    problems.append(_problem('model', got['code'], got['message']))
                else:
                    outcome, dropped = got['outcome'], got['dropped']
                    by_id = {e['id']: e for e in evidence}
                    options = [_row(o, by_id[o['evidence_id']]) for o in got['options']]
                    if outcome == 'signin_wall':
                        problems.append(_problem('page', 'signin_wall'))
        incomplete = bool(problems) or dropped > 0 or capped
        if dropped:
            notes.append(f'{dropped} finding(s) were discarded because their evidence did not check out.')
        if options:
            outcome = 'found'
        elif outcome != 'signin_wall':
            outcome = 'incomplete' if (problems or outcome == 'incomplete') else 'none'
        return {'ok': True, 'tier': 2, 'url': url, 'host': host, 'outcome': outcome, 'incomplete': incomplete,
                'options': options, 'problems': problems, 'notes': notes, 'warning': WARNING,
                'seconds': round(clock() - t0, 1),
                'evidence': [{'id': e['id'], 'kind': e['kind'], 'url': display_text.clean_url(e['url'])} for e in evidence]}
    finally:
        for pane in list(holder):
            try:
                pane.abort()
            except Exception as e:
                _log(f'[desk_connect] pane close failed: {type(e).__name__}', flush=True)
        try:
            proxy.close()
        except Exception as e:
            _log(f'[desk_connect] proxy close failed: {type(e).__name__}', flush=True)
        ex.shutdown(wait=False, cancel_futures=True)


def _remote_hosts() -> tuple:
    """Clayrune's own remote-access host name, so a public name that is really this
    install is refused like its loopback address."""
    try:
        from mc.blueprints import browser_routes
        h = browser_routes._own_remote_access_hostname()
    except Exception:
        return ()
    return (h,) if h else ()

