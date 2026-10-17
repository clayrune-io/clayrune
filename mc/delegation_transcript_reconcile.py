"""Confirm lost delegation acknowledgments from parent Claude user turns.

Runs in the delivery loop, never on a status poll. A five-minute gate covers
even failed/missing transcript reads. Parent worktree directories are scanned
in full, including rollovers absent from the agent log. Shared project dirs
require a known provider id or the parent's injected session identity.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from mc.agent_runtime import ClaudeRuntime
from mc.core import _log
from mc.delegation_delivery import DeliveryStore

INTERVAL_SECONDS = 300.0


def _user_text(record: dict) -> list[str]:
    message = record.get('message')
    if record.get('type') != 'user' or not isinstance(message, dict):
        return []
    content = message.get('content')
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b['text'] for b in content if isinstance(b, dict)
                and b.get('type') == 'text' and isinstance(b.get('text'), str)]
    return []


def transcript_matches(path: Path, rows: list[dict], *, owned: bool,
                       parent: str) -> set[str]:
    """Exact callback first line, strictly newer user timestamp; no tool results."""
    found: set[str] = set()
    needles = {json.dumps(row['header'], ensure_ascii=ascii_only)[1:-1]
               for row in rows for ascii_only in (True, False)}
    identity_markers = (f'/agent/{parent}/job', f'/floor/figure/{parent}/name')
    try:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                # Most JSONL bytes are tools/results. Avoid decoding those
                # large records; a candidate still needs full user/timestamp
                # validation below. Both JSON Unicode encodings are accepted.
                if not any(n in line for n in needles) and not any(m in line for m in identity_markers):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a CLI write may leave the last line incomplete
                if not isinstance(record, dict):
                    continue
                texts = _user_text(record)
                if not owned and any(m in t for m in identity_markers for t in texts):
                    owned = True
                try:
                    stamp = datetime.fromisoformat(record.get('timestamp', '').replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        continue
                    when = stamp.timestamp()
                except (TypeError, ValueError, AttributeError):
                    continue
                for row in rows:
                    if when > row['created_at'] and any(
                            t.split('\n', 1)[0] == row['header'] for t in texts):
                        found.add(row['event_id'])
        return found if owned else set()
    except (OSError, UnicodeError) as exc:
        _log(f'[delegation-reconcile] transcript read failed ({path.name}): {exc}', flush=True)
        return set()


class TranscriptReconciler:
    def __init__(self, *, projects_home: Path | None = None,
                 interval: float = INTERVAL_SECONDS):
        self.projects_home = projects_home or Path.home() / '.claude' / 'projects'
        self.interval = interval
        self._next_check = 0.0
        self._last_error = ''

    def tick(self, store: DeliveryStore, sessions: Mapping[str, Any],
             load_project: Callable, load_log: Callable) -> int:
        now = store.clock()
        if now < self._next_check:
            return 0
        self._next_check = now + self.interval
        try:
            changed = self._reconcile(store, sessions, load_project, load_log)
            self._last_error = ''
            return changed
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            if error != self._last_error:
                _log(f'[delegation-reconcile] scan failed: {error}', flush=True)
                self._last_error = error
            return 0

    def _reconcile(self, store: DeliveryStore, sessions: Mapping[str, Any],
                   load_project: Callable, load_log: Callable) -> int:
        with store._db() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT event_id,project_id,parent_session_id,payload,created_at FROM inbox "
                "WHERE state IN ('uncertain','blocked')").fetchall()]
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            try:
                payload = json.loads(row['payload'])['payload']
                header = payload['message'].split('\n', 1)[0]
                if not header.startswith('[dispatched agent finished] '):
                    continue
            except (TypeError, KeyError, ValueError, AttributeError):
                continue
            row['header'] = header
            groups.setdefault((row['project_id'], row['parent_session_id']), []).append(row)
        changed = 0
        for (pid, parent), pending in groups.items():
            project = load_project(pid) or {}
            pp = project.get('project_path')
            if not pp:
                continue
            identities = [s for s in load_log(pid) if s.get('session_id') == parent]
            live = sessions.get(parent)
            if isinstance(live, dict):
                identities.append(live)
            known = {str(s.get('claude_session_id') or '') for s in identities}
            known.update(str(c) for s in identities for c in s.get('claude_session_ids', []))
            dedicated = Path(pp) / '.clayrune' / 'agents' / parent
            dirs: dict[Path, bool] = {}
            for cwd, owned in [(pp, False), (str(dedicated), True)]:
                for encoded in ClaudeRuntime._encoded_dir_candidates(cwd):
                    directory = self.projects_home / encoded
                    dirs[directory] = dirs.get(directory, False) or owned
            matches: set[str] = set()
            for directory, owned in dirs.items():
                for path in directory.glob('*.jsonl'):
                    matches |= transcript_matches(path, pending,
                        owned=owned or path.stem in known, parent=parent)
            for event_id in matches:
                try:
                    changed += int(store.resolve_report(event_id, pid, confirmed=True))
                except ValueError:
                    continue  # retry/handle/claim raced the scan; never overwrite it
        return changed
