"""Everything the Gemini web engine believes about gemini.google.com, in ONE place.

When Google changes the page, this file is the whole fix: `mc/desk_gemini_web.py` (the
flow) and `mc/desk_gemini_web_driver.py` (the CDP glue) hold no selector, label, URL or
phrase of their own.

STATUS: NOTHING HERE HAS BEEN RUN AGAINST THE LIVE SITE. The visible labels ("Describe
your image", "Images", "Hydrogen", "Aspect ratio", "+") come from Ron's screenshot of the
screen he uses by hand (2026-11-14); every CSS selector and every other label is a
best guess and is expected to need one calibration pass at the first live run. A wrong
entry fails loudly ("the Gemini page did not match ... at step X") and nothing is sent.

Matching rules (mc/desk_assisted_pane.py `_FIND_JS`): a `labels` list is compared,
lower-cased, against a visible element's whole label (its text, else its aria-label), so
write every spelling Google might use. `css` may list several selectors.

Page text is untrusted. It is only ever compared against the `needles` below; it is never
copied into a message, a prompt or an agent's context.
"""
from __future__ import annotations

HOME = 'https://gemini.google.com/app'          # a new chat
HOSTS = ('gemini.google.com',)
# Where the picture may be read from. Anything else the page points at is refused.
IMAGE_HOSTS = ('googleusercontent.com', 'gemini.google.com')
CHAT_URL = r'^https://gemini\.google\.com/app/[A-Za-z0-9_-]{6,64}$'   # a chat that is safe to name in a message

# Seconds. `generate` is the ceiling for the picture to appear after Send.
TIMEOUTS = {'page': 25, 'step': 8, 'generate': 180, 'settle': 3.0, 'poll': 0.5, 'download': 60}

# The page is signed out when any of these shows, or when it left HOSTS (a Google login redirect).
SIGNED_OUT_CSS = ('a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/signin"], '
                  'a[aria-label^="Sign in" i]')

COMPOSER_CSS = ('rich-textarea div.ql-editor[contenteditable="true"], '
                'div[contenteditable="true"][role="textbox"]')
SEND = {'css': 'button, [role="button"]', 'labels': ['send message', 'send', 'submit']}

# Create-image mode. `active` showing means it is already on (Ron's screenshot: an "Images"
# chip in the composer). Otherwise `open` (a Tools menu, if the page has one) is clicked and
# then `choose`.
IMAGES_MODE = {
    'active': {'css': 'button, [role="button"], mat-chip, [role="option"]', 'labels': ['images']},
    'open': {'css': 'button, [role="button"]', 'labels': ['tools', 'create']},
    'choose': {'css': 'button, [role="menuitem"], [role="option"]', 'labels': ['images', 'create images', 'create image']},
}

# model_id -> the label the model picker shows for it ("Hydrogen" in the screenshot).
MODEL_LABELS = {'hydrogen': ['hydrogen']}
# The picker button shows whichever model is current, so besides the known model names it is
# also found by its accessible name.
MODEL_PICKER = {'css': 'button, [role="button"], [role="combobox"]',
                'labels': ['model picker', 'choose model', 'pick a model', 'model']}
MODEL_OPTION = {'css': '[role="menuitem"], [role="option"], button'}

# The Desk's three ratios -> every spelling the page may use for them.
RATIO_LABELS = {'1:1': ['1:1', 'square', 'square (1:1)'],
                '9:16': ['9:16', 'portrait', 'portrait (9:16)', 'vertical'],
                '16:9': ['16:9', 'landscape', 'landscape (16:9)', 'widescreen']}
RATIO_TRIGGER = {'css': 'button, [role="button"], [role="combobox"]', 'base_label': 'aspect ratio'}
RATIO_OPTION = {'css': '[role="menuitem"], [role="option"], button'}

# Reference pictures: the "+" button, its upload entry, then the file input the page exposes.
ATTACH = {
    'open': {'css': 'button, [role="button"]', 'labels': ['+', 'open upload file menu', 'add files', 'upload']},
    'choose': {'css': '[role="menuitem"], button', 'labels': ['upload files', 'upload file', 'upload from computer']},
    'file_input_css': 'input[type="file"]',
    'thumb_css': 'uploader-file-preview, [data-test-id*="file-preview"], img[src^="blob:"]',
}

# A result image: large and loaded, inside the model's answer. `min_px` keeps avatars and
# icons out of the count.
RESULT_IMAGE = {'css': 'model-response img, .response-container img, message-content img', 'min_px': 256}
# The URL suffix Google appends to size an image; replaced to ask for the original.
FULL_SIZE = {'pattern': r'=[swh]\d+(-[A-Za-z0-9-]+)?$', 'replace': '=s0'}

# While waiting for the picture, these phrases inside the answer end the wait. Checked only
# inside RESULT_SCOPE_CSS (the model's answer, never the person's own prompt).
RESULT_SCOPE_CSS = 'model-response, .response-container'
NOTICES = (
    {'kind': 'quota', 'needles': ["you've reached your limit", 'reached your daily limit', 'image generation limit',
                                  'come back later to create more']},
    {'kind': 'moderation', 'needles': ["i can't create", "i can't generate", "can't help with that",
                                       "couldn't generate", 'violates', 'unable to create images of']},
)

# What to do when the answer holds more than one picture:
#   'all'   save every new picture (nothing the person generated is lost)
#   'first' save only the first
#   'refuse' save none and say there were several
ON_MULTIPLE_IMAGES = 'all'
