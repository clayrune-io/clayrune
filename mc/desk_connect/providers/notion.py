"""Notion: the reviewed Notion MCP package (`@notionhq/notion-mcp-server`, pinned in
`mc/desk_connect/mcp_catalogue.json`) so agents can read and edit the pages and
databases shared with an integration the user made.

One method, `mcp`. The token is an internal-integration token stored as
`notion.token`; the server is registered in the global MCP config with that NAME,
never the value (`mcp_activation`). Notion's own connection rules are in the
reviewed catalogue entry: no README text from the package reaches a model.
No free read-only check exists for a stdio MCP server, so this is never "Verified".
"""
from __future__ import annotations

from mc import secrets_store as _vault
from mc.desk_connect.providers import base
from mc.desk_connect.providers.mcp_package import McpPackage


class NotionProvider(base.Provider):
    service_id = 'notion'
    summaries = {'mcp': 'Stores your Notion integration token in Secrets as "notion.token", then registers the '
                        'reviewed Notion MCP server at a pinned version so agents can use Notion. The server starts '
                        'the first time an agent session uses it. Nothing is verified.'}

    def __init__(self) -> None:
        self._mcp = McpPackage('notion')

    def guide(self, method):
        return [self._mcp.entry()['credential']['hint'],
                'Share the Notion pages you want agents to reach with that integration (the integration only sees what is shared with it).']

    def install(self, method):
        return self._mcp.card()

    def fields(self, method, vault_names=None):
        return self._mcp.fields(vault_names)

    def clean(self, method, fields, vault_names=None):
        return self._mcp.clean(fields, vault_names)

    def apply(self, method, clean, undo):
        stored = self._mcp.apply(clean, undo, {s['name'] for s in _vault.list_secrets()})
        return base.Applied(extra={'stored': stored})

    def after_commit(self, method, clean, applied):
        return self._mcp.after_commit()

    def credential_state(self, method, account_id=None):
        return self._mcp.state()

    def verify(self, method, account_id=None):
        return base.Probe(None, 'Clayrune has no free read-only check for an MCP server, so it is registered with '
                                'agents but never marked Verified.')
