"""Chromium launch flags that decide what display the browser pane reports.

MC-1090 (2026-10-13). X refused "Sign in with Google" from the pane while the
same account on the same box and IP signed in fine from a normal Chrome. The
pane was diffed against headed Chromium and Chrome 154 on a local probe page
(`_scratch/mc1090/`), and three pane-only differences came from two launch
flags, not from headless itself:

* ``--disable-gpu`` left the pane with NO WebGL, NO WebGL2 and a null WebGPU
  adapter, and a different canvas hash (software raster). Plain headless,
  headed Chromium and Chrome all reported the real GPU. X's logged-out login
  page asks for a webgl context 6 times and, once it gets one, reads
  ``UNMASKED_RENDERER_WEBGL`` (measured with a getter hook).
* Headless reports a fixed 800x600 screen (400x300 at dpr 2) while the window
  is 1296x951: a window bigger than its own screen, which no real browser
  reports. X's login page reads screen.width/height/avail* and
  outerWidth/outerHeight.
* ``--force-device-scale-factor`` divides a ``--screen-info`` size by the dpr
  TWICE for width/height but once for the work area (measured on Chromium 151:
  screen 960x540 with avail 1920x1080 at dpr 2), so it cannot be combined with
  a sane screen. ``--screen-info`` carries the dpr itself instead; the
  screencast frame was measured identical (2560x1600 at dpr 2, 1280x800 view).

Whether X keys on these exact signals is not proven: proving it needs a real
sign-in. They are the measured pane-vs-Chrome differences on surfaces X reads.

``browser_pane_gpu: false`` in config.json restores ``--disable-gpu`` for a
host whose GPU process misbehaves headless.

Mobile launches are the exception (b189ba58): the pane then claims to be a
Pixel 7, and the MC-1090 fix made it report the host's desktop GPU (NVIDIA
D3D11 via WebGL) and a 1920x1080 screen under that UA. A mobile launch gets
the pre-MC-1090 ``--disable-gpu`` (no WebGL beats a desktop renderer string
under an Android UA, and a spoofed GPU string would need injected getter
overrides, themselves a detectable inconsistency) and a screen the size of
the phone view. That view and dpr are the same values
``browser_routes._mobile_metrics_cmd`` hands Emulation.setDeviceMetricsOverride
(screenWidth/screenHeight = view), so the launch flag and the CDP override
state one screen, not two.
"""

from __future__ import annotations

# Common desktop screens, smallest first. The reported screen is the first
# that holds the whole launch window, so outerWidth/outerHeight never exceed
# screen.width/height for any pane size the launch allows.
_SCREENS = ((1920, 1080), (2560, 1440), (3840, 2160))


def screen_for(window_w: int, window_h: int) -> tuple[int, int]:
    """CSS-px screen size to report for a window of this size."""
    for w, h in _SCREENS:
        if window_w <= w and window_h <= h:
            return w, h
    return max(window_w, _SCREENS[-1][0]), max(window_h, _SCREENS[-1][1])


def _fmt(x: float) -> str:
    return f'{x:g}'


def display_args(window_w: int, window_h: int, dpr: float, gpu: bool = True,
                 mobile_view: tuple[int, int] | None = None) -> list[str]:
    """Launch flags for the pane's GPU and screen, given the launch window
    size (CSS px, chrome included) and the clamped device pixel ratio.

    ``mobile_view`` (the CSS viewport of a mobile launch) switches to the phone
    presentation: GPU off whatever ``gpu`` says, screen = that view."""
    if mobile_view:
        gpu = False
        sw, sh = mobile_view
    else:
        sw, sh = screen_for(window_w, window_h)
    args = [] if gpu else ['--disable-gpu']
    # --screen-info takes PHYSICAL pixels; devicePixelRatio scales them to CSS.
    info = f'0,0 {round(sw * dpr)}x{round(sh * dpr)}'
    if dpr != 1:
        info += f' devicePixelRatio={_fmt(dpr)}'
    args.append('--screen-info={' + info + '}')
    return args
