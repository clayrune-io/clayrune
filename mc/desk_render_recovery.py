"""Free recovery of linked clips and human acknowledgement of lost submission."""
from __future__ import annotations

from mc import desk_engines as eng
from mc.core import now_iso

SUBMISSION_HOLDS = ('submit_interrupted', 'submit_uncertain')


def needs_poll(record: dict) -> bool:
    return ((record.get('hold') or {}).get('kind') in SUBMISSION_HOLDS
            or (record.get('failure') or {}).get('kind') == 'submission_acknowledged')


def poll_linked(render_id: str, record: dict, *, unattended: bool) -> dict | None:
    # A missing scene is never submitted here. Normal job polling downloads
    # accepted clips, or settles a vendor-declared free failure's reservation.
    for child in record.get('children') or []:
        eng.poll(child['job_id'], unattended=unattended)
    with eng._lock:
        store = eng._read_store()
        current = store['renders'].get(render_id)
        if current is None:
            return None
        clips = [output for child in current.get('children') or []
                 for output in (store['jobs'].get(child['job_id']) or {}).get('outputs') or []]
        # Preserve status/hold/failure: acknowledgement can race with this GET.
        if current.get('clips') != clips:
            current.update(clips=clips, updated_at=now_iso())
            eng._write_store(store)
        return eng._public_render(current, store['jobs'])


def acknowledge(render_id: str) -> dict:
    eng.get_render(render_id)  # recover a lost pending/working worker first
    with eng._lock:
        store = eng._read_store()
        record = store['renders'].get(render_id)
        if record is None:
            raise eng.Refused('render_not_found', 'Render not found.', 404)
        if record['status'] != 'held' or (record.get('hold') or {}).get('kind') not in SUBMISSION_HOLDS:
            raise eng.Refused('submission_not_held', 'Only an interrupted or uncertain submission can be acknowledged.', 409)
        record.update(status='failed', submission_state='failed', hold=None, reserved_usd=0,
                      failure={'kind': 'submission_acknowledged',
                               'message': 'You acknowledged checking the engine dashboard. A new render is allowed. '
                                          'Existing clips and recorded costs are kept; no missing scenes were sent.'},
                      updated_at=now_iso())
        eng._write_store(store)
        return eng._public_render(record, store['jobs'])
