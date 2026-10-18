# RetroByte still-scene video pipeline

The scheduled host build now generates one stationary local Stable Diffusion
1.5 scene, then renders a vertical 1080×1920 video with the approved animated
RetroByte MP4, local Kokoro `am_fenrir` narration, a title, and burned captions.
Only the avatar moves. The scene has no camera pan, zoom, video diffusion or
motion loop. The avatar has its own stage below the device illustration and
above the captions.

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

The PalmPilot hero is an interpretive PDA illustration drawn by the compositor,
not a historical photograph. Diffusion generates the quiet desk atmosphere;
it no longer invents a device or animation. This keeps the screen, handwriting
area, application buttons and stylus readable.

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
