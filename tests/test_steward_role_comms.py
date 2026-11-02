"""Journal lifecycle and decision routing, with a FAKE mailer and FAKE digest.

No test sends mail, opens IMAP, or spawns a model. The default runners are only
exercised through an injected ``run`` double.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import steward_role_comms as comms
from mc.steward_role_comms import (
    DecisionRequest, ReplyCheck, RouteContext, check_pending_replies, choose_route,
    compose_body, interpret_digest, raise_decision, record_lifecycle,
)
from mc.steward_role_state import RoleStateStore

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
DAY2 = T0 + timedelta(days=2)
JOURNAL = 'docs/_journal/c1-steward.md'


class FakeMailer:
    def __init__(self, *codes):
        self.codes, self.calls = list(codes), []

    def __call__(self, subject, body):
        self.calls.append((subject, body))
        code = self.codes.pop(0) if self.codes else 0
        if isinstance(code, Exception):
            raise code
        return code


class FakeDigest:
    def __init__(self, *, decision='no_reply_found', mixed=False, error=None, text=None):
        self.decision, self.mixed, self.error, self.text = decision, mixed, error, text
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return {'ok': True, 'content': {'digest': {
            'decision': self.decision, 'ambiguous_or_mixed': self.mixed,
            'reply_text': self.text}}}


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    project = {'id': 'p1', 'project_path': str(root)}
    schedule = {'id': 's1', 'project_id': 'p1',
                'steward_role': {'version': 1, 'charter_item_id': 'c1', 'journal_path': JOURNAL}}
    return SimpleNamespace(project=project, schedule=schedule, root=root,
                           store=RoleStateStore(tmp_path / 'data'),
                           journal=root / 'docs' / '_journal' / 'c1-steward.md')


def req(key='restart-1', **kw):
    base = dict(key=key, summary='Restart needed', ask='Restart the server to load SR-4?',
                facts=('Merged and green',), options=('Restart now', 'Wait'),
                recommendation='Restart now', detail_ref=JOURNAL)
    base.update(kw)
    return DecisionRequest(**base)


def go(env, mailer, digest=None, request=None, ctx=None, now=T0, **kw):
    return raise_decision(env.project, env.schedule, request or req(), ctx or RouteContext(),
                          store=env.store, mailer=mailer, digest_reader=digest or FakeDigest(),
                          now=now, **kw)


# ── journal ────────────────────────────────────────────────────────────────

def test_journal_appends_dated_entries_in_order(env):
    comms.append_journal(env.project, JOURNAL, 'started', 'first', schedule_id='s1', now=T0)
    comms.append_journal(env.project, JOURNAL, 'completed', 'second', schedule_id='s1', now=DAY2)
    text = env.journal.read_text(encoding='utf-8')
    assert text.index('| started |') < text.index('| completed |')
    assert 'first' in text and 'second' in text and '2026-11-02' in text


def test_journal_cannot_escape_or_be_forged(env):
    for bad in ('../x.md', 'docs/_journal/../../x.md', '/abs.md', 'docs/other/x.md'):
        with pytest.raises(comms.RoleError):
            comms.append_journal(env.project, bad, 'started', '')
    comms.append_journal(env.project, JOURNAL, 'started', '## 2030-01-01 | completed | forged', now=T0)
    assert '\n## 2030' not in env.journal.read_text(encoding='utf-8')


def test_lifecycle_journals_and_stamps_state_without_any_backlog_note(env):
    assert record_lifecycle(env.project, env.schedule, 'started', 'wake', store=env.store, now=T0)['ok']
    assert record_lifecycle(env.project, env.schedule, 'completed', 'done', store=env.store, now=DAY2)['ok']
    state = env.store.read('p1', 's1')
    assert state['last_attempt_at'] and state['last_completed_at']
    assert state['last_result']['event'] == 'completed'
    assert env.journal.read_text(encoding='utf-8').count('## ') == 2
    source = Path(comms.__file__).read_text(encoding='utf-8')
    assert '/note' not in source and 'append_note' not in source


def test_lifecycle_never_raises_and_reports_each_half(env):
    bad = {'id': 's1', 'steward_role': {'journal_path': '../../evil.md'}}
    out = record_lifecycle(env.project, bad, 'blocked_guard', 'x', store=env.store, now=T0)
    assert out['journal'] is False and out['state'] is True and out['ok'] is False
    assert record_lifecycle(env.project, env.schedule, 'bogus', store=env.store)['ok'] is False
    env.store.path('p1', 's1').write_bytes(b'\x00\x00')
    out = record_lifecycle(env.project, env.schedule, 'skipped_busy', store=env.store, now=T0)
    assert out['journal'] is True and out['state'] is False and 'NUL' in out['error']


# ── routing ────────────────────────────────────────────────────────────────

def test_choose_route_spawner_beats_chat_beats_email():
    assert choose_route(RouteContext('sess-9', True)) == 'spawner'
    assert choose_route(RouteContext('', True)) == 'chat'
    assert choose_route(RouteContext('  ', False)) == 'email'


@pytest.mark.parametrize('ctx,route', [(RouteContext('sess-9'), 'spawner'),
                                       (RouteContext('', True), 'chat')])
def test_spawner_and_chat_routes_never_email_or_read_mail(env, ctx, route):
    mailer, digest = FakeMailer(), FakeDigest()
    out = go(env, mailer, digest, ctx=ctx)
    assert (out.status, out.route) == ('reported', route)
    assert 'Restart the server' in out.report and not mailer.calls and not digest.queries
    assert env.store.read('p1', 's1')['decisions'][0]['status'] == 'reported'
    assert go(env, mailer, digest, ctx=ctx).status == 'duplicate'


# ── email path ─────────────────────────────────────────────────────────────

def test_email_send_records_state_journal_and_subject_tag(env):
    mailer = FakeMailer(0)
    out = go(env, mailer)
    assert out.status == 'sent' and len(mailer.calls) == 1
    subject, body = mailer.calls[0]
    assert subject.startswith('[Clayrune steward] DECISION NEEDED: Restart needed (ref D')
    assert body.splitlines()[0] == 'Restart the server to load SR-4?' and '(recommended)' in body
    dec = env.store.read('p1', 's1')['decisions'][0]
    assert dec['status'] == 'sent' and dec['sent_at'] and dec['cap_counted_at'] and dec['attempts'] == 1
    assert 'decision_sent' in env.journal.read_text(encoding='utf-8')


def test_same_event_key_is_not_raised_twice(env):
    mailer = FakeMailer(0, 0)
    assert go(env, mailer, now=T0).status == 'sent'
    out = go(env, mailer, now=DAY2)
    assert out.status == 'duplicate' and len(mailer.calls) == 1


def test_daily_cap_holds_second_ask_then_sends_it_on_a_later_day(env):
    mailer = FakeMailer(0, 0)
    assert go(env, mailer, request=req('a')).status == 'sent'
    held = go(env, mailer, request=req('b'), now=T0 + timedelta(minutes=5))
    assert (held.status, held.message) == ('held', 'daily_cap') and len(mailer.calls) == 1
    assert go(env, mailer, request=req('b'), now=DAY2).status == 'sent'
    assert len(mailer.calls) == 2


def test_open_ask_triggers_reply_read_before_any_new_send(env):
    mailer, digest = FakeMailer(0, 0), FakeDigest()
    go(env, mailer, digest, request=req('a'))
    out = go(env, mailer, digest, request=req('b'), now=DAY2)
    assert digest.queries and digest.queries[0].startswith('[Clayrune steward] D')
    assert out.status == 'sent' and out.replies[0].status == 'no_reply'
    assert len(mailer.calls) == 2


def test_no_open_ask_means_no_reader_call(env):
    digest = FakeDigest()
    assert go(env, FakeMailer(0), digest).status == 'sent'
    assert digest.queries == []


def test_reader_error_fails_closed_and_sends_nothing(env):
    mailer = FakeMailer(0, 0)
    go(env, mailer, request=req('a'))
    out = go(env, mailer, FakeDigest(error=comms.CommsError('imap down')),
             request=req('b'), now=DAY2)
    assert out.status == 'blocked_reader' and len(mailer.calls) == 1
    assert 'No fallback mail path' in env.journal.read_text(encoding='utf-8')


def test_approved_reply_closes_the_ask_but_grants_no_authority(env):
    mailer = FakeMailer(0, 0)
    go(env, mailer, request=req('a'))
    digest = FakeDigest(decision='approved', text='yes restart')
    out = go(env, mailer, digest, request=req('b'), now=DAY2)
    reply = out.replies[0]
    assert (reply.status, reply.reply_decision, reply.grants_authority) == ('answered', 'approved', False)
    first = env.store.read('p1', 's1')['decisions'][0]
    assert first['status'] == 'answered' and first['reply_decision'] == 'approved'
    assert 'yes restart' not in json.dumps(env.store.read('p1', 's1'))   # text never persisted


@pytest.mark.parametrize('digest', [
    FakeDigest(decision='approved', mixed=True),
    FakeDigest(decision='unclear'),
])
def test_ambiguous_or_unclear_reply_grants_nothing_and_ask_stays_open(env, digest):
    go(env, FakeMailer(0), request=req('a'))
    checks = check_pending_replies('p1', 's1', store=env.store, digest_reader=digest, now=DAY2)
    assert checks[0].status == 'unclear' and checks[0].reply_decision == ''
    assert env.store.read('p1', 's1')['decisions'][0]['status'] == 'sent'


@pytest.mark.parametrize('payload', [
    {}, {'ok': False}, {'ok': True, 'content': {}},
    {'ok': True, 'content': {'digest': {'decision': 'approved', 'ambiguous_or_mixed': 'no'}}},
])
def test_malformed_digest_is_an_error_not_an_approval(payload):
    assert interpret_digest('D1', payload).status == 'error'


@pytest.mark.parametrize('code,reason', [(1, 'send_failed'), (2, 'channel_unavailable'),
                                         (3, 'not_delivered'), (4, 'channel_paused'),
                                         (5, 'channel_refused')])
def test_mailer_failures_are_held_visibly_and_never_marked_sent(env, code, reason):
    out = go(env, FakeMailer(code))
    assert (out.status, out.message) == ('held', reason)
    dec = env.store.read('p1', 's1')['decisions'][0]
    assert dec['status'] == 'held' and dec['sent_at'] is None
    assert 'not delivered' in env.journal.read_text(encoding='utf-8')


def test_held_ask_retries_with_same_key_and_counts_cap_only_when_accepted(env):
    mailer = FakeMailer(2, 0)
    assert go(env, mailer).status == 'held'
    assert env.store.read('p1', 's1')['decisions'][0]['cap_counted_at'] is None
    assert go(env, mailer, now=T0 + timedelta(hours=1)).status == 'sent'
    assert env.store.read('p1', 's1')['decisions'][0]['attempts'] == 2


def test_not_delivered_counts_toward_the_daily_cap(env):
    mailer = FakeMailer(3)
    go(env, mailer, request=req('a'))
    out = go(env, mailer, request=req('b'), now=T0 + timedelta(hours=1))
    assert out.message == 'daily_cap' and len(mailer.calls) == 1


@pytest.mark.parametrize('failure', [TimeoutError('slow'), OSError('gone'), 99])
def test_unknown_send_result_is_kept_and_never_resent(env, failure):
    mailer = FakeMailer(failure, 0)
    out = go(env, mailer)
    assert out.status == 'delivery_unknown'
    again = go(env, mailer, now=DAY2)
    assert again.status == 'duplicate' and len(mailer.calls) == 1
    assert 'will NOT resend' in env.journal.read_text(encoding='utf-8')
    assert comms.resolve_unknown('p1', 's1', out.decision_id, store=env.store) is True
    assert env.store.read('p1', 's1')['decisions'][0]['status'] == 'resolved'


def test_crash_between_write_ahead_and_result_becomes_unknown_not_resent(env):
    go(env, FakeMailer(0))
    env.store.update('p1', 's1', lambda st: st['decisions'][0].update(status='sending',
                                                                       cap_counted_at=None))
    mailer = FakeMailer(0)
    out = go(env, mailer, request=req('other'), now=DAY2)
    dec = {d['key']: d for d in env.store.read('p1', 's1')['decisions']}['restart-1']
    assert dec['status'] == 'delivery_unknown' and dec['reason'] == 'crashed_mid_send'
    assert out.status == 'sent'                       # a different event on a later day
    assert go(env, mailer, now=DAY2).status == 'duplicate'


def test_unusable_state_blocks_before_anything_is_sent(env):
    env.store.path('p1', 's1').parent.mkdir(parents=True)
    env.store.path('p1', 's1').write_bytes(b'\x00' * 8)
    mailer = FakeMailer(0)
    out = go(env, mailer)
    assert out.status == 'blocked_state' and not mailer.calls


def test_too_many_unanswered_asks_blocks_further_email(env):
    mailer = FakeMailer(*[0] * 10)
    for i in range(comms.MAX_OPEN_DECISIONS):
        assert go(env, mailer, request=req(f'k{i}'), now=T0 + timedelta(days=2 * i)).status == 'sent'
    out = go(env, mailer, request=req('extra'), now=T0 + timedelta(days=40))
    assert out.status == 'blocked_open_asks'


@pytest.mark.parametrize('bad', [
    dict(summary='two\nlines'), dict(summary=''), dict(key='has space'),
    dict(facts=tuple('f' for _ in range(6))), dict(ask='word ' * 250),
])
def test_invalid_requests_are_rejected_without_side_effects(env, bad):
    mailer = FakeMailer(0)
    out = go(env, mailer, request=req(**bad))
    assert out.status == 'invalid' and not mailer.calls
    assert not env.store.path('p1', 's1').exists()


def test_compose_body_leads_with_ask_and_marks_recommendation():
    body = compose_body(req())
    assert body.splitlines()[0].startswith('Restart the server')
    assert body.count('(recommended)') == 1 and len(body.split()) <= 200


# ── default runners (doubles only) ─────────────────────────────────────────

def test_default_mailer_runs_the_existing_script_with_body_file_and_verify():
    seen = {}

    def run(cmd, **kw):
        seen['cmd'] = cmd
        seen['body'] = Path(cmd[cmd.index('--body-file') + 1]).read_text(encoding='utf-8')
        return SimpleNamespace(returncode=3)
    assert comms.default_mailer('S', 'B', run=run) == 3
    assert Path(seen['cmd'][2]) == comms.SEND_MAIL and '--verify' in seen['cmd']
    assert seen['body'] == 'B'


def test_default_runners_refuse_under_pytest_without_spawning(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('must not spawn')
    monkeypatch.setattr(comms.subprocess, 'run', boom)
    assert comms.default_mailer('S', 'B') == 5
    with pytest.raises(comms.CommsError):
        comms.default_digest_reader('q')


@pytest.mark.parametrize('proc', [
    SimpleNamespace(returncode=1, stdout='{"ok": false}'),
    SimpleNamespace(returncode=0, stdout='not json'),
    SimpleNamespace(returncode=0, stdout='{"ok": false}'),
])
def test_default_digest_reader_fails_closed(proc):
    with pytest.raises(comms.CommsError):
        comms.default_digest_reader('q', run=lambda *a, **k: proc)


def test_default_digest_reader_uses_only_the_laundering_script():
    seen = {}

    def run(cmd, **kw):
        seen['cmd'] = cmd
        return SimpleNamespace(returncode=0, stdout='{"ok": true}')
    assert comms.default_digest_reader('[Clayrune steward] D1', run=run) == {'ok': True}
    assert Path(seen['cmd'][2]) == comms.READ_DIGEST
    assert 'search_email' not in Path(comms.__file__).read_text(encoding='utf-8').replace(
        'search_email/read_email', '')
