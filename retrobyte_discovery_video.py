#!/usr/bin/env python3
"""
RetroByte — "Discovering 90's Tech" video generator (HOST build step).

Generates a narrated, on-brand RetroByte video about a 90s tech artifact,
in RetroByte's excited-intern voice, then stages it for the in-container
retrobyte-cron poster to publish to X / IG / TikTok.

Pipeline:
  1. Pick a 90s-tech subject (rotating, no repeat until cycled).
  2. Narrate it in RetroByte voice via the local retrobyte:3b Ollama model.
  3. Composite a retro CRT-style frame (PIL: scanlines, neon terminal text).
  4. Mux to a narrated MP4 via media_clip.build_clip() (burned CC).
  5. Stage mp4 + caption into the RetroByte media dir; write
     _discovery_ready.json so the container poster picks it up.

Run from host (needs content-os venv: edge-tts + PIL; system ffmpeg; Ollama
retrobyte:3b). Invoked by a host cron at 07:40 + 19:10 (before the
container poster fires at 08:00 / 19:30).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests  # stdlib-free; content-os venv has it (or system)

# --- Paths ---------------------------------------------------------------
HERE = Path(__file__).resolve().parent
MEDIA_DIR = Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/media")
OUTBOX = HERE / "generated"
OUTBOX.mkdir(parents=True, exist_ok=True)
READY_FILE = HERE / "_discovery_ready.json"

# --- 90s-tech subject pool (rotating) ------------------------------------
SUBJECTS = [
    ("Game Boy", "the brick that made gaming pocket-sized"),
    ("VHS tape", "the rewind war that ate your afternoon"),
    ("Dial-up modem", "the scream that connected you to the world"),
    ("Floppy disk", "1.44MB of pure possibility"),
    ("Tamagotchi", "the pet that died if you forgot to feed it"),
    ("CRT monitor", "the tube that glowed like the future"),
    ("Cassette Walkman", "music in your pocket, no skip"),
    ("Super Nintendo", "16-bit Saturdays forever"),
    ("LaserDisc", "the disc before the disc"),
    ("Pagers / Beepers", "the original notification dot"),
    ("Polaroid camera", "instant memories, no darkroom"),
    ("Arcade cabinet", "quarters, leaderboards, glory"),
]
SUBJECTS_PER_DAY = 1  # one discovery video per run (2 runs/day)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "retrobyte:3b"

# --- Voice narration via local retrobyte:3b ------------------------------
def narrate(subject: str, hook: str) -> str:
    prompt = (
        "You are RetroByte, a curious AI intern from the PCMedicalist universe who "
        "just time-traveled to the 1990s and is AMAZED by old technology. "
        "Write a SHORT (2-3 sentences, max 60 words) spoken narration about "
        f"\"{subject}\" ({hook}). Be excited, use 'WAIT...', 'NO WAY...', "
        "wide-eyed amazement. Plain text only — NO hashtags, NO emojis (they get "
        "read aloud by TTS). End with a retro sign-off like 'RetroByte, signing off!'"
    )
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False, "temperature": 0.9},
            timeout=90,
        )
        if r.ok:
            t = r.json().get("response", "").strip()
            # strip any stray hashtags/emoji defensively for TTS
            t = re.sub(r"[#@]\w+", "", t)
            t = "".join(ch for ch in t if ord(ch) < 0x2600)
            return t.strip() or f"WAIT... {subject}! {hook}! RetroByte, signing off!"
    except Exception as e:
        print(f"[discovery-gen] narrate fail: {e}", file=sys.stderr)
    return f"WAIT... {subject}! {hook}! RetroByte, signing off!"


# --- RetroByte character model (used as the video frame centerpiece) ------
CHARACTER_IMG = Path(
    "/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand/"
    "retrobyte-video-model.png")

# --- Retro CRT frame (PIL) ----------------------------------------------
def make_frame(subject: str, hook: str, out_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (0, 0, 0))  # black bg matches character art
    d = ImageDraw.Draw(img)

    # Neon-amber terminal vibe
    amber = (255, 176, 0)
    green = (120, 255, 140)

    try:
        font_big = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 38)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # --- Character model at BOTTOM (speaking the captions above) ----------
    # media_clip.build_clip() burns the narrated CC into a reserved band at
    # ~y[844,1056] of the 1920-tall frame (upper-middle). Place RetroByte in
    # the LOWER portion, BELOW that band, so he reads as the speaker — and keep
    # him clear of the footer (H-120) and inside the amber border (30..H-30).
    if CHARACTER_IMG.exists():
        char = Image.open(CHARACTER_IMG).convert("RGB")
        cw, ch = char.size
        # ~55% width keeps him inside the side borders with margin
        target = int(W * 0.55)
        try:
            _resample = Image.Resampling.LANCZOS
        except AttributeError:
            _resample = Image.LANCZOS
        char = char.resize((target, target), _resample)
        cx = (W - target) // 2
        # Top below the CC band (1056); bottom must clear footer (~H-120=1800).
        cy = int(H * 0.58)          # ~1114 -> bottom ~1708, clear of footer
        img.paste(char, (cx, cy))
    else:
        print("[discovery-gen] WARN: character image missing; using text frame",
              file=sys.stderr)

    # --- Header + subject text (top band, above the character) -----------
    d.text((60, 90), "RETROBYTE DISCOVERS:", fill=green, font=font_big)
    words = subject.split()
    lines, cur = [], ""
    for w in words:
        if len(cur + " " + w) > 16:
            lines.append(cur.strip()); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    y = 230
    for ln in lines:
        d.text((60, y), ln, fill=amber, font=font_big)
        y += 100

    # NOTE: the spoken narration is burned as timed closed-captions by
    # media_clip.build_clip() in its OWN reserved band (lower third). We do
    # NOT draw the hook here — doing so collided with that CC band + footer.
    # Scanlines + border + footer only below.

    # --- Scanlines overlay (retro CRT feel) ------------------------------
    for y2 in range(0, H, 6):
        d.rectangle([0, y2, W, y2 + 2], fill=(0, 0, 0))

    # --- Glow border + footer -------------------------------------------
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    d.text((60, H - 120), "PCMedicalist · baseline.click",
           fill=amber, font=font_small)

    img.save(out_path)
    return out_path


# --- video build via media_clip -----------------------------------------
def build_video(narration: str, frame_path: Path, out_path: Path) -> str | None:
    # Import media_clip from the PCMedicalist social pipelines dir
    pipelines = Path("/home/pcmedicalist/.hermes/skills/social-media/"
                     "pcmedicalist-social-publisher/pipelines")
    sys.path.insert(0, str(pipelines))
    import media_clip as mc
    try:
        clip = mc.build_clip(narration, frame_path, out_path,
                             voice="en-US-AnaNeural", is_reel=True)
        if clip.get("ok") and clip.get("parts"):
            return str(Path(clip["parts"][0]["path"]))
    except Exception as e:
        print(f"[discovery-gen] build_clip fail: {e}", file=sys.stderr)
    return None


def pick_subject(date: _dt.date, slot: int) -> tuple[str, str]:
    idx = (int(date.strftime("%Y%m%d")) + slot * 1000) % len(SUBJECTS)
    return SUBJECTS[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=0, choices=[0, 1])
    ap.add_argument("--force-subject", default=None, help="Override subject (debug)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    date = _dt.date.today()
    if args.force_subject:
        subj = next((s for s in SUBJECTS if s[0].lower() == args.force_subject.lower()),
                    (args.force_subject, "a blast from the past"))
    else:
        subj = pick_subject(date, args.slot)
    subject, hook = subj

    stamp = date.strftime("%Y-%m-%d") + f"-d{args.slot}"
    frame = OUTBOX / f"disc_frame_{stamp}.png"
    video = OUTBOX / f"disc_video_{stamp}.mp4"

    print(f"[discovery-gen] subject={subject} slot={args.slot}", flush=True)
    narration = narrate(subject, hook)
    print(f"[discovery-gen] narration: {narration[:80]}...", flush=True)

    make_frame(subject, hook, frame)
    if args.dry_run:
        print(f"[discovery-gen] DRY-RUN: frame={frame} (video not built)")
        return

    video_path = build_video(narration, frame, video)
    if not video_path:
        print("[discovery-gen] video build FAILED — not staging", file=sys.stderr)
        sys.exit(1)

    # Stage into BOTH locations:
    #  - HERE/media  = pcmedicalist-retrobyte-media repo, which MEDIA_BASE_URL
    #    serves via GitHub raw (the SAME repo the 18 random clips live in and
    #    which Buffer fetches from). MUST be git-pushed by the host cron so the
    #    raw URL resolves (404 otherwise — this was the first end-to-end failure).
    #  - agent media dir: kept as a local mirror so any local existence check
    #    (MEDIA_DIR) still passes inside the container.
    repo_media = HERE / "media"
    repo_media.mkdir(parents=True, exist_ok=True)
    dest = repo_media / Path(video_path).name
    shutil.copy(video_path, dest)
    try:
        agent_dest = Path(MEDIA_DIR) / Path(video_path).name
        agent_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(video_path, agent_dest)
    except Exception as _ce:
        print(f"[discovery-gen] agent-mirror skip: {_ce}", file=sys.stderr)

    caption = f"{narration}\n\n#RetroByte #90sTech #PCMedicalist\nDiscover 90s tech with RetroByte on the baseLINE Twitch extension → baseline.click"
    ready = {
        "video": str(dest),
        "video_name": dest.name,
        "caption": caption,
        "subject": subject,
        "slot": args.slot,
        "ts": _dt.datetime.utcnow().isoformat() + "Z",
    }
    READY_FILE.write_text(json.dumps(ready, indent=2))
    print(f"[discovery-gen] STAGED: {dest.name} | caption {len(caption)} chars", flush=True)


if __name__ == "__main__":
    main()
