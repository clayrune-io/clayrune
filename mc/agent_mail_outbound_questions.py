"""Question-channel adapter. Registration is Python-only and absent by default."""
from __future__ import annotations

from typing import Callable

from mc import state
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_questions import revision
from mc.core import _log

# Unit 7d owns a reviewed operational factory. No HTTP/config object can supply it.
factory: Callable[[], Outbound] | None = None


def selected(session: dict, question: dict) -> bool:
    key = revision(question)
    choices = session.setdefault('_offline_question_channels', {})
    preferred = choices.get(key) or ('agent_mail' if state.CONFIG.get('agent_mail_outbound_enabled') is True else 'legacy')
    if factory is not None:
        try:
            preferred = factory().question_channel(session['project_id'], session['session_id'], question, preferred)
        except Exception:
            # A missing channel receipt cannot authorize switching to SMTP.
            _log('[agent-mail] question channel held: ownership_unavailable', flush=True)
            preferred = 'agent_mail'
    choices[key] = preferred
    return choices[key] == 'agent_mail'


def deliver(session: dict, question: dict, project_name: str) -> dict[str, str]:
    """Called only after shared attendance grace. Never fall back to SMTP."""
    if factory is None or state.CONFIG.get('agent_mail_outbound_enabled') is not True:
        return {'state': 'blocked', 'reason': 'operational_adapter_unavailable'}
    # This renderer covers free text and multi-question answers; legacy's body
    # says free text cannot be answered by mail, which is false for this adapter.
    lines = ['Reply to continue the originating conversation.',
             'Replies are screened and cannot approve protected actions.', '']
    for n, item in enumerate(question.get('questions') or [], 1):
        lines.append(f"Q{n}. {item.get('question', '')}")
        for i, option in enumerate(item.get('options') or [], 1):
            label = option.get('label', '') if isinstance(option, dict) else str(option)
            desc = option.get('description', '') if isinstance(option, dict) else ''
            lines.append(f"  {i}. {label}" + (f": {desc}" if desc else ''))
    lines += ['', 'For several questions use Q1: answer, Q2: answer on separate lines.',
              'For multiple selections use comma-separated numbers or exact labels.']
    try:
        outbound = factory()
        # Only content and stable local id enter the interface. No legacy `to`.
        result = outbound.prepare(session['project_id'], session['session_id'], dict(
            request_id='q_' + revision(question),
            subject=f'Clayrune question: {project_name}'.encode()[:200].decode(errors='ignore'),
            body='\n'.join(lines), kind='question'), question=question)
        return outbound.send(result['intent_id'])
    except Exception:
        _log('[agent-mail] question delivery held: outbound_unavailable', flush=True)
        return {'state': 'blocked', 'reason': 'outbound_unavailable'}


def parsed(session: dict) -> None:
    """One narrow parser hook covers mc:question on every provider's reader."""
    if state.CONFIG.get('agent_mail_outbound_enabled') is not True or factory is None:
        return
    try:
        from mc.question_channel import on_question_raised
        on_question_raised(session)
    except Exception:
        _log('[agent-mail] question notification held: channel_unavailable', flush=True)
