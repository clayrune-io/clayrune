"""Default pages the generic pane reader (`mc/desk_engagement_pane_digest.py`) reads, one
table entry per platform. DATA ONLY: no per-platform logic lives anywhere, so a new site
is a new entry here, or just addresses on the account (`read_pages`), never new code.

Each entry:
  label     how the UI names the site
  home      the site's start page, where discovery begins when no address is known
  activity  addresses of the page(s) that list replies / mentions / comments / messages
            about the account. Read every poll, one model call each. Empty when the page
            needs an id the Desk does not hold (the user adds the address on the account).
  post      address PREFIXES under which one of our published posts lives. A ledger post
            whose permalink starts with one is read at that address for its counters.

Not one of these addresses has been checked against a live signed-in session (a
fenced builder cannot open one). A wrong address reads as a page the model does not
recognise and is reported as a gap, never as an empty feed. `activity` is left empty
where it could only be a guess.
"""
from __future__ import annotations

PLATFORM_PAGES: dict[str, dict] = {
    'linkedin': {
        'label': 'LinkedIn',
        'home': 'https://www.linkedin.com/',
        'activity': ['https://www.linkedin.com/notifications/'],
        'post': ['https://www.linkedin.com/feed/update/'],
    },
    'x': {
        'label': '\U0001D54F',
        'home': 'https://x.com/',
        'activity': ['https://x.com/notifications/mentions'],
        'post': ['https://x.com/'],
    },
    'youtube': {
        'label': 'YouTube',
        'home': 'https://www.youtube.com/',
        'activity': [],          # Studio's comment inbox is per channel id
        'post': ['https://www.youtube.com/watch', 'https://youtu.be/',
                 'https://www.youtube.com/shorts/'],
    },
    'instagram': {
        'label': 'Instagram',
        'home': 'https://www.instagram.com/',
        'activity': ['https://www.instagram.com/accounts/activity/'],
        'post': ['https://www.instagram.com/p/', 'https://www.instagram.com/reel/'],
    },
    'tiktok': {
        'label': 'TikTok',
        'home': 'https://www.tiktok.com/',
        'activity': [],          # the inbox has no stable address to name here
        'post': ['https://www.tiktok.com/@'],
    },
}
