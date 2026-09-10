"""The Desk — the brief a drafting agent is handed.

Spec: `docs/THE_DESK_SPEC.md`. Scan: `docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md`.

WHO WRITES, AND WHY THIS MODULE IS NOT A PERSONA. The Desk is staffed from the
existing roster and does not add a marketing agent — the standing position of
2026-08-29 declined one and its reasoning holds. **Posy**
(`social-media-strategist`) writes and holds the platform judgement. What the
Desk adds is *the office she works in*: the state she reads, the constraints she
writes under, and the surface Ron manages her from. This module is the office's
briefing desk, nothing more. It assembles; it does not judge and it does not
generate.

WHAT GOES IN A BRIEF, and each earns its place from something measured:

  * THE SIGNAL, with its ref. A draft must link to what it came from so Ron can
    check the claim before releasing it. Untraceable claims are the fastest way
    to lose trust in the whole system.
  * THE VOICE, as verbatim rewrites rather than adjectives. `desk.voice_brief`
    hands over the edits Ron actually made, because a summary of a voice is
    where its specificity goes to die.
  * WHAT WE HAVE ALREADY SAID that is close to this. Without it an autonomous
    writer re-announces the same feature every month, in front of the exact
    B2B founder-credibility audience the scan says punishes that hardest.
  * THE PLATFORM'S REAL CONSTRAINTS, including what a post COSTS. X bills
    $0.015 a post and **$0.200 if it contains a URL** (verified at
    docs.x.com/x-api/getting-started/pricing, 2026-09-09). That is a 13x
    multiplier the writer should know about before reflexively appending a link.
  * THE CAMPAIGN THESIS, if the draft belongs to one, so the post argues the
    campaign rather than merely reporting the event.

WHAT IS DELIBERATELY NOT IN A BRIEF: permission to publish. The brief asks for a
PENDING draft on the queue and says so explicitly. Nothing in this module or in
`desk_routes` can post, and the approval gate is a platform TERM rather than our
caution — Pinterest requires per-item human choice, YouTube prior express
consent, and the March-2026 Meta incident is what an *expected* but unenforced
gate costs.
"""

from __future__ import annotations

from mc import desk as _desk

# The two identities, fixed by Ron's 2026-09-09 decision. They are not variants
# of one another and they own different platforms, so the platform implies the
# voice and a mismatch is a bug rather than a preference.
VOICE_PLATFORM = {'ron': 'x', 'clayrune': 'linkedin'}

# Verified at docs.x.com/x-api/getting-started/pricing on 2026-09-09, and at
# learn.microsoft.com for LinkedIn's Share on LinkedIn limits. These are in the
# brief because a writer who does not know a link costs 13x will add one every
# time out of habit.
PLATFORM_NOTES = {
    'x': (
        '280 characters. A plain post costs $0.015 to publish; a post CONTAINING '
        'A LINK costs $0.200 — 13x. Include a URL only when the link is the '
        'point, not as a reflex. Threads are fine; each part bills separately.'
    ),
    'linkedin': (
        'Long-form is fine and rewarded. Published free via Share on LinkedIn, '
        'capped at 150/day. LinkedIn suppressed reach on content its classifier '
        'reads as AI slop by ~40% (its CPO Hari Srinivasan, 2026-08-21), and '
        'external-link posts are demoted — put the link in a comment or omit it. '
        'Specific, first-hand and concrete survives; generic summary does not.'
    ),
}


def platform_for(voice: str) -> str:
    return VOICE_PLATFORM.get(voice, 'x')


def build_brief(signal: dict, *, voice: str = 'ron',
                campaign: dict | None = None,
                project_name: str | None = None) -> str:
    """Assemble the task text for the drafting agent. Pure string work."""
    if voice not in _desk.VOICES:
        raise ValueError(f'unknown voice {voice!r}; expected one of {_desk.VOICES}')
    platform = platform_for(voice)
    pid = signal.get('project_id') or ''

    # Run the repetition check on the SIGNAL's own words before a line is
    # written. Cheaper than drafting first and rejecting after, and it lets the
    # brief name the specific earlier post rather than warning in the abstract.
    prior = _desk.similar_published(signal.get('summary') or '', threshold=0.30)

    out = [
        'You are drafting ONE social post for The Desk (docs/THE_DESK_SPEC.md).',
        '',
        f'PLATFORM: {platform}',
        f'VOICE: {voice}',
        PLATFORM_NOTES.get(platform, ''),
        '',
        '── WHAT HAPPENED (this is your only source; do not invent beyond it) ──',
        f'Project: {project_name or pid}',
        f'Kind: {signal.get("kind")}',
        f'When: {signal.get("occurred_at")}',
        f'Ref: {signal.get("ref")}   (cite this if you make a specific claim)',
        f'Summary: {signal.get("summary")}',
    ]
    if signal.get('detail'):
        out += [f'Detail: {signal["detail"][:1200]}']

    if campaign:
        out += [
            '',
            '── THE CAMPAIGN THIS BELONGS TO ──',
            f'Title: {campaign.get("title")}',
            f'Thesis: {campaign.get("thesis")}',
            f'Why it is running now: {campaign.get("agenda") or "(not stated)"}',
            'Argue the thesis. Do not merely report the event.',
        ]

    out += ['', '── THE VOICE YOU ARE WRITING IN ──', _desk.voice_brief(voice)]

    if prior:
        out += ['', '── WE HAVE ALREADY SAID SOMETHING LIKE THIS ──']
        for p in prior[:3]:
            out += [f'  ({p["published_at"][:10]}, {p["platform"]}, '
                    f'{int(p["overlap"] * 100)}% overlap) {p["body"][:200]}']
        out += ['Either find a genuinely new angle, or say plainly that this is '
                'not worth a second post and stop. Repeating ourselves costs '
                'more than silence does.']

    out += [
        '',
        '── HOW TO DELIVER IT ──',
        'POST the draft to this endpoint, which puts it on the approval queue:',
        f'  curl -s -X POST http://localhost:5199/api/project/{pid}/social/queue \\',
        "    -H 'Content-Type: application/json' \\",
        '    -d \'{"platform":"%s","voice":"%s","signal_id":"%s",'
        '"body":"...","teaching":"..."}\'' % (platform, voice, signal.get('id')),
        '',
        '`teaching` is REQUIRED and is not a summary of the post. In two or three '
        'sentences: why this angle, why this platform, why now, and what it is '
        'competing against in the reader\'s feed. Ron reads it while deciding, '
        'so it has to teach at the moment of judgement.',
        '',
        'The draft lands as PENDING. You do not publish and you cannot — a human '
        'releases it or it does not go out. Do not ask for that to change.',
        '',
        'If the signal is not worth a post, say so in one line and post nothing. '
        'A period with nothing worth saying produces nothing; that is a '
        'requirement of this system, not a failure of it.',
    ]
    return '\n'.join(x for x in out if x is not None)
