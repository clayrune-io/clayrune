# Studio local MP4 export

**Export** beside the preview controls joins the current standalone or campaign
timeline into a local MP4. It uses the approved ffmpeg add-on and makes no paid
engine, generation, upload, publishing or passcode request. Branch candidate
only: no merge, push or server restart.

## Modules and API

- `mc/desk_export.py`: command builder, validation, bounded jobs and worker.
  `mc/blueprints/desk_export_routes.py`: own blueprint with minimal server hooks.
- `static/js/desk-v1-studio-export.js` and its CSS: start, progress, polling,
  retry and download UI. Studio supplies owner, real scenes, pending-editor save
  callback and refresh hook. `static/index.html` loads both. No new dependency.
- `POST /api/desk/exports` accepts `{owner:{kind:"piece"|"studio",id},scenes}`
  and returns 202 with a job or held-approval explanation. Scenes are
  `{id,label,line,duration_sec,picture:{path,kind,title}|null}`. Owner validity,
  scene limits and media containment reuse the storyboard store. Malformed
  scenes reject the whole request; examples are excluded.
- `GET /api/desk/exports/<id>` reads `{status,progress,message,...}`: queued,
  running, done or failed. Approval absence returns held without a worker.
  `GET /api/desk/exports?kind=studio|piece&id=...` restores the latest job on
  reopening without starting another one.
- Completed jobs expose `download`, served by
  `GET /api/desk/exports/<id>/file` (MP4 attachment), or `?open=1` (inline).
  Only a completed job's own output is served; current explorer authorization
  is checked again at download time.

## Media and encoding

The UI snapshots exactly the entries Preview uses, in current array order and
with the same `durationSec` fallback. It commits pending editor text first.
Export does not read an older server list while a save is in flight. Example
scenes are skipped; empty boards disable Export.

Segments use H.264/yuv420p at **1280x720, 30 fps** with aspect-preserving scaling
and black padding. Audio is **AAC, 48 kHz, stereo**. Video starts at zero and is
trimmed to scene length. A short legacy clip holds its final frame; short audio
pads with silence. Pictures freeze their first frame for their scene length,
including animated inputs. Missing-media scenes use black.

Clip audio is detected with the approved ffmpeg itself; ffprobe approval is not
required for export. Only an explicit ?matches no streams? result supplies
silence. Corruption or another probe failure fails visibly. Pictures/text have
silent tracks. Matching streams are concatenated with stream-copy and MP4
faststart. Fractional times quantize to 30 fps; AAC/container padding can make
the output slightly longer than the timeline.

On Windows, an existing `%WINDIR%/Fonts/segoeui.ttf` supplies centered white
titles for text-only scenes. Titles are UTF-8 text files with drawtext expansion
off, never filter expressions. Other platforms, missing fonts, unusual filter
paths or unsupported drawtext produce plain black. A failed title encode
retries black and logs the reason. The result explicitly reports which title
treatment was used. No font dependency or PATH lookup is introduced.

Files use the existing `data/uploads/desk/library/video/Generated/` convention,
named `<owner-kind>-<owner-id>-export-<job-id>.mp4`. They appear in the material
library and remain on this machine. Sources and outputs must pass the current
file explorer's Home/registered-project policy, including private/link guards.
An external uploads directory needs an allowed project root. Nothing is written
to `data/projects/`.

## Jobs and approval

One local export runs at a time. Duplicate requests for the active owner return
the same job; another owner gets a visible busy explanation. At most 32 jobs
are retained; files remain when metadata is evicted. Progress advances after
each normalized scene, then joins the MP4. Temporary segments are removed;
errors log and remove partial outputs. Thread-start failures are visible.

Only `mc/desk_stitch.py`'s approved `ffmpeg_path`, `run_ffmpeg`, `request_ffmpeg`,
concat list and stitch command are used. Missing approval requests the existing
add-on card: approve in **Settings > Add-ons**, then explicitly retry **Export**.
The worker rechecks approval and holds the add-on while encoding. Nothing
installs automatically or expands a human-only gate.

The browser polls every 800 ms and releases its timer on navigation. Server
encoding continues independently; reopening restores the current job/download.
Jobs are in-memory: restart loses status and reports a clear retry message;
finished files remain in Generated. This version has no cancellation or durable
job recovery. A failed history read is visible but does not disable Export.

## Verification

Mocked command/job/route tests cover normalization, silence/preserved audio,
approval/retry, order, examples, malformed inputs, path containment, current-root
download refusal, queued/duplicate/busy jobs, failure cleanup, title fallback and
thread-start failure. Both new modules pass pyright basic with zero errors.
Validation result: **78 pytest checks passed**, including 23 export checks;
all **seven listed smokes returned 0**. The final preview smoke also verifies
Tab order and focus transfer through Space/Enter toggles at both viewport sizes.
The broader add-on regression test uses its own temporary add-on installation;
it does not change the live approval manifest.

Real approved-add-on verification used isolated scratch data and the actual
blueprint/worker: silent clip + still + text + short audio clip, **2.0 seconds**
requested -> **2.021333 seconds** MP4, **1280x720 at 30 fps**, stereo 48 kHz AAC.
Decoded frames confirmed order, still, black background and final-frame padding;
decoded PCM confirmed silent still/text and preserved clip sound. Windows titles
used Segoe UI. POST returned in approximately **0.2 seconds**, before encoding
finished. No PATH discovery, install or live Desk data was used.

```powershell
python -m pytest tests/test_desk_export.py tests/test_desk_storyboard.py tests/test_desk_storyboard_clips.py tests/test_addons_ffmpeg.py
pyright mc/desk_export.py mc/blueprints/desk_export_routes.py
node tools/smoke/desk-studio-preview.mjs
node tools/smoke/desk-studio-export.mjs
node tools/smoke/desk-studio-insert.mjs
node tools/smoke/desk-studio-timeline.mjs
node tools/smoke/desk-studio-uploaded-clips.mjs
node tools/smoke/desk-v1-live-storyboard.mjs
node tools/smoke/boot-smoke.mjs
```

The export smoke uses a fake job API and real worktree UI at 1440 and 390 for
campaign and standalone: snapshot/trim, approval/retry, progress, completed
links/reopen, failure/restart and cleanup. Screenshots are
`_scratch/desk-studio-export/{preview-idle,preview-playing,export-done}-{campaign,studio}-{1440,390}.png`;
export screenshots illustrate the disclosed plain-black fallback. The encoder
report is `_scratch/desk-studio-export/real-result.json`.

Worktree smokes use the main checkout's existing dependency junction; remove
that junction itself before committing. No npm install is needed. Native phone
WebView, arbitrary user codecs, macOS/Linux encoding and live server integration
are not verified by these checks.
