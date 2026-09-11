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

# PLATFORM IS A PROPERTY OF THE VOICE, not a separate choice, and that is the
# answer to "should a campaign set the platform too?" — no. A voice and a
# platform are one decision: `personal` is first person and posts to X;
# `product` speaks as the product and posts to LinkedIn. Letting a campaign pick
# both would let them disagree, and a first-person post in the product's voice on
# the wrong network is the exact incoherence the split exists to prevent.
#
# What a campaign DOES carry is a set of voices (see `voices` on the campaign
# record), because a thesis often deserves both rooms — written twice, once per
# voice, never cross-posted.
#
# This was a hardcoded {'ron': 'x', 'clayrune': 'linkedin'} dict. It is now read
# off the voice record so a user-created voice picks its own platform.

# PLATFORM RULES ARE USER DATA — see mc/desk.py's "platform rules" section.
# This used to be a hardcoded dict with exactly two keys, so every OTHER
# platform got `.get(platform, '')`: an empty string where the char limit and
# cost should have been. Measured 2026-09-10: an 837-char facebook draft and a
# 2236-char discord draft, both briefed with nothing. `_platform_rules_text`
# below is what replaced the `.get(platform, '')` lookup, and it never returns
# a silent empty string — a platform with no rules says so, in the brief.


def _platform_rules_text(platform: str) -> str:
    rules = _desk.get_platform_rules(platform)
    if rules is None:
        return (
            f'NO RULES ARE SET for "{platform}" yet — nobody has told this system '
            'its character limit, cost, or behaviour on this platform. Keep the '
            'post SHORT (a headline plus one supporting line) until someone does, '
            f'and use your `teaching` field to say the {platform} rules need to be '
            'set on the Desk before this platform is used again.'
        )
    parts = []
    if rules.get('char_limit'):
        parts.append(f"{rules['char_limit']} characters.")
    if rules.get('text'):
        parts.append(rules['text'])
    if not parts:
        return (
            f'A rules record exists for "{platform}" but has no text yet — treat '
            'it the same as no rules: keep the post short.'
        )
    return ' '.join(parts)


def platform_for(voice: str) -> str:
    """The platform this voice posts to, read off the voice itself."""
    try:
        return (_desk.get_voice(voice) or {}).get('platform') or 'x'
    except ValueError:
        return 'x'


def destination_for(voice: str) -> str:
    """The account this voice publishes to, read off the voice itself.

    Distinct from platform: two voices can share a platform (two LinkedIn
    voices, say) and still need to publish to different accounts. Empty means
    the platform's default account — the only meaning that keeps an existing
    install, which never set this, working unchanged."""
    try:
        return (_desk.get_voice(voice) or {}).get('destination') or ''
    except ValueError:
        return ''


def build_triage_brief(signals: list[dict], *, voices: list[str],
                       campaign: dict | None = None,
                       max_picks: int = 5) -> str:
    """Ask Posy WHICH signals are worth a post, and in which voice.

    This is the judgement `score_signal` was standing in for. A keyword regex
    cannot tell a shipped feature from a chore that happens to say "shipped", and
    it cannot explain itself — so the human was left reading every row. With 120
    signals in the feed that does not scale, which is the whole reason this
    exists.

    The instruction to DISCARD is the load-bearing part. The spec requires that a
    period with nothing worth saying produces nothing, so the brief has to make
    proposing fewer items the successful outcome rather than a failure to fill a
    quota. An agent asked for "the best five" will always find five.
    """
    lines = [
        'You are the Desk\'s editor for one pass. Read what happened across the '
        'projects below and decide WHICH items deserve a post — and which voice '
        'should carry each one.',
        '',
        'YOU ARE NOT WRITING POSTS IN THIS PASS. You are proposing. A human '
        'accepts or dismisses each proposal, and only an accepted one gets '
        'drafted. Keep each `why` to one or two sentences.',
        '',
        f'PICK AT MOST {max_picks}. Picking FEWER is the better answer whenever '
        'the rest are not worth a reader\'s attention — a period with nothing '
        'worth saying is required to produce nothing. Do not fill a quota. If '
        'none of these deserve a post, say so in one line and propose nothing.',
        '',
        '── THE VOICES AVAILABLE ──',
    ]
    for v in voices:
        try:
            rec = _desk.get_voice(v) or {}
        except ValueError:
            continue
        lines.append(f'  {v} -> posts to {rec.get("platform") or "?"}. '
                     f'{rec.get("register") or ""}')
    lines += ['', 'The platform comes with the voice. A story that suits both '
              'gets proposed twice, once per voice, and will be WRITTEN twice '
              'rather than cross-posted — the platforms demote a copy-paste.']

    if campaign:
        lines += [
            '',
            '── THE RUNNING CAMPAIGN ──',
            f'Title: {campaign.get("title")}',
            f'Thesis: {campaign.get("thesis")}',
            f'Why it is running now: {campaign.get("agenda") or "(not stated)"}',
            f'Visual its posts need: {campaign.get("visual") or _desk.DEFAULT_VISUAL_REQUIREMENT}',
            'Prefer items that ARGUE this thesis. An item that cannot be made to '
            'serve it is usually not worth a post right now, however interesting.',
        ]

    recent = _desk.list_ledger(limit=10)
    if recent:
        lines += ['', '── WHAT WE HAVE ALREADY POSTED (do not repeat it) ──']
        for p in recent:
            lines.append(f'  ({p.get("published_at","")[:10]}, {p.get("platform")}) '
                         f'{(p.get("body") or "")[:160]}')

    lines += ['', f'── WHAT HAPPENED ({len(signals)} items, most will not be posts) ──']
    for s in signals:
        lines.append(
            f'  [{s.get("id")}] {s.get("occurred_at","")[:10]} '
            f'{s.get("project_id")} / {s.get("kind")}: {(s.get("summary") or "")[:200]}')

    lines += [
        '',
        '── HOW TO DELIVER IT ──',
        'POST each pick, one call per proposal:',
        '  curl -s -X POST http://localhost:5199/api/desk/proposals \\',
        "    -H 'Content-Type: application/json' \\",
        '    -d \'{"signal_id":"<the [id] above>","voice":"<one of the voices>",'
        '"why":"..."}\'',
        '',
        '`why` is read by a human deciding in about three seconds. Say what the '
        'story IS and who it is for — not that the item "looks significant". '
        'Name the angle, not the category.',
        '',
        'Do not invent signals. Every proposal must cite an id from the list '
        'above, because the human checks the claim against it.',
    ]
    return '\n'.join(lines)


def build_brief(signal: dict, *, voice: str | None = None,
                campaign: dict | None = None,
                project_name: str | None = None) -> str:
    """Assemble the task text for the drafting agent. Pure string work."""
    voice = voice or _desk.default_voice() or ''
    if not _desk.is_voice(voice):
        raise ValueError(f'unknown voice {voice!r}; expected one of {_desk.voice_names()}')
    platform = platform_for(voice)
    destination = destination_for(voice)
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
        f'DESTINATION: {destination or "the platform default account (none set)"} '
        '— this changes what you may say. What standing this destination '
        "holds (first person or not, and what it may claim) is the voice's "
        'own Register below; do not assume a standing from the destination '
        'name itself.',
        _platform_rules_text(platform),
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

    # A post carries a visual, or plainly says it has none — never an invented
    # or described one. `media` on the queue item has existed since Phase 1 but
    # nothing ever populated it, so no draft has ever actually shown a reader
    # anything. Standing position (2026-09-10): default is a REAL captured
    # screenshot, never generated art depicting the product.
    visual_need = (campaign.get('visual') if campaign else None) or _desk.DEFAULT_VISUAL_REQUIREMENT
    out += [
        '',
        '── THE VISUAL ──',
        f'What this post needs: {visual_need}',
        'A post without a visual is measurably weaker on both target '
        'platforms. Attach one by naming a REAL file in the draft\'s `media` '
        'list — capture one with '
        '`node tools/smoke/capture-screenshot.mjs <url> <output-name.png>` '
        '(writes under data/media/, the only path a media entry is allowed '
        'to resolve to). Do NOT invent, describe, or ask for a generated '
        'image of the product — if you have no real screenshot to attach, '
        'say so plainly in the draft and post without one.',
    ]

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
        '"body":"...","teaching":"...","media":["data/media/your-file.png"]}\''
        % (platform, voice, signal.get('id')),
        '',
        '`teaching` is REQUIRED and is not a summary of the post. In two or three '
        'sentences: why this angle, why this platform, why now, and what it is '
        'competing against in the reader\'s feed. Ron reads it while deciding, '
        'so it has to teach at the moment of judgement.',
        '',
        '`media` is a list of real file paths, or omit it if you have no '
        'screenshot — never a placeholder or an invented path.',
        '',
        'The draft lands as PENDING. You do not publish and you cannot — a human '
        'releases it or it does not go out. Do not ask for that to change.',
        '',
        'If the signal is not worth a post, say so in one line and post nothing. '
        'A period with nothing worth saying produces nothing; that is a '
        'requirement of this system, not a failure of it.',
    ]
    return '\n'.join(x for x in out if x is not None)
