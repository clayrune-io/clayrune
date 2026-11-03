"""Metadata-only readiness for the existing pilot mail/digest channel.

This is configuration evidence, not authentication or delivery verification.
Never import the tool scripts, load the credential file or inspect environment
values. SR-4's real runners retain their send, pause and laundering guards.
"""
from __future__ import annotations

import os
from pathlib import Path

from mc import question_channel, steward_role_comms as comms
from mc.steward_role_model import RoleError

_EVENTS = frozenset(('first_fire', 'continue', 'successor', 'accepted'))
_CREDENTIAL_NAMES = frozenset(('NIGHT_MAIL_USER', 'NIGHT_MAIL_APP_PASSWORD'))


def require_ready(project: dict, schedule: dict, event: str, detail: dict) -> str:
    """Approve pilot configuration metadata or raise a concrete channel hold.

    Only the project owning the exact tools used by SR-4 can use this pilot;
    another project's copies do not register a new sender. A credential file's
    presence OR both environment names are sufficient metadata. Neither proves
    a nonempty/valid credential, a working model, network or mail delivery.
    Schedule/detail carry no new policy; child continuity stays in SR-3.
    """
    if event not in _EVENTS:
        raise RoleError('role channel lifecycle event unsupported')
    if question_channel.channel_for(project) != 'email':
        raise RoleError('role channel requires email routing; channel is off or unsupported')
    root = project.get('project_path')
    if not isinstance(root, str) or not root.strip():
        raise RoleError('role channel project path unavailable')
    try:
        project_root = Path(root).resolve()
        tools = (
            ('mailer', Path('tools/night-review/send_mail.py'), comms.SEND_MAIL),
            ('reply digest', Path('tools/mail-mcp/read_digest.py'), comms.READ_DIGEST),
        )
        for label, relative, runner in tools:
            if (project_root / relative).resolve() != runner.resolve():
                raise RoleError('role channel pilot tooling is not configured for this project')
            if not runner.is_file():
                raise RoleError(f'role channel {label} script unavailable')
        for dependency in (comms.READ_DIGEST.parent / 'server.py',
                           comms.READ_DIGEST.parent / 'imap_quote.py',
                           comms.REPO_ROOT / 'mc/mail_launder.py'):
            if not dependency.is_file():
                raise RoleError('role channel reply digest dependency unavailable')
        # These tools use Path.home(), not CLAYRUNE_HOME. Match their source
        # without importing either script or reading any credential contents.
        mail_home = Path.home() / '.clayrune'
        if (mail_home / 'mail_paused').exists():
            raise RoleError('role channel mail is paused')
        # Iteration yields names only; membership/get on os.environ can fetch
        # values internally. Do not even inspect whether a value is empty.
        names = frozenset(iter(os.environ))
        if not (_CREDENTIAL_NAMES <= names or (mail_home / 'night-mail.json').is_file()):
            raise RoleError('role channel credential source is not configured')
    except RoleError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        # Filesystem errors can include arbitrary paths. Keep the diagnostic
        # concrete but metadata-only, without echoing exception text.
        raise RoleError('role channel filesystem metadata unavailable') from exc
    return ('Pilot email channel configuration metadata present; '
            'credentials and delivery are unverified.')
