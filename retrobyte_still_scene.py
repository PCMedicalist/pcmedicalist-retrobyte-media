#!/usr/bin/env python3
"""Generate one cinematic local still for the RetroByte video renderer.

Model/resolution/steps come from the approved FORMAT contract. SDXL (832px/30)
replaced SD1.5 (640px/28) on 2026-11-12: same prompt, SDXL produced measurably
sharper, better-lit, better-composed scenes (edge energy 33.8 vs 27.5, and a
clean sci-fi set instead of a muddy home office). SDXL needs ~9.3GB VRAM, so
the local chat models are stopped first — exactly as the SD1.5 path did.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from retrobyte_video_format import FORMAT, PROFILE_SHA256


def scene_prompt(subject: str = "") -> str:
    """Atmosphere-only art direction; exact props are composited separately."""
    return (
        "cinematic retro-futurist computer lab, dark graphite room, warm amber "
        "practical desk lights, restrained cyan edge lighting, clean empty "
        "display platform, atmospheric depth, polished 3D environment, "
        "vertical social-video composition, no text"
    )


def _free_local_vram():
    """Stop resident Ollama chat models so the diffusion model fits in VRAM."""
    try:
        models = subprocess.run(['ollama', 'ps'], capture_output=True, text=True, timeout=15)
        for row in models.stdout.splitlines()[1:]:
            name = row.split()[0] if row.split() else ''
            if name:
                subprocess.run(['ollama', 'stop', name], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def generate(subject, angle, output):
    import torch
    _free_local_vram()
    cfg = FORMAT['scene']
    prompt = scene_prompt(subject)
    seed = int(hashlib.sha256((subject + PROFILE_SHA256).encode()).hexdigest()[:8], 16)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    size, steps = int(cfg['size']), int(cfg['steps'])
    print(f'[still-scene] local {cfg["model"]} device={device} seed={seed} '
          f'size={size} steps={steps}', flush=True)

    if 'xl' in cfg['model'].lower():
        from diffusers import StableDiffusionXLPipeline, EulerDiscreteScheduler
        pipe = StableDiffusionXLPipeline.from_pretrained(
            cfg['model'], local_files_only=True, torch_dtype=dtype,
            variant='fp16' if device == 'cuda' else None,
            safety_checker=None, requires_safety_checker=False).to(device)
        pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config)
        pipe.enable_vae_slicing()
        if device == 'cpu':
            pipe.enable_attention_slicing()
    else:
        from diffusers import StableDiffusionPipeline, EulerDiscreteScheduler
        pipe = StableDiffusionPipeline.from_pretrained(
            cfg['model'], local_files_only=True, torch_dtype=dtype,
            safety_checker=None, requires_safety_checker=False).to(device)
        pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config)
        pipe.enable_vae_slicing()
        if device == 'cpu':
            pipe.enable_attention_slicing()

    if len(pipe.tokenizer(prompt).input_ids) > pipe.tokenizer.model_max_length:
        raise ValueError("Still-scene prompt exceeds the local model's token budget")
    image = pipe(prompt, negative_prompt=(
                 'product, gadget, device, computer, phone, tablet, laptop, '
                 'cable, wire, connector, plug, port, electronics, robot, '
                 'person, text, letters, logo, watermark, clutter, collage, '
                 'multiple objects, warped, tilted camera, dutch angle'),
                 height=size, width=size, num_inference_steps=steps, guidance_scale=7.0,
                 generator=torch.Generator(device=device).manual_seed(seed)).images[0]
    output = Path(output); output.parent.mkdir(exist_ok=True, parents=True); image.save(output)
    output.with_suffix('.json').write_text(json.dumps({
        'model': cfg['model'], 'profile_sha256': PROFILE_SHA256, 'device': device,
        'seed': seed, 'subject': subject, 'angle': angle, 'prompt': prompt,
        'size': size, 'steps': steps,
        'subject_reference': 'composited separately; never passed to diffusion',
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))
    return str(output)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--subject', required=True)
    ap.add_argument('--angle', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    print(json.dumps({'path': generate(a.subject, a.angle, a.out)}))

