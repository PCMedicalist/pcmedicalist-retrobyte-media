#!/usr/bin/env python3
"""Generate one calm, local SD1.5 still for the RetroByte video renderer."""
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
    # Never ask text-to-image to invent hardware or connector geometry.
    prompt = scene_prompt(subject)
    seed = int(hashlib.sha256((subject + PROFILE_SHA256).encode()).hexdigest()[:8],16)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    print(f'[still-scene] local SD1.5 device={device} seed={seed}',flush=True)
    pipe = StableDiffusionPipeline.from_pretrained(
        FORMAT['scene']['model'], local_files_only=True,
        torch_dtype=dtype, safety_checker=None, requires_safety_checker=False).to(device)
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
                 height=FORMAT['scene']['size'], width=FORMAT['scene']['size'], num_inference_steps=FORMAT['scene']['steps'], guidance_scale=7.0,
                 generator=torch.Generator(device=device).manual_seed(seed)).images[0]
    output = Path(output);output.parent.mkdir(exist_ok=True,parents=True);image.save(output)
    output.with_suffix('.json').write_text(json.dumps({'model':FORMAT['scene']['model'],'profile_sha256':PROFILE_SHA256,'device':device,'seed':seed,'subject':subject,'angle':angle,'prompt':prompt,'subject_reference':'composited separately; never passed to diffusion','sha256':hashlib.sha256(output.read_bytes()).hexdigest()},indent=2))
    return str(output)

if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--subject',required=True);ap.add_argument('--angle',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();print(json.dumps({'path':generate(a.subject,a.angle,a.out)}))
