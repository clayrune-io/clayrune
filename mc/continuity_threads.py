"""Per-thread confirmation age, independent of an owner's rewrite time."""
from datetime import datetime, timedelta, timezone
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
