#!/usr/bin/env python3
"""Publish validated RetroByte episodes to X, Instagram and TikTok via Buffer.

Only the versioned approved format is eligible. Legacy clip rotation is disabled.
--dry-run validates the staged episode without sending a social post.
"""
import argparse
import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import fcntl
import urllib.request
from pathlib import Path
from retrobyte_video_format import validate_publish_manifest

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = "/media/pcmedicalist/PCM/.pcmedicalist/.env.retrobyte"
MEDIA_BASE_URL = "https://raw.githubusercontent.com/PCMedicalist/pcmedicalist-retrobyte-media/main/media"
# Fallback clips must come from the same published media/ tree used by
# MEDIA_BASE_URL, so every selected filename resolves at Buffer's raw URL.
MEDIA_DIR = os.path.join(SCRIPT_DIR, "media")
STATE_FILE = os.environ.get(
    "RETROBYTE_STATE_FILE",
    os.path.join(SCRIPT_DIR, ".retrobyte_post_state.json"))
BUFFER_GRAPHQL = "https://api.buffer.com/graphql"
OLLAMA_URL = os.environ.get(
    "RETROBYTE_OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = "retrobyte:3b"

# Channels we post to (service names as they appear in Buffer)
CHANNEL_SERVICES = ["twitter", "instagram", "tiktok"]

# Rotating baseLINE call-to-action pool — every caption ends with one of these
# so RetroByte posts drive viewers to the baseLINE Twitch extension + site.
# Each post advances a CTA rotation index (stored in state) so the CTA varies
# across the 9-day video loop and feels fresh on reposts.
BASELINE_CTAS = [
    "Support RetroByte live on Twitch via the baseLINE extension → baseline.click",
    "Watch RetroByte on the baseLINE Twitch extension → baseline.click",
    "Tip RetroByte in real time on Twitch — powered by baseLINE → baseline.click",
    "Go live with RetroByte + baseLINE: streamer crypto tips on Twitch → baseline.click",
    "RetroByte runs on baseLINE — get the Twitch tipping extension → baseline.click",
]

# Hashtag rotation pool — varied per post to avoid samey #RetroByte/#PCMedicalist spam
HASHTAG_POOL = [
    "#RetroByte",
    "#RetroByte #PCMedicalist",
    "#RetroByte #RetroTech",
    "#RetroByte #TechNostalgia",
    "#RetroByte #ThrowbackTech",
    "#RetroByte #BaseLINE",
]

# Caption generation prompt — pulls persona voice from RetroByte SOUL/IDENTITY
CAPTION_SYSTEM = (
    "You are RetroByte, a curious retro-computing intern AI companion. "
    "Personality: excited, innocent, endlessly curious, easily amazed by old tech. "
    "Use varied, natural openings and specific details from this video. "
    "Do not rely on repeated catchphrases or subjects already used. "
    "Writes like a human posting to social media, not like a bot."
)

CAPTION_TASK = (
    "Write ONE short RetroByte social caption about the video titled: \"{title}\".\n"
    "Rules:\n"
    "- Max 200 characters total (hard limit; a separate CTA is appended after).\n"
    "- 1-2 sentences, in RetroByte's excited voice.\n"
    "- Relate it to this video's specific subject and detail.\n"
    "- Do NOT add any hashtags or call-to-action — those are added automatically.\n"
    "- No quotation marks around the whole thing. Write like a human, not a bot.\n"
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
    # SEND IMMEDIATELY: use mode:"shareNow" (Buffer's explicit send-now mode).
    # The original addToQueue+automatic left posts stranded in RetroByte's
    # Buffer queue for 12h+ with nothing reaching the socials. shareNow pushes
    # straight to the channel. schedulingType is required by the schema, and
    # "automatic" is accepted alongside shareNow (shareNow wins -> sends now).
    inp = {
        "text": text,
        "channelId": channel_id,
        "mode": "shareNow",
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


def _caption_key(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _recent_captions(state, limit=12):
    return [str(item.get("caption", "")).split("\n\n#", 1)[0]
            for item in state.get("history", [])[-limit:]
            if item.get("caption")]


def generate_caption(theme, hashtag, cta, history=()):
    recent = list(history)
    recent_block = "\n".join(f"- {caption[:180]}" for caption in recent)
    prompt = CAPTION_SYSTEM + "\n\n" + CAPTION_TASK.format(title=theme)
    if recent_block:
        prompt += "\nRecent captions: do not repeat their wording, hooks, or structure.\n" + recent_block
    body = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.9, "num_predict": 120, "top_p": 0.9},
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
    # Clean: strip stray quotes, collapse whitespace, and remove any hashtags
    # the model may have appended (we control hashtags via HASHTAG_POOL).
    text = text.strip().strip('"').strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"#\S+", "", text).strip()  # drop model-emitted hashtags
    if not text:
        raise RuntimeError("Local caption generation returned no fresh caption; refusing to post.")
    key = _caption_key(text)
    for old in recent:
        old_key = _caption_key(old)
        if key and old_key and difflib.SequenceMatcher(None, key, old_key).ratio() >= 0.76:
            raise RuntimeError("Generated caption is too similar to a recent post; refusing to repeat it.")
    # Build final caption: lead line + hashtag + blank line + baseLINE CTA.
    # Enforce hard X limit of 280 chars on the WHOLE thing.
    base = text
    suffix = f"\n\n{hashtag}\n\n{cta}"
    if len(base) + len(suffix) > 280:
        # trim lead line, keep CTA + hashtag intact
        head_room = 280 - len(suffix)
        if head_room > 20:
            base = base[:head_room].rstrip()
        else:
            # extreme fallback: drop lead line entirely, keep hashtag + CTA
            base = ""
    if base:
        final = base + suffix
    else:
        final = hashtag + suffix
    if not final.strip():
        final = f"WAIT... the internet had a good-morning button this whole time?! ☀️💾\n\n{hashtag}\n\n{cta}"
    return final


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
            st = json.load(f)
    except FileNotFoundError:
        st = {}
    st.setdefault("last_index", -1)
    st.setdefault("cta_index", -1)
    st.setdefault("history", [])
    return st


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


LOCK_FILE = STATE_FILE + ".lock"


def reserve_video(force=None, dry_run=False):
    """Atomically pick the next rotation video AND advance the CTA + hashtag
    rotation indices, persisting everything IMMEDIATELY (before the slow caption
    generation), so concurrent runs can never select the same clip or CTA.
    Uses an exclusive flock so two near-simultaneous cron runs serialize
    cleanly and each reserve a distinct index.

    A clip is retired after one successful post and is never recycled. Existing
    history and active reservations are both treated as used content."""
    with open(LOCK_FILE, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        videos = list_videos()
        if not videos:
            raise RuntimeError(f"No videos found in {MEDIA_DIR}")
        state = load_state()
        used = {str(item.get("video", "")) for item in state.get("history", [])}
        used.update(state.get("reserved_videos", []))
        if force:
            if force not in videos:
                raise RuntimeError(f"--force-video {force} not found in {MEDIA_DIR}")
            if force in used:
                raise RuntimeError(f"Refusing to repeat previously used video: {force}")
            idx = videos.index(force)
        else:
            candidates = [v for v in videos if v not in used]
            if not candidates:
                raise RuntimeError("No unseen fallback videos remain; add fresh videos instead of recycling old posts.")
            li = state.get("last_index", -1)
            idx = videos.index(candidates[0])
            if isinstance(li, int) and 0 <= li < len(videos):
                for step in range(1, len(videos) + 1):
                    candidate_idx = (li + step) % len(videos)
                    if videos[candidate_idx] in candidates:
                        idx = candidate_idx
                        break
        video = videos[idx]
        state["last_index"] = idx
        state["last_video"] = video
        if not dry_run:
            state["reserved_videos"] = sorted(set(state.get("reserved_videos", [])) | {video})

        # Advance CTA + hashtag rotation in lock-step with the video pick.
        cta_idx = (state.get("cta_index", -1) + 1) % len(BASELINE_CTAS)
        hashtag_idx = (state.get("cta_index", -1) + 1) % len(HASHTAG_POOL)
        state["cta_index"] = cta_idx
        if not dry_run:
            save_state(state)  # reserve now, before any slow work
        cta = BASELINE_CTAS[cta_idx]
        hashtag = HASHTAG_POOL[hashtag_idx]
        return video, idx, cta, hashtag


def release_video_reservation(video):
    """Release an unpublished clip after generation or delivery fails."""
    with open(LOCK_FILE, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        state = load_state()
        state["reserved_videos"] = [v for v in state.get("reserved_videos", [])
                                    if v != video]
        save_state(state)


def record_discovery_post(state, subject, video, caption, slot, results):
    """Persist a discovery clip as used as soon as any channel accepts it."""
    posted = set(state.get("posted_episodes", []))
    posted.add(subject)
    state["posted_episodes"] = sorted(posted)
    history = state.setdefault("history", [])
    if not any(item.get("video") == video for item in history):
        history.append({"video": video, "caption": caption, "slot": slot,
                        "episode": subject, "results": results,
                        "ts": datetime.datetime.utcnow().isoformat() + "Z"})
    save_state(state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["morning", "evening"], required=True,
                    help="Which posting slot (affects log label only).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Generate caption + resolve video but DO NOT call Buffer.")
    args = ap.parse_args()

    env = load_env(ENV_FILE)
    token = env.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print(f"ERROR: BUFFER_ACCESS_TOKEN not found in {ENV_FILE}", file=sys.stderr)
        sys.exit(2)

    # --- RetroByte "Discovering 90s Tech" generated video (preferred) ------
    # If the host generator staged a fresh discovery video, post THAT as a
    # narrated video (video mode on all channels) instead of a random clip.
    #
    # DEDUP GUARD: never post the same discovery episode twice. The generator
    # keys each episode by subject+date; we record posted episodes in state and
    # skip (or consume-and-skip) if we already posted it. This is what prevented
    # the "same video posted multiple times" failure.
    ready = Path(SCRIPT_DIR) / "_discovery_ready.json"
    state = load_state()
    posted_episodes = set(state.get("posted_episodes", []))
    posted_episodes.update(item.get("episode", "") for item in state.get("history", []))
    if ready.exists():
        try:
            disc = json.loads(ready.read_text())
            dvideo = disc.get("video_name")
            dcaption = disc.get("caption")
            dpath = disc.get("video")  # absolute path of staged file
            dep_key = re.sub(r"\s*\|\s*\d{4}-\d{2}-\d{2}$", "", str(disc.get("subject", "?"))).strip().casefold()
            dep_key = " ".join(re.findall(r"[a-z0-9]+", dep_key))
            posted_keys = {" ".join(re.findall(r"[a-z0-9]+", str(x).split("|", 1)[0].casefold()))
                           for x in posted_episodes}
            # Validate the staged file still exists (prefer the real path; fall
            # back to MEDIA_DIR join for backward-compat).
            _exists = os.path.exists(dpath) if dpath else False
            if not _exists and dvideo:
                _exists = os.path.exists(os.path.join(MEDIA_DIR, dvideo))
            if dvideo and dcaption and _exists:
                validate_publish_manifest(disc)
                used_discovery = {str(item.get("video", ""))
                                  for item in state.get("history", [])}
                used_discovery.update(state.get("reserved_videos", []))
                if dvideo in used_discovery:
                    print(f"[{args.slot}] SKIP: discovery clip {dvideo} was already used; refusing to recycle it.")
                    if not args.dry_run:
                        try:
                            ready.unlink()
                        except OSError:
                            pass
                    return
                # Already posted this episode? Consume the flag but do NOT repost.
                if dep_key in posted_keys:
                    print(f"[{args.slot}] SKIP: episode {dep_key} already posted (dedup). Clearing stale ready flag.")
                    try:
                        ready.unlink()
                    except Exception:
                        pass
                    return

                media_url = f"{MEDIA_BASE_URL}/{dvideo}"
                print(f"[{args.slot}] DISCOVERY video: {dvideo}")
                print(f"[{args.slot}] caption: {dcaption}")
                if args.dry_run:
                    print(f"[{args.slot}] DRY-RUN: skipping Buffer. "
                          f"Would post DISCOVERY video to {CHANNEL_SERVICES}.")
                    return
                channels = get_channels(token)
                results = {}
                for svc in CHANNEL_SERVICES:
                    ch = channels.get(svc)
                    if not ch:
                        print(f"[{args.slot}] WARN: channel '{svc}' not connected; skipping",
                              file=sys.stderr)
                        continue
                    # X/Twitter hard-limits captions to 280 chars and Buffer
                    # WEIGHTS multibyte chars — a 271-char caption was still
                    # rejected 2026-09-25. Clamp at 260 and also replace the
                    # weighted '→' for X only. If Buffer still rejects with a
                    # length error, retry once with a hard 200-char cut.
                    _cap = dcaption
                    if svc == "twitter" and len(dcaption) > 260:
                        _cap = dcaption[:257].rsplit(None, 1)[0] + "…"
                    if svc == "twitter":
                        _cap = _cap.replace("→", "->")
                    res = create_post(token, ch["id"], _cap, media_url, svc)
                    tn = res.get("__typename")
                    if tn != "PostActionSuccess" and svc == "twitter" \
                            and "280" in str(res.get("message", "")):
                        _cap = _cap[:197].rsplit(None, 1)[0] + "…"
                        print(f"[{args.slot}] twitter: length reject — retrying at "
                              f"{len(_cap)} chars")
                        res = create_post(token, ch["id"], _cap, media_url, svc)
                        tn = res.get("__typename")
                    results[svc] = (
                        f"OK id={res.get('post', {}).get('id')}"
                        if tn == "PostActionSuccess"
                        else f"ERROR {res.get('message', res)}")
                    print(f"[{args.slot}] {svc}: {results[svc]}")
                # Clear the ready flag ONLY after a successful post so a missed
                # run can retry (the generator also rotates subjects per day).
                if any(v.startswith("OK") for v in results.values()):
                    # Any successful channel retires this discovery video.
                    record_discovery_post(state, disc.get("subject", "?"),
                                          dvideo, dcaption, args.slot, results)
                    try:
                        ready.unlink()
                    except Exception:
                        pass
                    print(f"[{args.slot}] DISCOVERY posted; cleared {ready.name}; recorded episode {dep_key}")
                else:
                    print(f"[{args.slot}] NO successful posts — keeping discovery staged",
                          file=sys.stderr)
                    sys.exit(1)
                return
        except Exception as _e:
            # A network exception may happen after a channel accepted the
            # post. Persist partial success and stop instead of falling back.
            if "results" in locals() and any(
                    value.startswith("OK") for value in results.values()):
                record_discovery_post(state, disc.get("subject", "?"),
                                      dvideo, dcaption, args.slot, results)
                try:
                    ready.unlink()
                except OSError:
                    pass
            print(f"[{args.slot}] discovery processing failed ({_e}); refusing fallback to avoid a duplicate post",
                  file=sys.stderr)
            sys.exit(1)

    print(f"[{args.slot}] SKIP: no video staged with the approved RetroByte format; legacy fallback is disabled.")
    return


if __name__ == "__main__":
    main()
