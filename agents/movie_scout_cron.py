"""
Headless movie scout runner — called by cron to send weekly recommendations.
Imports run_movie_scout and send_email directly from nuncio.py, bypassing
the interactive confirmation gate.
"""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(__file__))
from nuncio import run_movie_scout, send_email, append_action_log

RECIPIENT = "vernie.oliveiro@gmail.com"

def main():
    print(f"[{datetime.datetime.now().isoformat()}] Movie scout cron starting.")

    try:
        digest = run_movie_scout()
    except Exception as e:
        print(f"[movie_scout_cron] run_movie_scout failed: {e}")
        append_action_log("movie_scout_cron", {"recipient": RECIPIENT}, f"ERROR: {e}", "cron")
        sys.exit(1)

    if not digest or digest.startswith("Movie scout did not complete"):
        print(f"[movie_scout_cron] Scout returned no results: {digest}")
        append_action_log("movie_scout_cron", {"recipient": RECIPIENT}, f"No results: {digest}", "cron")
        sys.exit(1)

    today = datetime.datetime.now().strftime("%d %B %Y")
    subject = f"[Nuncio] Weekly Movie Recommendations — {today}"
    body = f"{digest}\n\nEmail sent by Nuncio, Vernie's agent"

    try:
        result = send_email(RECIPIENT, subject, body)
        print(f"[movie_scout_cron] {result}")
        append_action_log("movie_scout_cron", {"recipient": RECIPIENT, "subject": subject}, result, "cron")
    except Exception as e:
        print(f"[movie_scout_cron] send_email failed: {e}")
        append_action_log("movie_scout_cron", {"recipient": RECIPIENT, "subject": subject}, f"ERROR: {e}", "cron")
        sys.exit(1)

if __name__ == "__main__":
    main()
