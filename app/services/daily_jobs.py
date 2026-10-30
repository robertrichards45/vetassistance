"""Once-a-day background jobs (one runner per container, guarded by a file lock)."""
from __future__ import annotations

import fcntl
import os
import threading
import time
from datetime import date


def start(app) -> bool:
    if app.config.get("TESTING") or not os.environ.get("RAILWAY_ENVIRONMENT"):
        return False
    try:
        fh = open("/tmp/vetassistance-daily.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    base = (os.environ.get("PUBLIC_BASE_URL") or "https://www.vetassistance.org").rstrip("/")

    def run_today():
        from app.services import itf_reminders
        from app.services.email_service import send_email
        with app.app_context():
            itf_reminders.run_due(date.today(), base, lambda to, subj, body: send_email(to, subj, body, context="ITF_REMINDER"))

    def loop():
        last = None
        time.sleep(180)
        while True:
            if date.today() != last:
                try:
                    run_today()
                    last = date.today()
                except Exception:
                    app.logger.exception("daily jobs failed; will retry in an hour")
            time.sleep(3600)

    t = threading.Thread(target=loop, name="daily-jobs", daemon=True)
    t._lock_handle = fh
    t.start()
    return True
