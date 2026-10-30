"""Per-thread confirmation age, independent of an owner's rewrite time."""
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import re

MAX_UNCONFIRMED_AGE = timedelta(hours=48)
_STAMP = re.compile(r'\s*<!-- confirmed:([^>]*) -->$')


def split_thread(line: str) -> tuple[str, str]:
    match = _STAMP.search(line)
    if not match:
        return line, ''
    return line[:match.start()].rstrip(), match.group(1).strip()


def stored_thread(text: str, stamp: str) -> str:
    return f'{text} <!-- confirmed:{stamp} -->'


def age_label(stamp: str, now: str) -> str:
    """Unknown/invalid stamps fail visibly stale; never borrow the owner stamp."""
    try:
        confirmed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        current = datetime.fromisoformat(now.replace('Z', '+00:00'))
        confirmed = confirmed.replace(tzinfo=confirmed.tzinfo or timezone.utc)
        current = current.replace(tzinfo=current.tzinfo or timezone.utc)
    except (ValueError, TypeError):
        return 'unconfirmed since unknown (legacy/invalid timestamp; verify before acting)'
    if current - confirmed >= MAX_UNCONFIRMED_AGE or confirmed > current:
        return f'unconfirmed since {stamp} (stale; verify before acting)'
    return f'last confirmed {stamp}'


# A rewrite that paraphrases a thread is the SAME thread. Below this ratio the
# two texts are different work; at or above it the new wording inherits the old
# confirmation. 0.8 is Tobin's proposal from the 4ff2433bec0d review; see the
# replay numbers in docs/_journal/noitem-continuity-data-loss-2026-10-29.md.
REWORD_RATIO = 0.8


def _identity(text: str, slot: str) -> tuple[str, str]:
    """(slot, normalised text). The slot is part of the identity so a thread
    and a commitment with the same words are never the same item."""
    return slot, ' '.join(str(text or '').split()).casefold()


def match_reworded(incoming, known, *, slot: str = 'threads',
                   threshold: float = REWORD_RATIO) -> dict[str, str]:
    """{new_text: old_text} for incoming items that reword a known one.

    Verbatim matches are not returned (the caller already treats them as
    known) but they do consume their old item, so a near-duplicate of a thread that
    was kept verbatim cannot also inherit its stamp. Among the rest, the best
    ratio wins and one old item is inherited by at most one new item.
    Both lists are one slot: items from different slots never match.
    """
    taken = set(known) & set(incoming)  # verbatim keeps: the caller's "known"
    candidates = [n for n in incoming if n not in taken]
    pool = [k for k in known if k not in taken]
    pairs = []
    for i, new in enumerate(candidates):
        a = _identity(new, slot)[1]
        for j, old in enumerate(pool):
            b = _identity(old, slot)[1]
            if not a or not b:
                continue
            ratio = SequenceMatcher(None, a, b).ratio()
            if ratio >= threshold:
                pairs.append((-ratio, i, j, new, old))
    out: dict[str, str] = {}
    used_old: set[int] = set()
    for _, i, j, new, old in sorted(pairs):
        if new in out or j in used_old:
            continue
        out[new] = old
        used_old.add(j)
    return out
