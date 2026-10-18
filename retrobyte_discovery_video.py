#!/usr/bin/env python3
"""RetroByte scheduled video: local diffusion still + animated avatar + Fenrir.

The background stays fixed. Grounded narration uses the topic catalog; timed
captions and the supplied keyed avatar are composed into a 1080x1920 video.
--test-render exercises the production renderer without staging or state changes.
"""
from __future__ import annotations

import argparse
import atexit
import datetime as _dt
import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from retrobyte_video_format import FORMAT, validate_render

# --- Paths ---------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PCM_ROOT = Path("/media/pcmedicalist/PCM")
PCMEDICALIST_DATA = PCM_ROOT / ".pcmedicalist"
RETROBYTE_ROOT = PCM_ROOT / "pcmedicalist-retrobyte"
BRAND_DIR = RETROBYTE_ROOT / "images" / "brand"
if not os.path.ismount(PCM_ROOT):
    raise RuntimeError(f"PCM drive is not mounted at {PCM_ROOT}; refusing home-disk fallback.")
if not RETROBYTE_ROOT.is_dir():
    raise RuntimeError(f"Canonical RetroByte assets are missing from the PCM drive: {RETROBYTE_ROOT}")
# Keep intermediate frames/audio/video on PCM. The publishable MP4 alone is
# copied into this repository's media/ folder for the configured raw GitHub URL.
OUTBOX = PCMEDICALIST_DATA / "cache" / "retrobyte-render"
OUTBOX.mkdir(parents=True, exist_ok=True)
READY_FILE = HERE / "_discovery_ready.json"
IMG_CACHE = PCMEDICALIST_DATA / "image_cache" / "retrobyte-artifacts"
IMG_CACHE.mkdir(parents=True, exist_ok=True)

# --- Historical subject archive and fresh topic queue ---------------------
SERIES_FILES = [
    RETROBYTE_ROOT / "RETROBYTE-OLD-TECH-DISCOVERY.md",
    HERE / "RETROBYTE OLD TECH DISCOVERY.md",  # sync copy fallback
]
FRESH_TOPICS_FILE = HERE / "RETROBYTE FRESH DISCOVERY TOPICS.md"
STATE_FILE = HERE / "_discovery_state.json"
POSTED_REGISTRY = HERE / "_posted_registry.json"
KOKORO_ROOT = Path("/media/pcmedicalist/PCM/.pcmedicalist/tts/kokoro")
KOKORO_SITE_PACKAGES = KOKORO_ROOT / "python-packages"
KOKORO_MODEL = KOKORO_ROOT / "v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES = KOKORO_ROOT / "v1.0/voices-v1.0.bin"
RETROBYTE_TTS_VOICE = FORMAT["voice"]["id"]


def _topic_key(value: str) -> str:
    """Normalize title-only and legacy title|date keys for lifetime dedup."""
    value = str(value or "").split("|", 1)[0]
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    value = re.sub(r"^the\s+", "", value)
    return " ".join(value.split())


def _read_json_history(path: Path) -> dict:
    if not path.exists():
        return {}
    # A corrupt existing ledger must stop generation; treating it as empty
    # would silently re-use subjects after a full-cycle restart.
    return json.loads(path.read_text(encoding="utf-8"))


def load_posted() -> set:
    """Return all subjects seen in generator and publisher ledgers."""
    posted = set()
    registry = _read_json_history(POSTED_REGISTRY)
    posted.update(_topic_key(x) for x in registry.get("titles", []))
    for legacy in SERIES_FILES:
        if legacy.exists():
            text = legacy.read_text(encoding="utf-8", errors="ignore")
            posted.update(_topic_key(m.group(1)) for m in re.finditer(
                r"^#+\s*\d{1,3}\s*[—-]\s*(.+)$", text, re.M))
    publisher_state = _read_json_history(HERE / ".retrobyte_post_state.json")
    for value in publisher_state.get("posted_episodes", []):
        posted.add(_topic_key(value))
    for item in publisher_state.get("history", []):
        posted.add(_topic_key(item.get("episode", "")))
    return {key for key in posted if key}


def record_posted(title: str, slot: int, fname: str):
    """Persist a generated episode in a lifetime dedup registry."""
    data = {"titles": [], "cycle": 0}
    if POSTED_REGISTRY.exists():
        try:
            data = json.loads(POSTED_REGISTRY.read_text())
        except Exception as exc:
            raise RuntimeError(f"Cannot read topic registry {POSTED_REGISTRY}: {exc}")
    titles = set(data.get("titles", []))
    titles.add(title)
    data["titles"] = sorted(titles)
    data["last"] = {"title": title, "slot": slot, "file": fname,
                    "ts": _dt.datetime.utcnow().isoformat() + "Z"}
    POSTED_REGISTRY.write_text(json.dumps(data, indent=2))


# --- Optional locally cached artifact imagery -----------------------------
# The scheduled pipeline never downloads imagery. A subject photo is shown
# only when an operator or local image workflow has placed it in IMG_CACHE.
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
    """Return fresh episode topics with their distinct narration angles."""
    if not FRESH_TOPICS_FILE.exists():
        raise RuntimeError(f"Fresh topic catalog is missing: {FRESH_TOPICS_FILE}")
    text = FRESH_TOPICS_FILE.read_text(encoding="utf-8")
    eps = []
    blocks = re.split(r"(?=^##\s+\d+\s*[—-]\s*)", text, flags=re.M)
    for block in blocks:
        m = re.match(r"##\s+\d+\s*[—-]\s*(.+)", block.strip())
        if not m:
            continue
        title = m.group(1).strip()
        angle = re.search(r"^Angle:\s*(.+)$", block, re.M)
        eps.append((title, angle.group(1).strip() if angle else title))
    seen = set(); out = []
    for t, s in eps:
        if _topic_key(t) in seen:
            continue
        seen.add(_topic_key(t))
        out.append((t, s))
    if not out:
        raise RuntimeError(f"No topics found in fresh topic catalog: {FRESH_TOPICS_FILE}")
    return out


def next_episode(slot: int, force: str | None = None, persist: bool = True, allow_used: bool = False):
    eps = load_episodes()
    n = len(eps)
    st = _read_json_history(STATE_FILE)
    last = int(st.get("last_index", -1))
    # The old 25-subject list used this same state file. Its numeric index has
    # no meaning in the new catalog, so start the fresh queue at entry one.
    if st.get("episode") and _topic_key(st.get("episode")) not in {
            _topic_key(title) for title, _angle in eps}:
        last = -1
    posted = load_posted()
    if force:
        idx = next((i for i, (t, _s) in enumerate(eps)
                    if _topic_key(t) == _topic_key(force)), None)
        if idx is None:
            raise RuntimeError(f"Forced topic is not in the fresh catalog: {force}")
        if _topic_key(eps[idx][0]) in posted and not allow_used:
            raise RuntimeError(f"Topic was already used and cannot be repeated: {eps[idx][0]}")
    else:
        # If the previous attempt failed before staging, retry that same
        # unposted subject. A successful staged episode is already in registry.
        pending = _topic_key(st.get("episode", ""))
        pending_idx = next((i for i, (title, _angle) in enumerate(eps)
                            if _topic_key(title) == pending), None)
        if pending_idx is not None and pending not in posted:
            idx = pending_idx
        else:
            # Walk forward without wrapping into a recycled cycle.
            for step in range(1, n + 1):
                cand = (last + step) % n
                if _topic_key(eps[cand][0]) not in posted:
                    idx = cand
                    break
            else:
                raise RuntimeError("No unseen topics remain; add new topics to the fresh catalog.")
    title, slug = eps[idx]
    if persist:
        STATE_FILE.write_text(json.dumps({"last_index": idx, "episode": title,
                                          "ts": _dt.datetime.utcnow().isoformat() + "Z"}))
    return title, slug, idx


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "retrobyte:3b"

_FACT_ANCHORS = {
    "THE DOT-MATRIX PRINTER": (
        "Electrically controlled pins in its print head strike an ink ribbon "
        "against paper to form characters from dots."
    ),
}


# --- Voice narration via local retrobyte:3b ------------------------------
def _recent_captions(limit: int = 4) -> list[str]:
    """Read a few local posted captions so Ollama can avoid recycling phrasing."""
    state_path = HERE / ".retrobyte_post_state.json"
    try:
        history = json.loads(state_path.read_text()).get("history", [])
        return [str(x.get("caption", "")).split("\n\n#", 1)[0]
                for x in history[-limit:] if x.get("caption")]
    except Exception:
        return []


def _fallback_narration(subject: str, hook: str, ep_idx: int) -> str:
    """Local-only fallback with rotating sentence shapes, not one canned line."""
    if subject.upper() == "THE DOT-MATRIX PRINTER":
        return (
            "A dot-matrix printer uses tiny electrically driven pins to strike "
            "an ink ribbon and build letters from dots. That clattering print "
            "head turns every page into a little machine show!"
        )
    patterns = (
        "I just met the {subject}, and it is wild: {hook}. RetroByte, signing off!",
        "Today's time-capsule is the {subject}. {hook} I need a minute to process this!",
        "Okay, the {subject} has my full attention. {hook} What a clever little machine!",
        "Found a {subject} from the 90s! {hook} I would absolutely try this again.",
    )
    return patterns[ep_idx % len(patterns)].format(subject=subject, hook=hook)


def narrate(subject: str, hook: str, ep_idx: int = 0) -> str:
    """Keep speech grounded in the catalog instead of allowing topic drift."""
    if _topic_key(subject) == "palmpilot":
        return ("Before smartphones, your calendar had a stylus. This is the PalmPilot: "
                "appointments and notes, right in your pocket. RetroByte wants to know: "
                "would you use one today?")
    fact = _FACT_ANCHORS.get(subject.upper(), hook).rstrip(".!?")
    return f"Meet {subject.removeprefix('The ')}. {fact}. RetroByte wants to know: would you use one today?"


def synthesize_local_voice(text: str, out_path: Path) -> dict:
    """Create narration with the approved local Kokoro voice, never cloud TTS."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg is missing"}
    if not KOKORO_MODEL.is_file() or not KOKORO_VOICES.is_file():
        return {"ok": False, "error": f"local Kokoro model files are missing under {KOKORO_ROOT}"}
    if not KOKORO_SITE_PACKAGES.is_dir():
        return {"ok": False, "error": f"local Kokoro runtime is missing under {KOKORO_SITE_PACKAGES}"}

    out_path = Path(out_path)
    wav_path = out_path.with_suffix(".wav")
    try:
        # The small neural runtime and weights live on the external PCM drive;
        # keep them out of the crowded home-disk cache and fail closed if absent.
        sys.path.insert(0, str(KOKORO_SITE_PACKAGES))
        import onnxruntime as ort
        import soundfile as sf
        from kokoro_onnx import Kokoro

        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(KOKORO_MODEL), sess_options=options,
            providers=["CPUExecutionProvider"])
        tts = Kokoro.from_session(session, str(KOKORO_VOICES))
        if RETROBYTE_TTS_VOICE not in tts.get_voices():
            return {"ok": False, "error": f"Kokoro voice {RETROBYTE_TTS_VOICE} is unavailable"}
        samples, sample_rate = tts.create(
            text, voice=RETROBYTE_TTS_VOICE, speed=1.0, lang="en-us")
        sf.write(str(wav_path), samples, sample_rate)
        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            return {"ok": False, "error": "Kokoro produced an empty WAV"}
        encoded = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path),
             "-codec:a", "libmp3lame", "-q:a", "4", str(out_path)],
            capture_output=True, text=True, timeout=90, check=False)
        if encoded.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
            return {"ok": False, "error": encoded.stderr.strip() or "Kokoro MP3 encoding failed"}
        return {"ok": True, "path": str(out_path), "voice": f"kokoro-onnx:{RETROBYTE_TTS_VOICE}"}
    except Exception as exc:
        return {"ok": False, "error": f"local Kokoro TTS failed: {exc}"}
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass


# --- Wikimedia fetch (cached) ------------------------------------------
def fetch_artifact(subject: str) -> Path | None:
    """Return a locally cached artifact photo; never fetch media over network."""
    key = subject.lower().strip()
    title = None
    for k, v in WIKIMEDIA_TITLES.items():
        if k in key or key in k:
            title = v; break
    cache = IMG_CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", key) + ".jpg")
    if cache.exists():
        return cache
    # Historical cache naming may differ in capitalization/prefix. Match an
    # already-local cached filename by normalized subject without network I/O.
    normalized = re.sub(r"[^a-z0-9]+", "", re.sub(r"^the\s+", "", key))
    for candidate in IMG_CACHE.glob("*"):
        candidate_key = re.sub(r"[^a-z0-9]+", "", candidate.stem.lower())
        if candidate_key == normalized or candidate_key.endswith(normalized):
            return candidate
    return None


# --- RetroByte character model ------------------------------------------
CHARACTER_IMG = BRAND_DIR / "retrobyte-video-model.png"

# ---- 2026-09-25: rotating cast + backgrounds (operator directive) ----------
# Foreground: 18 agent sprites (RGBA, transparent, ~330-363px) in brand/agents/.
# Background: 35 FLUX2 room backdrops (1024x576) in brand/backgrounds/.
# Per-episode pick is deterministic from the episode index so the same episode
# always renders the same character/room, and consecutive episodes rotate both.
AGENTS_DIR = BRAND_DIR / "agents"
BACKGROUNDS_DIR = BRAND_DIR / "backgrounds"


def _rotating_asset(directory: Path, idx: int) -> Path:
    """Pick a deterministic PCM-drive asset; missing brand assets stop renders."""
    files = sorted(p for p in directory.glob("*.png"))
    if not files:
        raise FileNotFoundError(f"Required RetroByte visual assets missing: {directory}")
    return files[idx % len(files)]


def episode_character(ep_idx: int) -> Path:
    return Path(os.environ.get("RETROBYTE_VIDEO_AVATAR",
        FORMAT["avatar"]["path"]))


def episode_background(ep_idx: int) -> Path:
    return _rotating_asset(BACKGROUNDS_DIR, ep_idx)


def _load_char_rgba(ep_idx: int) -> "Image.Image":
    """Use a keyed frame of the approved video for still-card previews."""
    import io
    from PIL import Image
    sys.path.insert(0, "/home/pcmedicalist/pcmedicalist/pcmedicalist-content-os")
    from generators.video_avatar import prepare_avatar
    keyed, bbox = prepare_avatar(episode_character(ep_idx))
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(keyed), "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True, timeout=30)
    return Image.open(io.BytesIO(frame.stdout)).convert("RGBA").crop(bbox)


def _paste_char(img, scale=0.55, cy_frac=0.60, ep_idx: int | None = None,
                bottom_center: bool = False, lift_px: int = 0):
    """Paste the agent sprite with ALPHA (replaces the old RGB square paste).

    ep_idx=None uses the canonical PCM-drive character model.
    bottom_center=True anchors the sprite centered at the bottom band.
    lift_px raises the sprite off the bottom edge (keeps CTA visible).
    """
    char = _load_char_rgba(ep_idx or 0)
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
    """Build the card base from a required PCM-drive room image and scrim."""
    from PIL import Image, ImageDraw
    W, H = img_size
    bg_path = episode_background(ep_idx)
    if not bg_path.is_file():
        raise FileNotFoundError(f"Required RetroByte room background missing: {bg_path}")
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
        _paste_char_lifted(base, scale=0.52, ep_idx=ep_idx, lift=_lift)
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
    # If this exact artifact is already cached locally, show it as the episode's
    # hero visual. No web request is made from the scheduled generation job.
    artifact = fetch_artifact(subject)
    if artifact and artifact.exists():
        try:
            photo = Image.open(artifact).convert("RGB")
            photo.thumbnail((860, 620), Image.Resampling.LANCZOS)
            frame = Image.new("RGB", (900, 660), (5, 7, 8))
            frame.paste(photo, ((900 - photo.width) // 2,
                                (660 - photo.height) // 2))
            base.paste(frame, ((W - 900) // 2, 840))
        except Exception as e:
            print(f"[discovery-gen] cached artifact image skipped: {e}",
                  file=sys.stderr)
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
        _paste_char_lifted(base, scale=0.52, ep_idx=ep_idx, lift=190)
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
        _paste_char_lifted(base, scale=0.52, ep_idx=ep_idx, lift=190)
    d.rectangle([30, 30, W - 30, H - 30], outline=amber, width=6)
    d.text((60, H - 110), "PCMedicalist · baseline.click", fill=amber, font=fsmall)
    base.save(out_path)
    return out_path


def _probe_ok_for_reels(path: Path) -> bool:
    """Read named ffprobe fields; CSV stream/format ordering is not stable."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=duration:stream=avg_frame_rate,height",
             "-of", "json", str(path)], capture_output=True, text=True,
            check=True, timeout=30)
        data = json.loads(r.stdout)
        stream = data["streams"][0]
        duration = float(data["format"]["duration"])
        num, den = stream["avg_frame_rate"].split("/")
        fps = float(num) / float(den)
        height = int(stream["height"])
        print(f"[discovery-gen] probe: dur={duration:.2f}s fps={fps:.2f} h={height}", flush=True)
        return duration >= 3 and fps >= 23 and height >= 360
    except Exception as exc:
        print(f"[discovery-gen] probe failed: {exc}", file=sys.stderr)
        return False


def generate_t2v_clip(narration: str, subject: str, out_path: Path) -> str | None:
    """Generate REAL motion video via the sovereign T2VZ wrapper (no pan/still).

    Generate gadget motion with space for the separately keyed approved
    avatar. Returns the clip path or None.

    Output must meet Instagram Reels + TikTok minimums (>=3s, >=23fps,
    >=360px height) or we fall back to the still-frame montage path, which
    posts reliably to all three channels. The old --frames 8 --fps 8 config
    produced a 1s clip that Reels/TikTok both rejected.
    """
    from PIL import Image
    # Generate only the gadget; the approved video supplies the mascot.
    prompt = (
        f"retro 1990s nostalgia animation, product close-up of an old {subject.lower()}, "
        f"the gadget is the only subject, no characters, no robots, no people, "
        f"gadget on the right, clear lower-left space for a separate mascot overlay, "
        f"warm amber lighting, "
        f"vaporwave palette, "
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
        log_path = base.with_suffix(".log")
        print(f"[discovery-gen] motion generation log: {log_path}", flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            r = subprocess.run(
                [str(venv_py), str(t2vz), "--prompt", prompt, "--out", str(base),
                 "--steps", "25", "--frames", "96", "--size", "576*576", "--fps", "30"],
                stdout=log, stderr=subprocess.STDOUT, text=True, timeout=1200)
        if r.returncode != 0 or not base.exists():
            print(f"[discovery-gen] t2vz fail: {log_path.read_text()[-2400:]}",
                  file=sys.stderr)
            return None
        # Validate platform minimums — fall back to montage if T2VZ still came
        # out too short/low-res (T2VZ is an 11GB VRAM squeeze on the 2060, so the
        # recipe can produce marginal clips; montage is the safe posting path).
        if not _probe_ok_for_reels(base):
            print("[discovery-gen] T2VZ clip below platform minimums — "
                  "rejecting output; production will stop", file=sys.stderr)
            return None
        return str(base)
    except Exception as e:
        print(f"[discovery-gen] t2vz exc: {e}", file=sys.stderr)
        return None


def brand_overlay(clip_path: Path, out_path: Path,
                  ep_idx: int | None = None) -> str | None:
    """Loop the approved RetroByte MP4 in its own lower-left stage."""
    sys.path.insert(0, "/home/pcmedicalist/pcmedicalist/pcmedicalist-content-os")
    from generators.video_avatar import overlay_avatar
    avatar = Path(os.environ.get(
        "RETROBYTE_VIDEO_AVATAR",
        FORMAT["avatar"]["path"]))
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(clip_path)],
            capture_output=True, text=True, check=True)
        stream = json.loads(probe.stdout)["streams"][0]
        w, h = stream["width"], stream["height"]
        box = (int(w * 0.05), int(h * 0.48), int(w * 0.40), int(h * 0.38))
        overlay_avatar(clip_path, avatar, out_path, box)
        return str(out_path)
    except Exception as exc:
        print(f"[discovery-gen] video avatar overlay failed: {exc}", file=sys.stderr)
        return None


def mux_audio_cc(clip_path: Path, audio_path: Path, script: str, out_path: Path) -> str | None:
    """Loop generated motion to the complete narration; never cut speech."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(audio_path)], capture_output=True, text=True, check=True)
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(clip_path),
             "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-t", str(duration),
             "-movflags", "+faststart", str(out_path)],
            capture_output=True, text=True, timeout=180, check=True)
        return str(out_path)
    except Exception as exc:
        print(f"[discovery-gen] narration mux failed: {exc}", file=sys.stderr)
        return None


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

    subject, hook, ep_idx = next_episode(
        args.slot, args.force_subject,
        persist=not (args.dry_run or args.test_render), allow_used=args.test_render)
    print(f"[discovery-gen] episode#{ep_idx + 1} subject={subject} slot={args.slot}",
          flush=True)

    _subject_slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:36]
    _run_stamp = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")
    stamp = f"{_run_stamp}-d{args.slot}-{_subject_slug}"
    run_dir = OUTBOX / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    # Failed runs and review previews retain their evidence on PCM.
    video = run_dir / f"disc_video_{stamp}.mp4"
    narration = narrate(subject, hook, ep_idx)
    (run_dir / "narration.txt").write_text(narration, encoding="utf-8")
    print(f"[discovery-gen] narration: {narration}", flush=True)
    if args.dry_run:
        print("[discovery-gen] dry-run: stationary local diffusion scene + animated avatar + Fenrir + captions")
        return
    scene = run_dir / "diffusion-scene.png"
    log_path = run_dir / "diffusion.log"
    scene_script = HERE / "retrobyte_still_scene.py"
    print(f"[discovery-gen] generating a stationary diffusion scene; log={log_path}", flush=True)
    with log_path.open("w") as log:
        generated = subprocess.run(
            [sys.executable, str(scene_script), "--subject", subject, "--angle", hook,
             "--out", str(scene)], stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if generated.returncode or not scene.is_file():
        raise RuntimeError(f"Stationary diffusion failed: {log_path.read_text()[-2000:]}")
    mp3 = run_dir / "narration.mp3"
    print("[discovery-gen] synthesizing approved local Fenrir narration", flush=True)
    voice = synthesize_local_voice(narration, mp3)
    if not voice.get("ok"):
        raise RuntimeError(f"Local Fenrir voice failed: {voice.get('error')}")
    from retrobyte_still_video import render
    print("[discovery-gen] rendering fixed scene + animated avatar + timed captions", flush=True)
    provenance = render(scene, mp3, episode_character(ep_idx), subject, hook, narration, video)
    provenance.update({"subject": subject, "narration": narration, "voice": voice.get("voice"),
                       "avatar_source": str(episode_character(ep_idx)),
                       "avatar_sha256": hashlib.sha256(episode_character(ep_idx).read_bytes()).hexdigest(),
                       "social_post_sent": False})
    print("[discovery-gen] validating the approved RetroByte format", flush=True)
    provenance["format_validation"] = validate_render(video, provenance)
    (run_dir / "format-check.json").write_text(json.dumps(provenance["format_validation"], indent=2))
    (run_dir / "render.json").write_text(json.dumps(provenance, indent=2))
    if not _probe_ok_for_reels(video):
        raise RuntimeError("Completed video failed platform media validation")
    if args.test_render:
        print(f"[discovery-gen] REVIEW VIDEO: {video}", flush=True)
        print(f"[discovery-gen] REVIEW REPORT: {run_dir / 'render.json'}", flush=True)
        return
    video_path = str(video)

    repo_media = HERE / "media"
    repo_media.mkdir(parents=True, exist_ok=True)
    dest = repo_media / Path(video_path).name
    shutil.copy(video_path, dest)

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
             "subject": subject, "slot": args.slot, "narration": narration,
             "avatar_source": str(episode_character(ep_idx)),
             "avatar_sha256": hashlib.sha256(episode_character(ep_idx).read_bytes()).hexdigest(),
             "avatar_green_screen_removed": True,
             "scene_mode": "stationary_local_diffusion",
             "scene_sha256": provenance["scene_sha256"],
             "captions_burned": True,
             "voice": provenance["voice"],
             "format_validation": provenance["format_validation"],
             "ts": _dt.datetime.utcnow().isoformat() + "Z"}
    READY_FILE.write_text(json.dumps(ready, indent=2))
    record_posted(subject, args.slot, dest.name)
    audit_dir = HERE / "audit" / stamp
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "generation.json").write_text(json.dumps(ready, indent=2))
    for evidence in list(run_dir.glob("*.log")) + list(run_dir.glob("*.txt")) + list(run_dir.glob("*.json")):
        shutil.copy2(evidence, audit_dir / evidence.name)
    # The published MP4 now lives in this repo's media/ directory. Remove only
    # this run's temporary frames, audio, and diffusion files from PCM cache.
    shutil.rmtree(run_dir, ignore_errors=True)

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
