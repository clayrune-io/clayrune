"""YouTube Studio assisted upload: fill Details, then a person finishes.

None of the Create, Upload videos, file-input, title, description or Publish/Save
selectors has been run against live signed-in Studio. Missing markup stops the
run for manual completion. The route deliberately stays on Details: audience,
checks and visibility are human choices, and Publish/Save is NEVER clicked.
Studio may upload and save a private draft as soon as the file is attached.

The version's first line is the title; remaining lines are the description.
No truncation, generated title, or unapproved piece-title fallback is used.
Limits/flow: https://support.google.com/youtube/answer/57407
"""
from __future__ import annotations

NETWORK = 'youtube'
LABEL = 'YouTube'
HOME = 'https://studio.youtube.com/'
HOSTS = ('studio.youtube.com',)
MEDIA_KINDS = ('video',)
MAX_MEDIA = 1
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
CAPTION_LIMIT = TITLE_LIMIT + DESCRIPTION_LIMIT + 2  # allows CRLF between fields


def text_fields(body: str) -> dict[str, str]:
    title, _, description = body.strip().partition('\n')
    return {'title': title.strip(), 'description': description.strip()}


def validation_errors(body: str, assets: list[dict]) -> list[str]:
    fields = text_fields(body)
    errors = []
    if len(assets) != 1 or assets[0].get('kind') != 'video':
        errors.append('YouTube needs exactly one video attachment; images and text-only posts cannot be uploaded')
    if not fields['title']:
        errors.append('YouTube needs a title on the first line')
    if len(fields['title']) > TITLE_LIMIT:
        errors.append('YouTube title (first line) takes at most 100 characters')
    if len(fields['description']) > DESCRIPTION_LIMIT:
        errors.append('YouTube description (remaining lines) takes at most 5000 characters')
    if any(c in fields['title'] + fields['description'] for c in '<>'):
        errors.append('YouTube title and description cannot contain < or >')
    return errors


FINAL_SUBMIT = {'css': 'ytcp-button#done-button, button#done-button',
                'label': 'Publish', 'aliases': ('Save',)}
STEPS: tuple[dict, ...] = (
    {'op': 'navigate', 'url': HOME},
    {'op': 'wait', 'css': 'ytcp-button#create-icon, button[aria-label="Create"]', 'timeout_s': 20},
    {'op': 'click', 'css': 'ytcp-button#create-icon, button[aria-label="Create"]', 'label': 'Create'},
    {'op': 'click', 'css': 'tp-yt-paper-item, ytcp-text-menu-item', 'label': 'Upload videos',
     'wait_css': 'ytcp-uploads-dialog'},
    {'op': 'attach', 'css': 'ytcp-uploads-dialog input[type="file"]'},
    {'op': 'wait', 'css': 'ytcp-video-metadata-editor #title-textarea #textbox', 'timeout_s': 30},
    {'op': 'caption', 'css': 'ytcp-video-metadata-editor #title-textarea #textbox', 'field': 'title', 'replace': True},
    {'op': 'caption', 'css': 'ytcp-video-metadata-editor #description-textarea #textbox', 'field': 'description', 'replace': True},
)
