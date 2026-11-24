#!/usr/bin/env node
'use strict';
/* Exports tools/film-export/fixture.html and checks the mp4 with ffprobe/ffmpeg.
   node tools/film-export/selfcheck.cjs       (exit 0 = every assertion held) */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const FPS = 30, SECONDS = 2;
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'film-selfcheck-'));
const out = path.join(work, 'fixture.mp4');
const fails = [];
const facts = [];
function check(name, ok, detail) { (ok ? facts : fails).push(`${name}: ${detail}`); }
function run(cmd, args) { return spawnSync(cmd, args, { encoding: 'utf8', maxBuffer: 1 << 26 }); }

try {
  const ex = run(process.execPath, [path.join(__dirname, 'export.cjs'), path.join(__dirname, 'fixture.html'), out, '--fps', String(FPS)]);
  if (ex.status !== 0) {
    console.error('selfcheck FAILED: export exited ' + ex.status + '\n' + ex.stdout + ex.stderr);
    process.exit(1);
  }
  const pr = run('ffprobe', ['-v', 'error', '-count_frames', '-show_entries',
    'stream=codec_type,codec_name,pix_fmt,width,height,nb_read_frames,duration', '-of', 'json', out]);
  if (pr.status !== 0) { console.error('selfcheck FAILED: ffprobe: ' + pr.stderr); process.exit(1); }
  const streams = JSON.parse(pr.stdout).streams;
  const v = streams.find((s) => s.codec_type === 'video'), a = streams.find((s) => s.codec_type === 'audio');
  check('video stream', !!v && v.codec_name === 'h264' && v.pix_fmt === 'yuv420p', v ? `${v.codec_name} ${v.pix_fmt} ${v.width}x${v.height}` : 'missing');
  check('audio stream', !!a && a.codec_name === 'aac', a ? a.codec_name : 'missing');
  const dur = v ? Number(v.duration) : NaN;
  check('duration', Math.abs(dur - SECONDS) <= 0.1, `${dur}s (want ${SECONDS} +- 0.1)`);
  const adur = a ? Number(a.duration) : NaN;
  check('audio duration', Math.abs(adur - SECONDS) <= 0.1, `${adur}s (want ${SECONDS} +- 0.1)`);
  const frames = v ? Number(v.nb_read_frames) : NaN;
  check('frame count', frames === FPS * SECONDS, `${frames} (want ${FPS * SECONDS})`);

  const vd = run('ffmpeg', ['-hide_banner', '-nostats', '-i', out, '-vn', '-af', 'volumedetect', '-f', 'null', '-']);
  const mean = /mean_volume:\s*(-?[\d.]+) dB/.exec(vd.stderr), max = /max_volume:\s*(-?[\d.]+) dB/.exec(vd.stderr);
  check('audio not silent', !!mean && Number(mean[1]) > -60, mean ? `mean ${mean[1]} dB, max ${max ? max[1] : '?'} dB (want mean > -60)` : 'volumedetect printed no mean');

  /* picture is stepped, not a still: first and last frame must differ */
  const md5 = (n) => {
    const r = run('ffmpeg', ['-hide_banner', '-v', 'error', '-i', out, '-vf', `trim=start_frame=${n}:end_frame=${n + 1}`, '-frames:v', '1', '-f', 'md5', '-']);
    return /^MD5=[0-9a-f]{32}/.test(r.stdout.trim()) ? r.stdout.trim() : '';
  };
  const first = md5(0), last = md5(FPS * SECONDS - 1);
  check('frames differ', !!first && !!last && first !== last, !first || !last ? 'could not hash a frame' : first !== last ? 'first and last frame differ' : 'first and last frame are identical');
} finally {
  fs.rmSync(work, { recursive: true, force: true });
}

if (fails.length) {
  console.error('selfcheck FAILED:\n  ' + fails.join('\n  ') + (facts.length ? '\npassed:\n  ' + facts.join('\n  ') : ''));
  process.exit(1);
}
console.log('selfcheck OK: ' + facts.join(' | '));
