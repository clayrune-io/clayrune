# film-export

Turns one of the self-contained HTML promo films into an mp4 **with its sound**.
Picture is frame-stepped, not screen-recorded, so an export is exact and repeatable.

```
node tools/film-export/export.cjs <film.html> <out.mp4> [--fps 30] [--width 1920 --height 1080]
node tools/film-export/export.cjs --all <films-dir> <out-dir> [--skip-existing]
node tools/film-export/selfcheck.cjs
```

Other options: `--duration S` (override the length read from the film), `--audio-lead S`
(default 0.08), `--fade-out S` (default 0.15, `0` = off), `--no-audio` (picture-only, on purpose).

## Needs

- `ffmpeg` and `ffprobe` on PATH (export stops with a clear message if ffmpeg is missing).
- Playwright, **never installed by this tool**. Looked up in this order:
  1. `$FILM_EXPORT_PLAYWRIGHT` (a `node_modules` folder holding `playwright`, or the package itself)
  2. `tools/smoke/node_modules/playwright` in this checkout
  3. a plain `require('playwright')`

  `tools/smoke/node_modules` is gitignored and lives only in the main checkout. In an agent
  worktree, point at the main checkout's copy instead of installing
  (see CLAUDE.md, "If Playwright looks missing, you're in a worktree"):
  `FILM_EXPORT_PLAYWRIGHT=<main checkout>/tools/smoke/node_modules node tools/film-export/export.cjs ...`

## The film contract

A film is one HTML file, 1920x1080, no network, and keeps:

- `?freeze&t=0` loads it paused; `window.__render(t)` draws the frame at `t` seconds;
  `window.__t()` returns the clock.
- A `DUR` variable holds the length in seconds. It is private to the film's closure, so the exporter
  reads it from the source (the `var ... DUR=...;` statement, evaluated as arithmetic; `promo-bumpers.html`
  has `DUR=BL*NB`). `--duration` overrides it.
- `?mute` disables sound. Sound is Web Audio, all scheduled ahead of time on one `AudioContext` when
  playback starts (a click on `#prompt`, or Space), at `AC.currentTime + .08 - offset`.

## How it works

- **Picture.** Chromium (headless) loads `film?freeze&mute&t=0`. For `n = 0 .. DUR*fps-1` it calls
  `__render(n/fps)`, screenshots (PNG) and pipes the frames to ffmpeg: H.264, `yuv420p`, CRF 18,
  BT.709 tagged. Frame count is exactly `DUR*fps`; the last frame is at `DUR - 1/fps`.
- **Sound.** A second page loads the film without `freeze` or `mute`, with `AudioContext` replaced (before
  any film script runs) by a subclass of `OfflineAudioContext`, `Math.random` seeded (the films fill their
  noise buffer with it, so the sound is the same every run) and `requestAnimationFrame` stubbed (only the
  scheduling is wanted, not playback). The exporter clicks the film's own play prompt, so the film's own
  scheduler runs, then `startRendering()`s `DUR + 0.08` seconds, drops the first 0.08 s (the lead the films
  schedule ahead of their clock, so a sound at film time `x` lands at `x` in the mp4), applies a 0.15 s
  fade-out so the cut at `DUR` does not click, and writes a 48 kHz stereo WAV.
- **Mux.** Picture and WAV go through one ffmpeg call: AAC 192k, `-movflags +faststart`.
- **No silent file passed off as sound.** If the film never creates an `AudioContext` after its start, or the
  render is all zeros, the export fails and says so. `--no-audio` is the only way to get a picture-only mp4.

## `--all`

Exports every `.html` in `<films-dir>` to `<out-dir>/<name>.mp4`, one after another, keeps going after a
failure, prints a per-file summary and exits 1 if any failed. `--skip-existing` leaves films that already
have an mp4. Expect about 5 s of wall time per second of film at 1080p.

## What the self-check proves

`selfcheck.cjs` exports `fixture.html` (2 s, one 440 Hz tone, same contract) and asserts, with ffprobe and
ffmpeg `volumedetect`:

- a video stream (`h264`, `yuv420p`) and an audio stream (`aac`);
- video duration and audio duration each within 0.1 s of 2.0;
- frame count equals `fps * 2` (60 at 30 fps), counted by decoding, not read from metadata;
- audio is not silent: mean volume above -60 dB;
- the first and last frame hash differently (the picture really stepped).

It proves the pipeline works end to end. It does not prove a particular film's sound is right; for that,
`volumedetect` the real export and check that silences sit where the film puts them.

## Not covered

- A 9:16 cut. The films are laid out for 1920x1080; `--width/--height` changes the viewport, and a film
  scales its stage to fit, so a vertical viewport letterboxes rather than re-lays-out.
- `promo-bumpers.html` is five 6-second bumpers in one 30-second file and exports as one mp4.
