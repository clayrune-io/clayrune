"""Agent reads of the browser pane: the human-only policy route and the digest route
(backlog 0be19837, MC-1056).

  GET  /api/browser/agent-read                      every profile's policy (names, domains)
  PUT  /api/browser/profiles/<name>/agent-read      set a profile's policy. HUMAN ONLY:
                                                    an unattended caller is refused, then the
                                                    dashboard passcode must be retyped in the
                                                    body (mcp_write_gate, the same two checks
                                                    the MCP panel and Desk Connect use)
  POST /api/browser/read-digest {profile,url,question}
                                                    what an unattended agent may call

`read-digest` is the narrow door. It reads ONE page, from a saved profile a human switched
on, on a domain the human listed, and returns an answer written by a toolless model call
(mc/browser_digest.py). The caller never sees the page text. It exposes no click, type,
input, navigate or launch: the only commands the reader sends are `Page.navigate` and the
read template (`browser_routes.ProfilePageReader`), and the fence keeps
launch/read/input/navigate blocked for unattended callers (docs/patches/
fence-browser-read-digest.patch).

Every refusal is a structured error whose `guidance` says not to fall back to curl/wget or
another way of fetching the page (`browser_digest.NO_FALLBACK_GUIDANCE`).
"""
import threading

from flask import Blueprint, jsonify, request

from mc import browser_agent_read as policy
from mc import browser_agent_read_ask as ask
from mc import browser_agent_read_requests as requests_store
from mc import browser_digest
from mc.blueprints import browser_routes as br
from mc.blueprints.mcp_write_gate import refuse_unattended, require_passcode
from mc.browser_hidden_read import HiddenTabReader
from mc.core import _log

bp = Blueprint('browser_agent_read_routes', __name__)

_DEFAULT_PROJECT = 'mission_control'      # pane_reader.read_page's default, same precedent
_busy: set[str] = set()                    # profiles with a read in flight
_busy_lock = threading.Lock()

_STATUS = {'bad_request': 400, 'agent_read_off': 403, 'domain_not_allowed': 403,
           'own_origin_blocked': 403, 'redirect_off_list': 403, 'no_profile': 404,
           'profile_in_use': 409, 'profile_busy': 409, 'non_html_content': 415,
           'cdp_timeout': 504}


def _refuse(kind, detail, status=None, asked=None):
    """`asked` is the sentence `browser_agent_read_ask.ask` returns when the refusal also put
    a card in the agent's chat; it joins the guidance so the agent stops and waits."""
    guidance = browser_digest.NO_FALLBACK_GUIDANCE
    if asked:
        guidance = f'{guidance} {asked}'
    body = {'ok': False, 'error': kind, 'detail': detail, 'guidance': guidance}
    return jsonify(body), status or _STATUS.get(kind, 502)


class _AllowList(br.ProfilePageReader):
    """A `ProfilePageReader` (mixed in ahead of `HiddenTabReader` too) that refuses to read a page whose address is off the list.

    The list is checked against where the page ENDED UP, not the URL that was asked for:
    `_refuse_href` runs after navigation settles and before any text is read, so a redirect
    off the list is refused unread. After the read the address is checked once more, so a
    script that moves the tab during the read cannot hand back another site's text under an
    allowed `final_url`.
    """

    def __init__(self, project_id, profile, domains):
        super().__init__(project_id, profile)
        self._domains = list(domains)

    def _off_list(self, href):
        return self._fail('redirect_off_list',
                          f"the page ended up at a host that is not on profile "
                          f"'{self.profile}''s allowed list ({href_host(href)})")

    def _refuse_href(self, href):
        return None if policy.url_allowed(href, self._domains) else self._off_list(href)

    def read(self, url):
        body = super().read(url)
        if not isinstance(body, dict) or not body.get('ok'):
            return body
        ok, val = self._evaluate('({href: location.href})', 5, 15)
        href = val.get('href') if ok and isinstance(val, dict) else None
        if not isinstance(href, str) or not policy.url_allowed(href, self._domains):
            return self._off_list(href or '')
        if href != body.get('final_url'):
            return self._fail('redirect_off_list', 'the page moved while it was being read')
        return body


class _AllowListedReader(_AllowList):
    """Starts the profile's own Chromium to read (the profile is not open anywhere)."""


class _AllowListedHiddenReader(_AllowList, HiddenTabReader):
    """Reads in a hidden background tab of the pane's Chromium (the profile is already open)."""


def href_host(href):
    return policy.url_host(href) or '(not an allowed https address)'


@bp.route('/api/browser/agent-read', methods=['GET'])
def agent_read_policies():
    """Metadata only: which profiles an agent may read, and the domains. Never a page."""
    return jsonify({'profiles': policy.all_policies()})


@bp.route('/api/browser/profiles/<name>/agent-read', methods=['PUT'])
def agent_read_set(name):
    refused = refuse_unattended('change which sites an agent may read')
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    try:
        profile, enabled, domains = policy.validate_policy(
            name, data.get('enabled'), data.get('domains', []))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not br.named_profile_exists(profile):
        return jsonify({'error': f"no saved profile '{profile}'"}), 404
    refused = require_passcode(data)
    if refused:
        return refused
    rec = policy.set_policy(profile, enabled, domains)
    _log(f"[browser] agent-read policy for profile '{profile}': enabled={rec['enabled']} "
         f"domains={rec['domains']}", flush=True)
    return jsonify({'ok': True, 'profile': profile, **rec})


@bp.route('/api/browser/read-digest', methods=['POST'])
def browser_read_digest():
    data = request.get_json(silent=True) or {}
    profile, url, question = data.get('profile'), data.get('url'), data.get('question')
    if not (isinstance(profile, str) and isinstance(url, str) and isinstance(question, str)
            and profile.strip() and question.strip()):
        return _refuse('bad_request', 'profile, url and question must all be non-empty strings')
    profile = profile.strip().lower()
    project_id = data.get('project_id') if isinstance(data.get('project_id'), str) else _DEFAULT_PROJECT

    session_id = ask.caller_session_id(data.get('session_id'))
    rec = policy.get_policy(profile)
    refusal = None
    if not rec['enabled']:
        refusal = ('agent_read_off', f"profile '{profile}' is not switched on for agent reads; "
                                     f"only a human can switch it on")
    elif not policy.url_allowed(url, rec['domains']):
        refusal = ('domain_not_allowed', f"{href_host(url)} is not on profile '{profile}''s "
                                         f"allowed list (https addresses only)")
    once, domains = None, rec['domains']
    if refusal:
        # A pass the user granted from the request card (Allow once) covers exactly this
        # profile + site + session, and nothing the profile's policy says.
        once = requests_store.find_once(profile, url, session_id)
        if once is None:
            return _refuse(*refusal, asked=ask.ask(profile, url, session_id))
        domains = [once['domain']]

    with _busy_lock:
        if profile in _busy:
            return _refuse('profile_busy', f"profile '{profile}' is already being read")
        _busy.add(profile)
    reader_cls = (_AllowListedHiddenReader if br._session_using_profile(profile)
                  else _AllowListedReader)
    reader = reader_cls(project_id, profile, domains)
    try:
        body = reader.read(url)
    except Exception as e:
        _log(f"[browser] read-digest raised {type(e).__name__} profile={profile}", flush=True)
        return _refuse('cdp_error', 'the page read failed', 502)
    finally:
        reader.close()
        with _busy_lock:
            _busy.discard(profile)

    if not isinstance(body, dict) or not body.get('ok'):
        err = body if isinstance(body, dict) else {}
        _log(f"[browser] read-digest FAILED profile={profile} host={href_host(url)} "
             f"reason={err.get('error')}", flush=True)
        return _refuse(str(err.get('error') or 'cdp_error'), str(err.get('detail') or 'read failed'))

    final = str(body.get('final_url') or '')
    out = browser_digest.launder_page(
        question, str((body.get('content') or {}).get('text') or ''), origin_url=final,
        hidden_content=body.get('hidden_content') or {})
    _log(f"[browser] read-digest profile={profile} host={href_host(final)} "
         f"ok={out.get('ok')} {'' if out.get('ok') else 'reason=' + str(out.get('error'))}",
         flush=True)
    if not out.get('ok'):
        return jsonify(out), 502
    # Spent by the first read that returned an answer; a read that failed leaves it for a retry.
    if once is not None and not requests_store.consume_once(once['id']):
        return _refuse('domain_not_allowed', 'that one-time permission was already used')
    return jsonify(out), 200
