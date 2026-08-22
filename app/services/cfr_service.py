import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# How old the generated ratings chart can get before a background rebuild
# is triggered. eCFR.gov itself only changes on the government's own
# amendment schedule, so this doesn't need to be tight — it just keeps the
# "live from eCFR.gov" page from silently going stale for months if nobody
# happens to rerun the build script by hand.
REFRESH_STALE_DAYS = 7
# Once a refresh has been enqueued, don't enqueue another one for this long
# even if the page keeps getting hit while the rebuild is still stale/still
# running — avoids flooding the queue with duplicate jobs.
REFRESH_MARKER_COOLDOWN_SECONDS = 6 * 60 * 60


def _refresh_marker_path(base_dir: str) -> str:
    return os.path.join(base_dir, "cfr_data", ".va_ratings_chart_refresh_triggered_at")


def maybe_trigger_stale_refresh(base_dir: str, payload: Dict[str, Any]) -> None:
    """Enqueues a background rebuild of the VA ratings chart from the live
    eCFR.gov API if the current one is older than REFRESH_STALE_DAYS, so the
    page self-refreshes over time instead of relying on someone remembering
    to rerun scripts/build_va_ratings_chart_ecfr.py by hand. Safe to call on
    every request: it's a no-op unless the data is actually stale, and any
    failure (no Redis configured, no worker running) is swallowed so page
    rendering is never affected."""
    generated_at = payload.get("generated_at")
    if not generated_at:
        return
    try:
        generated = datetime.fromisoformat(generated_at)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - generated).total_seconds() / 86400
    except (ValueError, TypeError):
        return
    if age_days < REFRESH_STALE_DAYS:
        return

    marker = _refresh_marker_path(base_dir)
    try:
        if os.path.exists(marker) and (time.time() - os.path.getmtime(marker)) < REFRESH_MARKER_COOLDOWN_SECONDS:
            return
    except OSError:
        pass

    try:
        from app.services.queue import get_queue
        from scripts.build_va_ratings_chart_ecfr import build_chart
        get_queue("default").enqueue(build_chart)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def load_va_ratings_chart(base_dir: str) -> Dict[str, Any]:
    """Loads cfr_data/va_ratings_chart.json and groups it for display.
    Shared by the public /cfr/ratings-chart route and the staff
    /employee/hub/ratings route so the two stay in sync."""
    data_path = os.path.join(base_dir, "cfr_data", "va_ratings_chart.json")
    result = {
        "has_data": os.path.exists(data_path),
        "source": None,
        "source_part_url": None,
        "effective_date": None,
        "generated_at": None,
        "count": 0,
        "grouped": [],
        "categories": [],
    }
    if not result["has_data"]:
        return result

    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    result["source"] = payload.get("source")
    result["source_part_url"] = payload.get("source_part_url")
    result["effective_date"] = payload.get("effective_date")
    result["generated_at"] = payload.get("generated_at")
    conditions = payload.get("conditions", [])
    result["count"] = len(conditions)

    def _category_for(section: Optional[str]) -> str:
        s = (section or "").lower()
        if s.startswith("4.130"):
            return "Mental Health"
        if s.startswith(("4.16", "4.25", "4.26", "4.28", "4.29", "4.30")):
            return "Secondary"
        return "Physical Health"

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for c in conditions:
        buckets.setdefault(_category_for(c.get("cfr_section")), []).append(c)

    for cat in ["Physical Health", "Mental Health", "Secondary"]:
        items = buckets.get(cat, [])
        if not items:
            continue
        items.sort(key=lambda x: (x.get("condition") or ""))
        result["grouped"].append({"category": cat, "items_list": items})
    result["categories"] = [g["category"] for g in result["grouped"]]
    maybe_trigger_stale_refresh(base_dir, payload)
    return result


def find_condition_by_dc(base_dir: str, diagnostic_code: str) -> Optional[Dict[str, Any]]:
    """Looks up a single condition's raw entry (condition, diagnostic_code,
    cfr_section, criteria, source_url) from cfr_data/va_ratings_chart.json by
    diagnostic code."""
    data_path = os.path.join(base_dir, "cfr_data", "va_ratings_chart.json")
    if not os.path.exists(data_path):
        return None
    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    for c in payload.get("conditions", []):
        if str(c.get("diagnostic_code") or "") == str(diagnostic_code or ""):
            return c
    return None


class CFRService:
    """
    Loads CFR rules from a local JSON file now.
    Later: swap load method to pull from an API (and cache locally).
    """

    def __init__(self, base_dir: str, json_rel_path: str = "cfr_data/cfr38_rules.json"):
        self.base_dir = base_dir
        self.json_path = os.path.join(base_dir, json_rel_path)
        self._data: Dict[str, Any] = {}
        self._rules: List[Dict[str, Any]] = []

    def load(self) -> None:
        if not os.path.exists(self.json_path):
            self._data = {"version": "missing", "rules": []}
            self._rules = []
            return

        with open(self.json_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        self._rules = self._data.get("rules", [])

    @property
    def version(self) -> str:
        return str(self._data.get("version", "unknown"))

    def find_best_match(self, condition_name: str) -> Optional[Dict[str, Any]]:
        """
        Match a scan finding condition name to CFR rule by:
        - direct key match
        - alias match (substring)
        """
        if not condition_name:
            return None

        q = condition_name.strip().lower()

        # direct key
        for r in self._rules:
            if r.get("key", "").lower() == q:
                return r

        # alias substring
        best = None
        best_score = 0
        for r in self._rules:
            aliases = [a.lower() for a in r.get("aliases", [])]
            score = 0
            for a in aliases:
                if a and a in q:
                    score += max(3, len(a))
            if score > best_score:
                best_score = score
                best = r

        # also try reverse containment (finding name in alias)
        if not best:
            for r in self._rules:
                aliases = [a.lower() for a in r.get("aliases", [])]
                score = 0
                for a in aliases:
                    if q and q in a:
                        score += max(3, len(q))
                if score > best_score:
                    best_score = score
                    best = r

        return best


    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        if not self._rules:
            self.load()

        tokens = [t for t in re.split(r"\W+", q) if t]
        scored = []
        for rule in self._rules:
            hay = " ".join([
                str(rule.get("key") or ""),
                str(rule.get("title") or ""),
                str(rule.get("citation") or ""),
                str(rule.get("text") or ""),
            ]).lower()
            overlap = sum(1 for t in tokens if t and t in hay)
            if overlap == 0 and q not in hay:
                continue
            score = overlap + (5 if q in hay else 0)
            scored.append((score, rule))
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, rule in scored[:limit]:
            text = (rule.get("text") or "").strip()
            summary = text[:700] + ("…" if len(text) > 700 else "")
            results.append({
                "key": rule.get("key"),
                "title": rule.get("title") or rule.get("key"),
                "citation": rule.get("citation") or "",
                "summary": summary,
            })
        return results

    def enrich_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Add CFR fields to finding dicts:
        - cfr
        - diagnostic_code
        - rating_criteria
        - evidence_tips
        """
        enriched = []
        for f in findings:
            name = (f.get("condition_name") or f.get("name") or "").strip()
            rule = self.find_best_match(name)

            item = dict(f)
            if rule:
                item["cfr"] = rule.get("cfr")
                item["diagnostic_code"] = rule.get("diagnostic_code")
                item["rating_criteria"] = rule.get("rating_criteria", [])
                item["evidence_tips"] = rule.get("evidence_tips", [])
                item["cfr_title"] = rule.get("title")
                item["cfr_key"] = rule.get("key")
            else:
                item["cfr"] = None
                item["diagnostic_code"] = None
                item["rating_criteria"] = []
                item["evidence_tips"] = []
                item["cfr_title"] = None
                item["cfr_key"] = None

            enriched.append(item)

        return enriched
