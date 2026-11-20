#!/usr/bin/env python3
"""Flush the stranded RetroByte discovery backlog to Buffer.

2026-11-17..19 six narrated videos were committed locally but their pushes were
blocked by the dual-account governance pre-push hook (author was McIntoshiS, the
repo feeds github.com/PCMedicalist/*). The heal step then found the raw URLs 404
and aborted, so the episodes were never consumed and RetroByte went silent.

This replays the EXACT validated manifests captured in audit/<stamp>/generation.json
(the same manifest the generator stages as _discovery_ready.json) through the
poster's own posting path, so format validation, caption clamping, dedup and
state recording are identical to a normal cycle.

Usage:
  python3 retrobyte_flush_backlog.py --dry-run          # validate + list, no posts
  python3 retrobyte_flush_backlog.py --slot morning     # post all stranded, oldest first
"""
import argparse
import datetime
import glob
import json
import os
import sys

import post_retrobyte_social as poster

# The stranded window: commits whose pushes were blocked. Newest is the one the
# heal step currently keeps aborting on; flush oldest-first so the queue drains
# in chronological order.
BACKLOG = [
    "2026-11-17T111106Z-d0-the-cassette-single",
    "2026-11-18T001127Z-d1-the-computer-password-notebook",
    "2026-11-18T111144Z-d0-the-answering-machine-outgoing-messa",
    "2026-11-19T001105Z-d1-the-desktop-computer-speaker",
    "2026-11-19T111123Z-d0-the-radio-cassette-player",
    "2026-11-20T001142Z-d1-the-scanner-glass-bed",
]


def load_manifest(stamp):
    path = os.path.join(poster.SCRIPT_DIR, "audit", stamp, "generation.json")
    with open(path) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["morning", "evening"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None,
                    help="Flush a single audit stamp instead of the backlog.")
    args = ap.parse_args()

    stamps = [args.only] if args.only else BACKLOG
    env = poster.load_env(poster.ENV_FILE)
    token = env.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print("ERROR: BUFFER_ACCESS_TOKEN missing", file=sys.stderr)
        return 2

    channels = poster.get_channels(token)
    state = poster.load_state()
    posted = set(state.get("posted_episodes", []))
    posted.update(item.get("episode", "") for item in state.get("history", []))
    used_videos = {str(item.get("video", "")) for item in state.get("history", [])}
    used_videos.update(state.get("reserved_videos", []))

    print(f"[flush] channels: {sorted(channels)}")
    print(f"[flush] already posted episodes: {len(posted)}")
    print(f"[flush] already used videos: {len(used_videos)}")
    print()

    failures = []
    for stamp in stamps:
        m = load_manifest(stamp)
        subject = m.get("subject", "?")
        video_name = m.get("video_name")
        caption = m.get("caption", "")
        video_path = m.get("video")
        print(f"=== {stamp}")
        print(f"    subject: {subject}")
        print(f"    video:   {video_name}")
        print(f"    caption: {len(caption)} chars")

        if not os.path.isfile(video_path or ""):
            print("    SKIP: video file missing on disk")
            failures.append((stamp, "video missing"))
            continue
        if video_name in used_videos:
            print("    SKIP: video already used (dedup)")
            continue
        if subject in posted:
            print("    SKIP: episode already posted (dedup)")
            continue

        if args.dry_run:
            print("    DRY-RUN: would validate + post")
            print()
            continue

        # Same gate the poster applies to a staged ready file.
        try:
            poster.validate_publish_manifest(m)
        except Exception as e:
            print(f"    SKIP: format validation failed: {e}")
            failures.append((stamp, f"validation: {e}"))
            continue

        media_url = f"{poster.MEDIA_BASE_URL}/{video_name}"
        results = {}
        for svc in poster.CHANNEL_SERVICES:
            ch = channels.get(svc)
            if not ch:
                print(f"    WARN: channel '{svc}' not connected; skipping")
                continue
            _cap = caption
            if svc == "twitter" and len(caption) > 260:
                _cap = caption[:257].rsplit(None, 1)[0] + "…"
            if svc == "twitter":
                _cap = _cap.replace("→", "->")
            res = poster.create_post(token, ch["id"], _cap, media_url, svc)
            tn = res.get("__typename")
            if tn != "PostActionSuccess" and svc == "twitter" \
                    and "280" in str(res.get("message", "")):
                _cap = _cap[:197].rsplit(None, 1)[0] + "…"
                res = poster.create_post(token, ch["id"], _cap, media_url, svc)
                tn = res.get("__typename")
            results[svc] = (
                f"OK id={res.get('post', {}).get('id')}"
                if tn == "PostActionSuccess"
                else f"ERROR {res.get('message', res)}")
            print(f"    {svc}: {results[svc]}")

        if any(v.startswith("OK") for v in results.values()):
            poster.record_discovery_post(state, subject, video_name, caption,
                                         args.slot, results)
            print("    RECORDED")
        else:
            print("    NO successful posts — leaving for retry")
            failures.append((stamp, "no successful posts"))
        print()

    if failures:
        print("FAILURES:")
        for stamp, why in failures:
            print(f"  {stamp}: {why}")
        return 1
    print("flush complete: no failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
