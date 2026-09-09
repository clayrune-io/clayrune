"""Artifact coverage — did the turn actually run what the user specified?

THE DEFECT THIS CLOSES (2026-09-08). Ron pasted a LinkedIn search URL carrying
`f_EA=true`, `distance=0.0` and a specific `keywords` value. The agent's fetch
of that page returned nothing, so it quietly re-ran a DIFFERENT search
(`distance=25`, no `f_EA`, different keywords) and reported those 25 results as
if they were the user's page — a confident, fully-reasoned verdict ("nothing
worth chasing") built on data the user never asked for. Nothing in the chat
distinguished that from a real answer; it took two rounds of pushing back to
surface it.

`SHARED_RULES.md` now forbids substitution in words ("SUBSTITUTION IS A LIE").
This module is the part that does not rely on the model's honesty: the check is
computed from the ACTUAL tool inputs recorded off the stream, so the agent can
neither author, suppress, nor forge it.

WHAT IT IS, PRECISELY: a coverage check, not a similarity score. Prose distance
between "review these results" and whatever the agent did is meaningless — it
fires constantly or never. Instead we extract only the LITERAL, machine-
comparable artifacts a user's message carries (URLs and their query params,
file paths, backticked tokens, long ids) and ask a binary question of each: did
ANY tool call this turn contain it? An artifact the user typed and no tool ever
touched is the exact signature of a substitution.

DESIGN RULES, each one paid for by a false-positive class:

  - **Param-level, not URL-level, when the host was reached.** Rewriting a URL
    is legitimate and common (LinkedIn's own `/jobs/search-results/` →
    `/jobs/search/`). So if the host appears anywhere in the turn's tool
    inputs, only the missing PARAMS are reported; the whole URL is reported
    only when the host was never touched at all.
  - **Tracking params are ignored.** `refId`, `trackingId`, `utm_*` and friends
    are noise the user pasted by accident, not intent (`_NOISE_PARAMS`).
  - **Silent on a turn with no tool calls.** A conversational turn that
    discusses a URL without fetching it is not a substitution.
  - **Annotate, never block.** The output is one advisory line in the chat.
    Blocking would put an unmeasured heuristic in front of real work, and a
    false block costs more than the failure it catches.

Wired the way `mc.negation_interrupt` is: a passive observer at the live
`tool_use` stream sites in `agent_routes.py`, AFTER the call has been
dispatched, so a bug here can never delay, deny or alter a tool call. Nothing
in this module raises.

Leaf module: no imports from `mc.blueprints.*` or `server`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib.parse import unquote_plus, urlsplit, parse_qsl

# Query params that carry no user intent — pasted-along session/tracking junk.
# Reporting these as "you asked for it and it was not used" is pure noise.
_NOISE_PARAMS = {
    'refid', 'trackingid', 'referralsearchid', 'ebp', 'origin', 'sessionid',
    'fbclid', 'gclid', 'msclkid', 'igshid', 'ref', 'ref_src', 'source',
    'si', 'sca_esv', 'ved', 'usg', 'ei', 'oq', 'gs_lcrp', 'sourceid', 'ie',
}
_NOISE_PARAMS |= {'utm_' + s for s in
                  ('source', 'medium', 'campaign', 'term', 'content', 'id')}

_URL_RE = re.compile(r'https?://[^\s<>"\'\)\]]+', re.IGNORECASE)
_BACKTICK_RE = re.compile(r'`([^`\n]{2,120})`')
# A path-looking token: a slash or backslash, no whitespace. Anchored on the
# separator rather than an extension so `docs/_journal` counts too.
_PATH_RE = re.compile(r'(?<![\w/\\])(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])?'
                      r'[\w.\-]+(?:[\\/][\w.\-]+)+')
# Long mixed alnum tokens — commit shas, session ids, job ids.
_ID_RE = re.compile(
    r'(?<![\w-])(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{10,64}(?![\w-])')

# Artifacts the check refuses to raise on even when unmatched — these are how
# people write, not instructions to fetch something.
_IGNORE_TOKENS = {'and/or', 'n/a', 'i/o', 'w/o', 'localhost:5199'}

_MAX_REPORTED = 4


def _norm(s: Any) -> str:
    return unquote_plus(str(s or '')).strip().strip('.,;:').lower()


def extract_artifacts(text: str) -> List[Dict[str, Any]]:
    """Literal, machine-comparable things the user's message specified.

    Each artifact is `{'kind', 'label', 'needle', 'scope'}`:
      - `needle` — lowercased string looked for in the turn's tool inputs.
      - `label` — what the user sees in the advisory line.
      - `scope` — params only: the host that must appear before a missing
        param is worth reporting.
    """
    text = str(text or '')
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(kind: str, label: str, needle: Any, scope: str = '') -> None:
        n = _norm(needle)
        if not n or n in _IGNORE_TOKENS:
            return
        key = kind + ':' + n + ':' + scope
        if key in seen:
            return
        seen.add(key)
        out.append({'kind': kind, 'label': label, 'needle': n, 'scope': scope})

    url_spans: List = []
    hosts_seen: set = set()
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip('.,;)')
        url_spans.append((m.start(), m.start() + len(url)))
        try:
            parts = urlsplit(url)
        except Exception:
            continue
        host = (parts.netloc or '').lower()
        if not host:
            continue
        hosts_seen.add(host)
        # The host is the coarse needle: if it never appears in any tool input,
        # the page was never visited at all.
        _add('url', url if len(url) <= 80 else url[:77] + '...', host)
        for k, v in parse_qsl(parts.query, keep_blank_values=False):
            if k.lower() in _NOISE_PARAMS or not v:
                continue
            _add('param', k + '=' + v, k + '=' + v, host)

    def _outside_url(pos: int) -> bool:
        return not any(s <= pos < e for s, e in url_spans)

    for m in _BACKTICK_RE.finditer(text):
        if _outside_url(m.start()):
            _add('quoted', m.group(1), m.group(1))
    for m in _PATH_RE.finditer(text):
        if _outside_url(m.start()):
            _add('path', m.group(0), m.group(0))
    for m in _ID_RE.finditer(text):
        if _outside_url(m.start()):
            _add('id', m.group(0), m.group(0))
    return out


def flatten_tool_input(tool_name: str, tool_input: Any) -> str:
    """One searchable string for a tool call. URL-decoded, so a param the agent
    passed percent-encoded still matches the plain form the user typed."""
    try:
        blob = tool_input if isinstance(tool_input, str) else json.dumps(
            tool_input, ensure_ascii=False, default=str)
    except Exception:
        blob = str(tool_input)
    try:
        blob = unquote_plus(blob)
    except Exception:
        pass
    return (str(tool_name or '') + ' ' + blob).lower()


def uncovered(asked: str, tool_blobs: List[str]) -> List[Dict[str, Any]]:
    """Artifacts the user specified that no tool call this turn contained.

    Empty when the turn made no tool calls — a purely conversational turn has
    nothing to substitute.
    """
    blobs = [b for b in (tool_blobs or []) if b]
    if not blobs:
        return []
    haystack = '\n'.join(blobs)
    misses: List[Dict[str, Any]] = []
    for art in extract_artifacts(asked):
        if art['needle'] in haystack:
            continue
        # A missing param only counts once the host WAS reached. Otherwise the
        # whole URL is already reported and its params would triple-count one
        # miss.
        if art['kind'] == 'param' and art['scope'] not in haystack:
            continue
        misses.append(art)
    return misses[:_MAX_REPORTED]


def advisory_line(asked: str, tool_blobs: List[str]) -> str:
    """The one chat line, or '' when everything the user specified was used."""
    misses = uncovered(asked, tool_blobs)
    if not misses:
        return ''
    labels = ', '.join(str(m['label']) for m in misses)
    return ('[coverage] you specified ' + labels + ' — no tool call this turn '
            'used it. Check the answer came from what you asked for.')
