"""Confirm lost delegation acknowledgments from parent Claude user turns.

Runs in the delivery loop, never on a status poll. A five-minute gate covers
even failed/missing transcript reads. Each directory is scanned once for all
parents, skipping files older than its oldest candidate. Shared project dirs
require a known provider id or the parent's injected session identity.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from mc.agent_runtime import ClaudeRuntime
from mc.core import _log
from mc.delegation_delivery import DeliveryStore

INTERVAL_SECONDS = 300.0
GroupKey = tuple[str, str]
HeaderIndex = dict[str, list[tuple[GroupKey, str, float]]]


def _markers(key: GroupKey) -> tuple[str, str]:
    return (f'/agent/{key[1]}/job', f'/floor/figure/{key[1]}/name')


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


def _scan_file(path: Path, headers: HeaderIndex,
               markers: dict[str, set[GroupKey]], owned: set[GroupKey]) -> set[tuple[str, str]]:
    """Read once for every parent sharing a directory; retain only owned matches."""
    found: set[tuple[GroupKey, str]] = set()
    try:
        with path.open('rb') as stream:
            for line in stream:
                # Byte prefilter only; JSON user/text/timestamp checks below
                # remain authoritative. No per-parent or per-header file reads.
                if b'[dispatched agent finished]' not in line and b'/agent/' not in line and b'/floor/figure/' not in line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue  # a CLI write may leave the last line incomplete
                if not isinstance(record, dict):
                    continue
                texts = _user_text(record)
                for marker, keys in markers.items():
                    if any(marker in t for t in texts):
                        owned.update(keys)
                candidates = [entry for text in texts for whole_line in text.splitlines()
                              for entry in headers.get(whole_line, [])]
                if not candidates:
                    continue
                try:
                    stamp = datetime.fromisoformat(record.get('timestamp', '').replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        continue
                    when = stamp.timestamp()
                except (TypeError, ValueError, AttributeError):
                    continue
                for key, event_id, created_at in candidates:
                    if when > created_at:
                        found.add((key, event_id))
        return {(key[0], event_id) for key, event_id in found if key in owned}
    except (OSError, UnicodeError) as exc:
        _log(f'[delegation-reconcile] transcript read failed ({path.name}): {exc}', flush=True)
        return set()


def transcript_matches(path: Path, rows: list[dict], *, owned: bool,
                       parent: str) -> set[str]:
    """Exact callback whole line in newer parent user text, including prefixes."""
    key = ('', parent)
    headers: HeaderIndex = {}
    for row in rows:
        headers.setdefault(row['header'], []).append((key, row['event_id'], row['created_at']))
    matches = _scan_file(path, headers, {m: {key} for m in _markers(key)}, {key} if owned else set())
    return {event_id for _, event_id in matches}


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
        result = self.scan_rows(rows, sessions, load_project, load_log)
        changed = 0
        for pid, event_id in result['matches']:
            try:
                changed += int(store.resolve_report(event_id, pid, confirmed=True))
            except ValueError:
                continue  # retry/handle/claim raced the scan; never overwrite it
        return changed

    def scan_rows(self, rows: list[dict], sessions: Mapping[str, Any],
                  load_project: Callable, load_log: Callable) -> dict[str, Any]:
        """Pure read-only scan shared by delivery and the dry-run entry point."""
        started = time.perf_counter()
        groups: dict[GroupKey, list[dict]] = {}
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row['project_id'], {'parked': 0, 'eligible': 0, 'matched': 0})['parked'] += 1
            try:
                payload = json.loads(row['payload'])['payload']
                header = payload['message'].split('\n', 1)[0]
                if not header.startswith('[dispatched agent finished] '):
                    continue
            except (TypeError, KeyError, ValueError, AttributeError):
                continue
            row['header'] = header
            counts[row['project_id']]['eligible'] += 1
            groups.setdefault((row['project_id'], row['parent_session_id']), []).append(row)
        projects = {pid: load_project(pid) or {} for pid in {pid for pid, _ in groups}}
        logs = {pid: load_log(pid) for pid in projects}
        known_by_group: dict[GroupKey, set[str]] = {}
        directories: dict[Path, dict[GroupKey, bool]] = {}
        for pid, parent in groups:
            project = projects[pid]
            pp = project.get('project_path')
            if not pp:
                continue
            identities = [s for s in logs[pid] if s.get('session_id') == parent]
            live = sessions.get(parent)
            if isinstance(live, dict):
                identities.append(live)
            known = {str(s.get('claude_session_id') or '') for s in identities}
            known.update(str(c) for s in identities for c in s.get('claude_session_ids', []))
            known_by_group[(pid, parent)] = known
            dedicated = Path(pp) / '.clayrune' / 'agents' / parent
            for cwd, owned in [(pp, False), (str(dedicated), True)]:
                for encoded in ClaudeRuntime._encoded_dir_candidates(cwd):
                    directory = self.projects_home / encoded
                    owners = directories.setdefault(directory, {})
                    owners[(pid, parent)] = owners.get((pid, parent), False) or owned
        matches: set[tuple[str, str]] = set()
        scanned = skipped = 0
        for directory, owners in directories.items():
            headers: HeaderIndex = {}
            markers: dict[str, set[GroupKey]] = {}
            oldest = min(r['created_at'] for key in owners for r in groups[key])
            for key in owners:
                for row in groups[key]:
                    headers.setdefault(row['header'], []).append((key, row['event_id'], row['created_at']))
                for marker in _markers(key):
                    markers.setdefault(marker, set()).add(key)
            for path in directory.glob('*.jsonl'):
                try:
                    if path.stat().st_mtime < oldest:
                        skipped += 1
                        continue
                except OSError as exc:
                    _log(f'[delegation-reconcile] transcript stat failed ({path.name}): {exc}', flush=True)
                    continue
                owned = {key for key, dedicated in owners.items()
                         if dedicated or path.stem in known_by_group[key]}
                scanned += 1
                matches |= _scan_file(path, headers, markers, owned)
        for pid, _ in matches:
            counts[pid]['matched'] += 1
        return {'projects': counts, 'matches': sorted(matches), 'files_scanned': scanned,
                'files_skipped_mtime': skipped, 'scan_seconds': time.perf_counter() - started}


def dry_run(database: Path, data_dir: Path, *, projects_home: Path | None = None) -> dict[str, Any]:
    """Open the actual DB read-only; never instantiate a DeliveryStore or resolve."""
    started = time.perf_counter()
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute(
            "SELECT event_id,project_id,parent_session_id,payload,created_at FROM inbox "
            "WHERE state IN ('uncertain','blocked')").fetchall()]
    finally:
        db.close()

    def read(name: str, default):
        path = data_dir / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding='utf-8'))

    result = TranscriptReconciler(projects_home=projects_home).scan_rows(
        rows, {}, lambda pid: read(f'{pid}.json', {}),
        lambda pid: read(f'{pid}_agent_log.json', []))
    result.update({'dry_run': True, 'tick_seconds': time.perf_counter() - started,
                   'ownership': 'dedicated directory, saved parent provider ids, or injected parent identity'})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Read-only delegation receipt reconciliation measurement')
    parser.add_argument('--dry-run', required=True, action='store_true')
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--projects-home', type=Path)
    args = parser.parse_args()
    print(json.dumps(dry_run(args.database, args.data_dir, projects_home=args.projects_home), indent=2))
