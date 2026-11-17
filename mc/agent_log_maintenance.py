"""Server maintenance uses the same ownership rules as offline tools."""
from copy import deepcopy
from typing import Any

from mc.agent_log_file import claim_rows, merge_rows, all_rows_claimed


def snapshot(project_id: str) -> list[dict[str, Any]]:
    from mc.blueprints import agent_routes as ar

    with ar.get_manager(project_id).lock:
        # MC-1179 AP34: a snapshot is a read. Writes to the log are atomic
        # replaces, so a lock-free read sees a whole file; only a row that
        # still lacks an attempt id needs the claim, and so the rewrite.
        # Any read trouble falls through to the update path, which keeps its
        # own retry and its refusal on an unreadable file.
        try:
            rows = ar._load_agent_log(project_id, strict=True)
        except OSError:
            rows = None
        if rows is not None and all_rows_claimed(rows):
            return deepcopy(rows)
        return ar._update_agent_log(project_id, claim_rows)


def commit(project_id: str, before: list[dict], after: list[dict]) -> int:
    from mc.blueprints import agent_routes as ar

    with ar.get_manager(project_id).lock:
        return ar._update_agent_log(project_id, lambda rows: merge_rows(rows, before, after))
