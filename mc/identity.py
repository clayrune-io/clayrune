"""Shared "who is this session" fallback — the ONE place a session with no
persona of its own resolves to an identity, so the Floor and the Channel
roster cannot independently drift on what that resolution is.

Born from ws_005 (`docs/research/_ws005_channel_roster_audit.md`): Vector,
the operator's default agent, was structurally invisible on the Channel rail.
`floor_routes._figure_name`/`_figure_avatar` already had the fallback arm
(explicit label > persona's own name > CONFIG's default agent, with an
'unnamed' carve-out for a delegated session with no persona — MC-925). The
Channel's `_convCharKey` (`static/js/conversation.js`) had none: it returned
`''` for any session whose `character` was falsy, and the roster `continue`d
on that empty key — so every session that was not explicitly hired as a
persona (the default agent chief among them) had no row to belong to.

This module covers only the TAIL of that precedence chain — a session with a
real persona, or a per-surface override (the Floor's per-session labels), is
resolved by its own caller first; those overrides are surface-specific by
design. This is what both surfaces fall through to once neither applies, so
adding a THIRD naming path was not the fix — see the audit doc's "where
matters more than what."
"""
from mc import state
from mc.characters import clean_avatar

_NAME_CHARS = 32


def resolve_default_identity(source):
    """(key, name, avatar, name_from) for a session with no persona and no
    per-session override.

    A DELEGATED session (source='agent' — dispatched by another agent, not
    typed by the user) with no persona is the MC-925 exception: its own
    system prompt tells it "you appear as unnamed"
    (`agent_routes._delegated_unnamed`), not the project's default name, so
    resolving it to the default here would put this surface at odds with
    what the agent itself was told. `key='unnamed:'` groups those sessions
    together without naming them the default agent.

    Every other no-persona session — including the operator's own default
    agent (e.g. "Vector") — shares ONE identity. The key is a constant
    (`'default:'`), not per-session, so many sessions collapse into a single
    roster row instead of each being individually unrepresentable.
    """
    if (source or '') == 'agent':
        return 'unnamed:', 'unnamed', '', 'unnamed'
    name = ' '.join(str(state.CONFIG.get('agent_name', '')).split())[:_NAME_CHARS]
    avatar = clean_avatar(state.CONFIG.get('agent_avatar', ''))
    return 'default:', name, avatar, 'default'


def resolve_identity(character, source=''):
    """{key, name, avatar, from} — the identity a roster groups this session
    under: the persona it was hired as, or the shared fallback above.

    `key` mirrors the scheme `conversation.js`'s `_convCharKey` already uses
    for a persona (`scope:name`) so a character-bearing row's grouping is
    UNCHANGED by this function's existence — only the previously-empty-key
    case (no persona) gets a real bucket instead of being dropped.
    """
    if isinstance(character, dict) and character.get('name'):
        scope = (character.get('scope') or 'global')
        name = (character.get('agent_name') or character.get('display_name')
                or character.get('name') or '')
        return {'key': f"{scope}:{character['name']}", 'name': name,
                'avatar': character.get('avatar') or '', 'from': 'character'}
    key, name, avatar, name_from = resolve_default_identity(source)
    return {'key': key, 'name': name, 'avatar': avatar, 'from': name_from}
