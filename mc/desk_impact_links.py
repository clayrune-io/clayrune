"""Deterministic UTM link generation and reverse lookup (MC-1152 P2, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 6 and the P2 row of section
12. Nothing here is imported by a route or the UI unless the flag in
`mc/desk_impact_link_insert.py` is on (default off), and no function here edits
a post: it composes a URL, and `issue()` writes one row of the P1 link manifest.

IDENTITY. A link id is a hash of the stable internal ids it stands for --
project, campaign, account, post, version and the destination as typed -- never
of a title, an account label or a slug. So reopening a draft, renaming a post or
renaming an account yields the same id, and the same URL, with nothing stored
first (preview and insert agree because they run the same function). Two
versions of one post, two accounts on one platform, or two destinations in one
version each get their own id. Once a row is issued it is FROZEN: a later call
returns the stored row, so a changed mapping in `desk_impact_channels` cannot
move a link that is already in an approved body.

URL RULES, each pinned in tests/test_desk_impact_links.py:

  * The destination's query, its order, repeated keys, encodings and its
    fragment are kept byte for byte; the four tags are appended to the query
    string and the fragment is re-attached after it. A `?` inside a fragment
    (a hash-routed app) is not mistaken for the query.
  * A tag already present with a DIFFERENT value, a repeated tag, or a
    differently-cased spelling is refused (`conflicting_tags`) and named, never
    overwritten: that attribution belongs to someone else. An identical tag
    already on the URL is left in place, not duplicated.
  * A signed or expiring URL is refused (`signed_url`): adding a parameter to
    one invalidates it.
  * A URL that would exceed the public-URL length, or any URL the P1 manifest
    schema would reject, is refused with the schema's rule, not truncated.
  * The legacy `?ref=desk-<id>` tag is never emitted, here or beside the UTMs.

UTM tags assert nothing about the destination: `coverage_note` says so, and
says whether this campaign has a conversion definition at all.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl

from mc import desk_impact_schema as S
from mc.core import now_iso
from mc.desk_agent_public_data import MAX_URL_LEN, PublicDataRefused, validate_public_url
from mc.desk_impact_channels import CHANNEL_UTM
from mc.desk_impact_store import ImpactStore, LinkConflict, NotFound

# Parameters whose presence means the URL is signed or expires. Lower-case.
SIGNED_QUERY_KEYS = frozenset((
    'signature', 'sig', 'x-amz-signature', 'x-amz-credential', 'x-amz-algorithm',
    'x-amz-expires', 'x-amz-security-token', 'x-goog-signature', 'x-goog-credential',
    'x-goog-expires', 'awsaccesskeyid', 'expires', 'policy', 'key-pair-id', 'hmac',
))

CODES = ('invalid_destination', 'signed_url', 'conflicting_tags', 'too_long',
         'unknown_channel', 'not_in_campaign', 'invalid_identity')

NOTE_NO_DEFINITION = ('This campaign has no conversion defined yet, so nothing in the '
                      'Desk reads these tags. They are recorded for when one is.')
NOTE_DEFINED = ('Desk tags this link; whether the destination records the tags depends '
                "on its own analytics, and on the campaign's conversion source.")

_URL_IN_TEXT = re.compile(r'https?://[^\s<>"\']+')
_TRAILING = '.,;:!?)]}'


class LinkRefused(ValueError):
    """A destination or identity cannot carry a tracked link. `code` is one of
    `CODES`; `keys` names the offending parameters. The message never repeats a
    submitted value, so it is safe to show and to log."""

    def __init__(self, code: str, message: str, *, keys: tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.keys = tuple(keys)


# -- pure URL work -----------------------------------------------------------------

def _split(url: str) -> tuple[str, bool, str, bool, str]:
    """(base, has_query, query, has_fragment, fragment). The fragment is cut at
    the first `#`, the query at the first `?` before it."""
    head, hash_, frag = url.partition('#')
    base, q, query = head.partition('?')
    return base, bool(q), query, bool(hash_), frag


def _pairs(query: str) -> list[tuple[str, str]]:
    try:
        return parse_qsl(query, keep_blank_values=True)
    except ValueError:
        raise LinkRefused('invalid_destination', 'destination: query could not be parsed')


def check_destination(destination: Any) -> list[tuple[str, str]]:
    """Refuse what cannot carry a tracked link; return the query pairs."""
    if not isinstance(destination, str):
        raise LinkRefused('invalid_destination', 'destination: a URL is required')
    pairs = _pairs(_split(destination)[2])
    signed = sorted({k.lower() for k, _ in pairs if k.lower() in SIGNED_QUERY_KEYS})
    if signed:
        raise LinkRefused('signed_url', 'destination: this URL is signed or expires, and '
                          'adding tags would break it; use an unsigned address',
                          keys=tuple(signed))
    try:
        validate_public_url('destination', destination)
    except PublicDataRefused as e:
        raise LinkRefused('invalid_destination', str(e))
    return pairs


def build_final_url(destination: str, utm: Mapping[str, str]) -> str:
    """`destination` with the four `utm` tags appended, everything else intact.
    Raises `LinkRefused` for a signed URL or a conflicting tag."""
    pairs = check_destination(destination)
    base, has_q, query, has_frag, frag = _split(destination)
    present: dict[str, list[str]] = {}
    conflicts: set[str] = set()
    for k, v in pairs:
        low = k.lower()
        if low not in S.UTM_KEYS:
            continue
        if k != low:
            conflicts.add(low)
        else:
            present.setdefault(low, []).append(v)
    for key in S.UTM_KEYS:
        vals = present.get(key, [])
        if len(vals) > 1 or (vals and vals[0] != utm[key]):
            conflicts.add(key)
    if conflicts:
        raise LinkRefused(
            'conflicting_tags',
            'destination: already carries ' + ', '.join(sorted(conflicts)) + ' with another '
            'value; edit the address to remove it, or choose a different one',
            keys=tuple(sorted(conflicts)))
    add = [f'{k}={utm[k]}' for k in S.UTM_KEYS if k not in present]
    if add:
        glue = '' if (not query or query.endswith('&')) else '&'
        query = query + glue + '&'.join(add)
        has_q = True
    out = base + ('?' + query if has_q else '')
    return out + ('#' + frag if has_frag else '')


def link_id_for(project_id: str, campaign_id: str, account_id: str, post_id: str,
                version_id: str, destination: str) -> str:
    """The stable id for this (project, campaign, account, post, version,
    destination). Pure: no clock, no randomness, no store."""
    digest = S.canonical_hash({
        'v': 1, 'project': project_id, 'campaign': campaign_id, 'account': account_id,
        'post': post_id, 'version': version_id, 'destination': destination})
    return f"{S.ID_PREFIX['link']}-{digest.split(':', 1)[1][:16]}"


def _placed(plan_accounts: Any, account_id: str) -> bool:
    """Is the account on the campaign plan? The stored plan lists channel ids as
    strings or as `{channel_id|id}` objects."""
    for a in plan_accounts or []:
        got = a if isinstance(a, str) else (a or {}).get('channel_id') or (a or {}).get('id')
        if got == account_id:
            return True
    return False


# -- the service ---------------------------------------------------------------------

class TrackedLinks:
    def __init__(self, store: ImpactStore, *, channels: Mapping[str, tuple[str, str]] | None = None,
                 clock: Callable[[], str] = now_iso):
        self._store = store
        self._channels = CHANNEL_UTM if channels is None else channels
        self._clock = clock

    def _compose(self, project_id: Any, campaign_id: Any, *, account_id: Any, platform: Any,
                 post_id: Any, version_id: Any, destination: Any) -> dict:
        try:
            for name, value in (('account_id', account_id), ('post_id', post_id),
                                ('version_id', version_id)):
                S.require_id(name, value)
            camp = self._store.get_campaign(project_id, campaign_id)
        except S.SchemaError as e:
            raise LinkRefused('invalid_identity', str(e))
        if not _placed((camp.get('plan') or {}).get('accounts'), account_id):
            raise LinkRefused('not_in_campaign', 'account: is not placed in this campaign')
        channel = self._channels.get(platform) if isinstance(platform, str) else None
        if channel is None:
            raise LinkRefused('unknown_channel', 'platform: has no tracked-link source '
                              'configured; tracked links are not available there')
        source, medium = channel
        check_destination(destination)
        link_id = link_id_for(project_id, campaign_id, account_id, post_id, version_id, destination)
        utm = {'utm_source': source, 'utm_medium': medium,
               'utm_campaign': campaign_id, 'utm_content': link_id}
        final = build_final_url(destination, utm)
        if len(final) > MAX_URL_LEN:
            raise LinkRefused('too_long', f'destination: the tagged address would be longer '
                              f'than {MAX_URL_LEN} characters')
        record = {'link_id': link_id, 'campaign_id': campaign_id, 'account_id': account_id,
                  'post_id': post_id, 'version_id': version_id,
                  'original_destination': destination, 'final_url': final, 'utm': utm,
                  'created_at': self._clock()}
        try:
            S.validate_link_record(record)
        except S.SchemaError as e:
            raise LinkRefused('invalid_destination', str(e))
        return record

    def _frozen(self, project_id: Any, record: dict) -> dict | None:
        """The row already issued under this id, if any. A row whose identity
        fields differ from what was composed is a collision, never reused."""
        try:
            row = self._store.get_link(project_id, record['link_id'])
        except NotFound:
            return None
        keys = ('campaign_id', 'account_id', 'post_id', 'version_id', 'original_destination')
        if any(row[k] != record[k] for k in keys):
            raise LinkConflict('link id already issued to a different identity')
        return row

    def preview(self, project_id: Any, campaign_id: Any, *, account_id: Any, platform: Any,
                post_id: Any, version_id: Any, destination: Any) -> dict:
        """What `issue` would return, without writing. `{link, url, issued}`."""
        rec = self._compose(project_id, campaign_id, account_id=account_id, platform=platform,
                            post_id=post_id, version_id=version_id, destination=destination)
        row = self._frozen(project_id, rec)
        link = row or rec
        return {'link': link, 'url': link['final_url'], 'issued': row is not None,
                'coverage_note': self.coverage_note(project_id, campaign_id)}

    def issue(self, project_id: Any, campaign_id: Any, *, account_id: Any, platform: Any,
              post_id: Any, version_id: Any, destination: Any) -> dict:
        """Preview, then record the row in the manifest. Idempotent: a second
        call returns the stored row (`issued` True, `created` False)."""
        rec = self._compose(project_id, campaign_id, account_id=account_id, platform=platform,
                            post_id=post_id, version_id=version_id, destination=destination)
        row = self._frozen(project_id, rec)
        created = False
        if row is None:
            try:
                row, created = self._store.add_link(project_id, rec)
            except LinkConflict:
                # A concurrent caller issued the same identity between our read and
                # write; its row differs only in created_at. Use it.
                row = self._frozen(project_id, rec)
                if row is None:
                    raise
        return {'link': row, 'url': row['final_url'], 'issued': True, 'created': created,
                'coverage_note': self.coverage_note(project_id, campaign_id)}

    def coverage_note(self, project_id: Any, campaign_id: Any) -> str:
        defs = self._store.get_definitions(project_id, campaign_id)
        return NOTE_DEFINED if defs.get('active') else NOTE_NO_DEFINITION

    # -- reverse lookup ------------------------------------------------------------

    def lookup_url(self, project_id: Any, url: Any) -> dict | None:
        """The manifest row a URL's tags point at, or None. Matches on the tags
        (a platform may rewrite the rest of the URL), and only when the row's
        campaign and source agree with the tags on the URL."""
        if not isinstance(url, str):
            return None
        try:
            tags = {k: v for k, v in _pairs(_split(url)[2]) if k in S.UTM_KEYS}
            row = self._store.get_link(project_id, S.require_own_id('link', tags.get('utm_content')))
        except (LinkRefused, NotFound, S.SchemaError):
            return None
        if any(tags.get(k) != row['utm'][k] for k in ('utm_campaign', 'utm_source', 'utm_medium')):
            return None
        return row

    def links_in_text(self, project_id: Any, text: Any) -> list[dict]:
        """Manifest rows for every Desk-tagged URL in a body, in order, once each."""
        out: list[dict] = []
        seen: set[str] = set()
        for m in _URL_IN_TEXT.finditer(text if isinstance(text, str) else ''):
            row = self.lookup_url(project_id, m.group(0).rstrip(_TRAILING))
            if row and row['link_id'] not in seen:
                seen.add(row['link_id'])
                out.append(row)
        return out
