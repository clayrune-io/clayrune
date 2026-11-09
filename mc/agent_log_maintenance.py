"""Server maintenance uses the same ownership rules as offline tools."""
from typing import Any

from mc.agent_log_file import claim_rows, merge_rows


def snapshot(project_id: str) -> list[dict[str, Any]]:
    from mc.blueprints import agent_routes as ar

    with ar.get_manager(project_id).lock:
        return ar._update_agent_log(project_id, claim_rows)


def commit(project_id: str, before: list[dict], after: list[dict]) -> int:
    from mc.blueprints import agent_routes as ar

    with ar.get_manager(project_id).lock:
        return ar._update_agent_log(project_id, lambda rows: merge_rows(rows, before, after))
