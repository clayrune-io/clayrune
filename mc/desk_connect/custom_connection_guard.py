"""The one place the older MCP write paths are held to Desk's approval
(docs/DESK_SERVICE_PROFILES_SPEC.md, section 6.5, slice U2a).

A server Desk approved is a specific package, digest, command and reach that a person saw and
confirmed with the passcode. The older paths (`POST/PUT /api/mcp`, the URL installer stream, the
catalogue's `mcp_activation.register`) all end in `mc.mcp.write_server`, so `write_server` calls
`check_write` first and `delete_server` calls `released` after it: one rule for every caller, none
of them edited to know about it.

    check_write   Refuses a write to a name Desk approved (same scope, matched by project id OR
                  project folder), and a PROJECT write named like an approved GLOBAL server
                  (the project entry would shadow the approved one and run something else).
                  Changing an approved server goes through Connect: a new Review, a new
                  fingerprint, the passcode.
    released      Removing an approved server from the MCP panel drops its Desk record too, so
                  the name is free again and Desk never lists a server that is gone.

This does not gate servers Desk never approved: those stay as they were. It also does not stop
someone editing `~/.claude.json` by hand; the card's state (`custom_connection_activation.
derive_state`) shows `changed` when the file no longer holds what was approved.
"""
from __future__ import annotations

from mc.core import _log
from mc.desk_connect import custom_connection_store as _store


class ManagedServerError(ValueError):
    """The server name belongs to a Desk approval. A ValueError so the MCP routes' existing
    handlers turn it into a normal refusal."""
    code = 'managed_by_desk'
    status = 409


def _message(name: str, scope: str) -> str:
    where = 'for every project' if scope == 'global' else 'for a project'
    return (f'The MCP server "{name}" was approved in Desk Connect ({where}) as one specific package and command. '
            f'It cannot be changed or replaced here, because that would run something nobody approved. Connect it '
            f'again from the Desk (a new Review and passcode Save), remove it, or choose another server name.')


def check_write(scope: str, name: str, project_id: str | None = None, project_path: str | None = None) -> None:
    """Raise ManagedServerError when `name` is owned by a Desk approval. Never raises anything else."""
    try:
        owner = _store.find(scope, name, project_id=project_id, project_path=project_path)
        if owner is None and scope == 'project':
            owner = _store.find('global', name)
    except Exception as e:                          # the guard fails CLOSED: an unreadable record blocks the write
        _log(f'[desk_connect] custom connection guard could not read the record: {type(e).__name__}', flush=True)
        raise ManagedServerError('Desk\'s record of approved MCP servers could not be read, so this server name was '
                                 'not changed. Try again.') from e
    if owner is not None:
        raise ManagedServerError(_message(name, owner['scope']))


def released(scope: str, name: str, project_id: str | None = None, project_path: str | None = None) -> None:
    """After a server was deleted from the config: drop its approval record. Never raises."""
    try:
        owner = _store.find(scope, name, project_id=project_id, project_path=project_path)
        if owner is not None:
            _store.remove(owner['scope'], owner.get('project_id'), owner['server_name'])
    except Exception as e:
        _log(f'[desk_connect] custom connection record could not be released: {type(e).__name__}', flush=True)
