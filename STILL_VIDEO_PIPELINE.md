# RetroByte still-scene video pipeline

The scheduled host build generates one stationary local Stable Diffusion 1.5
atmosphere-only scene, then renders a vertical 1080×1920 video with the
approved animated RetroByte MP4, local Kokoro `am_fenrir` narration, a title,
and burned captions. This is the active production format; image-to-video
generation remains a separate local experiment until its outputs pass the
same identity, text, and format checks.
The diffusion prompt never receives the product name or description. Product
art is shown only when the topic has an explicit catalog mapping and a matching
local cached reference with a matching SHA-256 and source entry in
`<image-cache>/manifest.json`; otherwise the scene stays atmospheric rather than
showing a fabricated cable, plug, port, or device. The selected reference path
and SHA-256 are recorded and checked before staging and publishing.
Only the avatar moves. The scene has no camera pan, zoom, video diffusion or
motion loop. The avatar has its own stage below the subject visual and above
the captions.

Narration comes from the subject catalog's fact/angle, with a specific grounded
PalmPilot script. The voice model and supplied avatar remain the same.

## Review the real renderer without publishing

```bash
bash ~/.hermes/scripts/retrobyte_discovery_gen.sh 0 --test-render --force-subject 'The PalmPilot'
```

This review mode uses the same scene, TTS, layout, caption, and avatar renderer
as scheduled production. It writes the MP4, complete narration, scene prompt,
scene hash, model log, timed cues and `render.json` on PCM. It does not write a
posting-ready manifest, advance topic history, commit media, or send a post.
A forced previously generated subject is allowed only in review mode.

Normal scheduled builds still stage their output for the separate posting job.
Existing schedules and the shared host resource lock remain unchanged.
RetroByte's agent runs in Docker; this media build runs on the host.

## Verification

`tests/test_still_video.py` renders a short fixture through the actual compositor
and checks vertical dimensions, complete audio duration, visible caption
pixels, a stationary scene, moving avatar, and grounded narration. The generic
media probe and narration-length regression remains in
`tests/test_generation_media.py`.

After a production video passes the final format gate, the successful build
removes only legacy `generated/disc_frame_*.png` previews. It preserves source
art, audit JSON/text evidence, and the final MP4 in `media/`. Failed builds do
not run that cleanup.

The diffusion model only creates the atmosphere. Product imagery is composited
from an explicitly mapped local reference whose source and SHA-256 are checked;
it is never inferred from a generated filename. If no verified reference is
available, the subject-specific fallback illustration is used only for the
PalmPilot. Other subjects remain atmosphere-only until their reference is
approved. This keeps connector layouts, labels, and brand marks out of the
diffusion model's control.

## Local image-to-video experiments

The attached cinematic clip is a style reference, not a factual source for
product hardware or RetroByte identity. Any local video model must animate an
atmosphere plate only. Titles, product references, RetroByte, narration, and
captions remain separately composited from approved local assets. A candidate
video must pass a manual review and the render checks before this profile or
scheduled production is changed. Generated previews are retained until a final
render succeeds; cleanup then removes only `generated/disc_frame_*.png` and
preserves source art, audit evidence, and final media.

## Approved format lock: retrobyte-still-v1

`retrobyte_video_format.json` is the versioned source of truth, tied to the
SHA-256 of the approved, published PalmPilot reference video. It fixes the
canvas, stationary scene policy, exact avatar asset/hash, Fenrir voice, scene /
avatar / caption regions, typography limits, branding, colors and CTA.
Each new topic keeps those elements while changing its complete title and
catalog-derived facts. Long titles and facts wrap to fit; they are not cut off.

Before staging, `validate_render` checks the encoded H.264/AAC video: dimensions,
30 fps, full audio duration, complete caption timeline and narration, readable
caption pixels, scene stability and avatar motion. The resulting validation
record binds the video hash to the exact profile version/hash.

Before publishing, `validate_publish_manifest` requires that record, unchanged
video bytes, the approved voice/avatar, stationary scene mode and burned
captions. The old rotating-clip publishing fallback is disabled. A failed
validation stops staging/publishing; it does not substitute another clip.

`AGENTS.md` carries the same requirements for future code changes. A new user-
approved format should update the versioned profile, reference and tests
intentionally. Current schedules remain unchanged.
