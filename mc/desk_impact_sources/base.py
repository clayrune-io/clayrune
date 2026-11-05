"""The conversion-source interface (MC-1152 P1, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 7. Repository and site
outcomes are not post engagement, so this is deliberately separate from the
platform `Reader` in `mc/desk_engagement.py`.

This module defines shapes and the contract only. It makes no network call,
dispenses no secret and holds no adapter: a connection is named by a reference
(`connection_ref`) and the injected `Transport` resolves it server-side, so a
credential value never appears in a request object, a snapshot or a log line.

Four operations, names fixed by the spec:

  capabilities(connection)        metadata only; no network, no secrets
  validate_definition(def, conn)  one selected metric; refuses unsupported
                                  metrics/targets/units and foreign connections
  collect(request, transport, clock)
                                  bounded, injectable, read-only; returns
                                  normalized observations, coverage, safe
                                  errors, request count, retry-after
  normalize(raw, request)         deterministic validation of raw rows

An adapter declares whether campaign / channel / post attribution is
supported. The aggregation layer must never upgrade a repository-scoped
observation to campaign scope: dividing a repository total by clicks or post
counts would invent attribution (spec sections 2, 7, 8).
"""
from __future__ import annotations

import abc
import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from mc import desk_impact_schema as S


class DefinitionRefused(ValueError):
    """An adapter or the capability check refused a conversion definition.
    The message names a rule, never a submitted value."""


# -- what an adapter declares ----------------------------------------------------------

@dataclass(frozen=True)
class MetricCapability:
    """One metric a source can supply. `target_schema` maps each target field to
    a type name (`str`, `int`, `bool`, `list`); `target_optional` names the
    fields that may be omitted."""
    metric: str
    unit: str
    counter_kind: str
    scope: str
    target_schema: Mapping[str, str]
    target_optional: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capabilities:
    source_id: str
    label: str
    metrics: tuple[MetricCapability, ...]
    # Which of campaign / channel / post attribution the source can filter by,
    # under its documented semantics. All False = repository/site scope only.
    attributes_campaign: bool = False
    attributes_channel: bool = False
    attributes_post: bool = False
    attribution_dimensions: tuple[str, ...] = ()
    time_granularity: tuple[str, ...] = ()
    history_limit_days: int | None = None
    # Gate names the adapter needs satisfied before it may read (for example a
    # vault reference, a repository permission). Names only, never values.
    requires: tuple[str, ...] = ()

    def metric_named(self, metric: str) -> MetricCapability | None:
        for m in self.metrics:
            if m.metric == metric:
                return m
        return None


# -- what a read asks for and returns ----------------------------------------------------

@dataclass(frozen=True)
class Window:
    """Half-open [start, end), UTC. A cumulative counter has `start=None`."""
    start: str | None
    end: str
    granularity: str
    timezone: str = 'UTC'

    def as_dict(self) -> dict:
        return {'start': self.start, 'end': self.end,
                'granularity': self.granularity, 'timezone': self.timezone}


@dataclass(frozen=True)
class CollectRequest:
    project_id: str
    source_id: str
    connection_ref: str            # a name; the Transport resolves the secret
    target: Mapping[str, Any]
    window: Window
    campaign_id: str | None = None
    definition_id: str | None = None
    definition_revision: int | None = None


@dataclass(frozen=True)
class SourceError:
    """A failed or refused read. Never a zero conversion (spec section 7)."""
    code: str                      # one of schema.ERROR_CODES
    detail: str = ''               # safe text: no credential, no raw body
    retry_after: str | None = None


@dataclass(frozen=True)
class CoverageReport:
    complete: bool
    gaps: tuple[str, ...] = ()
    incomplete_pages: bool = False

    def as_dict(self) -> dict:
        return {'complete': self.complete, 'gaps': list(self.gaps),
                'incomplete_pages': self.incomplete_pages}


@dataclass(frozen=True)
class CollectResult:
    # Dicts in `schema.SNAPSHOT_INPUT_FIELDS` shape, as `normalize` returns them.
    observations: tuple[dict, ...]
    coverage: CoverageReport
    errors: tuple[SourceError, ...] = ()
    request_count: int = 0
    retry_after: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b''


@runtime_checkable
class Transport(Protocol):
    """Injected, read-only HTTP. The real one (P3) resolves `connection_ref`
    through the vault and honours the connection/network policy; a test one
    returns fixtures. `get` only: this interface has no write verb."""

    def get(self, url: str, *, connection_ref: str,
            params: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None) -> TransportResponse: ...


Clock = Callable[[], str]          # returns a UTC ISO-8601 string


# -- the contract -----------------------------------------------------------------------

class SourceAdapter(abc.ABC):
    """Subclasses set `source_id` and implement the four operations. The base
    never reaches a network; `collect` receives its `transport`."""
    source_id: str = ''

    @abc.abstractmethod
    def capabilities(self, connection: Mapping[str, Any]) -> Capabilities: ...

    @abc.abstractmethod
    def validate_definition(self, definition: Mapping[str, Any],
                            connection: Mapping[str, Any]) -> None:
        """Raise `DefinitionRefused` for an unsupported metric, target or unit
        or a connection that is not this project's. One selected metric: a sum
        of stars plus downloads is not a metric."""

    @abc.abstractmethod
    def collect(self, request: CollectRequest, transport: Transport,
                clock: Clock) -> CollectResult: ...

    @abc.abstractmethod
    def normalize(self, raw: Any, request: CollectRequest) -> tuple[dict, ...]:
        """Deterministic: finite nonnegative counts, exact timestamps and
        dimensions, explicit counter kind. Reject malformed rows (raise
        `schema.SchemaError`); report partial coverage rather than guessing."""


# -- shared checks ------------------------------------------------------------------------

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    'str': lambda v: isinstance(v, str) and bool(v.strip()),
    'int': lambda v: isinstance(v, int) and not isinstance(v, bool),
    'bool': lambda v: isinstance(v, bool),
    'list': lambda v: isinstance(v, list),
}


def check_against_capabilities(definition: Mapping[str, Any],
                               caps: Capabilities) -> MetricCapability:
    """The generic part of `validate_definition`: the selected metric and unit
    exist in the source's declared capabilities, and the target has exactly the
    declared fields with the declared types. Adapters call this and then add
    their own rules; the store calls it again as defense in depth."""
    if definition.get('source_id') != caps.source_id:
        raise DefinitionRefused('source_id: does not match this source')
    cap = caps.metric_named(str(definition.get('metric')))
    if cap is None:
        raise DefinitionRefused('metric: not supported by this source')
    if definition.get('unit') != cap.unit:
        raise DefinitionRefused('unit: incompatible with this metric')
    tgt = definition.get('target')
    if not isinstance(tgt, Mapping):
        raise DefinitionRefused('target: required object')
    allowed = set(cap.target_schema)
    extra = sorted(set(tgt) - allowed)
    if extra:
        raise DefinitionRefused(f'target: unsupported field(s): {", ".join(extra)}')
    for name, type_name in cap.target_schema.items():
        if name not in tgt:
            if name in cap.target_optional:
                continue
            raise DefinitionRefused(f'target.{name}: required')
        check = _TYPE_CHECKS.get(type_name)
        if check is None or not check(tgt[name]):
            raise DefinitionRefused(f'target.{name}: must be {type_name}')
    return cap


def snapshot_input(request: CollectRequest, cap: MetricCapability, *,
                   adapter: str, value: int | float | None, null_reason: str | None,
                   observed_at: str, effective_at: str, coverage: CoverageReport,
                   request_count: int,
                   dimensions: Mapping[str, Any] | None = None,
                   attribution_supported: bool = False) -> dict:
    """Build one validated snapshot input from a request and a metric capability
    -- the single place an adapter should assemble an observation, so scope and
    counter kind always come from the declared capability, not from the call
    site. Returns the validated dict (`schema.validate_snapshot_input`)."""
    return S.validate_snapshot_input({
        'source_id': request.source_id,
        'connection_id': request.connection_ref,
        'target': copy.deepcopy(dict(request.target)),
        'definition_id': request.definition_id,
        'definition_revision': request.definition_revision,
        'window': request.window.as_dict(),
        'observed_at': observed_at,
        'effective_at': effective_at,
        'metric': cap.metric,
        'unit': cap.unit,
        'value': value,
        'null_reason': null_reason,
        'counter_kind': cap.counter_kind,
        'scope': cap.scope,
        'attribution': {'supported': attribution_supported,
                        'dimensions': dict(dimensions or {})},
        'coverage': coverage.as_dict(),
        'provenance': {'adapter': adapter, 'request_count': request_count},
    })
