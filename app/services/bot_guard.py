"""Lightweight bot protection for public forms: a hidden honeypot field plus a signed
timestamp. Bots fill every field and submit instantly; people don't."""
from __future__ import annotations

import re

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

MIN_SECONDS = 3
MAX_SECONDS = 60 * 60 * 6


def _s():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="form-timer")


def form_token() -> str:
    return _s().dumps("ok")


def looks_like_bot() -> bool:
    if (request.form.get("website") or "").strip():
        return True
    tok = request.form.get("form_ts") or ""
    if not tok:
        return True
    try:
        _value, issued = _s().loads(tok, max_age=MAX_SECONDS, return_timestamp=True)
    except BadSignature:
        return True
    from datetime import datetime, timezone
    age = (datetime.now(timezone.utc) - issued).total_seconds()
    return age < MIN_SECONDS


def gibberish_name(name: str) -> bool:
    """Random-looking single-word names like 'zrutmuwolk' (no space, long, few vowels or consonant runs)."""
    n = (name or "").strip().lower()
    if not n or " " in n or not n.isalpha() or len(n) < 7:
        return False
    vowels = sum(ch in "aeiouy" for ch in n) / len(n)
    return vowels < 0.3 or bool(re.search(r"[^aeiouy]{5,}", n))
