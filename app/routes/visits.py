"""Real-visitor beacon. The page sends it only after a person interacts; bots that just fetch HTML never do."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from hashlib import sha256
from urllib.parse import urlparse

from flask import Blueprint, current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.extensions import SessionLocal, csrf
from app.models import HumanVisit

visits_bp = Blueprint("visits", __name__)
BOT_UA = re.compile(r"bot|spider|crawl|slurp|headless|phantom|selenium|puppeteer|playwright|lighthouse|preview|python|curl|wget", re.I)


def _signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="human-visit")


def page_token(path: str) -> str:
    return _signer().dumps(path[:255])


def _ip_hash() -> str:
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or request.remote_addr or ""
    return sha256((ip + current_app.config.get("SECRET_KEY", "")).encode("utf-8")).hexdigest() if ip else ""


@visits_bp.post("/v")
@csrf.exempt
def beacon():
    try:
        path = _signer().loads(request.form.get("t", ""), max_age=6 * 3600)
    except BadSignature:
        return ("", 204)
    if BOT_UA.search(request.headers.get("User-Agent") or ""):
        return ("", 204)
    signal = (request.form.get("s") or "")[:20]
    if signal not in ("scroll", "pointer", "touch", "key", "click"):
        return ("", 204)
    try:
        secs = max(0, min(3600, int(request.form.get("d") or 0)))
    except ValueError:
        secs = 0
    ref = urlparse(request.form.get("r") or "").netloc.lower()[:120]
    if ref.endswith(request.host.split(":")[0]):
        ref = ""
    ua = (request.headers.get("User-Agent") or "").lower()
    ip = _ip_hash()
    db = SessionLocal()
    try:
        recent = (db.query(HumanVisit.id).filter(HumanVisit.ip_hash == ip, HumanVisit.path == path,
                                                 HumanVisit.created_at >= datetime.utcnow() - timedelta(seconds=30)).first())
        if not recent:
            db.add(HumanVisit(ip_hash=ip, path=path, referrer_host=ref, signal=signal, seconds_on_page=secs,
                              device="mobile" if ("mobi" in ua or "android" in ua or "iphone" in ua) else "desktop"))
            db.commit()
    finally:
        db.close()
    return ("", 204)
