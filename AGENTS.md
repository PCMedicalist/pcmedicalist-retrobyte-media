# RetroByte social video format

The user approved and published the PalmPilot video recorded in
`retrobyte_video_format.json`. Preserve that format for every generated episode.

- Read `retrobyte_video_format.json` and `STILL_VIDEO_PIPELINE.md` before changing generation or publishing.
- Keep a stationary local diffusion scene, the approved keyed animated avatar, local Kokoro/Fenrir voice, vertical canvas, subject title, factual callouts, full narration captions, and question CTA.
- Ground episode facts in the catalog. Do not introduce unrelated narration or motion/pan/zoom backgrounds.
- Generation must pass `validate_render` before writing a posting-ready manifest. Publishing must pass `validate_publish_manifest`. Never bypass these gates or restore legacy clip fallback.
- Preserve complete subject titles, facts, and narration; resize/wrap text rather than silently truncating it.
- Review renders use the production renderer with `--test-render`; they must not stage, post, or consume topics.
- A user-requested format change authorizes updating the versioned profile, reference, implementation and tests together. Do not weaken tests or edit hashes just to silence a failure.
- Validate with the offline test suite: `/home/pcmedicalist/pcmedicalist-diffusion/venv/bin/python3 -m unittest discover -s tests -p 'test_*.py'`.
- Preserve unrelated working-tree edits and runtime ledgers. Never commit credentials or private runtime data.
