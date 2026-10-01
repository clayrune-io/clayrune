# Desk v1: generation engines scan (MC-1019, backlog c7ac1e8c)

Research only. No product code touched. Branch `desk-engines-scan`, cut from master `16c5fa1c`.
Author: Quill. Every page was read on **2026-09-30 PDT (2026-10-01 UTC)** unless another date is given.
Three registers, kept apart: **Found** is what a source says. **Reading** is my inference from it.
**Dark** is what I could not establish.

## Findings first

1. **Imagen is gone, and Nano Banana (2.5) shuts down on 2026-10-02.** Found: the Gemini API Imagen page
   says Imagen "is now shut down and no longer available in the Gemini API". The pricing page lists
   `gemini-2.5-flash-image` as "deprecated and will be shut down on October 2, 2026". Google image
   generation means `gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image` and `gemini-3-pro-image`.
2. **Sora leaves the "later" list.** Found: OpenAI's deprecations page says the Sora 2 models and the
   Videos API were shut down on 2026-09-24 (announced 2026-03-24), with no replacement named.
   Independent press reports the same date.
3. **Every Veo model on the Gemini API is Preview, and Veo has no 1:1.** Found: Veo 3.1, 3.1 Fast and
   3.1 Lite are all `-preview`, and Veo 3.0 is deprecated. Clips are 4, 6 or 8 s, 16:9 or 9:16 only.
   On 3.1 and 3.1 Fast, 1080p, 4K, reference images and extension each require 8 s. Videos are deleted from
   Google's servers after 2 days. There is no free tier.
4. **Higgsfield's developer API is two weeks old, and it is not the subscription.** Found: launched
   2026-09-16. It is self-serve and pay-as-you-go in USD, and one key (ID + secret) covers 50+ models.
   Plan credits do not work on it. The OAuth MCP (`mcp.higgsfield.ai/mcp`) spends plan credits and
   "isn't the Higgsfield API". It is the only engine here with a **cost-estimate endpoint**.
5. **OpenAI's current image models are `gpt-image-2.5-sunburst` and `gpt-image-2.5-flare`**
   (2026-09-08 snapshots). Found: DALL·E was shut down on 2026-05-12. `gpt-image-1.5`, `gpt-image-1-mini`
   and `chatgpt-image-latest` shut down on 2026-12-01. Pricing is per token. Dark: a per-image dollar
   figure exists only in an interactive calculator I could not read.
6. **Remotion's licence lands on whoever runs it, and that means our users.** Found: it is free for
   individuals, teams of up to 3 people and non-profits. Anyone else needs a Company License. The
   pending v5.0 terms are stricter for an open-source app. A user who "is able to view or modify" a
   Remotion codebase "is considered to be directly engaging" and needs "their own Company License if
   they qualify as a Company License User". Headcount aggregates "across all involved parties", which
   are those that "own, control, or directly use the Remotion codebase". A desktop app may bundle
   Remotion only behind "an abstraction layer" the end user cannot edit through. Reading: an MIT repo
   hands every user the compositions, so under v5 every user is directly engaging. Dark: whether
   Clayrune and its users aggregate as "involved parties". If they did, the combined headcount would
   pass 3 and the free tier would apply to nobody. Only Remotion can answer that, in writing.

## The table (primary engines)

| | Higgsfield API | Google Veo (Gemini API) | Remotion (local) | Google Gemini image | OpenAI image |
|---|---|---|---|---|---|
| **Public API today** | Yes, self-serve since 2026-09-16, for individuals or companies. No waitlist found | Yes, but all Veo 3.1 models are Preview. `veo-3.0-*` is deprecated | No API. An npm library that renders on the user's machine (`remotion` 4.0.531, published 2026-09-30) | Yes: `gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`. 2.5 Flash Image shuts down 2026-10-02. Imagen is shut down | Yes: `gpt-image-2.5-sunburst` and `-flare`. `gpt-image-2` is current. 1.5, 1-mini and chatgpt-image-latest shut down 2026-12-01 |
| **What it makes** | Video and image | Video with native audio | Video and stills, composed from code and assets. Not a generative model | Image, plus edits | Image, plus edits |
| **Auth; can the user paste their own key?** | Key ID + secret, sent as `Authorization: Key <id>:<secret>`. Users create their own at console.higgsfield.ai | API key from AI Studio, sent as `x-goog-api-key`. Each key is tied to the user's Cloud project | None | Same Google key as Veo | API key, sent as `Authorization: Bearer`. GPT Image "may" require Organization Verification |
| **Job model** | Async. Submit, get back `request_id` + `status_url`, then poll. Webhooks exist (`hf_webhook`) but need a public HTTPS endpoint and document no signature | Async long-running operation (`predictLongRunning`), polled with `operations.get`. 11 s to 6 min. No webhook documented | Local process | Sync. Docs now show `interactions.create`, returning base64 inline | Sync, with optional streamed partial images. Batch for async |
| **Reference images (the user's product shots)** | Depends on the model. Passed as an HTTPS URL or via presigned upload (jpeg/png/webp/gif) | Up to 3 "asset" references. 3.1 and 3.1 Fast only, 8 s only | Any local file | 3.1 Flash: up to 10 object + 4 character images. 3 Pro: up to 6 object + 5 character. Flash Lite: up to 14 object | Multiple images to `/v1/images/edits`. Max count: Dark |
| **Image-to-video** | Depends on the model. Example: Kling 2.5 Turbo Pro takes `image_url`, 5 or 10 s | Yes, the image is the first frame. First+last frame and extension (7 s per call, up to 20 times, so 148 s, 720p only) on 3.1 and 3.1 Fast. Lite lists text and image input only | Animates stills | n/a | n/a |
| **Max duration per call** | Depends on the model (Kling 2.5 i2v 5/10 s, Hailuo 2.3 6/10 s) | 4, 6 or 8 s | Unbounded | n/a | n/a |
| **1:1 / 9:16 / 16:9** | Depends on the model. Soul has all three among 10 ratios. Kling i2v has no ratio field | **16:9 and 9:16 only** | Any | All three, among 10 ratios listed for 3.1 Flash and Lite | 1024², 1536×1024, 1024×1536, or custom W×H (multiples of 16, ratio 1:3 to 3:1, no edge over 3840). Example: 1536×864 |
| **Output** | URLs in `video.url` / `images[].url` | MP4, 24 fps, with audio. 720p, 1080p or 4K. SynthID watermark | Local file | Base64 PNG/JPEG, 0.5K to 4K. SynthID watermark | Base64 PNG by default (JPEG/WebP available). Transparent background option |
| **How long the output is kept** | At least 7 days | **2 days** | Local | Returned inline | Returned inline |
| **Price** | Set per model. Video per second, e.g. Kling 2.5 $0.042, Kling 3.0 $0.084, Seedance 2.5 $0.2057. Images e.g. Soul 2 $0.0032. Failed and `nsfw` runs are free. `POST /estimate/<model>` | Per second with audio. 3.1: $0.40 (720p/1080p), $0.60 (4K). Fast: $0.10 / $0.12 / $0.30. Lite: $0.05 (720p), $0.08 (1080p) | $0 for individuals, teams of up to 3, non-profits. Otherwise Creators at $25/seat/mo, or Automators at $0.01/render with a $100/mo minimum | 3.1 Flash: $0.067 (1K), $0.101 (2K), $0.151 (4K). Lite: $0.0336 (1K). 3 Pro: $0.134 (1K/2K), $0.24 (4K). Batch is 50% off | $30 per 1M image-output tokens ($15 in Batch), $8 per 1M image-input, $5 per 1M text-input. Per image: Dark |
| **Free tier** | $15 launch offer for verifying a business email and adding a card. $5 minimum top-up. Funds expire after 1 year | None for Veo | n/a | None for image models | Not checked |
| **Rate limits** | 20 concurrent requests per key at the start (the docs say it varies by account and model). Going over returns HTTP 400. No `Retry-After` header | Per project, by tier (T1: billing linked. T2: $100 paid + 3 days. T3: $1,000 + 30 days). The numbers are shown only in AI Studio | Bound by the machine | Same tiers as Veo | gpt-image-2.5, images per minute: T1 5, T2 20, T3 50, T4 150, T5 250 |
| **Commercial terms** | ToU §4.4: Higgsfield claims no ownership and puts no restriction on commercial use. May train on inputs and outputs (except Enterprise) | "Google won't claim ownership." EEA/CH/UK users must be on Paid Services. 18+ only | The output media is unrestricted. The licence covers use of the software | Same as Veo | OSA §4.1: the customer "owns all Output" |
| **Flags** | The API is not the app. Plan credits and the app's "unlimited" don't apply. The API catalog has no Veo, Sora, Nano Banana or GPT Image | Preview only. The Gemini API serves an allowlist of countries. People: only `allow_adult` in the EU, UK, CH and MENA | Source-available, not OSI open source. Pending v5.0 terms: a user who can view or modify the compositions needs their own licence at 4+ people, and headcounts of "involved parties" aggregate | Imagen is gone. 2.5 Flash shuts down 2026-10-02 | Organization Verification. The Sora API is gone (2026-09-24) |

## Notes per engine

### Higgsfield (video + image)

- **Found.** Base URL `https://api.higgsfield.ai`. Each model is its own POST path; the OpenAPI spec
  carries `/higgsfield-ai/soul/standard`, `/kling-video/v2.5-turbo/pro/image-to-video` and
  `/minimax/hailuo-2.3/standard/text-to-video`. Statuses are `queued`, `in_progress`, `completed`,
  `failed`, `nsfw` and `canceled`, and a request can be cancelled only while it is queued. The estimate
  call is `POST /estimate/<same model path>` with the same body. It returns `{"credits": "…", "usd": "…"}`.
  Uploads go through `POST /files/generate-upload-url`, which returns a presigned URL valid for 1 hour
  and then a `public_url`. Webhook endpoints must be public HTTPS and answer within 10 s. Deliveries
  are retried for 2 hours, and no signature scheme is documented. ToU §11.3 forbids embedding keys in
  client-side code. ToU §5.5: disclose that output is AI-generated where the law requires it, and keep
  provenance signals intact.
- **Found: Higgsfield's own prices disagree.** The 2026-09-16 blog lists Kling 3.0 at $0.112/s and
  Marketing Studio Image at $0.0059. The product page read today lists $0.084/s and $0.0126.
- **Reading.** Higgsfield is an aggregator. In the picker, "Higgsfield" is really a list of models with
  different input schemas, so the connector needs per-model capability data, not one Higgsfield shape.
  Treat `/estimate` as the price of record and never cache a table. Webhooks don't fit a local install:
  there is no public endpoint unless the tunnel is on, and unsigned payloads would be a spoofing
  surface. So we poll. The `soul/standard` schema has no image input, so the cheapest image model
  cannot use the user's product screenshots.
- **Dark.** Schemas for models other than the three read. How long uploads are kept, and the size cap.
  Whether image-to-video follows the input image's aspect ratio.

### Google Veo (Gemini API)

- **Found.** The models and constraints are in the table. The person-generation setting is `allow_all`
  for text-to-video and `allow_adult` for image, interpolation and reference modes, with `allow_adult`
  the only value allowed in the EU, UK, CH and MENA. One video per request. `seed` is available. Google's
  sample code polls every 10 s.
- **Reading.** A Desk storyboard of N scenes means N Veo calls of up to 8 s each, stitched together
  locally. A 1:1 post needs a 16:9 or 9:16 render cropped locally. Download on completion, because
  Google keeps the file for only 2 days. Preview models can change or disappear, so keep model IDs in
  data, not code. The Desk fixture's single `renderBudget.rate: 0.40` equals Veo 3.1 Standard's price
  per second. The picker needs a rate per model: Lite at 720p costs one eighth of that.
- **Dark.** Rate-limit numbers. Whether failed or safety-blocked generations are billed. Which input
  image formats and sizes are accepted.

### Remotion (local)

- **Found.** The licence shipped inside `remotion@4.0.531` grants the Free License to "an individual",
  "a for-profit organization with up to 3 employees" and non-profits, and covers commercial use. It
  forbids copying or modifying Remotion code "for the purpose of selling … or sublicensing your own
  derivate". The licence FAQ defines one render as "the successful generation of a video, audio, GIF,
  still image or PDF", and previews in the Studio or Player don't count. The v5.0 terms take effect
  "upon the release of Remotion 5.0"; npm's latest on 2026-09-30 was 4.0.531, with no 5.x version.
  Those terms:
  - **End-user code access:** a user "granted direct access to a Remotion codebase and is able to
    view or modify it … is considered to be directly engaging with a Remotion project", and must
    obtain "their own Company License if they qualify as a Company License User, independently of any
    license held by the service or platform provider";
  - **Team size:** a licence is mandatory when "the total number of personnel across all involved
    parties that operate the Remotion Software reaches the threshold of four or more". Involved
    parties "own, control, or directly use the Remotion codebase", and one that "merely receives
    generated media" is not counted. The worked examples are a studio and its client, or two
    agencies, on one codebase;
  - **Native application distribution:** a desktop app (Electron and Tauri are named) may bundle
    Remotion "provided the User introduces an abstraction layer between the end-user and the Remotion
    Software", so "the end-user must not have direct access to edit or upload Remotion code". "The
    regular licensing requirements still apply, including reporting renders". If the app stops being
    maintained, its distributor must hold "an active Company License for at least one year" after the
    last release;
  - **Rendering services** may not let end users "bring/upload their own Remotion code".

  Render reporting sends the licence key, event type, success or failure, and a production or
  development flag. IP and host domain are added only for client-side `@remotion/web-renderer`
  renders, and no media content is sent. Reporting becomes mandatory for Automators at 5.0 and stays
  optional for Creators. The FAQ says Remotion "is source-available software, but it is not open
  source software" by the OSI definition. It also allows "a service that generates Remotion code
  using artificial intelligence and renders it", whose customers "don't need to have a Company
  License".
- **Reading.** Remotion is a compositor, not a generator. It animates the user's real screenshots,
  captions and clips. That makes it the right "Local" engine, and also the natural assembler for clips
  rendered in the cloud. The licence follows whoever runs the software. That is free for individuals
  and teams of up to 3, but a for-profit company of 4 or more using the local engine needs its own Remotion
  licence, today and under v5. Under v5, an MIT repo gives every user view-and-modify access to the
  compositions, so every user is "directly engaging", and the abstraction-layer route for bundled
  desktop apps does not fit an app whose source the user holds. The FAQ's AI-service answer describes
  a hosted service that renders for customers. Clayrune renders on the user's own machine, so the
  user is the one operating Remotion. Install Remotion from npm on the user's machine at opt-in (not
  inside the installer or `.app`), show a licence notice on the Connect step, and do not vendor
  Remotion code into the repo. On a plain reading that keeps Clayrune's own builds out of the bundling
  clause. It does not settle aggregation.
- **Dark.** Whether Clayrune's maintainers and its users are "involved parties" on one Remotion
  project whose headcounts aggregate. The worked examples are collaborations, not an open-source app
  and its users. If they did aggregate, the combined headcount would pass 3 and the free tier would
  apply to nobody. Whether the one-year clause binds a distributor who is otherwise free-eligible.
  Getting an answer means writing to Remotion, which goes outside the company and is Ron's call.

### Google Gemini image (Nano Banana family)

- **Found.** The models, reference counts, ratios, resolutions (`image_size`, uppercase K) and prices
  are in the table. Every image carries a SynthID watermark. None of these models is on the free tier.
  Input is billed on top: $0.50 per 1M input tokens for 3.1 Flash, $2.00 for 3 Pro.
- **Reading.** One Google key serves both Veo and image, so Connections should show one Google
  connector covering two kinds. 3.1 Flash at 1K ($0.067) fits multi-reference product shots, and Flash
  Lite ($0.0336) fits drafts.
- **Dark.** 3 Pro's list of aspect ratios. Whether `generateContent` still works for these models (the
  docs show only the Interactions API). Per-project limits.

### OpenAI image

- **Found.** Quality levels are `low`, `medium`, `high`, `xhigh`, `max` and `auto` (xhigh and max are
  2.5-only). `moderation` is `auto` or `low`. "Complex prompts may take up to 2 minutes." Endpoints:
  `v1/images/generations`, `v1/images/edits`, `v1/batch`. OSA v.010126 §4.1: the customer "owns all
  Output", and OpenAI assigns its rights to the customer.
- **Reading.** Users can bring their own key, with two frictions: Organization Verification, and
  5 images per minute at Tier 1. Label OpenAI estimates "approximate" until per-image figures are
  measured.
- **Dark.** The per-image price of gpt-image-2.5 at each quality and size. The maximum number of input
  images for edits. Whether outputs carry C2PA metadata. OpenAI's list of supported countries.

## Secondary pass (one line each)

- **Runway**: public self-serve API, $0.01 per credit. gen4_turbo costs 5 credits/s ($0.05/s). It also
  resells Veo 3.1 (10 to 40 credits/s), Seedance, Wan and GPT Image 2. Async tasks are polled at
  `GET /v1/tasks/{id}` ([pricing](https://docs.dev.runwayml.com/guides/pricing/)).
- **Kling**: official API at kling.ai/dev, where klingai.com links now redirect. The pricing page
  renders client-side, so its unit prices are **unverified** (only a search-index excerpt). Kling 2.5
  to 3.0 is also on Higgsfield at $0.042 to $0.084/s.
- **Sora API**: **shut down 2026-09-24**, no replacement
  ([deprecations](https://developers.openai.com/api/docs/deprecations)). Drop it.
- **Luma**: API is live. Ray3.2 is billed per 5 s block: $0.15 (540p) / $0.30 (720p) / $1.20 (1080p)
  for 5 s. Uni-1.1 image costs $0.0404 at 2K, Max $0.10
  ([pricing](https://lumalabs.ai/api/pricing)). Auth and async details: docs moved to
  docs.agents.lumalabs.ai, not read.
- **Pika**: pika.art/api now redirects to a first-party API at [dev.pika.art](https://dev.pika.art/).
  Key via `X-API-Key` or Bearer, prepaid balance, no free tier. The catalog lists third-party models
  (Seedance 2.5, Wan 3.0, Seedream 5.0 Pro). Whether Pika's own models are in it: Dark.
- **Flux (BFL)**: FLUX.2 [pro] from $0.03, [flex] $0.05, [max] $0.07. Kontext [pro] $0.04. FLUX 3
  video costs $0.06/s (draft) to $0.80/s (UHD) ([BFL pricing](https://docs.bfl.ml/quick_start/pricing)).
  fal charges $0.03 for the first megapixel plus $0.015 for each additional one. Replicate price: Dark.
- **Ideogram**: `Api-Key` header. Image calls are sync unless `async` or `webhook_url` is set, and
  image URLs expire. Default limit is 10 requests in flight. Ideogram 4.0 costs $0.03 / $0.06 / $0.10
  per image (Turbo / Default / Quality) ([model page](https://ideogram.ai/models/4.0/)). 4.5 price: Dark.

## Recommended connector shape

Four connectors cover the first set: **Higgsfield**, **Google** (Veo + Gemini image on one key),
**OpenAI**, and **Local (Remotion)**. Every engine fills the same descriptor and speaks the same
request and job shapes. Statuses reuse `desk-v1-video.js` `JOB_COPY` (queued, rendering, ready, failed).

```text
EngineDescriptor            shipped as dated data, one per connector
  id                        "higgsfield" | "google" | "openai" | "local-remotion"
  auth                      { kind: "key_id_secret" | "api_key" | "none",
                              vault_entry: "<name>" }        # Higgsfield: user = key id, secret = key secret
  job_model                 "poll" | "sync" | "local"
  output_ttl_hours          168 Higgsfield (min) | 48 Veo | 0 inline (Gemini, OpenAI) | null local
  estimate                  "endpoint" (Higgsfield) | "price_table" | "free"
  prices_read               "2026-09-30"
  models[]                  ModelDescriptor

ModelDescriptor             capability gating is per MODEL, not per engine
  model_id, kind            "video" | "image"
  status, shutdown_date     "stable" | "preview" | "deprecated", ISO date or null
  inputs                    { text, first_frame, last_frame, reference_images_max,
                              reference_kinds: ["object","character","style","asset"] }
  durations_sec             [4,6,8] | [5,10] | "any"
  aspect_ratios             subset of the Desk's ["1:1","9:16","16:9"] the model can do natively
  resolutions               ["720p","1080p","4k"] | ["0.5K","1K","2K","4K"] | ["WxH"]
  constraints[]             e.g. { when: {resolution: ["1080p","4k"]}, require: {duration_sec: 8} }
  audio                     bool
  price                     { unit: "second"|"image"|"token", usd: {<config>: n}, read: date }

GenerationRequest           what every Desk surface sends (Render, Studio New image, per-scene image)
  engine_id, model_id, kind, prompt, negative_prompt?
  aspect_ratio, duration_sec?, resolution?, audio?, count?, seed?
  first_frame?, last_frame?, reference_images[]   AssetRef -> Material library item
  desk                      { piece_id, scene_id, revision, idempotency_key }

Estimate                    { usd, basis: "engine" | "table", read }    shown before submit
Job                         { job_id, engine_ref, status, failure?, progress?, outputs[], cost_usd }
  failure                   "moderation" | "auth" | "quota" | "invalid_input" | "engine" | "expired" | "canceled"
  outputs[]                 { local_path, mime, width, height, duration_sec }   always downloaded
```

| Engine | queued | rendering | ready | failed |
|---|---|---|---|---|
| Higgsfield | `queued` | `in_progress` | `completed` | `failed`; `nsfw` maps to moderation; `canceled` |
| Veo | (the operation is created) | `done: false` | `done: true` with a video | `done: true` with an error |
| Gemini image, OpenAI image | n/a | HTTP call in flight | 200 with an image | error response |
| Remotion | local queue | render running (only engine with a progress %) | file written | process error |

Rules every connector follows:

1. **Poll, never webhook.** Clayrune runs on the user's machine. Higgsfield's webhooks need a public
   HTTPS endpoint and are unsigned, and Veo documents no webhook. Poll with backoff, starting near
   Google's 10 s.
2. **Download on `ready`**, then point at the local file. Veo deletes after 2 days, Higgsfield after at
   least 7, and Ideogram URLs expire.
3. **One asset adapter per engine.** Higgsfield needs an HTTPS URL, which means a presigned upload. Veo
   and Gemini take inline image bytes. OpenAI takes a multipart upload to `/v1/images/edits`.
4. **Keys live in the vault (`mc/secrets_store.py`), server-side, and belong to the user.** All three
   vendors say keys must never be in client code. Clayrune ships no key.
5. **Grey out what a model can't do** (Veo 1:1; Veo 1080p/4K or references off 8 s; Flash Lite above 1K),
   or crop locally and say so in the UI.
6. **Show deprecation in the picker** from `status` + `shutdown_date`.
7. **Multi-scene video means one job per scene plus local assembly.** Cloud engines return clips of
   10 s or less, so the local compositor is needed whichever engine renders the scenes.
8. **Check the estimate against `renderBudget.perJobLimit` before submitting.** Higgsfield's
   `/estimate` is authoritative. Every other engine's figure is a dated table and is labelled as an
   estimate.

## Still dark, and what would close it

| Unknown | What would close it |
|---|---|
| OpenAI per-image price, gpt-image-2.5 | Read the calculator in the browser pane during an attended session (the steward fence blocked the pane in this dispatched run), or make one paid call per quality/size and read the billed tokens |
| Veo / Gemini rate-limit numbers | Open AI Studio's rate-limit page with a project that has billing enabled |
| Remotion v5: do Clayrune and its users aggregate as "involved parties"? Does the one-year clause bind a free-eligible distributor? | A written answer from Remotion (outside the company: Ron's call) |
| Higgsfield per-model schemas, upload retention, webhook signing | Model pages on docs.higgsfield.ai while wiring the model list. Ask on their Discord |
| Whether Veo bills failed or blocked runs | One deliberate blocked prompt on a test key, then check billing |
| Kling unit prices, Ideogram 4.5 price, Replicate Flux price | The pages render client-side. Read them in the browser pane |
| What replaces the Local engine if Remotion's answer is unfavourable | Not researched in this scan |

## Sources (all read 2026-09-30 PDT unless dated)

- Higgsfield: [API launch post, 2026-09-16](https://higgsfield.ai/blog/higgsfield-api) ·
  [video how-to, 2026-09-16](https://higgsfield.ai/blog/generate-ai-videos-higgsfield-api) ·
  [price list](https://higgsfield.ai/higgsfield-api) ·
  [What is the API, modified 2026-09-24](https://higgsfield.ai/creator-hub/help-center/integrations/what-is-the-higgsfield-api) ·
  [What is the MCP, modified 2026-09-24](https://higgsfield.ai/creator-hub/help-center/integrations/what-is-higgsfield-mcp) ·
  docs: [authentication](https://docs.higgsfield.ai/docs/authentication.md),
  [requests](https://docs.higgsfield.ai/docs/concepts/requests.md),
  [webhooks](https://docs.higgsfield.ai/docs/how-to/webhooks.md),
  [file uploads](https://docs.higgsfield.ai/docs/concepts/file-uploads.md),
  [rate limits](https://docs.higgsfield.ai/docs/concepts/rate-limits.md),
  [billing and retention](https://docs.higgsfield.ai/docs/concepts/billing-and-retention.md),
  [OpenAPI](https://docs.higgsfield.ai/docs/openapi.json),
  [model index](https://docs.higgsfield.ai/docs/models.md) ·
  [Terms of Use, updated 2026-07-26](https://higgsfield.ai/terms-of-use-agreement)
- Google: [Veo](https://ai.google.dev/gemini-api/docs/veo) ·
  [pricing, "Last updated 2026-10-01 UTC"](https://ai.google.dev/gemini-api/docs/pricing) ·
  [image generation](https://ai.google.dev/gemini-api/docs/image-generation) ·
  [Imagen (shut down)](https://ai.google.dev/gemini-api/docs/imagen) ·
  [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) ·
  [API keys](https://ai.google.dev/gemini-api/docs/api-key) ·
  [available regions](https://ai.google.dev/gemini-api/docs/available-regions) ·
  [Additional Terms, updated 2026-04-28](https://ai.google.dev/gemini-api/terms)
- OpenAI: [image generation](https://developers.openai.com/api/docs/guides/image-generation) ·
  [pricing](https://developers.openai.com/api/docs/pricing) ·
  [gpt-image-2.5-flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare) ·
  [gpt-image-2.5-sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst) ·
  [gpt-image-2](https://developers.openai.com/api/docs/models/gpt-image-2) ·
  [deprecations](https://developers.openai.com/api/docs/deprecations) ·
  [video generation (Sora, retired)](https://developers.openai.com/api/docs/guides/video-generation) ·
  [API auth](https://developers.openai.com/api/reference/overview) ·
  [Services Agreement v.010126, §4.1](https://cdn.openai.com/osa/openai-services-agreement.pdf)
- Remotion: `LICENSE.md` inside `remotion@4.0.531` (npm, published 2026-09-30) ·
  [pricing](https://www.remotion.pro/license) · [licence FAQ](https://www.remotion.dev/docs/license/faq) ·
  [v5.0 terms (pending)](https://www.remotion.dev/docs/license/terms) ·
  [@remotion/licensing](https://www.remotion.dev/docs/licensing/)
- Secondary: [Runway](https://docs.dev.runwayml.com/guides/pricing/) · [Luma](https://lumalabs.ai/api/pricing) ·
  [Pika](https://dev.pika.art/) · [BFL](https://docs.bfl.ml/quick_start/pricing) ·
  [fal FLUX.2 pro](https://fal.ai/models/fal-ai/flux-2-pro) · [Ideogram API](https://developer.ideogram.ai/) ·
  [Ideogram 4.0](https://ideogram.ai/models/4.0/) · Kling: kling.ai/dev/pricing (did not render)
