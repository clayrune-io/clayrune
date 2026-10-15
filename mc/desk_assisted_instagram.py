"""Instagram's assisted-post route: DATA ONLY (one unit, one file).

Steps the engine in `mc/desk_assisted_post.py` runs in the signed-in pane, in order, ending
with the caption typed and the final button left alone. Adding a network is a new module like
this one plus a line in `ROUTES` there; the engine does not change.

None of these selectors has been run against a live signed-in Instagram: the page's markup
was written from memory of the current web UI and a builder cannot open a signed-in session.
A selector that no longer matches ends the run at that step with `step_failed` and the
person finishes by hand; it never clicks something else.

Step keys:
  navigate  url                      open the address
  click     css, label, [aliases], [wait_css]   click the visible element matching `css` whose own text or
                                     icon label equals `label` (case-insensitive). The label
                                     must not be a final-submit word.
  wait      css, [timeout_s]         wait for a visible element
  attach    css                      hand the post's media files to the file input matching `css`
  caption   css                      focus the matching element and insert the post's text
"""
from __future__ import annotations

NETWORK = 'instagram'
LABEL = 'Instagram'
HOME = 'https://www.instagram.com/'
HOSTS = ('www.instagram.com', 'instagram.com')
MAX_MEDIA = 10                       # a carousel
MEDIA_KINDS = ('image', 'video')
CAPTION_LIMIT = 2200

# The button that publishes. The engine never clicks anything with this label, and finishes by
# confirming it is present, so the person knows the screen is where it should be.
FINAL_SUBMIT = {'css': 'div[role="dialog"] div[role="button"]', 'label': 'Share'}

STEPS: tuple[dict, ...] = (
    {'op': 'navigate', 'url': HOME},
    {'op': 'wait', 'css': 'a[role="link"], div[role="button"]', 'timeout_s': 15},
    {'op': 'click', 'css': 'a[role="link"], div[role="button"], span', 'label': 'Create',
     'aliases': ('New post',),
     'wait_css': 'div[role="menu"], div[role="dialog"], a[role="link"]'},
    {'op': 'click', 'css': 'a[role="link"], div[role="button"], span', 'label': 'Post',
     'wait_css': 'div[role="dialog"]'},
    {'op': 'attach', 'css': 'div[role="dialog"] input[type="file"]'},
    {'op': 'click', 'css': 'div[role="dialog"] div[role="button"]', 'label': 'Next',
     'wait_css': 'div[role="dialog"] div[role="button"]'},
    {'op': 'click', 'css': 'div[role="dialog"] div[role="button"]', 'label': 'Next',
     'wait_css': 'div[role="dialog"] [role="textbox"], div[role="dialog"] textarea'},
    {'op': 'caption', 'css': 'div[role="dialog"] [role="textbox"], div[role="dialog"] textarea'},
)
