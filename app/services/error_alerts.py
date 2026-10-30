"""Email the site owner when a request crashes. At most one email every 15 minutes; never raises."""
from __future__ import annotations

import os
import time
from datetime import datetime

from flask import got_request_exception, request

_last = {"t": 0.0}
WINDOW = 900


def notify(app, path: str, method: str, exc_name: str) -> bool:
    now = time.time()
    to = os.environ.get("ALERT_EMAIL") or app.config.get("SUPPORT_EMAIL")
    if not to or app.config.get("TESTING") or now - _last["t"] < WINDOW:
        return False
    _last["t"] = now
    try:
        from app.services.email_service import send_email
        send_email(to, "Server error on vetassistance.org",
                   f"{method} {path} failed with {exc_name} at {datetime.utcnow():%Y-%m-%d %H:%M} UTC.\n\n"
                   "No client data is included in this alert. Check the Railway logs for details.\n"
                   "You'll get at most one of these every 15 minutes.",
                   context="ERROR_ALERT", background=True)
    except Exception:
        pass
    return True


def install(app) -> None:
    def _on_exception(sender, exception, **extra):
        try:
            notify(sender, request.path, request.method, type(exception).__name__)
        except Exception:
            pass
    got_request_exception.connect(_on_exception, app, weak=False)
