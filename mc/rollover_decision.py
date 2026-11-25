"""Snapshot rollover inputs at the trigger and append them to the audit log."""

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mc import state
from mc.core import _log
from mc.rollover_model_table import canonical_model_id
from mc.rollover_request import call_facts
from mc.rollover_threshold import _character_ref, inputs_for_session, threshold_with_reason

_APPEND_LOCK = threading.Lock()


@dataclass(frozen=True)
class Decision:
    fields: dict[str, Any]
    private: bool

    @property
    def crosses_threshold(self) -> bool:
        threshold = self.fields['threshold']
        tokens = self.fields['context_tokens']
        return bool(threshold and threshold > 0 and isinstance(tokens, (int, float))
                    and tokens >= threshold)


def capture(
    source: Any, trigger_type: str | None, trigger_reason: str | None,
    detail: int | float | None, *, native_id: str | None = None,
) -> Decision:
    session = source if isinstance(source, dict) else {}
    tokens = detail if trigger_reason in ('tokens', None) else None
    observed, effort = call_facts(session, tokens)
    requested = session.get('model', session.get('agent_model')) or None
    observed = observed or session.get('observed_model')
    inputs = dict(session, observed_model=observed, effective_effort=effort)
    threshold, reason = (threshold_with_reason(inputs, state.CONFIG)
                         if trigger_reason in ('tokens', None) else (None, None))
    model_inputs = inputs_for_session(inputs)
    floor = session.get('_mt_fresh_floor')
    status = session.get('status')
    opening = None
    if status in ('idle', 'completed'):
        opening = False
    elif session.get('_mt_turn_prompt'):
        opening = True
    return Decision({
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'project_id': session.get('project_id'),
        'session_id': session.get('session_id'),
        'claude_session_id': native_id or session.get('claude_session_id'),
        'threshold': threshold,
        'threshold_reason': reason,
        'roll_number': None,
        'epoch': int(time.time()),
        'character': _character_ref(source) or None,
        'model': observed or requested,
        'requested_model': requested,
        'canonical_model': canonical_model_id(observed or requested),
        'provider': session.get('provider') or None,
        'effective_effort': effort,
        'fresh_floor_tokens': floor,
        'working_room_tokens': threshold - floor if floor is not None and threshold else None,
        'mean_growth_tokens': model_inputs['mean_growth_tokens'] if model_inputs else None,
        'growth_source': model_inputs['growth_source'] if model_inputs else None,
        'context_tokens': tokens,
        'transcript_bytes': detail if trigger_reason == 'bytes' else None,
        'trigger_type': trigger_type,
        'trigger_reason': trigger_reason,
        'opening_prompt_unanswered': opening,
        'recent_tools': len(session.get('_mt_recent_tools') or []),
        'journal_paths': len(session.get('_mt_journal_paths') or []),
        'tool_result_heads': len(session.get('_mt_tool_result_heads') or []),
        'unanswered_msgs': len(session.get('_mt_unanswered_msgs') or []) if status else None,
        'scribe_flushed': None,
    }, bool(session.get('incognito')))


def append_row(
    directory: Path, decision: Decision, extras: dict[str, Any],
) -> None:
    if decision.private:
        return
    row = dict(decision.fields, **extras)
    project = row.get('project_id') or 'unknown'
    safe = ''.join(char for char in project if char.isalnum() or char in '-_') or 'unknown'
    try:
        line = json.dumps(row) + '\n'
        with _APPEND_LOCK:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f'{safe}.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(line)
    except (OSError, TypeError, ValueError) as error:
        _log(f'[rollover] log write failed: {error}')
