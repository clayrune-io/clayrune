"""Validate owner-mail answers against one immutable question form."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def revision(question: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(question, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def answers(text: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Exact labels/positions. Multiple questions require explicit Qn: lines.

    None means clarification, never an inferred approval. Free text stays text.
    Multi-select positions/labels are comma separated inside their own question.
    """
    if not questions:
        return None
    parts = [text]
    if len(questions) > 1:
        matches = list(re.finditer(r'(?m)^Q([1-9][0-9]*):[ \t]*(.*)$', text))
        if (len(matches) != len(questions) or text[:matches[0].start()].strip()
                or any(m.group(1) != str(i + 1) for i, m in enumerate(matches))):
            return None
        parts = [text[m.start(2):matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
                 for i, m in enumerate(matches)]
    result = []
    for index, (question, value) in enumerate(zip(questions, parts)):
        options = question.get('options') or []
        if not options:
            result.append({'question': index, 'text': value})
            continue
        labels = [o.get('label') if isinstance(o, dict) else o for o in options]
        if any(not isinstance(label, str) or not label for label in labels):
            return None
        values = value.split(',') if question.get('multiSelect') else [value]
        picked = []
        for item in values:
            item = item.strip()
            candidates = [i for i, label in enumerate(labels)
                          if item.casefold() == str(label).casefold() or item == str(i + 1)]
            if len(candidates) != 1 or candidates[0] in picked:
                return None
            picked.append(candidates[0])
        result.append({'question': index, 'options': [labels[i] for i in picked]})
    return result


def matching(session: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    return next((q for q in session.get('pending_questions', [])
                 if q.get('question_id') == snapshot.get('question_id')
                 and revision(q) == revision(snapshot)), None)


def consume(session: dict[str, Any], snapshot: dict[str, Any] | None, text: str) -> bool:
    """Only the matching form, after acceptance; never touch plan approval."""
    match = matching(session, snapshot)
    if match is None or answers(text, match.get('questions') or []) is None:
        return False
    session['pending_questions'] = [q for q in session.get('pending_questions', []) if q is not match]
    session['waiting_for_question'] = bool(session['pending_questions'])
    return True
