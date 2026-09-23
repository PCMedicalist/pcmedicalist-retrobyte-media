#!/usr/bin/env python3
"""RetroByte Creative Studio — sovereign LOCAL video (GPU-shared safe).

Hard constraint (verified 2026-09-22): the RTX 2060's VRAM is ~10.6GB held by
root-owned snap ollama (MCINTOSHIbot/Docker agents); only ~1.4GB free and CANNOT be
freed (other agents need it). Wan2.1 / T2VZ-on-GPU OOM; T2VZ-CPU runs but is
~40-80 min/episode (impractical). So: NO diffusion. The clip is built from REAL
RetroByte assets only:

  - brand backdrop        : PIL neon-grid lab (instant, zero VRAM, cached)
  - mascot               : cycling sprite-sheet frames (A2: 4-6 expression frames
                          looped via ffmpeg -> "alive", on-brand, never drifts)
  - typography hook      : "WAIT... NO WAY... <SUBJECT>!" burn-in
  - narration audio      : TTS (retrobyte:3b / local) muxed under the clip
  - outputs              : 9:16 (TikTok/IG) + 1:1 (X)

Everything is ffmpeg/PIL — runs in seconds, zero GPU, fully sovereign. No cloud.
Posting stays GATED on operator sign-off (cron wired separately).
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image

ROOT = Path(__file__).resolve().parent
BRAND = Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand")
SPRITE = BRAND / "pcmedicalist-retrobyte-sprite-sheet.png"
MEDIA = ROOT / "media"
GENERATED = ROOT / "generated"
MEDIA.mkdir(exist_ok=True)
GENERATED.mkdir(exist_ok=True)

# A2: 6 lively sprite cells from the 6x4 sheet (idle, blink, talk, surprise, happy, point)
SPRITE_FRAMES = [0, 1, 2, 3, 6, 7]
SPRITE_COLS, SPRITE_ROWS = 6, 4

# RetroByte hook copy
HOOK_PREFIX = "WAIT... NO WAY..."


def next_episode(offset: int = 0) -> tuple[str, int, str]:
    """(title, slot, subject_prompt, narration) for the next unposted subject.

    Subject list comes from the dedup registry; a built-in fallback covers an empty
    registry. Narration uses the viral hook structure.
    """
    reg = json.loads((ROOT / "_posted_registry.json").read_text())
    titles = reg.get("titles", [])
    if not titles:
        titles = ["THE FLOPPY DISK", "THE PAGER", "THE DOT-MATRIX PRINTER",
                  "THE CASSETTE TAPE", "THE PORTABLE CD PLAYER", "THE ZIP DRIVE",
                  "THE GAME BOY", "THE DIAL-UP MODEM", "THE TAMAGOTCHI"]
    title = titles[min(offset, len(titles) - 1)]
    slot = offset % 2
    narration = f"{HOOK_PREFIX} {title.upper()}! RetroByte, signing off."
    return title, slot, narration


# ---------------------------------------------------------------------------
# 1. Brand backdrop (PIL, instant, cached)
# ---------------------------------------------------------------------------
def make_backdrop(out: Path) -> Path:
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


# ---------------------------------------------------------------------------
# 2. Slice sprite frames
# ---------------------------------------------------------------------------
def slice_sprite_frames(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    im = Image.open(SPRITE).convert("RGB")
    W, H = im.size
    cw, ch = W // SPRITE_COLS, H // SPRITE_ROWS
    frames = []
    for i, idx in enumerate(SPRITE_FRAMES):
        r, c = divmod(idx, SPRITE_COLS)
        cell = im.crop((c * cw, r * ch, c * cw + cw, r * ch + ch))
        p = dest / f"frame_{i:02d}.png"
        cell.save(p)
        frames.append(p)
    return frames


# ---------------------------------------------------------------------------
# 3. TTS narration (local; falls back to silent if unavailable)
# ---------------------------------------------------------------------------
def make_narration(text: str, out: Path) -> bool:
    """Best-effort local TTS. Returns True if audio was produced."""
    # Try the retrobyte local TTS pipeline if present
    tts_script = ROOT / "tts_narrate.py"
    if tts_script.exists():
        r = subprocess.run([sys.executable, str(tts_script), text, str(out)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and out.exists():
            return True
    # fallback: Look for an espeak/piper binary
    for bin_name in ("piper", "espeak-ng", "espeak"):
        if shutil.which(bin_name):
            try:
                if bin_name == "piper":
                    subprocess.run([bin_name, "-f", text, "-w", str(out)],
                                   capture_output=True, timeout=120, check=True)
                else:
                    subprocess.run([bin_name, "-w", str(out), text],
                                   capture_output=True, timeout=120, check=True)
                return out.exists()
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# 4. Compose: backdrop + looping sprite mascot + hook text + audio
# ---------------------------------------------------------------------------
def build_video(title: str, backdrop: Path, sprite_frames: list[Path],
                narration: str) -> dict[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg missing")

    # Build a looping mascot clip via the concat DEMUXER (image slideshow with held
    # frames). Each sprite frame is held `reps` ticks -> smooth expression cycle.
    mascot = GENERATED / f"{slug(title)}_mascot.mp4"
    fps = 8
    reps = 5
    tick = 1.0 / fps  # seconds per frame @8fps
    concat_list = GENERATED / f"{slug(title)}_mascot_list.txt"
    lines = []
    for fr in sprite_frames:
        for _ in range(reps):
            lines.append(f"file '{fr}'")
            lines.append(f"duration {tick}")
    concat_list.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-vsync", "cfr", "-r", str(fps), "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(mascot)],
                   check=True, stderr=subprocess.DEVNULL, timeout=120)

    # Hook text (top) via drawtext (reliable burn-in, no ASS/fontconfig chain)
    hook_text = f"{HOOK_PREFIX}  {title.upper()}!"
    # strip characters that break drawtext; escape ':[]' -> use textfile instead
    safe = hook_text.replace("'", "").replace('"', "")
    txtfile = GENERATED / f"{slug(title)}_hook.txt"
    txtfile.write_text(safe, encoding="utf-8")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    # drawtext reads text from file (textfile=), avoids shell-escaping issues
    hook_filter = (f"drawtext=textfile={txtfile}:fontfile={font}:"
                   f"fontcolor=white:fontsize=48:"
                   f"x=(w-text_w)/2:y=h*0.06:shadowcolor=black:shadowx=2:shadowy=2")

    # TTS narration
    audio = GENERATED / f"{slug(title)}_narration.mp3"
    has_audio = make_narration(narration, audio)

    variants = {}
    for aspect, scale, out_name in [
        ("9:16", "1080:1920", f"{slug(title)}_9x16.mp4"),
        ("1:1", "1080:1080", f"{slug(title)}_1x1.mp4"),
    ]:
        out_path = MEDIA / out_name
        # mascot lower-left ~32% width; scale backdrop to aspect; burn hook text
        # Use a filter-script file (no shell/string escaping ambiguity)
        fc = (f"[0:v]scale={scale}:force_original_aspect_ratio=decrease,"
              f"pad={scale}:(ow-iw)/2:(oh-ih)/2[base];"
              f"[1:v]scale=iw*0.32:-1[masc];"
              f"[base][masc]overlay=W*0.05:H-h-(H*0.05)[ov];"
              f"[ov]format=yuv420p,{hook_filter}[v]")
        fcs = GENERATED / f"{slug(title)}_{aspect.replace(':', 'x')}_fc.txt"
        fcs.write_text(fc, encoding="utf-8")
        cmd = [ffmpeg, "-y", "-i", str(backdrop), "-i", str(mascot),
               "-filter_complex_script", str(fcs),
               "-map", "[v]", "-r", "8"]
        if has_audio:
            cmd += ["-i", str(audio), "-map", "2:a:0", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", str(out_path)]
        # loop the 1.5s mascot/backdrop to fill 3s
        with open("/tmp/ff_compose_err.log", "wb") as ef:
            subprocess.run(cmd, check=True, stderr=ef, timeout=180)
        variants[aspect] = out_path
    return variants


def slug(t: str) -> str:
    return "disc_" + "".join(c if c.isalnum() else "_" for c in t.lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--no-render", action="store_true", help="debug: skip render")
    args = ap.parse_args()

    title, slot, narration = next_episode(args.slot)
    print(f"[studio] episode subject={title} slot={slot}", flush=True)
    print(f"[studio] narration: {narration}", flush=True)

    if args.no_render:
        print("[studio] --no-render; aborting")
        return

    backdrop = make_backdrop(GENERATED / f"{slug(title)}_bg.png")
    sprite_frames = slice_sprite_frames(GENERATED / "sprite_frames")
    variants = build_video(title, backdrop, sprite_frames, narration)

    # mark produced
    reg = json.loads((ROOT / "_posted_registry.json").read_text())
    if title not in reg["titles"]:
        reg["titles"].append(title)
    reg["last"] = {"title": title, "slot": slot,
                   "ts": datetime.now(timezone.utc).isoformat()}
    (ROOT / "_posted_registry.json").write_text(json.dumps(reg, indent=2))

    print("[studio] STAGED: " +
          ", ".join(f"{k}={v.name}" for k, v in variants.items()))


if __name__ == "__main__":
    main()
