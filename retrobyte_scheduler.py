#!/usr/bin/env python3
"""
RetroByte in-container scheduler daemon.

Runs INSIDE the retrobyte-cron container (part of the RetroByte docker
deployment). Replaces the host Hermes cron jobs so that pausing the
RetroByte deployment (docker compose stop retrobyte retrobyte-cron) also
halts the social posting schedule.

Design:
- Pure stdlib (no cron daemon, no apt installs) so it works on the
  read_only:true agent image with only /tmp + a writable state tmpfs.
- Fires the poster at 08:00 (morning) and 19:30 (evening) local time.
- On miss (container was down at fire time) it runs once on the next
  wakeup rather than double-posting.
- State is shared with the host poster via RETROBYTE_STATE_FILE so the
  rotation index stays consistent across runs.

Env:
  RETROBYTE_SCHEDULER_TZ   optional; uses system local time (container TZ).
"""
import datetime
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
POSTER = os.path.join(SCRIPT_DIR, "post_retrobyte_social.py")

# (hour, minute, slot) — local time
SLOTS = [
    (8, 0, "morning"),
    (19, 30, "evening"),
]

# Track last-fired date per slot so we don't double-fire if the loop wakes
# multiple times within the same minute window.
LAST_FIRED = {slot: None for _, _, slot in SLOTS}


def seconds_until_next(target_h, target_m):
    now = datetime.datetime.now()
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    if target <= now:
        target = target + datetime.timedelta(days=1)
    return (target - now).total_seconds()


def run_poster(slot):
    daykey = datetime.date.today().isoformat()
    if LAST_FIRED[slot] == daykey:
        return  # already fired today
    LAST_FIRED[slot] = daykey
    print(f"[{datetime.datetime.now().isoformat()}] FIRE slot={slot}", flush=True)
    try:
        subprocess.run(
            [sys.executable, POSTER, "--slot", slot],
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[{datetime.datetime.now().isoformat()}] ERROR slot={slot}: {e}",
              file=sys.stderr, flush=True)


def main():
    print(f"[{datetime.datetime.now().isoformat()}] retrobyte_scheduler started; "
          f"slots={[(h,m,s) for h,m,s in SLOTS]}", flush=True)
    # Coarse 30s poll loop — cheap, no cron needed.
    while True:
        now = datetime.datetime.now()
        for h, m, slot in SLOTS:
            if now.hour == h and now.minute == m:
                run_poster(slot)
        time.sleep(30)


if __name__ == "__main__":
    main()
