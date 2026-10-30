"""Watch the sister sites from this server and email the owner if one stops responding.

One watcher per container (a file lock keeps extra gunicorn workers from duplicating it).
A site must fail two checks in a row before an alert; at most one alert per site every 6 hours,
plus one 'back up' note when it recovers.
"""
from __future__ import annotations

import fcntl
import os
import threading
import time
import urllib.request

SITES = {
    "resumebuilder.shop": "https://resumebuilder.shop/",
    "createdocuments.org": "https://createdocuments.org/health",
}
INTERVAL = 15 * 60
ALERT_GAP = 6 * 3600


def check(url: str, timeout: int = 20) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VetAssistance-uptime/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


class Watcher:
    def __init__(self, send, checker=check, now=time.time):
        self.send, self.checker, self.now = send, checker, now
        self.fails = {k: 0 for k in SITES}
        self.alerted = {k: 0.0 for k in SITES}
        self.down = {k: False for k in SITES}

    def run_once(self) -> None:
        for name, url in SITES.items():
            if self.checker(url):
                if self.down[name]:
                    self.send(f"{name} is back up", f"{name} is responding again as of {time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.now()))} UTC.")
                self.fails[name], self.down[name] = 0, False
                continue
            self.fails[name] += 1
            if self.fails[name] >= 2 and self.now() - self.alerted[name] >= ALERT_GAP:
                self.alerted[name], self.down[name] = self.now(), True
                self.send(f"{name} looks down",
                          f"{url} did not respond on two checks in a row (checked from vetassistance.org every 15 minutes).\n\n"
                          "Open Railway and look at that site's latest deployment and logs.")


def start(app) -> bool:
    if app.config.get("TESTING") or os.environ.get("UPTIME_WATCH", "1") != "1" or not os.environ.get("RAILWAY_ENVIRONMENT"):
        return False
    try:
        fh = open("/tmp/vetassistance-uptime.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False  # another worker in this container already watches
    to = os.environ.get("ALERT_EMAIL") or app.config.get("SUPPORT_EMAIL")

    def send(subject: str, body: str) -> None:
        try:
            from app.services.email_service import send_email
            with app.app_context():
                send_email(to, subject, body, context="UPTIME_ALERT")
        except Exception:
            pass

    w = Watcher(send)

    def loop():
        time.sleep(120)
        while True:
            w.run_once()
            time.sleep(INTERVAL)

    t = threading.Thread(target=loop, name="uptime-watch", daemon=True)
    t._lock_handle = fh  # keep the lock for the life of the process
    t.start()
    return True
