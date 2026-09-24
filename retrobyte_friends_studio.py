#!/usr/bin/env python3
"""RetroByte Creative Studio — sovereign LOCAL video (GPU-shared safe).

The 16 RetroByte friend sprites (sliced from retrobyte-friends.png) are the mascot.
Each episode picks one friend, composites it over a brand backdrop, burns the hook.

Pipeline (zero GPU, ~seconds):
  1. pick next episode title + narration (from registry)
  2. pick a friend sprite (rotating through char_00..char_15)
  3. brand backdrop (PIL neon-grid, cached)
  4. ffmpeg: backdrop + friend (scaled, lower-left) + hook drawtext
  5. output 9:16 + 1:1 to media/ for Buffer (3 channels, 2x/day cron)
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "media"
GENERATED = ROOT / "generated"
FRIENDS = Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand/friends")
MEDIA.mkdir(exist_ok=True)
GENERATED.mkdir(exist_ok=True)

BRAND_DIR = Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand")
HOOK_PREFIX = "WAIT... NO WAY..."

FRIEND_FILES = sorted(FRIENDS.glob("char_*.png"))  # 16 friends


def next_episode(offset: int = 0) -> tuple[str, int, str]:
    """(title, slot, narration) for next unposted subject."""
    reg = json.loads((ROOT / "_posted_registry.json").read_text())
    titles = reg.get("titles", [])
    if not titles:
        titles = ["THE FLOPPY DISK", "THE PAGER", "THE GAME BOY",
                  "THE CASSETTE TAPE", "THE ZIP DRIVE", "THE TAMAGOTCHI"]
    title = titles[min(offset, len(titles) - 1)]
    slot = offset % len(FRIEND_FILES) if FRIEND_FILES else 0
    narration = f"{HOOK_PREFIX} {title.upper()}! RetroByte, signing off."
    return title, slot, narration


ROOM_BGS = sorted(BRAND_DIR.glob("retro_room_bg*.png"))  # multiple rotating backgrounds


def make_backdrop(out: Path, slot: int = 0) -> Path:
    if ROOM_BGS:
        bg = ROOM_BGS[slot % len(ROOM_BGS)]
        shutil.copy(bg, out)
        return out
    # fallback: solid navy grid
    cached = GENERATED / "backdrop_cache.png"
    if cached.exists():
        shutil.copy(cached, out)
        return out
    from PIL import Image, ImageDraw
    W, H = 576, 320
    base = (10, 12, 32)
    img = Image.new("RGB", (W, H), base)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(base[0] + (20 - base[0]) * (1 - abs(t - 0.5) * 2))
        g = int(base[1] + (60 - base[1]) * (1 - abs(t - 0.5) * 2))
        b = int(base[2] + (90 - base[2]) * (1 - abs(t - 0.5) * 2))
        d.line([(0, y), (W, y)], fill=(r, g, b))
    step = 32
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=(0, 180, 200), width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(180, 0, 160), width=1)
    d.ellipse([W * 0.25, H * 0.2, W * 0.75, H * 0.8], fill=(20, 30, 70))
    img.save(out)
    img.save(cached)
    return out


def make_hook_filter(title: str) -> str:
    hook_text = f"{HOOK_PREFIX}  {title.upper()}!"
    safe = hook_text.replace("'", "").replace('"', "")
    txtfile = GENERATED / f"{slug(title)}_hook.txt"
    txtfile.write_text(safe, encoding="utf-8")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return (f"drawtext=textfile={txtfile}:fontfile={font}:"
            f"fontcolor=white:fontsize=48:"
            f"x=(w-text_w)/2:y=h*0.05:shadowcolor=black:shadowx=2:shadowy=2")


def build_friends_composite(title: str, friend: Path, backdrop: Path,
                             hook: str) -> dict[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg missing")

    variants = {}
    for aspect, scale in [("9:16", "1080:1920"), ("1:1", "1080:1080")]:
        out_path = MEDIA / f"{slug(title)}_friends_{aspect.replace(':', 'x')}.mp4"
        # filter: backdrop scaled to aspect + friend lower-left + hook text
        fc = (f"[0:v]scale={scale}:force_original_aspect_ratio=decrease,"
              f"pad={scale}:(ow-iw)/2:(oh-ih)/2,setsar=1[base];"
              f"[1:v]scale=iw*0.55:-1,format=yuva420p[fr];"
              f"[base][fr]overlay=W*0.05:H-h-(H*0.05)[ov];"
              f"[ov]{hook}[v]")
        fcs = GENERATED / f"{slug(title)}_friends_{aspect.replace(':', 'x')}_fc.txt"
        fcs.write_text(fc, encoding="utf-8")
        # 3.75s, 30fps, silent (no audio input)
        cmd = [ffmpeg, "-y", "-loop", "1", "-t", "3.75", "-i", str(backdrop),
               "-i", str(friend),
               "-filter_complex_script", str(fcs),
               "-map", "[v]", "-r", "30", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-an", str(out_path)]
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL, timeout=120)
        variants[aspect] = out_path
    return variants


def slug(t: str) -> str:
    return "disc_" + "".join(c if c.isalnum() else "_" for c in t.lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    title, slot, narration = next_episode(args.slot)
    friend = FRIEND_FILES[slot % len(FRIEND_FILES)] if FRIEND_FILES else None
    if not friend:
        raise RuntimeError("no friend sprites found")

    print(f"[studio] episode={title} friend={friend.name} slot={slot}", flush=True)
    print(f"[studio] narration: {narration}", flush=True)

    if args.no_render:
        print("[studio] --no-render; aborting")
        return

    backdrop = make_backdrop(GENERATED / f"{slug(title)}_bg.png", slot=slot)
    hook = make_hook_filter(title)
    variants = build_friends_composite(title, friend, backdrop, hook)

    reg = json.loads((ROOT / "_posted_registry.json").read_text())
    if title not in reg["titles"]:
        reg["titles"].append(title)
    reg["last"] = {"title": title, "slot": slot, "friend": friend.name,
                   "ts": datetime.now(timezone.utc).isoformat()}
    (ROOT / "_posted_registry.json").write_text(json.dumps(reg, indent=2))

    print("[studio] STAGED: " +
          ", ".join(f"{k}={v.name}" for k, v in variants.items()))


if __name__ == "__main__":
    main()
