"""
Live 38 CFR Part 4 (VA Schedule for Rating Disabilities) text, pulled from the
official eCFR.gov versioner API — not the ecfr.io mirror the old build script
scraped, which produced misaligned/garbled table text.

Cached to disk with a TTL so normal requests never block on an outbound
fetch; callers get whatever was last successfully fetched if eCFR.gov is
briefly unreachable.
"""
import json
import os
import re
import time
from html import unescape
from typing import Any, Dict, List, Optional

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_PATH = os.path.join(ROOT, "cfr_data", "ecfr_live_cache.json")
CACHE_TTL_SECONDS = 24 * 60 * 60

TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles.json"
FULL_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-38.xml?part=4"
SOURCE_URL = "https://www.ecfr.gov/current/title-38/chapter-I/part-4"

_NUMERIC_ENTITY_RE = re.compile(r"&#x([0-9a-fA-F]+);|&#(\d+);")
_TAG_RE = re.compile(r"<[^>]+>")
_CLOSE_CELL_RE = re.compile(r"</(TD|TH)>", re.IGNORECASE)
_BREAK_TAG_RE = re.compile(r"</(TR|P|HD1|HD2|HD3|HD4|FP|FP-2|PSPACE)>", re.IGNORECASE)
_DIV8_RE = re.compile(r'<DIV8\s+N="([^"]+)"[^>]*TYPE="SECTION"[^>]*>(.*?)</DIV8>', re.DOTALL)
_HEAD_RE = re.compile(r"<HEAD>(.*?)</HEAD>", re.DOTALL)


def _decode_entities(text: str) -> str:
    def _numeric(match: "re.Match[str]") -> str:
        hex_val, dec_val = match.group(1), match.group(2)
        code_point = int(hex_val, 16) if hex_val else int(dec_val)
        return chr(code_point)

    text = _NUMERIC_ENTITY_RE.sub(_numeric, text)
    return unescape(text)


def _xml_fragment_to_text(fragment: str) -> str:
    with_breaks = _CLOSE_CELL_RE.sub(" | ", fragment)
    with_breaks = _BREAK_TAG_RE.sub("\n", with_breaks)
    with_breaks = _TAG_RE.sub("", with_breaks)
    decoded = _decode_entities(with_breaks)
    lines = []
    for raw_line in decoded.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line)
        line = re.sub(r"\s*\|\s*$", "", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _extract_va_sections(xml: str) -> List[Dict[str, Any]]:
    sections = []
    for match in _DIV8_RE.finditer(xml):
        identifier = match.group(1)
        inner = match.group(2)
        head_match = _HEAD_RE.search(inner)
        if head_match:
            heading = _xml_fragment_to_text(head_match.group(1)).replace("\n", " ")
            body = inner[head_match.end():]
        else:
            heading = identifier
            body = inner
        sections.append({
            "identifier": identifier,
            "heading": heading,
            "text": _xml_fragment_to_text(body),
        })
    return sections


def _fetch_live() -> Dict[str, Any]:
    titles_resp = requests.get(TITLES_URL, timeout=30)
    titles_resp.raise_for_status()
    titles = titles_resp.json().get("titles", [])
    title_info = next((t for t in titles if t.get("number") == 38), None)
    date = (title_info or {}).get("latest_issue_date") or time.strftime("%Y-%m-%d")

    xml_resp = requests.get(FULL_URL.format(date=date), timeout=60)
    xml_resp.raise_for_status()
    sections = _extract_va_sections(xml_resp.text)

    return {
        "effective_date": date,
        "source_url": SOURCE_URL,
        "sections": sections,
        "fetched_at": time.time(),
    }


def get_live_va_data(force_refresh: bool = False) -> Dict[str, Any]:
    """Cache-or-fetch. Falls back to a stale cache (or raises) if eCFR.gov is
    unreachable, rather than ever silently serving fabricated data."""
    cached = None
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, json.JSONDecodeError):
            cached = None

    if not force_refresh and cached and (time.time() - cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
        return cached

    try:
        fresh = _fetch_live()
    except requests.RequestException:
        if cached:
            return cached
        raise

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, indent=2)
    return fresh


def find_section(data: Dict[str, Any], identifier: str) -> Optional[Dict[str, Any]]:
    for section in data.get("sections", []):
        if section.get("identifier") == identifier:
            return section
    return None
