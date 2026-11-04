# Video editor libraries vs. our ffmpeg storyboard editor (2026-11-01)

**Question (Ron):** should Desk Studio adopt an existing open-source video editor library
instead of building more on its own vanilla JS timeline + ffmpeg export?

**Verdict: keep building on ffmpeg now. Re-evaluate Elah (`@elah/core`, the React-free engine)
when we add transitions, multi-track or audio mixing, and only if it is still active by then.**
OpenReel and OpenCut are full apps, not libraries. Neither can be embedded today.

Scope: three named projects plus a one-line pass over others. Data pulled 2026-11-01 from the
GitHub REST API (repo, commits, releases, tags, contributors), raw LICENSE files, package.json
manifests, the npm registry and MDN browser-compat-data (HEAD 2026-10-31). No code was
installed or run. Integration-cost figures are my estimates, not measurements.

## Where we are today (for the cost column)

- Desk Studio timeline/preview JS: about 1,180 lines across the `desk-v1-studio-*.js` modules
  (`static/js/desk-v1-studio.js` is 1,626 lines including the article side). Vanilla ES modules, no build step.
- Export: `mc/desk_stitch.py` drives a user-approved ffmpeg add-on, never PATH. It runs on the
  server, so it works whatever the client browser is (Firefox Android, Safari, Android WebView).
- Clayrune is MIT (`LICENSE`), so a GPL dependency that we bundle would change what we can ship.

## Comparison

| | Elah | OpenReel Video | OpenCut |
|---|---|---|---|
| Repo | [elahlabs/elah](https://github.com/elahlabs/elah) | [Augani/openreel-video](https://github.com/Augani/openreel-video) | [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) |
| License (LICENSE file read) | **Apache-2.0**: root and each of `packages/{core,editor,timeline,react,cli}/LICENSE`; npm says the same | **MIT** ("Copyright (c) 2024-2026 Augustus Otu and Contributors"). **But** `packages/core` depends on `@ffmpeg/core` 0.12.6, which npm lists as **GPL-2.0-or-later**, and `apps/desktop/LICENSES/` ships GPL-2.0, GPL-3.0, FFMPEG and BLENDER texts | **MIT** ("Copyright 2026 OpenCut") |
| Non-OSI / headcount limits | None found | None in the root license; GPL parts sit in the dependency tree | None found |
| Created | 2026-06-09 | 2026-01-19 | 2025-06-22 |
| Last commit | 2026-10-29 (pushed 2026-10-31) | 2026-10-17 | 2026-09-24 (CI: FFmpeg build workflow). Last product code: 2026-08-01 (GPUI desktop primitives) |
| Latest release | No GitHub Releases. Tags `v1.0.0` and `cli-v1.0.0`; npm `1.0.0` published 2026-10-28 (0.6.0 on 2026-10-02) | `v1.0.0-alpha.17`, 2026-08-29 (desktop builds) | `v0.3.0`, 2026-04-15; `ffmpeg-8.1.3-1` binary release 2026-09-24 |
| Stars / forks / contributors | 71 / 11 / 6 | 5,548 / 821 / 7 | 97,089 / 9,663 / about 101 |
| Library or app | **Library.** Published packages: `@elah/core` (engine, no React), `@elah/timeline`, `@elah/editor`, `@elah/react`, `@elah/cli`, plus `@elah/react-native` at 0.x | **App only.** Every package is `"private": true`; `@openreel/core` is not on npm (404) | **App only, and mid-rewrite.** README: "being rewritten from the ground up"; Editor API, plugins, headless mode and Rust core are listed as *coming*. Outside contributions are not accepted yet |
| Framework | `@elah/core`: none (immer, zustand/vanilla, mediabunny). Timeline/editor UI: React >= 18 + react-dom + lucide-react as peers | React 19, Vite, pnpm monorepo, Zustand, Three.js, WebGPU, mediabunny, ffmpeg.wasm; Electron desktop | Web: React 19, TanStack Start. Desktop: Rust + GPUI |
| Build tooling for us | ESM + TS types; we would need a bundler step (esbuild) to vendor it. Whether it runs from an import map without a bundler (export worker uses `import.meta`): **unverified** | Fork the monorepo | n/a |
| Export | **Client-side**: WebCodecs + OffscreenCanvas in a worker. Docs: "in practice Chrome or Edge"; effects need WebGL2. Optional `@elah/cli` renders headless via Playwright/Chrome, with an HTTP render server | Client-side in browser (MP4 H.264, WebM); native FFmpeg on desktop for ProRes/alpha | Classic version: client-side (method **unverified**). Rewrite: headless mode planned, not shipped |
| Integration into our vanilla SPA | **Medium (est.)** for engine-only: swap our preview for `TimelineEngine` + `PlaybackEngine` + `GpuRenderer`, keep our own timeline UI, add one esbuild step. **Large** if we adopt `@elah/timeline`/`@elah/editor`: puts a React island in a no-React app | **Very large**: fork a React 19 app and strip it down; we would own the fork | **Not possible today**: no API to embed. Watch for the announced Editor API |
| Notable caveats | 5 months old, 6 contributors. Stores are module singletons, so **one project per page** (its own README). 1.0 follows semver | 5.5k stars, but 7 contributors and essentially one maintainer (README credits and sponsor link) | The classic repo [opencut-classic](https://github.com/OpenCut-app/opencut-classic) is **archived** (last push 2026-05-17), even though the README still calls it "the one to reach for today" |

### Browser support for client-side export (WebCodecs; MDN BCD, 2026-11-01)

`VideoEncoder` and `VideoDecoder`: Chrome 94, Firefox 130 (desktop), Safari 16.4, and **Android
WebView and Chrome Android mirror Chrome (94)**. `AudioEncoder`: Safari only from 26.
**Firefox for Android: not supported.** BCD covers whether the API exists. Whether an
Android device has a hardware H.264 *encoder* available to WebView is **unverified**. That is
why Elah and OpenReel both tell users to use Chrome or Edge. Our server-side ffmpeg export has
no such dependency.

## Other libraries (one line each, not deep-checked)

- **mediabunny** ([Vanilagy/mediabunny](https://github.com/Vanilagy/mediabunny)): **MPL-2.0** (file-level copyleft, fine if we don't modify it), 7,420 stars, v1.65.0 on 2026-10-30. Pure-TS demux/mux on top of WebCodecs. Elah and OpenReel both use it. It is the lowest-level option if we ever want export in the browser.
- **etro** ([etro-js/etro](https://github.com/etro-js/etro)): **GPL-3.0**, not MIT (LICENSE read). 1,154 stars, v0.14.1 on 2026-08-12. Bundling it would GPL our distribution, so **avoid**.
- **ffmpeg.wasm** ([ffmpegwasm/ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm)): the wrapper `@ffmpeg/ffmpeg` is MIT, but `@ffmpeg/core` is **GPL-2.0-or-later**. Last npm release 2025-01-07. It does the same job as our native ffmpeg add-on but slower.
- **WebAV** ([WebAV-Tech/WebAV](https://github.com/WebAV-Tech/WebAV), formerly bilibili/WebAV): MIT, 2,094 stars, last push 2026-01-10. A WebCodecs SDK. Activity is slowing.
- **Omniclip** ([omni-media/omniclip](https://github.com/omni-media/omniclip)): MIT, 1,468 stars, pushed 2026-10-31. Whether it can be embedded as a library is **unverified** (it presents as an app).
- **Diffusion Studio core** ([diffusionstudio/core](https://github.com/diffusionstudio/core)): GitHub shows MPL-2.0, but its README says free use **keeps a "Made with Diffusion Studio" watermark**, and a paid key removes it. Last push 2025-11-18. **Avoid.**
- **Twick** ([ncounterspecialist/twick](https://github.com/ncounterspecialist/twick)): "Sustainable Use License v1.0", which is **non-OSI**. **Avoid.**
- **OpenVideo react-video-editor** (formerly designcombo): "OpenVideo License", two tiers; for-profit companies above a size need a company license. **Headcount-limited, like Remotion. Avoid.**
- **Remotion**: already dropped by standing position (2026-10-01).

## Why this verdict

1. **Our export is the strength, not the gap.** Both serious candidates export in the browser
   and recommend Chrome or Edge for it. Our ffmpeg export runs on the server and does not care
   which browser the user has. Adopting either would add a browser requirement, not remove work.
2. **Only Elah is a library**, and its framework-agnostic `@elah/core` is the only part that fits
   a no-React SPA. Its UI packages need React.
3. **Elah is young**: 5 months old, 6 contributors, 71 stars, 1.0 shipped four days ago. Its
   scope is good: tracks, keyframes, 9 transitions, 23 GPU effects, a per-clip audio envelope,
   and a JSON `VideoSpec` designed for models to write, which suits agent-authored video. That
   scope is exactly what we would otherwise build next.
4. **Our current scope is simple** (scene order, trim, stills, text cards, preview). It does not
   need a WebGL engine, and the scenes are already shipped.

## Trigger to reopen

Re-evaluate `@elah/core` (engine only, our UI kept, ffmpeg kept as the export path) when **any**
of these lands on the Desk roadmap: transitions between scenes, more than one video/audio track,
an audio mix (music under voice, ducking), or keyframed motion on text cards. Check at that point:
Elah commits in the prior 90 days, contributor count, an import-map or no-bundler load test, and
whether its project JSON can be rendered by our ffmpeg path or only by its own Chromium export.

## Unverified

- Whether Elah's dist loads without a bundler (worker plus `import.meta`).
- Hardware H.264 encode availability in Android WebView on real devices.
- How OpenCut classic exported. The purpose of `apps/cloud` in OpenReel.
- The OpenCut contributor count (about 101) comes from the API's anonymous-contributor paging.
