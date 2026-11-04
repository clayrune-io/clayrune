"""Video formats accepted by the Desk library and piece uploads.

Keep the explicit chooser hints in desk-v1-media-formats.js in sync. A format
being accepted does not promise that the current browser has its decoder.
"""

VIDEO_EXTS = (
    '.mp4', '.m4v', '.mov', '.qt', '.webm', '.mkv', '.avi', '.wmv', '.asf',
    '.mpg', '.mpeg', '.mpe', '.m1v', '.m2v', '.mpv', '.3gp', '.3g2', '.ogv',
    '.ogg', '.ogm', '.flv', '.f4v', '.ts', '.mts', '.m2ts', '.vob', '.divx',
    '.mxf', '.dv', '.rm', '.rmvb', '.nut', '.y4m',
)

MEDIA_FORMAT_ERROR = 'choose an image (PNG, JPG, GIF, WebP) or a video (for example MP4, MOV, MKV, AVI, WMV, MPEG, WebM, TS)'
