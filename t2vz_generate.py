#!/usr/bin/env python3
"""Sovereign local text-to-video via TextToVideoZero (SD1.5 backend).

Why this exists:
- Wan2.1-T2V-1.3B (our first sovereign choice) gets OOM-killed by the runtime
  cgroup at ~4.17GB RSS on this host, no matter the session. Unrunnable here.
- TextToVideoZero reuses the already-cached SD1.5 weights and runs at ~3.9GB RSS
  — inside the cgroup. It produces REAL generative motion (diffusion across
  frames), not an image pan/zoom. That is what the operator demanded after
  rejecting the pan.

VRAM note: this host's 2060 (12GB) also runs Ollama (llama-server ~6.4GB).
We unload Ollama before rendering so T2VZ gets the full GPU, then leave it
unloaded — the agent reloads Ollama automatically on its next chat
(per the staggered-cron design). Do NOT auto-reload here.

Usage (standalone):
  python3 t2vz_generate.py --prompt "..." --out clip.mp4 [--steps 25] [--frames 8]
"""
from __future__ import annotations
import argparse
import json
import resource
import subprocess
import sys
from pathlib import Path

import torch
from diffusers import TextToVideoZeroPipeline, DDIMScheduler
from diffusers.pipelines.text_to_video_synthesis.pipeline_text_to_video_zero import CrossFrameAttnProcessor2_0
from diffusers.utils import export_to_video
from PIL import Image

DEFAULT_MODEL = "stable-diffusion-v1-5/stable-diffusion-v1-5"


def unload_ollama() -> list[str]:
    """Unload any running Ollama models to free VRAM. Returns the model names."""
    try:
        r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=15)
        names = []
        for line in r.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if parts:
                names.append(parts[0])
        for n in names:
            subprocess.run(["ollama", "stop", n], capture_output=True, text=True, timeout=30)
        return names
    except Exception as e:  # never block the render on ollama issues
        print(f"[t2vz] ollama unload skipped: {e}", file=sys.stderr)
        return []


def generate(
    prompt: str,
    out_path: str | Path,
    steps: int = 25,
    frames: int = 8,
    height: int = 320,
    width: int = 576,
    fps: int = 8,
    model: str = DEFAULT_MODEL,
) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    prev = unload_ollama()
    if prev:
        print(f"[t2vz] unloaded ollama: {prev}", flush=True)

    try:
        pipe = TextToVideoZeroPipeline.from_pretrained(
            model, torch_dtype=torch.float16, local_files_only=True
        ).to("cuda")
        pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config)
        pipe.unet.set_attn_processor(CrossFrameAttnProcessor2_0(batch_size=2))
        pipe.enable_vae_slicing()
        # Disable the NSFW safety checker: it returns black frames and breaks the
        # pipeline's return type (ndarray instead of PIL). RetroByte prompts are
        # clean; we don't want a black clip.
        pipe.safety_checker = lambda images, **kwargs: (images, False)

        # t0/t1 are DDIM timestep indices for the forward-loop noise injection.
        # T2VZ defaults (t0=44, t1=47) assume 50 steps; for other step counts we
        # scale to the same relative position (~85% / ~95% of the schedule).
        t0 = max(1, int(steps * 0.85))
        t1 = max(t0 + 1, int(steps * 0.95))

        # Bound GPU memory: every chunk attends the same seeded first frame.
        # Avoid sending all 96 frames through the UNet in one allocation.
        imgs = []
        for start in range(0, frames, 7):
            ids = ([0] + list(range(max(1, start), min(frames, start + 7))))
            if start == 0:
                ids = list(range(min(frames, 7)))
            if len(ids) == 1 and start:
                continue
            print(f"[t2vz] frames {ids[1] if start else 0}..{ids[-1]}/{frames-1}", flush=True)
            out = pipe(
                prompt, num_inference_steps=steps, video_length=len(ids),
                frame_ids=ids, height=height, width=width, t0=t0, t1=t1,
                generator=torch.Generator(device="cuda").manual_seed(42),
            )
            chunk = out.images if start == 0 else out.images[1:]
            imgs.extend(
                Image.fromarray((i * 255).clip(0, 255).astype("uint8"))
                if not isinstance(i, Image.Image) else i for i in chunk)
            del out
            torch.cuda.empty_cache()
        if len(imgs) != frames:
            raise RuntimeError(f"Generated {len(imgs)} frames; expected {frames}")
        export_to_video(imgs, str(out_path), fps=fps)
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        return {"ok": True, "frames": len(imgs), "rss_mb": rss, "path": str(out_path)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
    # NOTE: intentionally do NOT reload Ollama — agent reloads on next chat.


def main() -> None:
    ap = argparse.ArgumentParser(description="Sovereign T2V via TextToVideoZero (SD1.5)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--size", default="320*576", help="H*W, e.g. 320*576")
    ap.add_argument("--fps", type=int, default=8)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    h, w = (int(x) for x in args.size.split("*"))
    res = generate(args.prompt, args.out, args.steps, args.frames, h, w, args.fps, args.model)
    print(json.dumps(res), flush=True)
    if not res.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
