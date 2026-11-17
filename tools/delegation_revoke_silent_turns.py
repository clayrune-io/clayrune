#!/usr/bin/env python3
"""Refuse the old agent-log completions that never owed their spawner a wake (MC-1196).

Before MC-1196 an agent-log row for a turn that finished with the spawner latch
already spent (a human or an agent follow-up that did not name `notify_session`)
carried a rebuilt `delegation_completion` and nothing said the turn owed no wake.
`DeliveryStore.recover_outbox` therefore treats each as a lost wake and would queue
it at the next boot. New rows carry `delegation_wake_not_owed`; this tool closes the
rows written before that, using the store's own `revoke_event`, which `enqueue`
already honours (a revoked event is refused, never queued).

A row is selected only when ALL of these hold; the rule is the evidence, not an id list:
  1. it is a recoverable row: has a spawner, is not incognito, finished
     (completed/error/stopped) and carries an exact `delegation_completion`;
  2. its event is in none of outbox, inbox, completion_sources, and is not already
     revoked. `_notify_agent_spawner` commits a completion_sources row before it
     enqueues, so an absent event never reached the enqueue code at all;
  3. a server log carries `[notify-spawner] callback suppressed for <child> ... for
     turn <N>` for that child and that turn: `_maybe_notify_spawner` found the latch
     already spent and sent nothing, on purpose.
A row that fails (3) is reported as UNPROVEN and left alone: it may be a wake that
was owed and lost, and recovery should queue it.

Dry run by default. `--apply` revokes. Re-runnable: a revoked event is not selected
again. Run it BEFORE the server restart that first runs the recovery.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mc.delegation_delivery import (WAKE_NOT_OWED_KEY, DeliveryStore,  # noqa: E402
                                    event_id_for_turn)

STORE_TABLES = ('outbox', 'inbox', 'completion_sources')
SUPPRESSED_RE = re.compile(
    rb'\[notify-spawner\] callback suppressed for (\w+): spawner latch '
    rb'_notify_session_sent already set for turn (\d+)')
AGENT_LOG_SUFFIX = '_agent_log.json'
REASON = ('turn finished with the spawner latch already spent (a follow-up that '
          'did not name notify_session); _maybe_notify_spawner suppressed it and '
          '_notify_agent_spawner never ran')


def suppressed_turns(log_paths: Iterable[Path]) -> dict[tuple[str, int], str]:
    """(child session, turn) -> the log file that recorded the suppression."""
    found: dict[tuple[str, int], str] = {}
    for path in log_paths:
        with open(path, 'rb') as fh:
            for line in fh:
                if b'callback suppressed for' not in line:
                    continue
                m = SUPPRESSED_RE.search(line)
                if m:
                    found.setdefault((m.group(1).decode(), int(m.group(2))), path.name)
    return found


def _recoverable(row: dict[str, Any]) -> bool:
    return bool((row.get('spawned_by_session_id') or '').strip()
                and (row.get('session_id') or '').strip()
                and not row.get('incognito')
                and row.get('status') in ('completed', 'error', 'stopped')
                and not row.get(WAKE_NOT_OWED_KEY)
                and isinstance(row.get('delegation_completion'), dict))


def _in_store(db: sqlite3.Connection, project_id: str, event_id: str) -> bool:
    for table in STORE_TABLES:
        if db.execute(f'SELECT 1 FROM {table} WHERE event_id=?', (event_id,)).fetchone():
            return True
    return db.execute('SELECT 1 FROM revocations WHERE identity=?',
                      (f'event:{project_id}:{event_id}',)).fetchone() is not None


def _open_ro(store_path: Path) -> sqlite3.Connection:
    if not store_path.is_file():
        raise SystemExit(f'store not found: {store_path}')
    return sqlite3.connect(f'file:{store_path.as_posix()}?mode=ro', uri=True)


def find_silent_turns(projects_dir: Path, store_path: Path, log_paths: list[Path]
                      ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(provable, unproven) rows that recovery would queue and the store lacks."""
    db = _open_ro(store_path)
    try:
        candidates = []
        for log_file in sorted(projects_dir.glob(f'*{AGENT_LOG_SUFFIX}')):
            project_id = log_file.name[:-len(AGENT_LOG_SUFFIX)]
            for row in json.loads(log_file.read_text(encoding='utf-8')):
                if not isinstance(row, dict) or not _recoverable(row):
                    continue
                child = row['session_id'].strip()
                turn = int(row.get('delegation_turn', 1))
                event_id = row['delegation_completion'].get('event_id') or \
                    event_id_for_turn(child, turn)
                if _in_store(db, project_id, event_id):
                    continue
                candidates.append({'project': project_id, 'event_id': event_id,
                                   'child': child, 'turn': turn,
                                   'parent': row['spawned_by_session_id'].strip()})
    finally:
        db.close()
    evidence = suppressed_turns(log_paths) if candidates else {}
    provable, unproven = [], []
    for cand in candidates:
        source = evidence.get((cand['child'], cand['turn']))
        if source:
            provable.append({**cand, 'reason': REASON, 'evidence': source})
        else:
            unproven.append(cand)
    return provable, unproven


def apply_revocations(store_path: Path, provable: list[dict[str, Any]]) -> int:
    """Revoke each selected event, re-checking right before that none of the
    three delivery tables holds it. Returns the number revoked."""
    store = DeliveryStore(store_path)
    revoked = 0
    for item in provable:
        db = _open_ro(store_path)
        try:
            present = _in_store(db, item['project'], item['event_id'])
        finally:
            db.close()
        if present:
            print(f"SKIPPED {item['event_id']}: now in the store or already revoked")
            continue
        store.revoke_event(item['project'], item['event_id'])
        revoked += 1
    return revoked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n\n')[0])
    ap.add_argument('--data-dir', default=str(ROOT / 'data'),
                    help='data directory holding projects/, logs/ and the delivery store')
    ap.add_argument('--store', help='delivery store (default: <data-dir>/delegation_delivery.sqlite3)')
    ap.add_argument('--log', action='append', default=[],
                    help='server log to read (repeatable; default: every <data-dir>/logs/*.log)')
    ap.add_argument('--apply', action='store_true', help='revoke the selected events (default: dry run)')
    args = ap.parse_args(argv)
    data_dir = Path(args.data_dir)
    store_path = Path(args.store) if args.store else data_dir / 'delegation_delivery.sqlite3'
    logs = [Path(p) for p in args.log] or sorted((data_dir / 'logs').glob('*.log'))
    provable, unproven = find_silent_turns(data_dir / 'projects', store_path, logs)
    for item in provable:
        print(f"{item['project']}\t{item['event_id']}\tchild={item['child']}\t"
              f"parent={item['parent']}\tturn={item['turn']}\t{item['reason']} "
              f"[log: {item['evidence']}]")
    for item in unproven:
        print(f"UNPROVEN (left alone, recovery will queue it)\t{item['project']}\t"
              f"{item['event_id']}\tchild={item['child']}\tparent={item['parent']}\tturn={item['turn']}")
    print(f"{len(provable)} provable, {len(unproven)} unproven, from {len(logs)} log file(s)")
    if not args.apply:
        print('dry run: nothing written. Re-run with --apply to revoke the provable rows.')
        return 0
    print(f"revoked {apply_revocations(store_path, provable)} event(s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
