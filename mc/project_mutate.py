"""Read-modify-write one project record without a stale copy (MC-1179 P55).

`load_project()` ... `save_project()` leaves a window in which another writer
can change the same record. The lost-update guard in `save_project` restores
rows that writer ADDED; it cannot tell that a row it changed or deleted is
newer than this caller's copy, so the stale copy of that row wins (a closed
backlog item reopens, a deleted one returns).

`mutate_project` closes the window for writers that change a field or two: it
takes the project's write lock, reads the record FRESH, lets `fn` change it,
and saves before releasing. The copy `fn` sees is the one on disk now, so no
other writer's change can be overwritten, and the lock is reentrant, so
`save_project`'s own lock and the guard still run unchanged.

`fn` must be quick: it runs under the lock, so it must not call a model, the
network or a subprocess. Do that first, then apply the result inside `fn`.
"""
from typing import Any, Callable, Optional


def mutate_project(project_id, fn: Callable[[dict], Any]) -> Optional[Any]:
    """Apply `fn(record)` to the freshly re-read record and save it.

    `fn` changes the record in place and may return a value, which this
    returns. Returns None, without calling `fn`, when the project has no
    usable record (absent, corrupt, or a refused id), so have `fn` return
    something truthy when the caller must tell "applied" from "no record". If
    `fn` raises, nothing is saved and the exception propagates.
    """
    # Late import: project_routes owns the lock, the load and the save, and it
    # imports this module.
    from mc.blueprints import project_routes as pr

    with pr._project_write_lock(project_id):
        record = pr.load_project(project_id)
        if record is None:
            return None
        result = fn(record)
        pr.save_project(project_id, record)
        return result
