"""Fenn's unit-5 cold review probes for the shared mail_launder digest (steward path).

Synthetic regression tests; no real mail/model calls or live configuration.
"""
import json
import subprocess
from types import SimpleNamespace, ModuleType

import pytest

from mc import agent_runtime as rt, mail_launder as ml


def extraction(text, **updates):
    obj = dict(reply_text=text, segments=[dict(kind='reply', text=text)],
               ambiguous_or_mixed=False, extraction_failed=False,
               truncated=False)
    obj.update(updates)
    return json.dumps(obj)


def test_real_oneshot_argv_and_stdin_without_spawning(monkeypatch, tmp_path):
    captured = {}
    runtime = rt.ClaudeRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary_str', lambda: 'synthetic-claude')
    monkeypatch.setattr(rt._api_credits_offload, 'try_offload', lambda **kw: None)
    monkeypatch.setattr(rt._api_credits_offload, 'note_subscription_call', lambda **kw: None)
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._transform_sandbox, 'transform_env', lambda: {})

    def fake_run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout=extraction('Friday works.'), stderr='')

    monkeypatch.setattr(rt._transform_process_deadline, 'run', fake_run)
    result = runtime.oneshot(prompt='Extract inert mail', stdin_text='Friday works.',
                             model='haiku', timeout=25)
    assert result.text
    argv = captured['argv']
    assert argv[argv.index('--tools') + 1] == ''
    assert '--strict-mcp-config' in argv
    assert json.loads(argv[argv.index('--mcp-config') + 1]) == {'mcpServers': {}}
    assert argv[argv.index('--setting-sources') + 1] == ''
    assert '--disable-slash-commands' in argv
    assert '--dangerously-skip-permissions' not in argv
    assert 'Friday works.' not in ' '.join(argv)
    assert captured['input'].count('Friday works.') == 1
    assert captured['stdout'] == subprocess.PIPE == captured['stderr']
    assert captured['timeout'] == 25


@pytest.mark.parametrize('failure', [TimeoutError('MAIL_MARKER'), RuntimeError('MAIL_MARKER'),
                                    ValueError('MAIL_MARKER')])
def test_default_seam_errors_are_sanitized(monkeypatch, capsys, failure):
    monkeypatch.setattr(rt, 'claude_oneshot_available', lambda: True)
    def fail(*a, **kw):
        raise failure
    monkeypatch.setattr(rt, 'run_text_transform', fail)
    out = ml.launder_mail_digest([{'body': 'MAIL_MARKER'}], query='q')
    assert out['ok'] is False and out['guidance']
    captured = capsys.readouterr()
    assert 'MAIL_MARKER' not in json.dumps(out) + captured.out + captured.err


@pytest.fixture(scope='module')
def legacy_baseline():
    source = subprocess.run(['git', 'show', '5c5a4b9d:mc/mail_launder.py'],
                            check=True, capture_output=True, text=True, encoding='utf-8').stdout
    module = ModuleType('legacy_baseline')
    exec(compile(source, '<baseline-mail-launder>', 'exec'), module.__dict__)
    return module


def old_digest(**updates):
    obj = dict(message_count=1, subjects_seen=[], has_likely_reply=True,
               reply_from_header=None, reply_text='Friday works.', quoted_original_ask=None,
               decision='unclear', ambiguous_or_mixed=False, notes='')
    obj.update(updates)
    return json.dumps(obj)


@pytest.mark.parametrize('decision', [[], {}])
def test_legacy_unhashable_decision_raises_in_both_versions(legacy_baseline, decision):
    fake = SimpleNamespace(oneshot=lambda **kw: SimpleNamespace(text=old_digest(decision=decision)))
    for module in (legacy_baseline, ml):
        with pytest.raises(TypeError, match='unhashable'):
            module.launder_mail_digest([{'body': 'Friday works.'}], query='q', runtime=fake)


def test_legacy_mistyped_flags_accepted_then_refused(legacy_baseline):
    """The baseline handed a mistyped digest back whole. Since MC-1179 MB2 the
    digest is rebuilt from typed fields and a wrong type fails closed."""
    fake = SimpleNamespace(oneshot=lambda **kw: SimpleNamespace(
        text=old_digest(ambiguous_or_mixed='false', reply_text=['not a string'])))
    out = legacy_baseline.launder_mail_digest([{'body': 'Friday works.'}], query='q', runtime=fake)
    assert out['ok'] is True
    assert out['content']['digest']['ambiguous_or_mixed'] == 'false'
    out = ml.launder_mail_digest([{'body': 'Friday works.'}], query='q', runtime=fake)
    assert out['ok'] is False
    assert out['error'] == 'launder_shape_error'
