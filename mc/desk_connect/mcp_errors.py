"""The refusal type shared by the MCP activation modules (`mcp_activation`,
`mcp_package_store`). Its own file so the package store can raise it without importing
the module that calls it."""
from __future__ import annotations


class ActivationError(ValueError):
    """A refusal with a short machine `code` and the HTTP status the route would use."""

    def __init__(self, message: str, code: str = 'activation_failed', status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status
