# RetroByte Video Output — Diagnosis & Improvement Plan

Session: 2026-11-12 · Agent: PCMedicalist · Scope: video quality on socials + Docker deployment

## EXECUTION LOG (what has been DONE, verified)

- [x] **Subject imagery** — 70 Commons photos cached + manifest; `WIKIMEDIA_TITLES`
  extended; 54/62 catalog subjects (87%) now show a REAL subject image instead of
  the generic room. QA'd visually (contact sheet), 8 off-topic images replaced.
- [x] **Caption truncation** — 5-word/2-line → clause-aware split + 10-word/3-line
  policy (box measured at 160px = exactly 3 lines at 43px). Captions now break on
  sentence/clause boundaries; pixel-measured inside the band.
- [x] **Scene model level-up** — SD1.5 640px/28 → **SDXL base 832px/30**.
  Measured: edge energy 33.8 vs 27.5; load 4.1s; generate 19.4s; peak VRAM 9.34GB.
  1024px OOMs (11.5GB card, ~6GB held by chat models) → 832px is the max that fits.
  Real E2E video verified: 832px SDXL scene + subject photo + captions in-band.
- [x] **QA metric** — `validate_render()` records `subject_coverage`.

## TL;DR

The videos are technically correct (1080×1920, 30fps, burned captions, approved avatar, local
Fenrir narration) but **content-weak**: 55 of 62 subjects show a generic sci-fi room instead of
the actual subject, the burned caption is truncated, and the audio is 24kHz mono. Zero of 41
audited videos ever showed a real subject image. The Docker containers are **not** in the video
path — they are the chat/bridge agent, so video quality is a host-pipeline problem.

---

## 1. ROOT CAUSE #1 — the subject is never shown (biggest issue)

**Evidence**
- `IMG_CACHE` (`/media/pcmedicalist/PCM/.pcmedicalist/image_cache/retrobyte-artifacts/`) is **empty** (0 files).
- `fetch_artifact()` requires a `manifest.json` entry with a matching sha256 → always returns `None`.
- `WIKIMEDIA_TITLES` maps only 15 legacy subjects; **55 of 62** current subjects have no mapping.
- `audit/*/render.json`: **0 of 41** videos have a `subject_reference_path` → no video ever showed a real subject.
- Confirmed visually: the PalmPilot reference (approved) shows a drawn PDA; current videos show a
  generic "sci-fi control room" (dark room + monitors) regardless of subject.

**Impact** — the hero visual is irrelevant to the topic. A viewer scrolling sees "COMPUTER MICROPHONE"
over a spaceship bridge. This is the single largest quality gap.

**Fixes (in priority order)**
1. Populate `IMG_CACHE` + `manifest.json`: download the 15 mapped Wikimedia Commons files, verify
   sha256, write manifest entries. Immediately fixes 15 subjects.
   - **Feasibility VERIFIED this session**: the Commons API resolves `File:` titles and serves the
     original image. Test: `File:Floppy_disk_2009_G1.jpg` → 3500×1700 JPEG, 1.6MB downloaded OK.
   - Recipe (verified):
     ```python
     url = "https://commons.wikimedia.org/w/api.php?" + urlencode({
         "action":"query","format":"json","prop":"imageinfo",
         "iiprop":"url|sha1|size","titles":"File:<NAME>.jpg"})
     # User-Agent REQUIRED (PCMedicalist-RetroByte/1.0)
     # read data["query"]["pages"][*]["imageinfo"][0]["url"] -> download -> sha256 -> manifest
     ```
   - Write `manifest.json` as `{"subjects": {"<subject key>": {"file": "<name>.jpg",
     "source": "File:<NAME>.jpg", "sha256": "<64hex>"}}}` — `fetch_artifact()` already reads exactly
     this shape (verified against the code).
2. Extend `WIKIMEDIA_TITLES` to cover all 62 catalog subjects (Commons file names are predictable;
   verify each resolves before adding).
3. For subjects with no Commons photo, add a **PIL vector illustration** path (like the existing
   `draw_pda()`) — a clean flat illustration reads better than a photo and is fully sovereign.
4. Add a QA gate: `validate_render()` should FAIL if `subject_visual == 'environment_only'` for a
   subject that has a mapping. That makes "generic room" impossible to ship silently.

## 2. ROOT CAUSE #2 — burned caption is truncated

**Evidence**
- Caption band crop shows "Meet Computer Microphone. A small" — cut mid-sentence.
- `caption_cues()` splits narration into 5-word groups, but `caption_layout()` caps at 2 lines and
  the box is 940×160px. Long first cues overflow and the text is clipped.
- `drawtext` uses `expansion=none` — no auto-fit; text wider than the box is clipped, not shrunk.

**Fixes**
1. Shrink-to-fit: compute the longest cue, reduce font until `text_w <= max_line_width`, or switch
   that cue to 3 lines.
2. Reserve the caption band: move the CTA ("Would you use one today?") into the same band and drop
   the separate `d.text((70,1850))` CTA so the caption has the full 1650–1810 box.
3. Shorten narration openings: "Meet X." + first detail is enough; the full sentence belongs in the
   audio, not the burned caption.

## 3. ROOT CAUSE #3 — audio quality

**Evidence**
- Current: AAC 24kHz, mono, ~78 kbps. Telephone-grade.
- Kokoro `am_fenrir` is a 24kHz model; ffmpeg copies it as-is.

**Fixes**
1. Upsample to 48kHz stereo at mux time (`-ar 48000 -ac 2 -b:a 128k`) — cheap, immediate perceived
   quality lift.
2. Add a short music bed (local, brand-consistent) under the narration at -18 LUFS ducking. Optional
   but a large social-quality signal.

## 4. ROOT CAUSE #4 — video bitrate / sharpness

**Evidence**
- Current: ~465 kbps at 1080×1920 (CRF 20, veryfast). Soft/blocky on high-contrast text.
- Text-heavy frames need more bitrate than talking-head footage.

**Fixes**
1. Raise to CRF 18 + `preset fast` (or CRF 17) — text edges sharpen noticeably.
2. Add `-tune stillimage`? No — there is avatar motion; use default tuning but bump bitrate floor:
   `-maxrate 1200k -bufsize 2400k`.
3. Render captions at full canvas resolution (already 1080 wide — good) but consider rendering the
   frame at 1080×1920 directly (already done) and avoid the extra resize.

## 5. ROOT CAUSE #5 — one format for all channels

**Evidence**
- The same 9:16 is sent to X, Instagram (reel), and TikTok.
- X/LinkedIn favor 1:1 or 16:9; a 9:16 on X is letterboxed and shrinks in-feed.

**Fixes**
1. The generator already has a 1:1 path (`disc_*_1x1.mp4` legacy). Re-enable a 1:1 crop/scale
   variant for X/LinkedIn while keeping 9:16 for IG/TikTok.
2. Per-channel caption length: X caption currently 235 chars (near the 280 limit) — trim to ~200.

## 6. DOCKER DEPLOYMENT — role clarified

| Container | Image | Role | In video path? |
|---|---|---|---|
| `retrobyte` | `0xpcmedicalist:latest` | Standalone chat/banter agent (uvicorn :8080) | **No** |
| `retrobyte-bridge` | `0xpcmedicalist-bridge:latest` | Discord/Telegram bridge | **No** |

Video generation runs **on the host** via the Hermes cron
(`retrobyte_discovery_gen.sh` → `retrobyte_discovery_video.py` in the diffusion venv).
Posting runs **on the host** via `retrobyte_heal.sh`. The Docker containers only serve the chat
agent — they do not affect video output. **No Docker change is needed for video quality.**

Note: the containers were built 4–7 weeks ago and are healthy; the video pipeline has since moved
on (the generator now uses local diffusion scenes + Kokoro, which the container does not contain).
If the goal is to make the video pipeline containerized, that is a separate migration project.

## 7. RECOMMENDED SEQUENCE

1. **Subject imagery** (highest impact) — populate the 15 mapped photos + extend mappings; add the
   QA gate so a generic-room video can never ship again.
2. **Caption fix** — shrink-to-fit + reclaim the band. Cheap, visible in every post.
3. **Audio bump** — 48kHz stereo 128k at mux. One-line change.
4. **Bitrate/CRF** — CRF 18, tuned for text. Re-encode settings.
5. **1:1 variant** for X/LinkedIn (larger effort; schedule after 1–4).

Each step should be verified with a real generated video + visual read before it goes live, per the
standing "intelligence over speed" directive.
