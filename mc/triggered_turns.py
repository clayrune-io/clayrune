"""Who a chat's seed line is attributed to when nobody typed it.

The chat buffer records every user-role turn as ``> <label>: <message>``.
For a message Ron types, ``<label>`` is his configured name. A scheduled
dispatch's first prompt is not something he typed, so it carries the
``[scheduled run]`` label the scheduler's live append (scheduler_routes
``_scheduled_continue``) already used: the chat UI
(static/js/triggered-collapse.js) collapses a line with that label to a
one-line "Scheduled: …" row, and the Scribe habit extractor
(the habit classifier in mc/memory.py) skips a line whose label is not the
user's, so a timer's task text is no longer mistaken for Ron's own words.

Display attribution only: the text the agent receives is unchanged.
"""

SCHEDULED_LABEL = '[scheduled run]'


class UserTurnLine(str):
    """A rendered user prompt that keeps its role in the live chat buffer."""


def user_turn_line(label: str, text: str, *, framed: bool = True) -> UserTurnLine:
    rendered = f'> {label}: {text}'
    return UserTurnLine(f'\n{rendered}\n' if framed else rendered)


def seed_label(user_label: str, trigger_type: str) -> str:
    """Label for the ``> <label>: <task>`` seed line of a dispatch."""
    return SCHEDULED_LABEL if trigger_type == 'schedule' else user_label
