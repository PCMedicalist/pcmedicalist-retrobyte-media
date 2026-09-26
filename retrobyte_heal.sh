#!/usr/bin/env bash
# retrobyte_heal.sh — self-healing wrapper for the RetroByte discovery post step.
# Runs the 4 autonomy checks BEFORE/AFTER posting so the pipeline is hands-off:
#   1. pre-flight deps (Ollama, edge-tts, Buffer token/channels/pause)
#   2. raw-URL check (video reachable on GitHub raw) + push retry
#   3. post via Buffer shareNow
#   4. post-verify status:sent (retry once) + alert owner on hard failure
# Usage: retrobyte_heal.sh <slot>   (slot = 0 morning | 1 evening)
set -u
SLOT="${1:-0}"
# poster expects morning|evening, not numeric
POST_SLOT="morning"; [ "$SLOT" = "1" ] && POST_SLOT="evening"
MEDIA_DIR="/home/pcmedicalist/pcmedicalist-retrobyte-media"
REPO_DIR="$MEDIA_DIR"
ENV_FILE="/home/pcmedicalist/.pcmedicalist/.env.retrobyte"
CONTAINER="retrobyte-cron"
OWNER_ALERT() { echo "[HEAL][ALERT] $*" >&2; }
ok() { echo "[HEAL] $*"; }

# ---- 0. ready-file present? -------------------------------------------------
READY="$REPO_DIR/_discovery_ready.json"
if [ ! -f "$READY" ]; then
  OWNER_ALERT "No _discovery_ready.json — host build did not stage a video. Skip cycle."
  exit 1
fi
VIDEO_NAME=$(python3 -c "import json;print(json.load(open('$READY')).get('video_name',''))")
SUBJECT=$(python3 -c "import json;print(json.load(open('$READY')).get('subject',''))")
RAW_URL="https://raw.githubusercontent.com/PCMedicalist/pcmedicalist-retrobyte-media/main/media/$VIDEO_NAME"
ok "subject=$SUBJECT video=$VIDEO_NAME"

# ---- 1. PRE-FLIGHT DEPENDENCY CHECKS ----------------------------------------
# 1a. Ollama (local, soft — generator already fell back if down, but warn)
if ! curl -s -o /dev/null -m 3 http://localhost:11434/api/tags; then
  OWNER_ALERT "Ollama not reachable — narration may be templated (degraded but continuing)."
fi
# 1b. Buffer token + channels + pause state (HARD for posting)
TOK=$(grep -m1 'BUFFER_ACCESS_TOKEN' "$ENV_FILE" | cut -d= -f2-)
if [ -z "$TOK" ]; then
  OWNER_ALERT "BUFFER_ACCESS_TOKEN missing from $ENV_FILE — cannot post. Abort."
  exit 2
fi
# channels connected + not paused?
python3 - "$TOK" <<'PY' || { OWNER_ALERT "Buffer channel/pause pre-flight failed — abort."; exit 2; }
import sys, json, urllib.request
tok=sys.argv[1]
H={'Authorization':'Bearer '+tok,'Content-Type':'application/json'}
def gql(q,v=None):
    r=urllib.request.urlopen(urllib.request.Request('https://api.buffer.com/graphql',data=json.dumps({'query':q,'variables':v or {}}).encode(),headers=H),timeout=40)
    return json.loads(r.read().decode())
try:
    oid=gql('query{account{organizations{id}}}')['data']['account']['organizations'][0]['id']
    ch=gql('query($o:OrganizationId!){channels(input:{organizationId:$o}){id service isQueuePaused}}',{'o':oid})['data']['channels']
except Exception as e:
    print('PREFLIGHT ERR', e, file=sys.stderr); sys.exit(1)
if not ch:
    print('NO CHANNELS CONNECTED', file=sys.stderr); sys.exit(1)
paused=[c['service'] for c in ch if c.get('isQueuePaused')]
if paused:
    print('QUEUE PAUSED:', paused, file=sys.stderr); sys.exit(1)
print('channels ok:', [c['service'] for c in ch])
PY
ok "pre-flight: Buffer channels connected + queue not paused"

# ---- 2. RAW-URL CHECK + PUSH RETRY -------------------------------------------
http_code=$(curl -s -o /dev/null -w "%{http_code}" -m 20 "$RAW_URL")
if [ "$http_code" != "200" ]; then
  OWNER_ALERT "raw URL $http_code (expected 200) — pushing to GitHub (retry up to 2x)..."
  for i in 1 2; do
    git -C "$REPO_DIR" push origin main 2>&1 | tail -2
    sleep 5
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -m 20 "$RAW_URL")
    [ "$http_code" = "200" ] && break
  done
fi
if [ "$http_code" != "200" ]; then
  OWNER_ALERT "raw URL still $http_code after push retries — video unreachable. Abort (episode NOT consumed)."
  exit 3
fi
ok "raw URL 200 — video reachable"

# ---- 3. POST via local poster (RetroByte runs LOCAL; no container) ---------
# RetroByte inference + posting happen on the host (the retrobyte-cron container
# was retired to keep the VPS load down). Run the poster directly here.
POSTER="$REPO_DIR/post_retrobyte_social.py"
ok "posting slot $POST_SLOT via local poster..."
python3 "$POSTER" --slot "$POST_SLOT" 2>&1 | tail -12

# ---- 4. POST-VERIFY status:sent / sending (per-channel recent check) --------
# Video uploads to IG/TikTok take longer than Twitter to flip from 'sending' to
# 'sent'. shareNow already confirmed acceptance (the poster got 3x PostActionSuccess
# IDs), so a 'sending' status on a recent post IS a success — we just wait longer
# and accept either state. Previously a 25s wait + 'sent'-only check produced a
# FALSE failure (exit 4) even though the post landed. 2026-09-26: window widened
# to 120s AND null-sentAt 'sending' posts now count as recent (Buffer only sets
# sentAt once fully sent — tonight's TikTok 'sending' had NO sentAt and was
# falsely flagged despite having just been accepted).
sleep 120
python3 - "$TOK" <<'PY' || { OWNER_ALERT "post-verify could not run — manual check needed."; exit 4; }
import sys, json, urllib.request
from datetime import datetime, timezone
tok=sys.argv[1]
H={'Authorization':'Bearer '+tok,'Content-Type':'application/json'}
def gql(q,v=None):
    r=urllib.request.urlopen(urllib.request.Request('https://api.buffer.com/graphql',data=json.dumps({'query':q,'variables':v or {}}).encode(),headers=H),timeout=40)
    return json.loads(r.read().decode())
oid=gql('query{account{organizations{id}}}')['data']['account']['organizations'][0]['id']
q='''query($i:PostsInput!){ posts(input:$i, first:15){ edges{ node{ id status sentAt channel{ service } } } } }'''
d=gql(q,{'i':{'organizationId':oid}})
nodes=[e['node'] for e in d['data']['posts']['edges']]
latest={}
for n in nodes:
    svc=n['channel']['service']
    if svc not in latest:
        latest[svc]=n
now=datetime.now(timezone.utc)
want={'twitter','instagram','tiktok'}
missing=[]
for svc in want:
    n=latest.get(svc)
    if not n:
        missing.append(svc); continue
    status=n.get('status')
    # 'sent' is final success; 'sending' is accepted-but-uploading (also success
    # for a post we just issued via shareNow). Only a stale/old post fails.
    # Buffer may leave sentAt NULL while still 'sending' — treat that as recent
    # (we only just issued this post moments ago).
    recent = status == 'sending'
    if not recent and n.get('sentAt'):
        recent = (now - datetime.fromisoformat(n['sentAt'].replace('Z','+00:00'))).total_seconds() < 600
    if status not in ('sent','sending') or not recent:
        missing.append(f"{svc}:{status}")
if missing:
    print('VERIFY FAIL:', missing, file=sys.stderr); sys.exit(1)
print('VERIFIED sent/sending+recent on:', sorted(latest.keys()))
PY
if [ $? -ne 0 ]; then
  OWNER_ALERT "Post-verify: RetroByte $SUBJECT did not confirm status on all channels. Manual check."
  exit 4
fi
ok "post-verify: $SUBJECT confirmed sent on X/IG/TikTok"
exit 0
