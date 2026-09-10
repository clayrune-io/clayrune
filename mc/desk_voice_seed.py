"""The Desk — seed a voice from how the human actually writes.

THE COLD START THIS FIXES. `desk.voice_brief` hands the writer the human's real
edits to earlier drafts, which is the differentiator — but on a fresh install
there are none. Measured 2026-09-10 on this machine: both voices had **0
rewrites**, so the brief was a one-line register and nothing else, and the first
ten drafts had no voice to imitate. The loop only starts paying after you have
already corrected it ten times, which is backwards.

RON'S CORRECTION, and it is the design: *"the way a user expresses himself in his
requests is also part of who he is — is he paying more attention to details, more
attention to actions, results."* That is voice evidence, and it exists in volume
before a single draft is written. Measured on this machine: **21,578 human-typed
messages across 16,272 transcripts.**

Note the distinction, because an earlier version of this idea got it wrong:
conversations are WEAK as SIGNALS (most chat is process, not story — the backlog
and journals already carry the ask in the human's words) and STRONG as VOICE
EVIDENCE. This module only does the second.

WHAT IT DOES NOT DO:

  * It does not read agent replies. Only what the human typed.
  * It does not touch INCOGNITO. Those transcripts exist on disk, so exclusion
    has to be explicit here rather than inherited — the whole point of incognito
    is leaving no trace, and a voice profile trained on one would be a trace.
  * It does not extract verbatim quotes for the brief. A transcript can contain
    anything the human pasted, including a credential. This collects the corpus;
    the CHARACTERISATION is done by an agent that is told to describe a register
    and never to quote. See `desk_routes.seed_voice`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
import json
import re

from mc.core import _log

# Whatever the human pasted, we did not type. A message that is mostly a stack
# trace, a diff or a JSON blob is evidence of what they were DOING, not of how
# they write, and it is also the most likely place for a secret to be sitting.
# Below this the sample is too thin to characterise anything, and a voice
# confidently inferred from a handful of messages is worse than an unseeded one:
# the human trusts it because it says 'learned'.
MIN_SAMPLES = 40

MAX_CHARS = 600
MIN_CHARS = 12
class IncognitoBoundaryUnresolved(RuntimeError):
    """Raised when we cannot prove which transcripts are incognito.

    THIS REPLACES A SENTINEL THAT CLOSED NOTHING. The first cut returned a
    `'<unresolvable-incognito-path>'` string into the skip set on a corrupt
    record and called that fail-closed. It was not: `collect` only ever asked
    `d.name not in skip_dirs`, and no real directory is named that, so every
    directory — including the actual incognito one — passed the check. The set
    was non-empty and the test asserted only non-emptiness, so it shipped green.
    A refusal has to be something the caller cannot ignore.
    """


# Credentials pasted into an ordinary-looking sentence are the leak `_looks_typed`
# cannot catch: it screens SHAPE (length, punctuation density), and `sk-...` in a
# short line looks exactly like prose. These patterns drop the whole message —
# never redact in place, because a sentence with a hole in it is still evidence we
# read something we should not have.
_SECRET_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r'\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}',        # OpenAI/Stripe-style
    r'\bgh[pousr]_[A-Za-z0-9]{16,}',              # GitHub
    r'\bAKIA[0-9A-Z]{12,}',                       # AWS access key id
    r'\bxox[baprs]-[A-Za-z0-9-]{10,}',            # Slack
    r'\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',  # JWT
    # `Authorization: Bearer <token>` puts a word between the separator and the
    # value, so `[:=]\s*\S{12,}` alone misses it — the separator may be a space.
    r'\b(?:bearer|authorization)\b[\s:=]+(?:bearer[\s:=]+)?\S{12,}',
    r'\b(?:pass(?:word|wd)?|secret|api[_ -]?key|token|credential)\s*[:=]\s*\S{6,}',
    r'\b[A-Za-z0-9+/]{40,}={0,2}\b',             # long base64 run
    r'\b[0-9a-f]{32,}\b',                        # long hex (keys, hashes)
))


def _carries_a_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


_PASTE_MARKERS = ('```', 'Traceback (most recent call last)', '<system-reminder>',
                  '[Image #', '[Request interrupted', '<command-name>')


# THE ENVELOPE PROBLEM, and it is not hypothetical: measured on this machine,
# **93 of the first 400 messages (23%) opened with Clayrune's own injected phone
# directive** — `_BRIEF_REPLY_DIRECTIVE` in `agent_routes.py`, which prepends a
# hidden "reply in Telegram style" instruction to every message sent from a
# phone. Left in, nearly a quarter of the voice sample would have been the
# product talking to itself, and the characterisation would have come back
# describing OUR prose style as the human's.
#
# Two shapes, handled differently:
#   * A WRAPPER the human typed inside — strip the bracket, keep the message.
#   * A whole message that is machinery — drop it; there is no human text under
#     a compaction notice or a subagent-finished report.
_DROP_WHOLE = ('[dispatched agent finished]',
               '[Continuing from a previous conversation',
               '[Resuming a previous conversation',
               'Stop hook feedback:',
               'This session is being continued')

# Attachment markers the composer appends after the typed text.
_TRAILING_MARKERS = ('[Screenshot', '[Image', '[Attached', '[Pasted')


def _strip_envelope(text: str) -> str | None:
    """Return what the human actually typed, or None if nothing of theirs is left."""
    t = (text or '').strip()
    if any(t.startswith(m) for m in _DROP_WHOLE):
        return None
    # A leading bracketed block long enough to be prose is machinery. People do
    # not open a message with a 40-character aside; the injectors always do.
    while t.startswith('[') and ']' in t:
        close = t.index(']')
        if close < 40:
            break
        t = t[close + 1:].strip()
    for m in _TRAILING_MARKERS:
        i = t.find(m)
        if i > 0:
            t = t[:i].strip()
    return t or None


def _looks_typed(text: str) -> bool:
    """True for something a person typed, not something they pasted or a hook did."""
    t = (text or '').strip()
    if not (MIN_CHARS <= len(t) <= MAX_CHARS):
        return False
    if t.startswith('<') or t.startswith('/'):
        return False       # tool envelopes and slash commands
    if any(m in t for m in _PASTE_MARKERS):
        return False
    # A wall of punctuation or path separators is a paste, whatever its length.
    if sum(c in '{}[]<>/\\|' for c in t) > len(t) * 0.08:
        return False
    return True


def incognito_workspace(data_dir: Path) -> Path | None:
    """The resolved cwd of the incognito workspace, or None if it never ran.

    Returns a PATH rather than encoded directory names because the encoding is
    not something to guess at. Both `Documents-_claude-...` and
    `Documents--claude-...` exist on this disk, so Claude Code's underscore
    handling has changed over time; matching on the transcript's own recorded
    `cwd` sidesteps the whole question.

    Raises `IncognitoBoundaryUnresolved` when the record exists but cannot be
    read or carries no path. A missing file is different and is NOT an error:
    it means incognito has never been used, so there is nothing to exclude.
    """
    fp = Path(data_dir) / '_incognito.json'
    try:
        rec = json.loads(fp.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except Exception as e:
        _log(f'[desk] incognito record unreadable, refusing to seed: {e}')
        raise IncognitoBoundaryUnresolved(
            f'{fp} could not be read, so incognito transcripts cannot be '
            f'identified') from e
    path = rec.get('project_path') or rec.get('path')
    if not path:
        raise IncognitoBoundaryUnresolved(
            f'{fp} carries no project_path, so incognito transcripts cannot be '
            f'identified')
    try:
        return Path(path).resolve()
    except Exception as e:
        raise IncognitoBoundaryUnresolved(f'incognito path {path!r} is unusable') from e


def _file_cwd(text: str) -> str | None:
    """The cwd a transcript records for itself, from the first record carrying one.

    Not every line has it — the first line of a real transcript did not, which is
    why this scans rather than reading `[0]`.
    """
    for line in text.splitlines():
        if '"cwd"' not in line:
            continue
        try:
            cwd = json.loads(line).get('cwd')
        except Exception:
            continue
        if cwd:
            return cwd
    return None


def collect(data_dir: Path,
            transcript_root: Path | None = None,
            *, limit: int = 400) -> list[str]:
    """The human's own recent messages, newest transcripts first.

    `data_dir` is REQUIRED and is not a convenience argument — it is how the
    incognito boundary is computed. Making it optional means a caller who forgot
    it reads every private transcript with no visible failure, which is the worst
    shape a privacy bug can take. So there is no default: no `data_dir`, no read.

    Raises `IncognitoBoundaryUnresolved` rather than degrading. There is no
    partial-credit mode here — either we can tell which transcripts are private
    or we do not read any of them.

    Newest-first: a voice is how someone writes NOW, and this corpus reaches back
    months, so the cap drops the oldest rather than diluting the sample.
    """
    # FIRST, before the root-exists shortcut and before any file is opened: an
    # unresolvable boundary is a refusal whatever else is true, and checking it
    # after an early return is how a guard stops guarding.
    private = incognito_workspace(data_dir)

    root = Path(transcript_root or (Path.home() / '.claude' / 'projects'))
    if not root.exists():
        return []
    private_key = str(private).casefold() if private else None

    files: list[Path] = []
    for d in root.iterdir():
        if d.is_dir():
            files.extend(d.glob('*.jsonl'))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    out: list[str] = []
    for f in files:
        if len(out) >= limit:
            break
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue

        # THE EXCLUSION, against the transcript's own recorded cwd rather than
        # against a guess at how its directory name was encoded. A file that
        # will not say where it ran is SKIPPED, not read: an unidentifiable
        # transcript is exactly the one we cannot clear.
        if private_key is not None:
            cwd = _file_cwd(text)
            if cwd is None or str(Path(cwd)).casefold() == private_key:
                continue

        for line in text.splitlines():
            if len(out) >= limit:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get('type') != 'user':
                continue
            # isMeta is the CLI's own injected turns; isCompactSummary is a
            # model-written recap wearing a user role. Neither was typed.
            if rec.get('isMeta') or rec.get('isCompactSummary'):
                continue
            content = (rec.get('message') or {}).get('content')
            if not isinstance(content, str):
                continue
            typed = _strip_envelope(content)
            if typed and _looks_typed(typed) and not _carries_a_secret(typed):
                out.append(typed)
    return out


def build_seed_brief(samples: Iterable[str], voice_name: str) -> str:
    """Ask an agent to characterise the register — never to quote it.

    The CHARACTERISATION is an agent's job for the same reason triage is: a regex
    can count words, and what matters here is what the person ATTENDS to. Ron's
    framing: detail, or action, or results. That is a judgement.
    """
    rows = list(samples)
    lines = [
        f'Read how this person writes and describe it, so another writer can '
        f'sound like them in the "{voice_name}" voice.',
        '',
        'These are their own messages to their coding agents — not marketing '
        'copy, not anything written for an audience. That is exactly why they '
        'are useful: this is the unperformed version.',
        '',
        'DESCRIBE, DO NOT QUOTE. Your output goes into a prompt that will be '
        'reused for months, and these messages can contain anything the person '
        'pasted — file paths, tokens, private detail. Never reproduce a specific '
        'sentence, name, path, number or credential from the sample.',
        '',
        'What to characterise, in order of how much it changes the writing:',
        '  1. WHAT THEY ATTEND TO. Detail, action, results, cost, correctness, '
        'the reader? A person who always asks "what did you measure" writes '
        'differently from one who asks "is it live yet".',
        '  2. Sentence shape — length, whether they front the point or build to '
        'it, how they open and how they close.',
        '  3. Words and constructions they REACH FOR, described as a pattern.',
        '  4. Words and constructions they never use, and any register they '
        'visibly dislike (hedging, hype, ceremony).',
        '  5. How they disagree, correct, and ask for something again.',
        '',
        f'── {len(rows)} MESSAGES ──',
    ]
    lines += [f'  - {r}' for r in rows]
    lines += [
        '',
        '── HOW TO DELIVER IT ──',
        'PATCH the voice with a `register` of 4 to 8 sentences. Write it as '
        'instructions TO a writer ("Lead with the outcome. Never hedge."), not as '
        'a description of a person.',
        '',
        f'  curl -s -X PATCH http://localhost:5199/api/desk/voices/{voice_name} \\',
        "    -H 'Content-Type: application/json' \\",
        '    -d \'{"register":"...","banned":["..."],"never_claims":["..."]}\'',
        '',
        '`banned` is words and constructions this voice will not use. Put a term '
        'there only if the sample shows they actually avoid it — an invented '
        'prohibition is worse than none, because the writer will obey it.',
        '',
        'This SEEDS the voice. It will be corrected afterwards by the human\'s '
        'real edits to real drafts, which outrank anything you infer here.',
    ]
    return '\n'.join(lines)
