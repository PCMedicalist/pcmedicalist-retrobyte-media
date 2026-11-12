#!/usr/bin/env python3
"""Re-download specific subjects with better-targeted search terms.

Used to replace off-topic images found in visual QA. Updates the manifest
entry (file/source/sha256) so fetch_artifact() picks the corrected image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import populate_subject_cache as p  # noqa: E402


FIXES = {
    "the audio mixing board": ["audio mixing console close-up", "sound mixing desk"],
    "the floppy disk": ["3.5 inch floppy disk", "floppy diskette"],
    "the handheld gps receiver": ["Garmin GPS receiver", "Magellan GPS"],
    "the rotary dial telephone": ["rotary dial telephone handset", "Western Electric rotary phone"],
    "the shareware registration card": ["software box floppy disks", "shareware floppy disk label"],
    "the television channel knob": ["vintage TV channel selector", "TV rotary channel switch"],
    "the cd burner": ["CD-RW drive internal", "optical disc drive CD-RW"],
    "the computer game strategy guide": ["Prima strategy guide book", "video game strategy guide cover"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(p.MANIFEST.read_text()) if p.MANIFEST.exists() else {"subjects": {}}
    subjects = manifest.setdefault("subjects", {})
    ok = fail = 0

    for subject, terms in sorted(FIXES.items()):
        key = subject.lower().strip()
        chosen = None
        for term in terms:
            try:
                chosen = p.best_candidate(p.search(term))
            except Exception as exc:
                print(f"  ERR '{term}': {type(exc).__name__}")
                continue
            if chosen:
                break
        if not chosen:
            fail += 1
            print(f"  FAIL  {subject}: no candidate")
            continue
        src = (chosen.get("thumburl") or chosen.get("url") or "")
        if not src:
            fail += 1
            print(f"  FAIL  {subject}: no url")
            continue
        suffix = Path(src.split("?")[0]).suffix.lower() or ".jpg"
        name = f"{key.replace(' ', '_').replace('/', '_')}{suffix}"
        if args.dry_run:
            print(f"  WOULD {subject}: {chosen.get('title')} -> {name}")
            continue
        try:
            req = urllib.request.Request(src, headers={"User-Agent": p.UA})
            data = urllib.request.urlopen(req, timeout=45).read()
        except Exception as exc:
            fail += 1
            print(f"  FAIL  {subject}: {type(exc).__name__}: {exc}")
            continue
        if len(data) < 8192:
            fail += 1
            print(f"  FAIL  {subject}: too small")
            continue
        (p.CACHE / name).write_bytes(data)
        subjects[key] = {"file": name, "source": chosen.get("title"),
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "width": chosen.get("width"), "height": chosen.get("height")}
        ok += 1
        print(f"  OK    {subject}: {name} ({len(data)} bytes)")
        time.sleep(0.3)

    if not args.dry_run:
        p.MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"\nsummary: ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
