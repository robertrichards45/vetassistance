"""IndexNow: tell Bing (and Yandex, Seznam, Naver) about new or changed pages right away.

The key is public by design: search engines fetch /<key>.txt to confirm the site owns it.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("vetassistance")
DEFAULT_KEY = "22328f3d050e2b8135c7f83da11e9cb1"


def key() -> str:
    return (os.environ.get("INDEXNOW_KEY") or DEFAULT_KEY).strip()


def submit(urls: list[str], host: str) -> tuple[bool, str]:
    urls = [u for u in urls if u.startswith("https://")][:10000]
    if not urls:
        return False, "No public https URLs to submit."
    try:
        import requests
        r = requests.post("https://api.indexnow.org/indexnow", timeout=20, json={
            "host": host, "key": key(), "keyLocation": f"https://{host}/{key()}.txt", "urlList": urls})
        ok = r.status_code in (200, 202)
        return ok, f"Search engines answered {r.status_code}" + ("" if ok else f": {r.text[:200]}")
    except Exception as exc:  # network trouble must never break a page
        log.warning("IndexNow submit failed: %s", exc)
        return False, str(exc)[:200]
