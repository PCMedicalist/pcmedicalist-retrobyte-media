#!/usr/bin/env python3
"""RetroByte Creative Studio — sovereign LOCAL video (GPU-shared safe).

REAL RetroByte footage is the mascot layer. The agent's own sprite-sheet animation
was rejected (inaccurate cells). The operator's generated MP4s (in media/) are clean,
on-model, cute RetroByte clips WITH audio — those are the source of truth.

Pipeline (zero GPU, zero cloud, ~seconds):
  1. pick next source clip (curated RetroByte MP4 list, dedup by registry)
  2. brand-colored letterbox/pad to 9:16 (TikTok/IG) + 1:1 (X)
  3. burn hook text (drawtext) on top
  4. keep original narration audio
  5. output to media/ for Buffer (3 channels, 2x/day cron)

No diffusion, no GPU. Fully sovereign.
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "media"
GENERATED = ROOT / "generated"
MEDIA.mkdir(exist_ok=True)
GENERATED.mkdir(exist_ok=True)

BRAND_DIR = Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand")
HOOK_PREFIX = "WAIT... NO WAY..."

# Curated real RetroByte footage the operator generated (clean, on-model, has audio).
# New clips get appended here as they're produced.
SOURCE_CLIPS = [
    "retrobyte-discovers-gameboy.mp4",
    "retrobyte-discovers-base.mp4",
    "retrobyte-discovers-VHS.mp4",
    "retrobyte-discovers-the-interner-modem.mp4",
    "retrobyte-discovers-an-electric-outlet.mp4",
    "retrobyte-wait-dont-leave-yet.mp4",
    "retrobyte-bonding-baseline.mp4",
    "retrobyte-baseline-base-intern.mp4",
    "retrobyte-i-saved-you-a-spot.mp4",
    "retrobyte-looks-for-internet.mp4",
]


def next_source(offset: int = 0) -> Path:
    """Return the next unposted source clip Path (dedup via registry)."""
    reg = json.loads((ROOT / "_posted_registry.json").read_text())
    done = set(reg.get("titles", []))
    # order: clips not yet posted, else cycle
    for name in SOURCE_CLIPS:
        if name not in done:
            return MEDIA / name
    # all posted -> cycle by offset
    return MEDIA / SOURCE_CLIPS[offset % len(SOURCE_CLIPS)]


def make_hook_filter(title: str) -> str:
    """drawtext burn-in for the hook. Text from a file (no shell-escaping)."""
    hook_text = f"{HOOK_PREFIX}  {title.upper()}!"
    safe = hook_text.replace("'", "").replace('"', "")
    txtfile = GENERATED / f"{slug(title)}_hook.txt"
    txtfile.write_text(safe, encoding="utf-8")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return (f"drawtext=textfile={txtfile}:fontfile={font}:"
            f"fontcolor=white:fontsize=48:"
            f"x=(w-text_w)/2:y=h*0.06:shadowcolor=black:shadowx=2:shadowy=2")


def build_video(src: Path, title: str) -> dict[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg missing")
    hook = make_hook_filter(title)
    variants = {}
    for aspect, scale in [("9:16", "1080:1920"), ("1:1", "1080:1080")]:
        out_path = MEDIA / f"{slug(title)}_{aspect.replace(':', 'x')}.mp4"
        # letterbox/pad source to aspect, keep audio, burn hook on top
        fc = (f"[0:v]scale={scale}:force_original_aspect_ratio=decrease,"
              f"pad={scale}:(ow-iw)/2:(oh-ih)/2[base];"
              f"[base]format=yuv420p,{hook}[v]")
        fcs = GENERATED / f"{slug(title)}_{aspect.replace(':', 'x')}_fc.txt"
        fcs.write_text(fc, encoding="utf-8")
        cmd = [ffmpeg, "-y", "-i", str(src),
               "-filter_complex_script", str(fcs),
               "-map", "[v]", "-map", "0:a:0", "-c:v", "libx264",
               "-c:a", "aac", "-shortest", "-r", "30", str(out_path)]
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL, timeout=180)
        variants[aspect] = out_path
    return variants


def slug(t: str) -> str:
    return "disc_" + "".join(c if c.isalnum() else "_" for c in t.lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    src = next_source(args.slot)
    title = src.stem  # e.g. retrobyte-discovers-gameboy -> title key
    print(f"[studio] source={src.name} slot={args.slot}", flush=True)

    if args.no_render:
        print("[studio] --no-render; aborting")
        return

    variants = build_video(src, title)

    reg = json.loads((ROOT / "_posted_registry.json").read_text())
    if src.name not in reg["titles"]:
        reg["titles"].append(src.name)
    reg["last"] = {"title": src.name, "slot": args.slot,
                   "ts": datetime.now(timezone.utc).isoformat()}
    (ROOT / "_posted_registry.json").write_text(json.dumps(reg, indent=2))

    print("[studio] STAGED: " +
          ", ".join(f"{k}={v.name}" for k, v in variants.items()))


if __name__ == "__main__":
    main()
