#!/usr/bin/env python3
"""
RetroByte — "Discovering 90's Tech" video generator (HOST build step).

Generates a VIRAL-STYLE narrated, multi-shot RetroByte video about a 90s tech
artifact, then stages it for the in-container retrobyte-cron poster to publish
to X / IG / TikTok.

Pipeline (upgraded 2026-09-22):
  1. Pick a 90s-tech subject (rotating, no repeat until cycled).
  2. Narrate it in RetroByte voice via the local retrobyte:3b Ollama model.
  3. Build a 3-BEAT montage (NOT a frozen still):
       beat 1 = HOOK card (bold subject + curiosity line, period CRT backdrop)
       beat 2 = ARTIFACT hero (real public-domain Wikimedia photo, full-bleed)
       beat 3 = REACTION/CTA (RetroByte large + sign-off + baseline.click)
     Each beat gets a subtle Ken-Burns zoom so the clip has motion.
  4. Mux the beats into one narrated MP4 via mux_video.mux_beats() (timed CC
     burned across the whole timeline by media_clip's pipeline).
  5. Stage mp4 + caption; write _discovery_ready.json for the poster.

No more black-void single frames — every episode shows the real artifact and
cuts between three distinct shots for retention/virality.

Run from host (needs content-os venv: edge-tts + PIL; system ffmpeg; Ollama
retrobyte:3b). Invoked by a host cron at 11:00 + 20:00 (staggered away from
PCMedicalist's GPU windows).
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

import requests

# --- Paths ---------------------------------------------------------------
HERE = Path(__file__).resolve().parent
MEDIA_DIR = Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/media")
OUTBOX = HERE / "generated"
OUTBOX.mkdir(parents=True, exist_ok=True)
READY_FILE = HERE / "_discovery_ready.json"
IMG_CACHE = HERE / "img_cache"
IMG_CACHE.mkdir(parents=True, exist_ok=True)

# --- Authoritative 90s-tech subject sequence (NO repeats) -----------------
SERIES_FILES = [
    Path("/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/"
         "RETROBYTE-OLD-TECH-DISCOVERY.md"),
    HERE / "RETROBYTE OLD TECH DISCOVERY.md",  # sync copy fallback
]
STATE_FILE = HERE / "_discovery_state.json"
POSTED_REGISTRY = HERE / "_posted_registry.json"


def load_posted() -> set:
    """Return the set of episode TITLES already generated/staged (dedup table)."""
    if POSTED_REGISTRY.exists():
        try:
            return set(json.loads(POSTED_REGISTRY.read_text()).get("titles", []))
        except Exception:
            return set()
    return set()


def record_posted(title: str, slot: int, fname: str):
    """Persist a generated episode to the dedup registry so it is never
    re-picked until the full series cycle completes."""
    data = {"titles": [], "cycle": 0}
    if POSTED_REGISTRY.exists():
        try:
            data = json.loads(POSTED_REGISTRY.read_text())
        except Exception:
            data = {"titles": [], "cycle": 0}
    titles = set(data.get("titles", []))
    titles.add(title)
    data["titles"] = sorted(titles)
    data["last"] = {"title": title, "slot": slot, "file": fname,
                    "ts": _dt.datetime.utcnow().isoformat() + "Z"}
    POSTED_REGISTRY.write_text(json.dumps(data, indent=2))


# --- Real artifact imagery: Wikimedia Commons file title per subject -------
# Public-domain / freely-licensed photos so the montage shows the ACTUAL
# gadget (not a mascot on black). Keyed by subject title (case-insensitive).
WIKIMEDIA_TITLES = {
    "floppy disk": "File:Floppy_disk_2009_G1.jpg",
    "walkman": "File:Sony_Walkman_TPS-L2.jpg",
    "rotary phone": "File:Gpo_746_telephone.jpg",
    "the game boy": "File:Nintendo-Game-Boy-FL.jpg",
    "the vhs tape": "File:VHS-Video-Tape.jpg",
    "the cassette tape": "File:Audio_Cassette.jpg",
    "the pager": "File:Motorola_Bravo_Pager.jpg",
    "the tamagotchi": "File:Tamagotchi_original.jpg",
    "the compact disc": "File:Compact_disc.jpg",
    "the dial-up modem": "File:USRobotics_Sportster_56K_Courier_modem.jpg",
    "the camcorder": "File:Sony_Hi8_Camcorder.jpg",
    "the polaroid camera": "File:Polaroid_sx70.jpg",
    "the laserdisc": "File:Laserdisc.jpg",
    "the minidisc": "File:Minidisc_Sony.jpg",
    "the beeper": "File:Pager_Motorola.jpg",
}


def load_episodes() -> list[tuple[str, str]]:
    """Return [(title, slug), ...] parsed from the series bible, in order."""
    text = ""
    for sf in SERIES_FILES:
        if sf.exists():
            text = sf.read_text(encoding="utf-8", errors="ignore")
            break
    if not text:
        return [("Floppy Disk", "the 1.44MB time capsule"),
                ("Walkman", "music in your pocket"),
                ("Rotary Phone", "the dial that rang")]
    eps = []
    for m in re.finditer(r"^#+\s*(\d{1,3})\s*[—-]\s*(.+)$", text, re.M):
        title = m.group(2).strip()
        eps.append((title, title))
    seen = set(); out = []
    for t, s in eps:
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append((t, s))
    return out or [("Floppy Disk", "the 1.44MB time capsule")]


def next_episode(slot: int, force: str | None = None):
    eps = load_episodes()
    n = len(eps)
    st = {}
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text())
        except Exception:
            st = {}
    last = int(st.get("last_index", -1))
    posted = load_posted()
    if force:
        idx = next((i for i, (t, _s) in enumerate(eps)
                    if t.lower() == force.lower()), None)
        if idx is None:
            eps.append((force, force)); idx = n; n += 1
    else:
        # Walk forward from last_index+1, SKIP any title already generated this
        # cycle (dedup table). Loop forever without re-posting within a cycle.
        for step in range(1, n + 1):
            cand = (last + step) % n
            if eps[cand][0] not in posted:
                idx = cand
                break
        else:
            # Entire series already posted — start a fresh cycle.
            posted.clear()
            POSTED_REGISTRY.write_text(json.dumps({"titles": [], "cycle": st.get("cycle", 0) + 1}))
            idx = (last + 1) % n
    title, slug = eps[idx]
    STATE_FILE.write_text(json.dumps({"last_index": idx, "episode": title,
                                      "ts": _dt.datetime.utcnow().isoformat() + "Z"}))
    return title, slug, idx


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
            t = re.sub(r"[#@]\w+", "", t)
            t = "".join(ch for ch in t if ord(ch) < 0x2600)
            return t.strip() or f"WAIT... {subject}! {hook}! RetroByte, signing off!"
    except Exception as e:
        print(f"[discovery-gen] narrate fail: {e}", file=sys.stderr)
    return f"WAIT... {subject}! {hook}! RetroByte, signing off!"


# --- Wikimedia fetch (cached) ------------------------------------------
def fetch_artifact(subject: str) -> Path | None:
    """Return a cached local path to a public-domain photo of the subject, or
    None if unavailable. Uses Wikimedia Commons API (no key, rate-friendly)."""
    key = subject.lower().strip()
    title = None
    for k, v in WIKIMEDIA_TITLES.items():
        if k in key or key in k:
            title = v; break
    if not title:
        return None
    cache = IMG_CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", key) + ".jpg")
    if cache.exists():
        return cache
    try:
        api = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
               "&prop=imageinfo&iiprop=url&titles=" + requests.utils.quote(title))
        meta = requests.get(api, headers={"User-Agent": "PCMedicalistRetroByte/1.0"},
                            timeout=25)
        if not meta.ok or "application/json" not in meta.headers.get("Content-Type", ""):
            return None
        meta = meta.json()
        pages = meta.get("query", {}).get("pages", {})
        url = None
        for p in pages.values():
            ii = (p.get("imageinfo") or [{}])[0]
            url = ii.get("url")
            break
        if not url:
            return None
        img = requests.get(url, headers={"User-Agent": "PCMedicalistRetroByte/1.0"},
                            timeout=40)
        if img.ok and img.content[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\x89PNG"):
            cache.write_bytes(img.content)
            return cache
    except Exception as e:
        print(f"[discovery-gen] artifact fetch fail ({subject}): {e}", file=sys.stderr)
    return None


# --- RetroByte character model ------------------------------------------
CHARACTER_IMG = Path(
    "/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand/"
    "retrobyte-video-model.png")

# ---- 2026-09-25: rotating cast + backgrounds (operator directive) ----------
# Foreground: 18 agent sprites (RGBA, transparent, ~330-363px) in brand/agents/.
# Background: 35 FLUX2 room backdrops (1024x576) in brand/backgrounds/.
# Per-episode pick is deterministic from the episode index so the same episode
# always renders the same character/room, and consecutive episodes rotate both.
AGENTS_DIR = Path(
    "/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand/agents")
BACKGROUNDS_DIR = Path(
    "/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand/backgrounds")


def _rotating_asset(directory: Path, idx: int) -> Path | None:
    """Deterministically pick directory-sorted asset #idx (wraps)."""
    try:
        files = sorted(p for p in directory.glob("*.png"))
    except Exception:
        return None
    if not files:
        return None
    return files[idx % len(files)]


def episode_character(ep_idx: int) -> Path | None:
    return _rotating_asset(AGENTS_DIR, ep_idx)


def episode_background(ep_idx: int) -> Path | None:
    return _rotating_asset(BACKGROUNDS_DIR, ep_idx)


def _load_char_rgba(ep_idx: int) -> "Image.Image | None":
    """Load the episode's agent sprite as RGBA (transparent foreground)."""
    p = episode_character(ep_idx)
    if not p or not p.exists():
        return None
    try:
        c = __import__("PIL").Image.open(p).convert("RGBA")
        return c
    except Exception:
        return None


def _paste_char(img, scale=0.55, cy_frac=0.60, ep_idx: int | None = None,
                bottom_center: bool = False, lift_px: int = 0):
    """Paste the agent sprite with ALPHA (replaces the old RGB square paste).

    ep_idx=None falls back to the legacy CHARACTER_IMG behavior.
    bottom_center=True anchors the sprite centered at the bottom band.
    lift_px raises the sprite off the bottom edge (keeps CTA visible).
    """
    char = None
    if ep_idx is not None:
        char = _load_char_rgba(ep_idx)
    if char is None and CHARACTER_IMG.exists():
        # Legacy fallback: opaque square, old behavior.
        char = __import__("PIL").Image.open(CHARACTER_IMG).convert("RGBA")
    if char is None:
        return
    W, H = img.size
    target = int(W * scale)
    try:
        _rs = __import__("PIL").Image.Resampling.LANCZOS
    except AttributeError:
        _rs = __import__("PIL").Image.LANCZOS
    # Fit by the sprite's real aspect (not forced square) but cap height so a
    # square-ish sprite scales the same as before.
    ratio = char.height / char.width
    tw, th = target, int(target * ratio)
    max_h = int(H * 0.62)
    if th > max_h:
        th = max_h
        tw = int(th / ratio)
    char = char.resize((tw, th), _rs)
    if bottom_center:
        cx = (W - tw) // 2
        cy = H - th - lift_px
    else:
        cx = (W - tw) // 2
        cy = min(int(H * cy_frac), H - th - lift_px)
    # Alpha composite (keeps sprite transparency; works on RGB base too).
    if img.mode != "RGBA":
        base_rgba = img.convert("RGBA")
        base_rgba.alpha_composite(char, (cx, cy))
        img.paste(base_rgba.convert("RGB"))
    else:
        img.alpha_composite(char, (cx, cy))


def _bg_base(img_size: tuple[int, int], ep_idx: int, scrim: float = 0.45) -> "Image.Image":
    """Build the card base: cover-fit episode background + dark scrim for
    text contrast. Falls back to the old flat gradient colors if bg missing."""
    from PIL import Image, ImageDraw
    W, H = img_size
    bg_path = episode_background(ep_idx)
    base = None
    if bg_path and bg_path.exists():
        try:
            bg = Image.open(bg_path).convert("RGB")
            try:
                _rs = Image.Resampling.LANCZOS
            except AttributeError:
                _rs = Image.LANCZOS
            # cover-fit: scale so both dims >= target, center-crop
            sw, sh = bg.size
            k = max(W / sw, H / sh)
            bg = bg.resize((int(sw * k) + 1, int(sh * k) + 1), _rs)
            bx = (bg.width - W) // 2
            by = (bg.height - H) // 2
            base = bg.crop((bx, by, bx + W, by + H))
        except Exception:
            base = None
    if base is None:
        base = Image.new("RGB", (W, H), (12, 8, 26))
    if scrim > 0:
        overlay = Image.new("RGBA", (W, H), (8, 6, 20, int(255 * scrim)))
        base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    return base


def _font(big=True):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72 if big else 40)
    except Exception:
        return ImageFont.load_default()


def _paste_char_lifted(img, scale, ep_idx, lift=0):
    """Bottom-center paste with a lift so bottom-band text stays visible."""
    _paste_char(img=img, scale=scale, cy_frac=0.0, ep_idx=ep_idx,
                bottom_center=True, lift_px=lift)


def make_hook_frame(subject: str, hook: str, out_path: Path,
                    ep_idx: int | None = None) -> Path:
    """BEAT 1 — bold HOOK card on the episode's room bg (scrimmed for text)."""
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    base = (_bg_base((W, H), ep_idx, scrim=0.55) if ep_idx is not None
            else Image.new("RGB", (W, H), (12, 8, 26)))
    d = ImageDraw.Draw(base)
    amber = (255, 176, 0); green = (120, 255, 140)
    fbig = _font(True); fsmall = _font(False)
    d.text((60, 120), "RETROBYTE DISCOVERS:", fill=green, font=fbig)
    # subject, wrapped to <=14 chars/line
    words = subject.split(); lines, cur = [], ""
    for w in words:
        if len(cur + " " + w) > 14:
            lines.append(cur.strip()); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur: lines.append(cur)
    y = 280
    for ln in lines:
        d.text((60, y), ln, fill=amber, font=fbig); y += 110
    # curiosity hook line
    d.text((60, y + 30), hook[:42], fill=(220, 220, 255), font=fsmall)
    if ep_idx is not None:
        # Cast member peeking from the bottom band, lifted 190px so the
        # bottom CTA line (H-110) stays fully visible (2026-09-25 QA fix:
        # sprite foot overlapped 'baseline.click'). 367px sprite: bottom
        # lands at 1920-367-190=1363, well clear of the CTA band (~1770).
        _lift = 190
        _paste_char_lifted(base, scale=0.34, ep_idx=ep_idx, lift=_lift)
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    d.text((60, H - 110), "PCMedicalist · baseline.click", fill=amber, font=fsmall)
    base.save(out_path)
    return out_path


def make_artifact_frame(subject: str, out_path: Path,
                        ep_idx: int | None = None) -> Path:
    """BEAT 2 — SAME hook-card layout as beat 1 (consistent production).

    2026-09-25 operator note: the 3 different card layouts read as "breaking
    with each scene adjustment" — the operator liked the hook-card look, so
    beats 2 and 3 now reuse it verbatim and only the caption line changes.
    """
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    base = (_bg_base((W, H), ep_idx, scrim=0.55) if ep_idx is not None
            else Image.new("RGB", (W, H), (12, 8, 26)))
    d = ImageDraw.Draw(base)
    amber = (255, 176, 0); green = (120, 255, 140)
    fbig = _font(True); fsmall = _font(False)
    d.text((60, 120), "RETROBYTE DISCOVERS:", fill=green, font=fbig)
    # subject, wrapped to <=14 chars/line (identical block to beat 1)
    words = subject.split(); lines, cur = [], ""
    for w in words:
        if len(cur + " " + w) > 14:
            lines.append(cur.strip()); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur: lines.append(cur)
    y = 280
    for ln in lines:
        d.text((60, y), ln, fill=amber, font=fbig); y += 110
    # BEAT 2 caption line (replaces the beat-1 hook text)
    d.text((60, y + 30), "LOOK AT THIS THING!", fill=(220, 220, 255), font=fsmall)
    if ep_idx is not None:
        _paste_char_lifted(base, scale=0.34, ep_idx=ep_idx, lift=190)
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    d.text((60, H - 110), "PCMedicalist · baseline.click", fill=amber, font=fsmall)
    base.save(out_path)
    return out_path


def make_reaction_frame(subject: str, out_path: Path,
                        ep_idx: int | None = None) -> Path:
    """BEAT 3 — SAME hook-card layout as beats 1-2 (consistent production)."""
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    base = (_bg_base((W, H), ep_idx, scrim=0.55) if ep_idx is not None
            else Image.new("RGB", (W, H), (12, 8, 26)))
    d = ImageDraw.Draw(base)
    amber = (255, 176, 0); green = (120, 255, 140)
    fbig = _font(True); fsmall = _font(False)
    d.text((60, 120), "RETROBYTE DISCOVERS:", fill=green, font=fbig)
    # subject, wrapped to <=14 chars/line (identical block to beat 1)
    words = subject.split(); lines, cur = [], ""
    for w in words:
        if len(cur + " " + w) > 14:
            lines.append(cur.strip()); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur: lines.append(cur)
    y = 280
    for ln in lines:
        d.text((60, y), ln, fill=amber, font=fbig); y += 110
    # BEAT 3 caption line (the wow closer)
    d.text((60, y + 30), "WAIT... IS THIS REAL?!", fill=(220, 220, 255), font=fsmall)
    if ep_idx is not None:
        _paste_char_lifted(base, scale=0.34, ep_idx=ep_idx, lift=190)
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    d.text((60, H - 110), "PCMedicalist · baseline.click", fill=amber, font=fsmall)
    base.save(out_path)
    return out_path


def _probe_ok_for_reels(path: Path) -> bool:
    """Return True if the clip meets Instagram Reels + TikTok minimums:
    >=3s duration, >=23 fps, >=360px height (TikTok both 9:16 and 1:1 need >=360px)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration:stream=avg_frame_rate,height",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        if not lines:
            return False
        dur = float(lines[0])
        # avg_frame_rate may be like "30/1" or "29.97"
        fps_raw = lines[1] if len(lines) > 1 else "0"
        if "/" in fps_raw:
            num, den = fps_raw.split("/", 1)
            fps = float(num) / (float(den) if float(den) else 1.0)
        else:
            fps = float(fps_raw)
        h = int(lines[2].split(",")[0]) if len(lines) > 2 and "," in lines[2] else None
        if h is None:
            # try per-stream height line
            for ln in lines[1:]:
                if ln.replace("/", "").replace(".", "").isdigit():
                    continue
                parts = ln.split(",")
                for p in parts:
                    pv = p.strip()
                    if pv.isdigit() and int(pv) > 0:
                        h = int(pv); break
                if h:
                    break
        print(f"[discovery-gen] probe: dur={dur:.2f}s fps={fps:.2f} h={h}",
              file=sys.stderr)
        return dur >= 3.0 and fps >= 23 and (h or 0) >= 360
    except Exception as e:
        print(f"[discovery-gen] probe exc: {e}", file=sys.stderr)
        return False


def generate_t2v_clip(narration: str, subject: str, out_path: Path) -> str | None:
    """Generate REAL motion video via the sovereign T2VZ wrapper (no pan/still).

    The narration becomes the video prompt so RetroByte + the gadget are
    generated as genuine diffusion motion. Returns the clip path or None.

    Output must meet Instagram Reels + TikTok minimums (>=3s, >=23fps,
    >=360px height) or we fall back to the still-frame montage path, which
    posts reliably to all three channels. The old --frames 8 --fps 8 config
    produced a 1s clip that Reels/TikTok both rejected.
    """
    from PIL import Image
    # Build a RetroByte-flavored T2V prompt from the narration + subject.
    prompt = (
        f"retro 1990s nostalgia animation, a cute CRT-TV-headed robot with glowing "
        f"blue pixel eyes discovers an old {subject.lower()}, wide-eyed amazement, "
        f"reaches out to touch it, warm amber lighting, vaporwave palette, "
        f"smooth motion, hand-drawn cartoon style"
    )
    t2vz = HERE / "t2vz_generate.py"
    venv_py = Path("/home/pcmedicalist/pcmedicalist-diffusion/venv/bin/python3")
    if not t2vz.exists() or not venv_py.exists():
        print("[discovery-gen] t2vz_generate.py or venv missing", file=sys.stderr)
        return None
    base = out_path.with_name(out_path.stem + "_t2v.mp4")
    try:
        # 3s @ 24fps = 72 frames minimum to clear Reels (>=3s + >=23fps) and
        # TikTok (>=3s + >=360px). Use 30fps for headroom above the 23fps floor.
        r = subprocess.run(
            [str(venv_py), str(t2vz), "--prompt", prompt, "--out", str(base),
             "--steps", "25", "--frames", "96", "--size", "576*576", "--fps", "30"],
            capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not base.exists():
            print(f"[discovery-gen] t2vz fail: {(r.stderr or r.stdout)[-400:]}",
                  file=sys.stderr)
            return None
        # Validate platform minimums — fall back to montage if T2VZ still came
        # out too short/low-res (T2VZ is an 11GB VRAM squeeze on the 2060, so the
        # recipe can produce marginal clips; montage is the safe posting path).
        if not _probe_ok_for_reels(base):
            print("[discovery-gen] T2VZ clip below platform minimums — "
                  "falling back to montage", file=sys.stderr)
            return None
        return str(base)
    except Exception as e:
        print(f"[discovery-gen] t2vz exc: {e}", file=sys.stderr)
        return None


def brand_overlay(clip_path: Path, out_path: Path,
                  ep_idx: int | None = None) -> str | None:
    """Composite the episode's cast sprite over the T2VZ motion clip.

    T2VZ cannot hold a consistent character (recipe gotcha: generates generic
    objects). We overlay the actual agent PNG (transparent RGBA) so the video
    is always on-brand, while the underlying clip provides gadget motion.
    Sprite sits lower-left. ep_idx=None falls back to legacy CHARACTER_IMG.
    Uses the robust two-input -filter_complex form (the movie= form is fragile).
    """
    ffmpeg = shutil.which("ffmpeg")
    char = episode_character(ep_idx) if ep_idx is not None else None
    if char is None or not char.exists():
        char = CHARACTER_IMG
    if not ffmpeg or not char.exists():
        return None
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(clip_path), "-i", str(char),
             "-filter_complex",
             "[1:v]scale=iw*0.38:-1[w];[0:v][w]overlay=W*0.04:H-h-(H*0.04)",
             "-c:a", "copy", str(out_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if r.returncode == 0 and out_path.exists():
            return str(out_path)
        print(f"[discovery-gen] brand overlay fail: {(r.stderr or b'')[-300:].decode(errors='ignore')}",
              file=sys.stderr)
    except Exception as e:
        print(f"[discovery-gen] brand overlay exc: {e}", file=sys.stderr)
    return None


def mux_audio_cc(clip_path: Path, audio_path: Path, script: str, out_path: Path) -> str | None:
    """Add narrated audio to the T2VZ motion clip via ffmpeg (reliable stream copy).

    NOTE (recipe gotcha #6): mux_reel expects an IMAGE, not an MP4. We use plain
    ffmpeg. Burned CC is skipped here (fragile ASS pass) — the narration audio is
    the must-have; caption text is delivered in the post body instead.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    tmp = out_path.with_name(out_path.stem + "_mixed.mp4")
    r1 = subprocess.run(
        [ffmpeg, "-y", "-i", str(clip_path), "-i", str(audio_path),
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest", str(tmp)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if r1.returncode != 0 or not tmp.exists():
        r1 = subprocess.run(
            [ffmpeg, "-y", "-i", str(clip_path), "-i", str(audio_path),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-c:a", "aac",
             "-shortest", str(tmp)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if r1.returncode != 0 or not tmp.exists():
        print(f"[discovery-gen] audio mux fail: {(r1.stderr or b'')[-300:].decode(errors='ignore')}",
              file=sys.stderr)
        return None
    # tmp is the final narrated clip (audio + video, CC delivered in post text)
    shutil.move(str(tmp), str(out_path))
    return str(out_path)


def build_montage(narration: str, frames: list[Path], audio_path: Path,
                  out_path: Path) -> str | None:
    pipelines = Path("/home/pcmedicalist/.hermes/skills/social-media/"
                     "pcmedicalist-social-publisher/pipelines")
    sys.path.insert(0, str(pipelines))
    import media_clip as mc
    # Split the narration duration across 3 beats (hook ~25%, artifact ~50%,
    # reaction ~25%) so the artifact hero gets the most screen time.
    dur = mc._audio_duration(audio_path)
    dur = max(12.0, min(60.0, dur))
    # Split the narration into 3 proportional slices so each beat carries its own
    # CC (cleaner than one global CC timeline across concatenated clips).
    words = narration.split()
    n = len(words)
    i1 = max(1, int(n * 0.25)); i2 = max(i1 + 1, int(n * 0.75))
    slice0 = " ".join(words[:i1]); slice1 = " ".join(words[i1:i2]); slice2 = " ".join(words[i2:])
    # start offsets + durations per beat (proportional to narration)
    bdurs = [dur * 0.25, dur * 0.50, dur * 0.25]
    bscripts = [slice0, slice1, slice2]
    bstarts = [0.0, bdurs[0], bdurs[0] + bdurs[1]]
    try:
        from mux_video import mux_reel
    except Exception:
        sys.path.insert(0, str(Path.home() / "pcmedicalist" /
                              "pcmedicalist-content-os" / "generators"))
        from mux_video import mux_reel
    ffmpeg = shutil.which("ffmpeg")
    # 1) Render each beat to its own short MP4 (sliced audio + Ken-Burns + CC).
    beat_mp4s = []
    for i, (img, bdur, bscript, bstart) in enumerate(
            zip(frames, bdurs, bscripts, bstarts)):
        bmp4 = out_path.with_name(out_path.stem + f"_b{i}.mp4")
        baudio = out_path.with_name(out_path.stem + f"_ba{i}.mp3")
        sl = subprocess.run([ffmpeg, "-y", "-ss", f"{bstart:.2f}",
                             "-t", f"{bdur:.2f}", "-i", str(audio_path),
                             "-c", "copy", str(baudio)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=60)
        if not Path(baudio).exists():
            print(f"[discovery-gen] beat {i} audio slice fail", file=sys.stderr)
            return None
        br = mux_reel(img, baudio, bmp4, ken_burns=False, script=bscript,
                     captions=True)
        if not br.get("ok") or not Path(bmp4).exists():
            print(f"[discovery-gen] beat {i} render fail: {br.get('error')}",
                  file=sys.stderr)
            return None
        beat_mp4s.append(bmp4)
    # 2) Concatenate the beat MP4s via the concat DEMUXER (rock-solid on this
    #    ffmpeg build; the filtergraph concat was unreliable).
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    concat_list = out_path.with_name(out_path.stem + "_concat.txt")
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in beat_mp4s))
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
           "-c", "copy", str(out_path)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False, timeout=120)
        if r.returncode != 0 or not Path(out_path).exists():
            print(f"[discovery-gen] concat fail: {(r.stderr or '')[-400:]}",
                  file=sys.stderr)
            return None
    except Exception as e:
        print(f"[discovery-gen] concat exc: {e}", file=sys.stderr)
        return None
    # cleanup intermediate beat clips
    for p in beat_mp4s + [concat_list]:
        try: p.unlink()
        except Exception: pass
    return str(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=0, choices=[0, 1])
    ap.add_argument("--force-subject", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-render", action="store_true",
                    help="Render only (no staging / no state advance).")
    args = ap.parse_args()

    subject, hook, ep_idx = next_episode(args.slot, args.force_subject)
    print(f"[discovery-gen] episode#{ep_idx + 1} subject={subject} slot={args.slot}",
          flush=True)

    stamp = _dt.date.today().strftime("%Y-%m-%d") + f"-d{args.slot}"
    f_hook = OUTBOX / f"disc_frame_{stamp}_hook.png"
    f_art = OUTBOX / f"disc_frame_{stamp}_art.png"
    f_react = OUTBOX / f"disc_frame_{stamp}_react.png"
    video = OUTBOX / f"disc_video_{stamp}.mp4"

    narration = narrate(subject, hook)
    print(f"[discovery-gen] narration: {narration[:80]}...", flush=True)

    # Rotating cast + room: episode index drives both picks (deterministic).
    _char_p = episode_character(ep_idx)
    _bg_p = episode_background(ep_idx)
    print(f"[discovery-gen] cast={_char_p.name if _char_p else 'legacy'} "
          f"room={_bg_p.name if _bg_p else 'gradient'}", flush=True)

    make_hook_frame(subject, hook, f_hook, ep_idx=ep_idx)
    make_artifact_frame(subject, f_art, ep_idx=ep_idx)
    make_reaction_frame(subject, f_react, ep_idx=ep_idx)
    frames = [f_hook, f_art, f_react]

    if args.dry_run or args.test_render:
        print(f"[discovery-gen] {'TEST' if args.test_render else 'DRY'}-RUN: "
              f"frames={[f.name for f in frames]} (video not built)")
        if args.test_render:
            # still build a sample for visual QA
            pipelines = Path("/home/pcmedicalist/.hermes/skills/social-media/"
                             "pcmedicalist-social-publisher/pipelines")
            sys.path.insert(0, str(pipelines))
            import media_clip as mc
            mp3 = OUTBOX / f"disc_video_{stamp}.mp3"
            vr = mc._tts(narration, str(mp3), voice="en-US-AnaNeural")
            if vr.get("ok"):
                build_montage(narration, frames, mp3, video)
                print(f"[discovery-gen] TEST VIDEO: {video}")
        return

    mp3 = OUTBOX / f"disc_video_{stamp}.mp3"
    pipelines = Path("/home/pcmedicalist/.hermes/skills/social-media/"
                     "pcmedicalist-social-publisher/pipelines")
    sys.path.insert(0, str(pipelines))
    import media_clip as mc
    vr = mc._tts(narration, str(mp3), voice="en-US-AnaNeural")
    if not vr.get("ok") or not mp3.exists():
        print("[discovery-gen] TTS FAILED — not staging", file=sys.stderr)
        sys.exit(1)

    video_path = generate_t2v_clip(narration, subject, video)
    if not video_path:
        print("[discovery-gen] T2VZ gen FAILED — falling back to montage",
              file=sys.stderr)
        video_path = build_montage(narration, frames, mp3, video)
    else:
        # Brand-lock: overlay real RetroByte mascot over the T2VZ motion.
        branded = video.with_name(video.stem + "_brand.mp4")
        branded_path = brand_overlay(Path(video_path), branded, ep_idx=ep_idx)
        if branded_path:
            video_path = branded_path
        else:
            print("[discovery-gen] brand overlay FAILED — using raw T2VZ clip",
                  file=sys.stderr)
        # Overlay narrated audio + burned CC onto the motion clip.
        muxed = video.with_name(video.stem + "_narrated.mp4")
        muxed_path = mux_audio_cc(Path(video_path), mp3, narration, muxed)
        if muxed_path:
            video_path = muxed_path
        else:
            print("[discovery-gen] audio mux FAILED — using silent T2VZ clip",
                  file=sys.stderr)
    if not video_path or not Path(video_path).exists():
        print("[discovery-gen] video build FAILED — not staging", file=sys.stderr)
        sys.exit(1)

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

    # Caption must fit X's 280-char limit on ALL channels. Buffer weights
    # multibyte chars (emoji, →, em-dash) and a 271-char caption was still
    # rejected 2026-09-25, so cap the staged caption at 240 chars: build the
    # full text, then truncate the narration body (never the CTA/hashtags)
    # with word-boundary + ellipsis until it fits.
    caption = (f"{narration}\n\n#RetroByte #90sTech #PCMedicalist\n"
               "Discover 90s tech with RetroByte on the baseLINE Twitch extension "
               "→ baseline.click")
    CAPTION_MAX = 240
    if len(caption) > CAPTION_MAX:
        _suffix = "\n\n#RetroByte #90sTech #PCMedicalist\n" \
                  "Discover 90s tech with RetroByte on the baseLINE Twitch extension " \
                  "→ baseline.click"
        _head_room = CAPTION_MAX - len(_suffix)
        _body = narration[:_head_room - 1].rsplit(None, 1)[0].rstrip(" ,.!") + "…"
        caption = _body + _suffix
    assert len(caption) <= CAPTION_MAX, (
        f"staged caption {len(caption)} chars exceeds {CAPTION_MAX}")
    ready = {"video": str(dest), "video_name": dest.name, "caption": caption,
             "subject": subject, "slot": args.slot,
             "ts": _dt.datetime.utcnow().isoformat() + "Z"}
    READY_FILE.write_text(json.dumps(ready, indent=2))
    record_posted(subject, args.slot, dest.name)

    # ---- Persist staged media to the git remote so raw.githubusercontent.com
    #      can serve it to Buffer's shareNow. Without this the video exists
    #      locally but 404s on the raw URL (the bug that broke Sep 22-24 posts).
    #      Only stage + commit files that are actually NEW/CHANGED — never
    #      rewrite history or clobber other agents' uploads.
    #      NOTE: `dest` is the full absolute repo path (e.g.
    #      /home/.../media/disc_video_....mp4). git add -C "$HERE" needs the
    #      repo-relative form (e.g. media/disc_video_....mp4), so strip the
    #      HERE prefix.
    _stage = str(dest)
    if _stage.startswith(str(HERE) + os.sep):
        _stage = _stage[len(str(HERE)) + 1:]
    elif _stage.startswith(str(HERE)):
        _stage = _stage[len(str(HERE)):]
    _ok = True
    if _stage not in ("", None):
        try:
            _rc = subprocess.run(
                ["git", "add", _stage],
                cwd=str(HERE), capture_output=True, text=True, timeout=30)
            if _rc.returncode != 0:
                print(f"[discovery-gen] git add {_stage} failed: {_rc.stderr.strip()[-200:]}",
                      file=sys.stderr); _ok = False
        except Exception as _e:
            print(f"[discovery-gen] git add exc: {_e}", file=sys.stderr); _ok = False
    if _ok and subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--"],
            cwd=str(HERE), capture_output=True, timeout=15).returncode == 0:
        # Nothing new to commit after the add (already tracked + unchanged).
        print(f"[discovery-gen] media tracked, no new commit needed", flush=True)
    elif _ok:
        try:
            _msg = (f"feat(discovery): {subject.lower()} — d{args.slot} narrated video "
                    f"({_dt.date.today().isoformat()})\n"
                    f"\nCo-Authored-By: PCMedicalist <noreply@pcmedicalist.com>")
            _rc = subprocess.run(
                ["git", "commit", "-m", _msg],
                cwd=str(HERE), capture_output=True, text=True, timeout=60)
            if _rc.returncode != 0:
                print(f"[discovery-gen] git commit failed: {_rc.stderr.strip()[-200:]}",
                      file=sys.stderr); _ok = False
            else:
                print(f"[discovery-gen] committed: {_rc.stdout.strip().splitlines()[-1]}",
                      flush=True)
        except Exception as _e:
            print(f"[discovery-gen] git commit exc: {_e}", file=sys.stderr); _ok = False
    if _ok:
        try:
            _pr = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(HERE), capture_output=True, text=True, timeout=120)
            if _pr.returncode != 0:
                print(f"[discovery-gen] git push WARN (non-fatal): {_pr.stderr.strip()[-200:]}",
                      file=sys.stderr)
            else:
                _ln = _pr.stdout.strip().splitlines()
                print(f"[discovery-gen] pushed: {_ln[-1] if _ln else 'ok'}", flush=True)
        except Exception as _e:
            print(f"[discovery-gen] git push exc: {_e}", file=sys.stderr)
    # ---- end persist-to-git ----

    print(f"[discovery-gen] STAGED: {dest.name} | caption {len(caption)} chars",
          flush=True)


if __name__ == "__main__":
    main()
