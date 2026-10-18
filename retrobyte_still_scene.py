#!/usr/bin/env python3
"""Generate one calm, local SD1.5 still for the RetroByte video renderer."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def generate(subject, angle, output):
    import torch
    from diffusers import StableDiffusionPipeline, EulerDiscreteScheduler
    try:
        models = subprocess.run(['ollama','ps'],capture_output=True,text=True,timeout=15)
        for row in models.stdout.splitlines()[1:]:
            if row.split():
                subprocess.run(['ollama','stop',row.split()[0]],capture_output=True,timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Device details are composed as a grounded illustration by the renderer.
    # Diffusion supplies atmosphere only; it must not invent the tech subject.
    prompt = ('quiet empty dark wooden desk in a retro study, warm amber desk lamp, '
              'soft blue wall, uncluttered tabletop, no objects in center, '
              'editorial illustration, straight camera, restrained colors')
    if 'palmpilot' not in subject.casefold():
        prompt = (f'editorial product illustration of one {subject}, {angle}, '
                  'upright centered still life on a quiet dark desk, warm lamp, soft blue shadows')
    seed = int(hashlib.sha256((subject + str(output)).encode()).hexdigest()[:8],16)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    print(f'[still-scene] local SD1.5 device={device} seed={seed}',flush=True)
    pipe = StableDiffusionPipeline.from_pretrained(
        'stable-diffusion-v1-5/stable-diffusion-v1-5', local_files_only=True,
        torch_dtype=dtype, safety_checker=None, requires_safety_checker=False).to(device)
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config)
    pipe.enable_vae_slicing()
    if device == 'cpu':
        pipe.enable_attention_slicing()
    if len(pipe.tokenizer(prompt).input_ids) > pipe.tokenizer.model_max_length:
        raise ValueError("Still-scene prompt exceeds the local model's token budget")
    image = pipe(prompt, negative_prompt=('devices, computer, phone, tablet, laptop, ' if 'palmpilot' in subject.casefold() else '')+'collage, multiple objects, warped, tilted camera, dutch angle, letters, text, logo, people, robot, clutter',
                 height=640, width=640, num_inference_steps=28, guidance_scale=7.0,
                 generator=torch.Generator(device=device).manual_seed(seed)).images[0]
    output = Path(output);output.parent.mkdir(exist_ok=True,parents=True);image.save(output)
    output.with_suffix('.json').write_text(json.dumps({'model':'SD1.5','device':device,'seed':seed,'prompt':prompt,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()},indent=2))
    return str(output)

if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--subject',required=True);ap.add_argument('--angle',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();print(json.dumps({'path':generate(a.subject,a.angle,a.out)}))
