"""Source registry (MC-1152 P1, DARK): data, not implementations.

Maps a source id to its adapter. P1 ships it EMPTY: the GitHub adapter arrives
in P3 and each analytics provider after that (spec section 12), each as its own
module imported here. Until then no definition can be saved against a real
source, which is the point of "dark".

Importing this module performs no collection and no network call.
"""
from __future__ import annotations

from mc.desk_impact_sources.base import SourceAdapter


class UnknownSource(KeyError):
    """No adapter is registered under this source id."""


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        sid = adapter.source_id
        if not sid:
            raise ValueError('adapter has no source_id')
        if sid in self._adapters:
            raise ValueError(f'source {sid!r} is already registered')
        self._adapters[sid] = adapter

    def get(self, source_id: str) -> SourceAdapter:
        try:
            return self._adapters[source_id]
        except KeyError:
            raise UnknownSource(source_id) from None

    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


# The process-wide registry. P3 registers `github` here; P5 the analytics
# providers. Each registration is one import line and one `register` call.
REGISTRY = AdapterRegistry()
