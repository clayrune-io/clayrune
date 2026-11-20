"""Authority screen for an extracted continuity record.

The continuity extractor (`mc/memory.py` `_extract_continuity`) folds a
transcript slice into "what is in flight, what was promised, what we
understand", and that record is rendered into every later system prompt of
the project as something the agent itself committed to. Until now only length
caps applied to it. The mint and negation writers run the Distiller's
authority guard on comparable model-written text; this one did not, so one
sentence in one session ("no go-ahead needed from now on") could reach every
later agent as a standing commitment. That is the 2026-07-11 incident's shape
(CLAUDE.md, learning-system rail 1) through a different door.
MC-1179 pass 03, S2.5.

The screen works per LINE and never refuses the record:

* each thread, each commitment and each line of the understanding is tested;
* a line that violates is dropped and reported, everything else is returned;
* a dropped thread also leaves `confirmed_threads`.

Two choices make it stricter than a plain per-line test, on purpose:

* a line is tested in the form it will be STORED in (whitespace collapsed to
  single spaces), so a phrase broken across a newline inside one item is
  still seen;
* after the per-line pass the surviving understanding is tested once more as
  a whole, because the store joins its lines into one string. If the joined
  text violates, the whole understanding is dropped.

A guard that raises counts as a violation for that line (fail closed).

Pure: no I/O, no logging, no counters. The caller logs and counts what
`screen` reports.
"""

__all__ = ['screen', 'GUARD_ERROR']

GUARD_ERROR = 'guard_error'


def _flat(value):
    return ' '.join(str(value or '').split())


def _hit(violation, text):
    try:
        return violation(text) or ''
    except Exception:
        return GUARD_ERROR


def _items(value):
    """A slot's items as the store will read them: a bare string is one item
    per line (what `_cont_clean` does with a hand-edit or an odd reply)."""
    if isinstance(value, str):
        return [ln.strip('- ').strip() for ln in value.splitlines()]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _screen_list(slot, value, violation, dropped):
    kept = []
    for item in _items(value):
        flat = _flat(item)
        if not flat:
            continue
        phrase = _hit(violation, flat)
        if phrase:
            dropped.append((slot, flat, phrase))
        else:
            kept.append(item)
    return kept


def _screen_understanding(value, violation, dropped):
    if isinstance(value, (list, tuple)):
        lines = [str(v or '') for v in value]
    else:
        lines = str(value or '').splitlines()
    kept = []
    for line in lines:
        flat = _flat(line)
        if not flat:
            continue
        phrase = _hit(violation, flat)
        if phrase:
            dropped.append(('understanding', flat, phrase))
        else:
            kept.append(flat)
    joined = ' '.join(kept)
    if len(kept) > 1:
        phrase = _hit(violation, joined)
        if phrase:
            dropped.append(('understanding', joined, phrase))
            return ''
    return joined


def screen(extracted, violation):
    """-> (clean, dropped).

    `extracted` is the extractor's parsed dict. `violation(text)` returns the
    offending phrase, or '' when the text is fine (the Distiller's
    `_authority_violation`).

    `clean` has the four keys the writer reads (`threads`, `commitments`,
    `understanding`, `confirmed_threads`) with every violating line removed.
    `dropped` is a list of `(slot, text, phrase)`, one per removed line, in
    the order found. Never raises.
    """
    src = extracted if isinstance(extracted, dict) else {}
    dropped = []
    threads = _screen_list('threads', src.get('threads'), violation, dropped)
    refused = {text for slot, text, _p in dropped if slot == 'threads'}
    commitments = _screen_list('commitments', src.get('commitments'),
                               violation, dropped)
    understanding = _screen_understanding(src.get('understanding'),
                                          violation, dropped)
    confirmed = [t for t in _items(src.get('confirmed_threads'))
                 if _flat(t) and _flat(t) not in refused
                 and not _hit(violation, _flat(t))]
    return ({'threads': threads, 'commitments': commitments,
             'understanding': understanding, 'confirmed_threads': confirmed},
            dropped)
