"""A note draft that never reached 'ready' must not strand silently or send late.

Incident 2026-11-15: intent 7223f4ee stayed in 'draft' after MailWake.bind raised
(bind_thread had already written its row); the note route answered 503 in under a
second and send_mail.py delivered the mail by Gmail. The draft must be abandoned
observably, and must never become sendable afterwards.
"""
import pytest

from test_agent_mail_outbound import out, kit, setup, env, prepare  # noqa: F401
from mc import agent_mail_outbound as outbound_mod
from mc.agent_mail_target import MailHeld
from mc.agent_mail_wake import MailWake


def _rows(out):
    with out.store._read() as db:
        return [dict(r) for r in db.execute('SELECT * FROM mail_outbound_intents ORDER BY created')]


def _insert_orphan(out, monkeypatch):
    """A draft whose creating call died after bind_thread wrote its row (crash shape)."""
    def crash(self, *a, **kw):
        raise SystemExit('process died mid-prepare')
    monkeypatch.setattr(MailWake, 'bind', crash)
    monkeypatch.setattr(outbound_mod.Outbound, '_abandon', lambda self, row, reason: None)
    with pytest.raises(SystemExit):
        prepare(out)
    monkeypatch.undo()
    [row] = _rows(out)
    assert row['state'] == 'draft' and row['payload'] is not None
    return row


def test_mail_held_in_bind_abandons_with_its_reason_and_logs(out, monkeypatch):
    logged = []
    monkeypatch.setattr(outbound_mod, '_log', lambda msg, **kw: logged.append(msg))
    monkeypatch.setattr(MailWake, 'bind', lambda *a, **kw: (_ for _ in ()).throw(MailHeld('rollover_required')))
    with pytest.raises(MailHeld):
        prepare(out)
    [row] = _rows(out)
    assert (row['state'], row['reason'], row['payload']) == ('abandoned', 'rollover_required', None)
    assert '[agent-mail] outbound abandoned: rollover_required' in logged
    assert out.outbound.send(row['intent_id'])['state'] == 'abandoned'
    assert out.recording.calls == []


def test_young_orphan_is_not_resumed_by_a_same_request_retry(out, monkeypatch):
    row = _insert_orphan(out, monkeypatch)
    again = prepare(out)
    assert again['intent_id'] == row['intent_id'] and again['state'] == 'draft'
    assert out.outbound.send(row['intent_id'])['state'] == 'draft'
    assert out.recording.calls == []


def test_orphan_draft_is_swept_to_abandoned_after_the_window(out, monkeypatch):
    row = _insert_orphan(out, monkeypatch)
    out.kit.now += (outbound_mod.ORPHAN_DRAFT_SECONDS + 1) * 1000
    status = out.outbound.status(row['intent_id'])
    assert (status['state'], status['reason']) == ('abandoned', 'draft_orphaned')
    [after] = _rows(out)
    assert after['payload'] is None
    with out.store._read() as db:
        assert db.execute('SELECT revoked_at FROM thread_bindings WHERE thread_hash=?',
                          (row['thread_hash'],)).fetchone()[0] is not None
    assert prepare(out)['state'] == 'abandoned'
    assert out.recording.calls == []


def test_abandoned_reason_survives_thread_expiry(out, monkeypatch):
    monkeypatch.setattr(MailWake, 'bind', lambda *a, **kw: (_ for _ in ()).throw(MailHeld('session_busy')))
    with pytest.raises(MailHeld):
        prepare(out)
    [row] = _rows(out)
    out.kit.now += int(out.store.config.thread_ttl_seconds * 1000) + 1000
    status = out.outbound.status(row['intent_id'])
    assert (status['state'], status['reason']) == ('abandoned', 'session_busy')


def test_question_draft_stays_resumable(out, monkeypatch):
    """Questions have no fallback channel, so a held question is retried, not abandoned."""
    prepare(out)
    [note] = _rows(out)  # copy a real install/generation/flow/epoch
    with out.store._tx() as db:
        db.execute("INSERT INTO mail_outbound_intents (intent_id,request_key,request_digest,project_id,"
                   "session_id,install,generation,flow,epoch,thread_id,thread_hash,question_revision,payload,"
                   "digest,state,reason,created,updated,expires) VALUES ('q1','q_x','d','proj1','s',?,?,?,?,"
                   "'t_q1','h_q1','rev',x'00','d','draft','',?,?,?)",
                   (note['install'], note['generation'], note['flow'], note['epoch'],
                    note['created'], note['created'], note['expires']))
    out.kit.now += (outbound_mod.ORPHAN_DRAFT_SECONDS + 1) * 1000
    out.outbound.reconcile()
    row = [r for r in _rows(out) if r['intent_id'] == 'q1'][0]
    assert row['state'] == 'draft' and row['payload'] is not None
