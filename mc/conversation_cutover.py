"""Default-off read/cutover seam for the provider-neutral conversation journal.

The canonical journal is richer than the legacy agent-log/transcript views, but
it is not automatically a trustworthy replacement: a source can be partial,
gapped, or still missing its terminal boundary.  This module makes that choice
explicit and testable without importing a provider runtime or changing a live
route.  A later composition root can pass the returned selection to the rail,
history, resume, search, export, and Scribe consumers as one policy decision.

No writer, native transcript parser, model call, or filesystem discovery lives
here.  The legacy callbacks are caller-owned and are invoked lazily only when a
canonical selection is not ready (or the policy is disabled).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterable, Literal, TYPE_CHECKING

from mc.conversation_projection import ProjectedHistory, project_conversation, scribe_input

if TYPE_CHECKING:
    from mc.conversation_store import ConversationEvent, ConversationStore, HistorySnapshot


Coverage = Literal['legacy_only', 'partial', 'canonical_verified', 'capture_gap']
ReadSource = Literal['legacy', 'canonical']


@dataclass(frozen=True)
class CutoverPolicy:
    """Read policy; all fields are deliberately conservative by default."""

    enabled: bool = False
    allow_legacy_fallback: bool = True
    require_complete_for_canonical: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.allow_legacy_fallback) is not bool:
            raise ValueError('cutover policy flags must be booleans')
        if type(self.require_complete_for_canonical) is not bool:
            raise ValueError('require_complete_for_canonical must be boolean')


@dataclass(frozen=True)
class CanonicalHistory:
    project_id: str
    conversation_id: str
    snapshot: Any
    events: tuple[Any, ...]
    projection: ProjectedHistory
    coverage: Coverage

    @property
    def complete(self) -> bool:
        """Whether source coverage and content projection are both complete."""
        return self.coverage == 'canonical_verified' and self.projection.complete

    @property
    def scribe_ready(self) -> bool:
        """Only complete canonical content may feed memory derivation."""
        return self.complete and bool(self.events)


@dataclass(frozen=True)
class ReadSelection:
    source: ReadSource
    coverage: Coverage
    events: tuple[Any, ...]
    canonical: CanonicalHistory | None = None
    reason: str = ''


@dataclass(frozen=True)
class ScribeSelection:
    source: ReadSource | Literal['none']
    coverage: Coverage
    lines: tuple[str, ...]
    canonical: CanonicalHistory | None = None
    reason: str = ''


class ConversationCutover:
    """Startup-composed canonical reader used by the read-only consumers.

    ``store_provider`` is deliberately injected by the composition root.  A
    disabled provider returns ``None`` and therefore cannot create, discover,
    or mutate a lifecycle database.  This keeps the route/memory modules free
    of invented paths while making the eventual opt-in cutover one policy
    decision.
    """

    def __init__(self, *, policy: CutoverPolicy,
                 store_provider: Callable[[], ConversationStore | None]) -> None:
        if not isinstance(policy, CutoverPolicy) or not callable(store_provider):
            raise ValueError('policy and store provider are required')
        self.policy = policy
        self._store_provider = store_provider

    def _store(self) -> ConversationStore | None:
        try:
            return self._store_provider()
        except Exception:
            return None

    def history(self, project_id: str, conversation_id: str, *,
                legacy_reader: Callable[[], Iterable[Any]] | None = None) -> ReadSelection:
        store = self._store() if self.policy.enabled else None
        return read_history_with_cutover(
            store, project_id, conversation_id, legacy_reader=legacy_reader,
            policy=self.policy) if store is not None else select_history(
                canonical=None, legacy_reader=legacy_reader, policy=self.policy)

    def scribe(self, project_id: str, conversation_id: str, *,
               legacy_reader: Callable[[], Iterable[str]] | None = None) -> ScribeSelection:
        store = self._store() if self.policy.enabled else None
        return read_scribe_with_cutover(
            store, project_id, conversation_id, legacy_reader=legacy_reader,
            policy=self.policy) if store is not None else select_scribe(
            canonical=None, legacy_reader=legacy_reader, policy=self.policy)

    def display_lines(self, project_id: str, conversation_id: str) -> tuple[str, ...] | None:
        """Return canonical display lines only for a complete history."""
        selection = self.history(project_id, conversation_id)
        if (selection.source != 'canonical' or selection.canonical is None
                or not selection.canonical.complete):
            return None
        return _scribe_projection_lines(selection.canonical, detail_limit=4000)

    def agent_log(self, project_id: str) -> tuple[CanonicalLogRow, ...]:
        store = self._store() if self.policy.enabled else None
        if store is None:
            return ()
        rows: list[CanonicalLogRow] = []
        for conversation_id in store.list_lifecycle_conversations(project_id):
            rows.extend(canonical_agent_log(store, project_id, conversation_id))
        return tuple(rows)

    def conversation_rows(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]] | None:
        """Return complete canonical rail rows, or ``None`` to use legacy."""
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        store = self._store() if self.policy.enabled else None
        if store is None:
            return None
        rows: list[dict[str, Any]] = []
        for conversation_id in store.list_lifecycle_conversations(project_id):
            history = read_canonical_history(store, project_id, conversation_id)
            if history is None or not history.complete:
                return None
            users = [block.text for block in history.projection.blocks
                      if block.kind == 'user_message' and block.text]
            if not users:
                continue
            assistants = [block.text for block in history.projection.blocks
                          if block.kind == 'assistant_message' and block.text]
            state = store.lifecycle_state(project_id, conversation_id)
            timestamps = [event.timestamp for event in history.events if event.timestamp]
            rows.append({
                'claude_session_id': '', 'mc_session_id': conversation_id,
                'conversation_id': conversation_id, 'status': (
                    state.attempts[-1].status.value if state.attempts else 'completed'),
                'label': ' '.join(users[-1].split()), 'first_user': users[0],
                'last_user': users[-1], 'turns': len(users), 'size': len(history.events),
                'mtime': 0, 'ts': timestamps[-1] if timestamps else '',
                'ts_relative': '', 'live': False, 'waiting_for_question': False,
                'waiting_for_plan_approval': False, 'trigger_type': '',
                'source': 'canonical', 'steward': False, 'steward_objective': '',
                'character': None, 'identity': None, 'spawned_by_session_id': '',
                'provider': '', 'provider_session_id': '', 'resumable': False,
                'resume_mode': 'readonly', 'canonical': True,
                'assistant_preview': ' '.join(assistants[-1].split()) if assistants else '',
            })
        rows.sort(key=lambda row: row.get('ts', ''), reverse=True)
        return rows[:limit]

    def search(self, project_id: str, query: str, *, limit: int = 50) -> list[dict[str, Any]] | None:
        """Search complete canonical projections; ``None`` means legacy fallback."""
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        store = self._store() if self.policy.enabled else None
        if store is None:
            return None
        needle = (query or '').strip().lower()
        if len(needle) < 2:
            return []
        rows = self.conversation_rows(project_id, limit=10000)
        if rows is None:
            return None
        out = []
        for row in rows:
            text = ' '.join(filter(None, (row.get('first_user', ''),
                                          row.get('last_user', ''),
                                          row.get('assistant_preview', ''))))
            if needle not in text.lower():
                continue
            out.append({
                'csid': '', 'conversation_id': row['conversation_id'],
                'label': row['label'], 'snippet': text[:400], 'matches': 1,
                'mtime': 0, 'ts_relative': '', 'canonical': True,
            })
        return out[:limit]

    def privacy_delete(self, project_id: str, conversation_id: str) -> bool:
        """Revoke one existing canonical conversation without creating state."""
        store = self._store() if self.policy.enabled else None
        if store is None:
            return False
        try:
            state = store.lifecycle_state(project_id, conversation_id)
            if not state.deleted:
                store.set_lifecycle_deleted(
                    project_id, conversation_id, True,
                    expected_revision=state.revision,
                    event_id=f'privacy-delete:cutover:{conversation_id}')
            return True
        except Exception:
            return False


@dataclass(frozen=True)
class CanonicalLogRow:
    """Read-only Agent Log shape derived from lifecycle authority."""

    project_id: str
    conversation_id: str
    attempt_id: str
    request_id: str
    status: str
    origin: str
    engine: dict[str, Any]
    native_handle: str
    canonical: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            'project_id': self.project_id,
            'session_id': self.conversation_id,
            'conversation_id': self.conversation_id,
            'attempt_id': self.attempt_id,
            'request_id': self.request_id,
            'status': self.status,
            'origin': self.origin,
            'engine': json.loads(json.dumps(self.engine, ensure_ascii=False,
                                             sort_keys=True, allow_nan=False)),
            'native_handle': self.native_handle,
            'canonical': self.canonical,
        }


def read_canonical_history(store: ConversationStore, project_id: str,
                           conversation_id: str) -> CanonicalHistory | None:
    """Read one privacy-fenced canonical boundary, even when it is gapped."""
    try:
        snapshot = store.history_snapshot(project_id, conversation_id)
    except Exception:
        # A missing/non-lifecycle journal is not a reason to touch or create
        # state.  The caller decides whether its legacy source is available.
        return None
    rows: list[ConversationEvent] = []
    cursor = 0
    while cursor < snapshot.high_water:
        page = store.read_history_snapshot(snapshot, after=cursor, limit=1000)
        if not page:
            break
        rows.extend(page)
        cursor = page[-1].sequence
    if cursor != snapshot.high_water:
        # A fixed boundary that cannot be read is not silently promoted.  Keep
        # the partial rows for display and let projection report the gap.
        coverage: Coverage = 'capture_gap'
    else:
        state = store.lifecycle_state(project_id, conversation_id)
        coverage = 'canonical_verified' if state.coverage_complete else 'partial'
    projection = project_conversation(rows)
    if any(issue.code == 'capture_gap' for issue in projection.issues):
        coverage = 'capture_gap'
    return CanonicalHistory(project_id, conversation_id, snapshot, tuple(rows),
                            projection, coverage)


def select_history(*, canonical: CanonicalHistory | None,
                   legacy_reader: Callable[[], Iterable[Any]] | None = None,
                   policy: CutoverPolicy | None = None) -> ReadSelection:
    """Select canonical history only when it is safe for the requested policy.

    A partial canonical source is retained as the last-resort view when no
    legacy source exists, but it is never mislabeled complete and never
    eligible for Scribe under the default complete-source policy.
    """
    policy = policy or CutoverPolicy()
    if policy.enabled and canonical is not None:
        if canonical.complete or not policy.require_complete_for_canonical:
            return ReadSelection('canonical', canonical.coverage, canonical.events,
                                 canonical, 'canonical_verified')
        if not policy.allow_legacy_fallback:
            return ReadSelection('canonical', canonical.coverage, canonical.events,
                                 canonical, 'canonical_incomplete_no_fallback')
    if legacy_reader is not None:
        legacy = tuple(legacy_reader())
        if legacy:
            reason = 'cutover_disabled' if not policy.enabled else 'canonical_incomplete'
            return ReadSelection('legacy', 'legacy_only', legacy, canonical, reason)
    if policy.enabled and canonical is not None:
        return ReadSelection('canonical', canonical.coverage, canonical.events, canonical,
                             'canonical_last_resort_gapped_view')
    return ReadSelection('legacy', 'legacy_only', (), canonical, 'no_read_source')


def read_history_with_cutover(store: ConversationStore, project_id: str,
                              conversation_id: str, *,
                              legacy_reader: Callable[[], Iterable[Any]] | None = None,
                              policy: CutoverPolicy | None = None) -> ReadSelection:
    """Composition helper that keeps the default-off gate at the read edge."""
    policy = policy or CutoverPolicy()
    canonical = (read_canonical_history(store, project_id, conversation_id)
                 if policy.enabled else None)
    return select_history(canonical=canonical, legacy_reader=legacy_reader,
                          policy=policy)


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    left = limit // 2
    right = limit - left
    return f'{text[:left]}\\n...[{len(text) - limit} chars elided]...\\n{text[-right:]}'


def _render_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _scribe_projection_lines(history: CanonicalHistory, *, detail_limit: int) -> tuple[str, ...]:
    """Render the structured projection without provider-specific inference."""
    result: list[str] = []
    for block in scribe_input(history.projection).blocks:
        payload: dict[str, Any]
        try:
            payload = json.loads(block.payload_json)
        except (TypeError, ValueError):
            continue
        if block.kind == 'user_message':
            if block.text:
                result.append(f'USER: {block.text}')
            for attachment in payload.get('user_message', {}).get('attachments', []):
                if isinstance(attachment, dict):
                    label = attachment.get('name') or attachment.get('id')
                    if label:
                        result.append(f'ATTACHMENT: {_shorten(str(label), detail_limit)}')
        elif block.kind == 'assistant_message':
            marker = '' if block.completeness == 'final' else ' [partial]'
            if block.text:
                result.append(f'ASSISTANT{marker}: {block.text}')
        elif block.kind == 'thinking' and block.text:
            result.append(f'THINKING: {_shorten(block.text, detail_limit)}')
        elif block.kind == 'tool_call':
            result.append(f"ACTION {payload.get('name', '')} [call {block.call_id}]: "
                          f"{_shorten(json.dumps(payload.get('input', {}), ensure_ascii=False,
                                                   sort_keys=True, allow_nan=False), detail_limit)}")
        elif block.kind == 'tool_result':
            status = ' error' if payload.get('is_error') else ''
            result.append(f"RESULT [call {block.call_id}{status}]: "
                          f"{_shorten(_render_value(payload.get('output')), detail_limit)}")
    return tuple(result)


def select_scribe(*, canonical: CanonicalHistory | None,
                  legacy_reader: Callable[[], Iterable[str]] | None = None,
                  policy: CutoverPolicy | None = None,
                  detail_limit: int = 2000) -> ScribeSelection:
    """Choose a complete canonical Scribe source or an explicit legacy source."""
    if type(detail_limit) is not int or detail_limit < 2:
        raise ValueError('detail_limit must be an integer >= 2')
    policy = policy or CutoverPolicy()
    if policy.enabled and canonical is not None and canonical.scribe_ready:
        return ScribeSelection('canonical', canonical.coverage,
                               _scribe_projection_lines(canonical, detail_limit=detail_limit),
                               canonical, 'canonical_verified')
    if policy.enabled and canonical is not None and not policy.allow_legacy_fallback:
        return ScribeSelection('none', canonical.coverage, (), canonical,
                               'canonical_incomplete_no_fallback')
    if legacy_reader is not None:
        lines = tuple(legacy_reader())
        if lines:
            return ScribeSelection('legacy', 'legacy_only', lines, canonical,
                                   'cutover_disabled' if not policy.enabled else 'canonical_incomplete')
    return ScribeSelection('none', 'legacy_only' if canonical is None else canonical.coverage,
                           (), canonical, 'no_scribe_source')


def read_scribe_with_cutover(store: ConversationStore, project_id: str,
                             conversation_id: str, *,
                             legacy_reader: Callable[[], Iterable[str]] | None = None,
                             policy: CutoverPolicy | None = None,
                             detail_limit: int = 2000) -> ScribeSelection:
    """Scribe composition helper; disabled mode performs no canonical read."""
    policy = policy or CutoverPolicy()
    canonical = (read_canonical_history(store, project_id, conversation_id)
                 if policy.enabled else None)
    return select_scribe(canonical=canonical, legacy_reader=legacy_reader,
                         policy=policy, detail_limit=detail_limit)


def canonical_agent_log(store: ConversationStore, project_id: str,
                        conversation_id: str) -> tuple[CanonicalLogRow, ...]:
    """Project lifecycle attempts into provider-neutral Agent Log rows.

    This is read-only and intentionally does not copy legacy JSON sidecars or
    infer provider/native IDs from message text.  An empty/missing journal
    returns an empty tuple rather than creating state.
    """
    try:
        state = store.lifecycle_state(project_id, conversation_id)
    except Exception:
        return ()
    rows: list[CanonicalLogRow] = []
    for attempt in state.attempts:
        request = next((item for item in state.requests if item.request_id == attempt.request_id), None)
        try:
            engine = json.loads(attempt.engine_key)
        except (TypeError, ValueError):
            engine = {}
        rows.append(CanonicalLogRow(project_id, conversation_id, attempt.attempt_id,
                                    attempt.request_id, attempt.status.value,
                                    request.origin if request is not None else 'unknown',
                                    engine, attempt.native_handle or ''))
    return tuple(rows)
