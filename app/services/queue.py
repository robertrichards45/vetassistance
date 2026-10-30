"""Background jobs.

If a Redis-backed RQ worker is actually running, jobs go to it. Otherwise (the
usual Railway setup: one web service, no worker) the job runs in a background
thread inside the web process, so pages return immediately and long AI calls
never hit the web server's request timeout.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from flask import current_app

log = logging.getLogger("vetassistance")
_worker_check = {"at": 0.0, "ok": False}


def get_redis():
    from redis import Redis
    return Redis.from_url(current_app.config["REDIS_URL"], socket_connect_timeout=2, socket_timeout=5)


def get_queue(name: str = "default"):
    from rq import Queue
    return Queue(name, connection=get_redis(), default_timeout=900)


def _rq_worker_available() -> bool:
    """True only when REDIS_URL is set and at least one RQ worker is registered (cached 60s)."""
    if not os.environ.get("REDIS_URL"):
        return False
    now = time.time()
    if now - _worker_check["at"] < 60:
        return _worker_check["ok"]
    ok = False
    try:
        from rq import Worker
        ok = len(Worker.all(connection=get_redis())) > 0
    except Exception:
        ok = False
    _worker_check.update(at=now, ok=ok)
    return ok


def enqueue(func, *args, queue: str = "default", **kwargs) -> str:
    """Run func(*args) in the background. Returns 'rq' or 'thread'."""
    if _rq_worker_available():
        try:
            get_queue(queue).enqueue(func, *args, **kwargs)
            return "rq"
        except Exception as exc:
            log.warning("RQ enqueue failed, running in a thread instead: %s", exc)
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            from app.extensions import SessionLocal
            try:
                func(*args, **kwargs)
            except Exception:
                log.exception("background job %s failed", getattr(func, "__name__", func))
            finally:
                SessionLocal.remove()

    threading.Thread(target=run, name=f"job-{getattr(func, '__name__', 'task')}", daemon=True).start()
    return "thread"
