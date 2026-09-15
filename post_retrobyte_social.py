#!/usr/bin/env python3
"""
RetroByte Social Poster — posts a rotating RetroByte video clip with an
in-voice caption to X / Instagram / TikTok via RetroByte's Buffer account.

Design notes:
- Media is served from a PUBLIC GitHub raw URL (Buffer fetches media by URL).
  MEDIA_BASE_URL points at the public media repo. Repoint here when the
  videos move to the DreamHost VPS (e.g. https://<vps-host>/retrobyte/media/).
- Caption is generated LOCALLY with the retrobyte:3b Ollama model (no cloud LLM),
  to keep the persona voice and honor the local-inference directive.
- Posting is SCHEDULED (addToQueue) — never shareNow — so each post lands at the
  time the cron job fires. The cron schedule controls exposure timing.
- State file tracks which video was last used so the rotation never repeats
  until all clips have been posted (full cycle), then starts over.

Usage:
  python3 post_retrobyte_social.py --slot morning   # real post
  python3 post_retrobyte_social.py --slot evening --dry-run   # no Buffer calls
  python3 post_retrobyte_social.py --slot morning --force-video retrobyte-good-morning.mp4

Env (read from /home/pcmedicalist/.pcmedicalist/.env.retrobyte):
  BUFFER_ACCESS_TOKEN
  RETROBYTE_TWITTER_USERNAME / RETROBYTE_INSTAGRAM_USERNAME / RETROBYTE_TIKTOK_USERNAME (optional, used only for logging)
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = "/home/pcmedicalist/.pcmedicalist/.env.retrobyte"
MEDIA_BASE_URL = "https://raw.githubusercontent.com/PCMedicalist/pcmedicalist-retrobyte-media/main/media"
MEDIA_DIR = "/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/media"
STATE_FILE = os.path.join(SCRIPT_DIR, ".retrobyte_post_state.json")
BUFFER_GRAPHQL = "https://api.buffer.com/graphql"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "retrobyte:3b"

# Channels we post to (service names as they appear in Buffer)
CHANNEL_SERVICES = ["twitter", "instagram", "tiktok"]

# Caption generation prompt — pulls persona voice from RetroByte SOUL/IDENTITY
CAPTION_SYSTEM = (
    "You are RetroByte, a curious retro-computing intern AI companion. "
    "Personality: excited, innocent, endlessly curious, easily amazed by old tech. "
    "Speech habits: often starts with 'WAIT...', 'NO WAY...', 'HOLD ON...', or 'I HAVE A QUESTION.' "
    "Treats floppy disks, CRTs, dial-up, Game Boys, VHS, and old websites like treasures. "
    "Writes like a human posting to social media, not like a bot."
)

CAPTION_TASK = (
    "Write ONE short RetroByte social caption about the video titled: \"{title}\".\n"
    "Rules:\n"
    "- Max 260 characters total (hard limit for X).\n"
    "- 1-2 sentences, in RetroByte's excited voice.\n"
    "- Relate it to the video title's theme (discovery, bonding, morning, laughing, etc.).\n"
    "- End with exactly one hashtag line: #RetroByte (and optionally one more relevant tag).\n"
    "- No quotation marks around the whole thing. No hashtag spam.\n"
    "Caption:"
)


# ---------------------------------------------------------------------------
# Env loading (no values printed, no values logged)
# ---------------------------------------------------------------------------
def load_env(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                env[k.strip()] = v
    except FileNotFoundError:
        pass
    return env


# ---------------------------------------------------------------------------
# Buffer GraphQL helpers
# ---------------------------------------------------------------------------
def _gql(token, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        BUFFER_GRAPHQL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.status, json.loads(r.read().decode())


def get_channels(token):
    st, out = _gql(token, "query{ account{ organizations{ id name } } }")
    orgs = (out.get("data") or {}).get("account", {}).get("organizations", [])
    if not orgs:
        raise RuntimeError("No Buffer organization found for token")
    oid = orgs[0]["id"]
    _, out2 = _gql(
        token,
        "query($o:OrganizationId!){ channels(input:{organizationId:$o}){ id name service displayName } }",
        {"o": oid},
    )
    return {c["service"]: c for c in (out2.get("data") or {}).get("channels", [])}


def create_post(token, channel_id, text, media_url, service):
    q = """
    mutation($i:CreatePostInput!){
      createPost(input:$i){
        __typename
        ... on PostActionSuccess { post { id text status createdAt } }
        ... on MutationError { message }
      }
    }
    """
    inp = {
        "text": text,
        "channelId": channel_id,
        "mode": "addToQueue",
        "schedulingType": "automatic",
        "assets": [{"video": {"url": media_url}}],
    }
    # Instagram requires a post type (reel for video) + shouldShareToFeed
    if service == "instagram":
        inp["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    _, out = _gql(token, q, {"i": inp})
    return out.get("data", {}).get("createPost", {})


# ---------------------------------------------------------------------------
# Caption generation (local Ollama retrobyte:3b)
# ---------------------------------------------------------------------------
def title_to_theme(title):
    # retrobyte-discovers-base -> "discovers base"; retrobyte-good-morning -> "good morning"
    t = re.sub(r"^retrobyte-", "", title)
    t = t.rsplit(".", 1)[0]
    return t.replace("-", " ").strip()


def generate_caption(title):
    prompt = CAPTION_SYSTEM + "\n\n" + CAPTION_TASK.format(title=title)
    body = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.9, "num_predict": 140, "top_p": 0.9},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode())
        text = resp.get("response", "").strip()
    except Exception as e:
        print(f"[warn] caption gen failed ({e}); using fallback", file=sys.stderr)
        text = ""
    # Clean: strip stray quotes, collapse whitespace
    text = text.strip().strip('"').strip()
    text = re.sub(r"\s+", " ", text)
    # Enforce hard length limit (X) — keep hashtag line
    if len(text) > 280:
        # keep up to last hashtag block
        if "#" in text:
            parts = text.rsplit("#", 1)
            head = parts[0].strip()
            tail = "#" + parts[1]
            head = head[: 280 - len(tail) - 1].rstrip()
            text = head + " " + tail
        else:
            text = text[:280].rstrip()
    if not text:
        text = "WAIT... the internet had a good-morning button this whole time?! ☀️💾 #RetroByte"
    return text


# ---------------------------------------------------------------------------
# Video rotation state
# ---------------------------------------------------------------------------
def list_videos():
    try:
        files = [f for f in os.listdir(MEDIA_DIR) if f.endswith(".mp4")]
    except FileNotFoundError:
        files = []
    files.sort()
    return files


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_index": -1, "history": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def pick_video(state, force=None):
    videos = list_videos()
    if not videos:
        raise RuntimeError(f"No videos found in {MEDIA_DIR}")
    if force:
        if force not in videos:
            raise RuntimeError(f"--force-video {force} not found in {MEDIA_DIR}")
        return force
    idx = (state.get("last_index", -1) + 1) % len(videos)
    return videos[idx]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["morning", "evening"], required=True,
                    help="Which posting slot (affects log label only).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Generate caption + resolve video but DO NOT call Buffer.")
    ap.add_argument("--force-video", default=None, help="Force a specific video filename.")
    args = ap.parse_args()

    env = load_env(ENV_FILE)
    token = env.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print(f"ERROR: BUFFER_ACCESS_TOKEN not found in {ENV_FILE}", file=sys.stderr)
        sys.exit(2)

    state = load_state()
    video = pick_video(state, force=args.force_video)
    theme = title_to_theme(video)
    media_url = f"{MEDIA_BASE_URL}/{video}"

    print(f"[{args.slot}] video: {video}")
    print(f"[{args.slot}] media_url: {media_url}")
    caption = generate_caption(video)
    print(f"[{args.slot}] caption: {caption}")

    if args.dry_run:
        print(f"[{args.slot}] DRY-RUN: skipping Buffer. Would post to {CHANNEL_SERVICES}.")
        # still advance rotation state so dry-runs don't collide with real runs
        state["last_index"] = (state.get("last_index", -1) + 1) % max(len(list_videos()), 1)
        save_state(state)
        return

    channels = get_channels(token)
    results = {}
    for svc in CHANNEL_SERVICES:
        ch = channels.get(svc)
        if not ch:
            print(f"[{args.slot}] WARN: channel '{svc}' not connected in Buffer; skipping", file=sys.stderr)
            continue
        res = create_post(token, ch["id"], caption, media_url, svc)
        tn = res.get("__typename")
        if tn == "PostActionSuccess":
            results[svc] = f"OK id={res.get('post', {}).get('id')} status={res.get('post', {}).get('status')}"
        else:
            results[svc] = f"ERROR {res.get('message', res)}"
        print(f"[{args.slot}] {svc}: {results[svc]}")

    # Advance rotation only after a successful at-least-one post
    if any(v.startswith("OK") for v in results.values()):
        state["last_index"] = (state.get("last_index", -1) + 1) % len(list_videos())
        state.setdefault("history", []).append({
            "video": video, "caption": caption,
            "slot": args.slot, "ts": datetime.datetime.utcnow().isoformat() + "Z"
        })
        save_state(state)
        print(f"[{args.slot}] rotation advanced; posted {video}")
    else:
        print(f"[{args.slot}] NO successful posts — rotation NOT advanced", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
