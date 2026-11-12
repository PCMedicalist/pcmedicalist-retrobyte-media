#!/usr/bin/env python3
"""Populate the RetroByte subject-image cache from Wikimedia Commons.

Uses the Commons search API to find a real image per subject (guessed filenames
are unreliable — most do not exist), verifies the download, and writes the
manifest.json that fetch_artifact() reads. The scheduled pipeline never
downloads imagery at generation time; this runs offline/batch only.

Usage:
  python3 populate_subject_cache.py            # search + download all missing
  python3 populate_subject_cache.py --dry-run  # report candidates only
  python3 populate_subject_cache.py --subject "The Zip Drive"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path("/media/pcmedicalist/PCM/.pcmedicalist/image_cache/retrobyte-artifacts")
MANIFEST = CACHE / "manifest.json"
UA = "PCMedicalist-RetroByte/1.0 (sovereign local pipeline; contact: info@pcmedicalist.com)"

# Preferred search terms per subject (Commons search queries). Ordered; the
# first candidate that resolves to a raster image is used.
SUBJECT_QUERIES: dict[str, list[str]] = {
    "the palmpilot": ["PalmPilot 5000", "Palm Pilot PDA"],
    "the cd-rom encyclopedia": ["Encarta CD-ROM", "encyclopedia CD-ROM"],
    "the cd burner": ["CD burner drive", "optical disc drive"],
    "the windows 95 start menu": ["Windows 95 desktop", "Windows 95"],
    "the computer screensaver": ["screensaver computer", "flying windows screensaver"],
    "the pc turbo button": ["turbo button PC", "computer turbo button"],
    "the vga cable": ["VGA connector", "DE-15 connector"],
    "the parallel port": ["parallel port connector", "DB-25 connector"],
    "the ps/2 connector": ["PS/2 connector", "PS/2 port"],
    "the beige computer keyboard": ["IBM Model M keyboard", "beige computer keyboard"],
    "the computer tower power switch": ["computer power button", "PC case power switch"],
    "the infrared tv remote": ["television remote control", "infrared remote"],
    "the portable dvd player": ["portable DVD player", "DVD player portable"],
    "the playstation memory card": ["PlayStation memory card", "PS1 memory card"],
    "the computer game demo disc": ["demo disc PC game", "PC game CD-ROM"],
    "the shareware registration card": ["shareware registration", "software registration card"],
    "the aol installation disc": ["AOL CD", "AOL installation disc"],
    "the computer startup beep": ["PC speaker", "computer internal speaker"],
    "the trackpoint keyboard nub": ["TrackPoint", "pointing stick keyboard"],
    "the digital voice recorder": ["digital voice recorder", "dictaphone"],
    "the digital photo memory card": ["SD card", "memory card digital camera"],
    "the film scanner": ["film scanner", "negative scanner"],
    "the computer microphone port": ["microphone jack", "3.5mm audio jack"],
    "the usb flash drive": ["USB flash drive", "USB thumb drive"],
    "the handheld gps receiver": ["handheld GPS", "GPS receiver"],
    "the electronic dictionary": ["electronic dictionary", "dictionary electronic"],
    "the computer game strategy guide": ["strategy guide book", "video game guide"],
    "the rotary dial telephone": ["rotary telephone", "rotary phone"],
    "the dot matrix printer": ["dot matrix printer", "daisy wheel printer"],
    "the answering machine": ["answering machine", "telephone answering machine"],
    "the zip drive": ["Iomega Zip drive", "Zip disk drive"],
    "the portable cd player": ["portable CD player", "Discman"],
    "the fax machine": ["fax machine", "facsimile machine"],
    "the television channel knob": ["television channel knob", "TV tuner knob"],
    "the video game arcade stick": ["arcade stick", "joystick arcade"],
    "the scanner glass bed": ["flatbed scanner", "image scanner"],
    "the radio cassette player": ["boombox", "radio cassette player"],
    "the slide rule": ["slide rule", "slide rule calculator"],
    "the computer fan grill": ["computer fan grill", "PC case fan"],
    "the audio mixing board": ["audio mixing console", "mixing desk"],
    "the 3d glasses with red and blue lenses": ["anaglyph 3D glasses", "3D glasses red blue"],
    "the desktop computer speaker": ["computer speakers", "multimedia speakers"],
    "the computer microphone": ["computer microphone", "desktop microphone"],
    "the computer lab mouse ball": ["trackball mouse", "mouse ball"],
    "the computer tower": ["computer tower case", "PC case"],
    "the crt television": ["CRT television", "CRT TV"],
    "the rabbit-ear antenna": ["rabbit ear antenna", "television antenna"],
    "the old computer joystick": ["computer joystick", "joystick controller"],
    "the tamagotchi": ["Tamagotchi", "Tamagotchi virtual pet"],
    "the digital alarm clock": ["digital alarm clock", "digital clock"],
    "the pocket calculator": ["pocket calculator", "electronic calculator"],
    "the scsi scanner": ["SCSI scanner", "SCSI controller"],
    "the computer speakers": ["computer speakers", "multimedia speakers"],
    "the ethernet hub": ["Ethernet hub", "network hub"],
    "the overhead projector": ["overhead projector", "projector overhead"],
    "the slide projector": ["slide projector", "carousel projector"],
    "the polaroid camera": ["Polaroid SX-70", "Polaroid camera"],
    "the disposable camera": ["disposable camera", "single-use camera"],
    "the compact disc": ["compact disc", "CD audio"],
    "the dial-up modem": ["dial-up modem", "USRobotics modem"],
    "the beeper": ["pager beeper", "Motorola pager"],
    "the walkman": ["Sony Walkman", "Walkman TPS-L2"],
    "the game boy": ["Game Boy", "Nintendo Game Boy"],
    "the floppy disk": ["floppy disk", "3.5 inch floppy"],
    "the minidisc": ["MiniDisc", "Sony MiniDisc"],
    "the laserdisc": ["LaserDisc", "LaserDisc player"],
    "the cassette tape": ["compact cassette", "audio cassette"],
    "the pager": ["Motorola pager", "beeper pager"],
    "the vhs tape": ["VHS tape", "VHS cassette"],
    "the camcorder": ["camcorder", "video camcorder"],
}

# Subjects already verified to resolve (skip search, fetch directly).
KNOWN_FILES: dict[str, str] = {}


def search(term: str) -> list[dict]:
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {term}", "gsrnamespace": "6",
        "gsrlimit": "8", "prop": "imageinfo",
        "iiprop": "url|sha1|size|mime", "iiurlwidth": "1200"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    out = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = page.get("imageinfo")
        if info:
            out.append({"title": page.get("title"), **info[0]})
    return out


def best_candidate(cands: list[dict]) -> dict | None:
    """Pick a sensible raster image: photo-ish size, not a diagram/icon/logo."""
    good = [c for c in cands
            if c.get("mime") in ("image/jpeg", "image/png")
            and (c.get("width") or 0) >= 500 and (c.get("height") or 0) >= 500]
    if not good:
        return None
    # prefer a balanced aspect ratio and mid size (avoid huge panoramas)
    def score(c):
        w, h = c.get("width") or 0, c.get("height") or 0
        ratio = min(w, h) / max(w, h) if max(w, h) else 0
        return ratio
    return sorted(good, key=score, reverse=True)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--subject", default=None)
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = {"subjects": {}}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text())
        except ValueError:
            manifest = {"subjects": {}}
    subjects = manifest.setdefault("subjects", {})

    keys = [args.subject] if args.subject else sorted(SUBJECT_QUERIES)
    ok = fail = skip = 0
    for subject in keys:
        key = subject.lower().strip()
        existing = subjects.get(key)
        if existing and existing.get("sha256"):
            path = CACHE / str(existing.get("file", ""))
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == existing["sha256"]:
                skip += 1
                print(f"  SKIP  {subject}")
                continue

        chosen = None
        for term in SUBJECT_QUERIES.get(subject, [subject]):
            try:
                chosen = best_candidate(search(term))
            except Exception as exc:
                print(f"  ERR   search '{term}': {type(exc).__name__}")
                continue
            if chosen:
                break
        if not chosen:
            fail += 1
            print(f"  FAIL  {subject}: no suitable Commons image")
            continue

        src = (chosen.get("thumburl") or chosen.get("url") or "")
        if not src:
            fail += 1
            print(f"  FAIL  {subject}: no url")
            continue
        suffix = Path(src.split("?")[0]).suffix.lower() or ".jpg"
        name = f"{key.replace(' ', '_').replace('/', '_')}{suffix}"
        if args.dry_run:
            print(f"  WOULD {subject}: {chosen.get('title')} -> {name} "
                  f"({chosen.get('width')}x{chosen.get('height')})")
            continue
        try:
            req = urllib.request.Request(src, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=45).read()
        except Exception as exc:
            fail += 1
            print(f"  FAIL  {subject}: download {type(exc).__name__}: {exc}")
            continue
        if len(data) < 8192:
            fail += 1
            print(f"  FAIL  {subject}: too small ({len(data)} bytes)")
            continue
        (CACHE / name).write_bytes(data)
        subjects[key] = {"file": name, "source": chosen.get("title"),
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "width": chosen.get("width"), "height": chosen.get("height")}
        ok += 1
        print(f"  OK    {subject}: {name} ({len(data)} bytes)")
        time.sleep(0.3)

    if not args.dry_run:
        MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"\nsummary: ok={ok} fail={fail} skip={skip}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
