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


def _font(big=True):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72 if big else 40)
    except Exception:
        return ImageFont.load_default()


def _paste_char(img, scale=0.55, cy_frac=0.60):
    if not CHARACTER_IMG.exists():
        return
    char = __import__("PIL").Image.open(CHARACTER_IMG).convert("RGB")
    W, H = img.size
    target = int(W * scale)
    try:
        _rs = __import__("PIL").Image.Resampling.LANCZOS
    except AttributeError:
        _rs = __import__("PIL").Image.LANCZOS
    char = char.resize((target, target), _rs)
    cx = (W - target) // 2
    cy = int(H * cy_frac)
    img.paste(char, (cx, cy))


def make_hook_frame(subject: str, hook: str, out_path: Path) -> Path:
    """BEAT 1 — bold HOOK card on a period-CRT gradient (NO black void)."""
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    # CRT gradient backdrop instead of flat black.
    base = Image.new("RGB", (W, H), (12, 8, 26))
    px = base.load()
    for y in range(H):
        # deep indigo top -> warm amber-tinted bottom
        t = y / H
        r = int(12 + t * 60); g = int(8 + t * 30); b = int(26 + t * 10)
        for x in range(0, W, 4):
            px[x, y] = (r, g, b)
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
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    d.text((60, H - 110), "PCMedicalist · baseline.click", fill=amber, font=fsmall)
    base.save(out_path)
    return out_path


def make_artifact_frame(subject: str, out_path: Path) -> Path:
    """BEAT 2 — real artifact photo full-bleed with neon frame + RetroByte."""
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    art = fetch_artifact(subject)
    img = Image.new("RGB", (W, H), (18, 14, 34))
    if art and art.exists():
        try:
            ph = __import__("PIL").Image.open(art).convert("RGB")
            try:
                _rs = __import__("PIL").Image.Resampling.LANCZOS
            except AttributeError:
                _rs = __import__("PIL").Image.LANCZOS
            # cover-fit top 78% (leave lower band for RetroByte)
            ph = ph.resize((W, int(H * 0.78)), _rs)
            img.paste(ph, (0, 0))
        except Exception:
            pass
    d = ImageDraw.Draw(img)
    amber = (255, 176, 0)
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    _paste_char(img, scale=0.42, cy_frac=0.80)  # RetroByte lower, reacting
    d.text((60, H - 110), "PCMedicalist · baseline.click", fill=amber,
           font=_font(False))
    img.save(out_path)
    return out_path


def make_reaction_frame(subject: str, out_path: Path) -> Path:
    """BEAT 3 — RetroByte LARGE + sign-off + CTA (the "wow" closer)."""
    from PIL import Image, ImageDraw
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (8, 10, 28))
    d = ImageDraw.Draw(img)
    amber = (255, 176, 0); green = (120, 255, 140)
    d.text((60, 140), "WAIT... IS THIS REAL?!", fill=green, font=_font(True))
    _paste_char(img, scale=0.70, cy_frac=0.42)  # big RetroByte center
    d.text((60, H - 260), "Discover 90s tech with RetroByte", fill=amber,
           font=_font(False))
    d.text((60, H - 200), "on the baseLINE Twitch extension -> baseline.click",
           fill=amber, font=_font(False))
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    img.save(out_path)
    return out_path


def generate_t2v_clip(narration: str, subject: str, out_path: Path) -> str | None:
    """Generate REAL motion video via the sovereign T2VZ wrapper (no pan/still).

    The narration becomes the video prompt so RetroByte + the gadget are
    generated as genuine diffusion motion. Returns the clip path or None.
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
        r = subprocess.run(
            [str(venv_py), str(t2vz), "--prompt", prompt, "--out", str(base),
             "--steps", "25", "--frames", "8", "--size", "320*576", "--fps", "8"],
            capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not base.exists():
            print(f"[discovery-gen] t2vz fail: {(r.stderr or r.stdout)[-400:]}",
                  file=sys.stderr)
            return None
        return str(base)
    except Exception as e:
        print(f"[discovery-gen] t2vz exc: {e}", file=sys.stderr)
        return None


def brand_overlay(clip_path: Path, out_path: Path) -> str | None:
    """Composite the real RetroByte brand mascot over the T2VZ motion clip.

    T2VZ cannot hold a consistent character (recipe gotcha: generates generic
    objects). We overlay the actual RetroByte PNG so the video is always on-brand,
    while the underlying clip provides the gadget motion. Mascot sits lower-left
    with a subtle scale pulse for life.
    """
    ffmpeg = shutil.which("ffmpeg")
    char = CHARACTER_IMG
    if not ffmpeg or not char.exists():
        return None
    # scale mascot to ~38% width, anchor bottom-left with 4% margin
    vf = (
        f"movie='{char.as_posix()}'[m];"
        f"[0:v][m]overlay=W*0.04:H-h-(H*0.04):"
        f"shortest=1:"
        f"eval=init:"
        f"format=auto"
    )
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(clip_path), "-vf", vf,
             "-c:a", "copy", str(out_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if r.returncode == 0 and out_path.exists():
            return str(out_path)
        # fallback: simple overlay without movie filter
        vf2 = f"overlay=W*0.04:H-h-(H*0.04)"
        r2 = subprocess.run(
            [ffmpeg, "-y", "-i", str(clip_path), "-i", str(char),
             "-filter_complex", vf2, "-c:a", "copy", str(out_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if r2.returncode == 0 and out_path.exists():
            return str(out_path)
    except Exception as e:
        print(f"[discovery-gen] brand overlay exc: {e}", file=sys.stderr)
    return None


def mux_audio_cc(clip_path: Path, audio_path: Path, script: str, out_path: Path) -> str | None:
    """Add narrated audio + burned captions to the T2VZ motion clip via ffmpeg.

    NOTE (recipe gotcha #6): mux_reel expects an IMAGE input, not an existing MP4,
    so it fails ('Option loop not found'). We mux audio with plain ffmpeg and burn
    CC in a separate pass.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    # 1) Mux narration audio under the silent T2VZ clip (stream copy, no re-encode).
    tmp = out_path.with_name(out_path.stem + "_mixed.mp4")
    r1 = subprocess.run(
        [ffmpeg, "-y", "-i", str(clip_path), "-i", str(audio_path),
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest", str(tmp)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if r1.returncode != 0 or not tmp.exists():
        # fallback: re-encode audio if stream copy failed
        r1 = subprocess.run(
            [ffmpeg, "-y", "-i", str(clip_path), "-i", str(audio_path),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-c:a", "aac",
             "-shortest", str(tmp)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if r1.returncode != 0 or not tmp.exists():
        print(f"[discovery-gen] audio mux fail: {(r1.stderr or b'')[-300:].decode(errors='ignore')}",
              file=sys.stderr)
        return None
    # 2) Burn the narration as captions (ass) so it reads like a real video.
    ass = out_path.with_name(out_path.stem + ".ass")
    try:
        _write_ass(script, ass)
        r2 = subprocess.run(
            [ffmpeg, "-y", "-i", str(tmp), "-vf", f"subtitles={ass}",
             "-c:a", "copy", str(out_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if r2.returncode == 0 and out_path.exists():
            return str(out_path)
    except Exception as e:
        print(f"[discovery-gen] CC burn skip: {e}", file=sys.stderr)
    # If CC burn fails, return the audio-mixed clip (still narrated).
    if tmp.exists():
        shutil.move(str(tmp), str(out_path))
        return str(out_path)
    return None


def _write_ass(text: str, path: Path):
    """Write a simple centered ASS subtitle for the narration."""
    safe = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    path.write_text(
        "[Script Info]\nScriptType: v4.00\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "BackColour, Bold, Alignment, MarginL, MarginR, MarginV\n"
        "Style: Default,Arial,28,&H00FFFFFF,&H80000000,-1,2,40,40,60\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
        f"Dialogue: 0,0:00:00.00,9:59:59.99,Default,{safe}\n",
        encoding="utf-8")


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

    make_hook_frame(subject, hook, f_hook)
    make_artifact_frame(subject, f_art)
    make_reaction_frame(subject, f_react)
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
        branded_path = brand_overlay(Path(video_path), branded)
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

    caption = (f"{narration}\n\n#RetroByte #90sTech #PCMedicalist\n"
               "Discover 90s tech with RetroByte on the baseLINE Twitch extension "
               "→ baseline.click")
    ready = {"video": str(dest), "video_name": dest.name, "caption": caption,
             "subject": subject, "slot": args.slot,
             "ts": _dt.datetime.utcnow().isoformat() + "Z"}
    READY_FILE.write_text(json.dumps(ready, indent=2))
    record_posted(subject, args.slot, dest.name)
    print(f"[discovery-gen] STAGED: {dest.name} | caption {len(caption)} chars",
          flush=True)


if __name__ == "__main__":
    main()
