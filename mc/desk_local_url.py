"""Loopback address for Desk commands and tools."""
import os

from mc import state


def local_url(host: str = 'localhost') -> str:
    port = int(os.environ.get('MC_PORT') or state.CONFIG.get('port') or 5199)
    return f'http://{host}:{port}'
